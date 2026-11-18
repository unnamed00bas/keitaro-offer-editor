from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app import main, services
from app.config import Settings, get_settings
from app.db import get_db


@pytest.fixture
def stream(db, kt, settings):
    c = services.create_campaign(db, kt, settings, "race", "AU", 11)
    st = next(s for s in c.streams if s.has_offers)
    services.add_offer(db, kt, st.id, 12)
    services.push_stream(db, kt, st.id)  # KT: 11:50, 12:50
    db.commit()
    return st


def state(st):
    return {r.offer_id: (r.share, r.status) for r in st.offers}


def row(st, oid):
    return next(r for r in st.offers if r.offer_id == oid)


# ---------------- защита от гонки при Push


def test_push_conflict_when_kt_changed_after_fetch(db, kt, stream):
    services.add_offer(db, kt, stream.id, 13)
    kt.streams[stream.kt_id]["offers"] = [{"offer_id": 11, "share": 70}, {"offer_id": 14, "share": 30}]
    with pytest.raises(services.PushConflict) as e:
        services.push_stream(db, kt, stream.id)
    assert e.value.diff == {
        "added_in_kt": [14],
        "removed_in_kt": [12],
        "share_changed": [{"offer_id": 11, "was": 50, "now": 70}],
    }
    assert len(kt.puts) == 1  # второй PUT не ушёл
    services.push_stream(db, kt, stream.id, force=True)
    assert kt.kt_shares(stream.kt_id) == {11: 33, 12: 33, 13: 34}


def test_push_without_kt_changes_passes(db, kt, stream):
    services.remove_offer(db, stream.id, 12)
    services.push_stream(db, kt, stream.id)
    assert kt.kt_shares(stream.kt_id) == {11: 100}


# ---------------- Fetch сливает черновик с Keitaro


def test_fetch_keeps_local_add_and_applies_kt_delete(db, kt, stream):
    services.add_offer(db, kt, stream.id, 13)  # черновик: 33/33/34
    kt.streams[stream.kt_id]["offers"] = [{"offer_id": 11, "share": 100}]  # в KT удалили 12
    services.fetch_campaign(db, kt, stream.campaign.kt_id)
    assert state(stream) == {11: (50, "active"), 12: (0, "removed"), 13: (50, "active")}
    assert row(stream, 12).removed_in_kt and row(stream, 12).conflict
    assert row(stream, 13).pub_status is None  # локальное добавление ждёт Push
    assert services.is_draft(stream)
    services.push_stream(db, kt, stream.id)  # снапшот обновился на Fetch, конфликта нет
    assert kt.kt_shares(stream.kt_id) == {11: 50, 13: 50}


def test_fetch_keeps_local_remove_and_pin(db, kt, stream):
    services.add_offer(db, kt, stream.id, 13)
    services.push_stream(db, kt, stream.id)  # 33/33/34
    services.pin_offer(db, stream.id, 11, True, 40)
    services.remove_offer(db, stream.id, 13)  # черновик: 11:40 pin, 12:60, 13 removed
    kt.streams[stream.kt_id]["offers"].append({"offer_id": 15, "share": 0})  # в KT добавили оффер
    services.fetch_campaign(db, kt, stream.campaign.kt_id)
    s = state(stream)
    assert s[11] == (40, "active") and row(stream, 11).pinned
    assert s[13] == (0, "removed") and row(stream, 13).pub_status == "active"
    assert s[15][1] == "active" and row(stream, 15).conflict
    assert s[12][0] + s[15][0] == 60
    assert services.is_draft(stream)


def test_fetch_flags_share_conflict_and_keeps_draft(db, kt, stream):
    services.pin_offer(db, stream.id, 11, True, 20)  # черновик 20/80
    kt.streams[stream.kt_id]["offers"] = [{"offer_id": 11, "share": 90}, {"offer_id": 12, "share": 10}]
    services.fetch_campaign(db, kt, stream.campaign.kt_id)
    assert state(stream) == {11: (20, "active"), 12: (80, "active")}
    assert "90%" in row(stream, 11).conflict
    assert row(stream, 11).pub_share == 90


def test_fetch_without_draft_takes_kt_state(db, kt, stream):
    kt.streams[stream.kt_id]["offers"] = [{"offer_id": 11, "share": 71}, {"offer_id": 12, "share": 29}]
    services.fetch_campaign(db, kt, stream.campaign.kt_id)
    assert state(stream) == {11: (71, "active"), 12: (29, "active")}
    assert not services.is_draft(stream)
    assert all(r.conflict is None for r in stream.offers)


def test_cancel_clears_conflicts(db, kt, stream):
    services.add_offer(db, kt, stream.id, 13)
    kt.streams[stream.kt_id]["offers"] = [{"offer_id": 11, "share": 100}]
    services.fetch_campaign(db, kt, stream.campaign.kt_id)
    services.cancel_draft(db, stream.id)
    assert state(stream) == {11: (100, "active"), 12: (0, "removed")}
    assert all(r.conflict is None for r in stream.offers)


