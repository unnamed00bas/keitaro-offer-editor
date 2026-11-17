import pytest

from app import services
from app.models import Stream


@pytest.fixture
def campaign(db, kt, settings):
    c = services.create_campaign(db, kt, settings, "demo", "au, mx", 11)
    db.commit()
    return c


def offers_stream(campaign) -> Stream:
    return next(s for s in campaign.streams if s.has_offers)


def shares(stream):
    return {r.offer_id: (r.share, r.status) for r in stream.offers}


def test_create_campaign_payloads(campaign, kt, settings):
    camp = kt.campaigns[campaign.kt_id]
    assert camp["name"] == "test_pogodina_demo"
    assert (camp["domain_id"], camp["group_id"], camp["traffic_source_id"], camp["type"]) == (1813, 10332, 836, "position")
    google, offers = kt.get_streams(campaign.kt_id)
    assert google["schema"] == "redirect" and google["action_payload"] == settings.google_url
    assert google["filters"] == [{"name": "country", "mode": "accept", "payload": ["AU", "MX"]}]
    assert offers["type"] == "default" and offers["offers"] == [{"offer_id": 11, "share": 100, "state": "active"}]
    # поток Google — простая строка, поток с офферами — редактируемый
    assert [s.has_offers for s in campaign.streams] == [False, True]


def test_bad_geo_rejected(db, kt, settings):
    with pytest.raises(services.EditorError):
        services.create_campaign(db, kt, settings, "x", "Australia", 11)


def test_add_splits_evenly_and_marks_draft(db, kt, campaign):
    st = offers_stream(campaign)
    services.add_offer(db, kt, st.id, 12)
    services.add_offer(db, kt, st.id, 13)
    assert {k: v[0] for k, v in shares(st).items()} == {11: 33, 12: 33, 13: 34}
    assert services.is_draft(st)
    assert kt.kt_shares(st.kt_id) == {11: 100}  # в Keitaro ничего не ушло


def test_cancel_reverts_draft(db, kt, campaign):
    st = offers_stream(campaign)
    services.add_offer(db, kt, st.id, 12)
    services.remove_offer(db, st.id, 11)
    services.cancel_draft(db, st.id)
    assert shares(st) == {11: (100, "active")}
    assert not services.is_draft(st)


def test_push_sends_offers(db, kt, campaign):
    st = offers_stream(campaign)
    services.add_offer(db, kt, st.id, 12)
    services.add_offer(db, kt, st.id, 13)
    services.push_stream(db, kt, st.id)
    assert kt.kt_shares(st.kt_id) == {11: 33, 12: 33, 13: 34}
    assert not services.is_draft(st)


def test_remove_is_soft_and_bring_back(db, kt, campaign):
    st = offers_stream(campaign)
    services.add_offer(db, kt, st.id, 12)
    services.add_offer(db, kt, st.id, 13)
    services.push_stream(db, kt, st.id)

    services.remove_offer(db, st.id, 12)
    assert shares(st)[12] == (0, "removed")
    assert {shares(st)[11][0], shares(st)[13][0]} == {50}
    assert services.is_draft(st)
    services.push_stream(db, kt, st.id)
    assert kt.kt_shares(st.kt_id) == {11: 50, 13: 50}
    assert shares(st)[12] == (0, "removed")  # остался у нас в архиве

    services.bring_back(db, st.id, 12)
    assert services.is_draft(st)
    services.push_stream(db, kt, st.id)
    assert kt.kt_shares(st.kt_id) == {11: 33, 12: 33, 13: 34}


def test_pin_keeps_share_on_later_changes(db, kt, campaign):
    st = offers_stream(campaign)
    for oid in (12, 13, 14):
        services.add_offer(db, kt, st.id, oid)
    services.pin_offer(db, st.id, 11, True)  # закрепляем текущие 25%
    assert shares(st)[11][0] == 25
    services.remove_offer(db, st.id, 13)
    assert shares(st)[11][0] == 25
    assert shares(st)[12][0] + shares(st)[14][0] == 75
    services.bring_back(db, st.id, 13)
    assert shares(st)[11][0] == 25
    assert sum(v[0] for v in shares(st).values()) == 100
    services.push_stream(db, kt, st.id)
    assert kt.kt_shares(st.kt_id)[11] == 25


def test_pin_with_value(db, kt, campaign):
    st = offers_stream(campaign)
    services.add_offer(db, kt, st.id, 12)
    services.pin_offer(db, st.id, 12, True, 70)
    assert {k: v[0] for k, v in shares(st).items()} == {11: 30, 12: 70}
    with pytest.raises(services.EditorError):
        services.pin_offer(db, st.id, 11, True, 40)  # 40 + 70 > 100
    assert {k: v[0] for k, v in shares(st).items()} == {11: 30, 12: 70}


def test_offer_deleted_in_keitaro_marked_removed_on_fetch(db, kt, campaign):
    st = offers_stream(campaign)
    services.add_offer(db, kt, st.id, 12)
    services.add_offer(db, kt, st.id, 13)
    services.push_stream(db, kt, st.id)
    # кто-то удалил оффер 12 прямо в Keitaro и поправил веса
    kt.streams[st.kt_id]["offers"] = [{"offer_id": 11, "share": 50}, {"offer_id": 13, "share": 50}]
    services.fetch_campaign(db, kt, campaign.kt_id)
    row = next(r for r in st.offers if r.offer_id == 12)
    assert (row.status, row.share, row.removed_in_kt) == ("removed", 0, True)
    assert not services.is_draft(st)
    services.bring_back(db, st.id, 12)
    services.push_stream(db, kt, st.id)
    assert set(kt.kt_shares(st.kt_id)) == {11, 12, 13}


def test_fetch_picks_up_offer_added_in_keitaro(db, kt, campaign):
    st = offers_stream(campaign)
    kt.streams[st.kt_id]["offers"].append({"offer_id": 15, "share": 0})
    kt.streams[st.kt_id]["offers"][0]["share"] = 100
    services.fetch_campaign(db, kt, campaign.kt_id)
    assert shares(st)[15] == (0, "active")


def test_push_rejects_bad_total(db, kt, campaign):
    st = offers_stream(campaign)
    services.add_offer(db, kt, st.id, 12)
    services.pin_offer(db, st.id, 11, True, 30)
    services.pin_offer(db, st.id, 12, True, 30)
    with pytest.raises(services.EditorError):
        services.push_stream(db, kt, st.id)
    assert kt.puts == []


def test_add_then_remove_unpublished_disappears_after_push(db, kt, campaign):
    st = offers_stream(campaign)
    services.add_offer(db, kt, st.id, 12)
    services.remove_offer(db, st.id, 12)
    services.push_stream(db, kt, st.id)
    assert 12 not in shares(st)