# ---------------- статистика


def test_campaign_stats_aggregates_and_trend(kt):
    kt.report_rows = [
        {"stream_id": 5, "offer_id": 11, "day": "2026-11-12", "clicks": 2, "conversions": 0, "revenue": 0},
        {"stream_id": 5, "offer_id": 11, "day": "2026-11-17", "clicks": 6, "conversions": 1, "revenue": 3.5},
        {"stream_id": 5, "offer_id": 11, "day": "2026-11-18", "clicks": 4, "conversions": 1, "revenue": 3.5},
        {"stream_id": 5, "offer_id": 12, "day": "2026-11-18", "clicks": 3, "conversions": 0, "revenue": 0},
        {"stream_id": 4, "offer_id": 0, "day": "2026-11-18", "clicks": 7, "conversions": 0, "revenue": 0},
    ]
    st = services.campaign_stats(kt, 99, days=7, today=date(2026, 11, 18))
    req = kt.reports[0]
    assert req["range"]["from"] == "2026-11-12" and req["range"]["to"] == "2026-11-18"
    assert req["filters"] == [{"name": "campaign_id", "operator": "EQUALS", "expression": 99}]
    o = st["offers"]["5:11"]
    assert (o["clicks"], o["conversions"], o["revenue"], o["cr"], o["today"]) == (12, 2, 7.0, 16.67, 4)
    assert o["series"] == [2, 0, 0, 0, 0, 6, 4] and o["trend"] == 400  # 2 → 10
    assert st["offers"]["5:12"]["trend"] is None and st["offers"]["5:12"]["trend_new"]
    assert st["streams"]["4"]["today"] == 7 and st["streams"]["5"]["clicks"] == 15
    assert "4:0" not in st["offers"]


# ---------------- вход в обёртку


def _client(engine, kt, settings):
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    main.app.dependency_overrides[get_db] = _db
    main.app.dependency_overrides[main.get_api] = lambda: kt
    main.app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(main.app)


def test_basic_auth(engine, kt):
    settings = Settings(keitaro_api_key="x", auth_username="marina", auth_password="s3cret")
    try:
        c = _client(engine, kt, settings)
        assert c.get("/").status_code == 401
        assert c.get("/api/offers").status_code == 401
        assert c.get("/static/app.js").status_code == 401
        assert c.get("/api/offers", auth=("marina", "wrong")).status_code == 401
        assert c.get("/api/offers", auth=("marina", "s3cret")).status_code == 200
    finally:
        main.app.dependency_overrides.clear()


def test_push_conflict_http_409_and_force(engine, kt, settings):
    try:
        c = _client(engine, kt, settings)
        camp = c.post("/api/campaigns", json={"name": "h", "geo": "AU", "offer_id": 11}).json()
        s = next(x for x in camp["streams"] if x["has_offers"])
        c.post(f"/api/streams/{s['id']}/offers", json={"offer_id": 12})
        kt.streams[s["kt_id"]]["offers"] = [{"offer_id": 13, "share": 100}]
        r = c.post(f"/api/streams/{s['id']}/push")
        assert r.status_code == 409 and r.json()["diff"]["added_in_kt"] == [13]
        r = c.post(f"/api/streams/{s['id']}/push?force=true")
        assert r.status_code == 200 and not r.json()["draft"]
        assert c.get(f"/api/campaigns/{camp['kt_id']}/stats").status_code == 200
    finally:
        main.app.dependency_overrides.clear()


def test_fetch_keeps_published_pin_even_if_kt_share_changed(db, kt, stream):
    services.pin_offer(db, stream.id, 11, True, 25)
    services.push_stream(db, kt, stream.id)
    # закрепление уже опубликовано: черновика нет, но свои 25% нужно сохранить
    assert not services.is_draft(stream)
    kt.streams[stream.kt_id]["offers"] = [{"offer_id": 11, "share": 90}, {"offer_id": 12, "share": 10}]
    services.fetch_campaign(db, kt, stream.campaign.kt_id)
    assert state(stream) == {11: (25, "active"), 12: (75, "active")}
    assert row(stream, 11).pinned and row(stream, 11).conflict
    assert row(stream, 11).pub_share == 90
    assert services.is_draft(stream)


def test_offer_can_be_added_by_id_when_card_hidden_by_keitaro(db, kt, settings):
    c = services.create_campaign(db, kt, settings, "hidden", "AU", 100)
    st = next(s for s in c.streams if s.has_offers)
    services.add_offer(db, kt, st.id, 200)
    assert state(st) == {100: (50, "active"), 200: (50, "active")}
    assert row(st, 200).offer_name == "Offer 200"
