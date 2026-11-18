"""Бизнес-логика: создание кампании и редактор офферов."""

import re
import secrets
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.keitaro import KeitaroAPI
from app.models import ACTIVE, REMOVED, Campaign, Stream, StreamOffer
from app.shares import ShareError, Slot, recalc, validate_total


class EditorError(ValueError):
    pass


GEO_RE = re.compile(r"^[A-Z]{2}$")


def parse_geo(raw: str | list[str]) -> list[str]:
    items = raw if isinstance(raw, list) else re.split(r"[\s,;]+", raw)
    codes = []
    for item in items:
        code = item.strip().upper()
        if not code:
            continue
        if not GEO_RE.match(code):
            raise EditorError(f"Неверный код страны: {item!r} (нужен ISO-код из двух букв, например AU)")
        if code not in codes:
            codes.append(code)
    if not codes:
        raise EditorError("Укажите хотя бы один код страны")
    return codes


def normalize_name(name: str, prefix: str) -> str:
    name = name.strip()
    if not name:
        raise EditorError("Название кампании пустое")
    return name if name.startswith(prefix) else prefix + name


def offer_names(api: KeitaroAPI) -> dict[int, str]:
    return {int(o["id"]): o.get("name", "") for o in api.list_offers()}


# ---------------------------------------------------------------- создание


def create_campaign(db: Session, api: KeitaroAPI, settings: Settings, name: str, geo, offer_id: int) -> Campaign:
    codes = parse_geo(geo)
    full_name = normalize_name(name, settings.campaign_prefix)
    if offer_id <= 0:
        raise EditorError("Неверный ID оффера")
    # оффер может быть не виден в списке (права пользователя Keitaro), но доступен по ID
    names = offer_names(api)

    kt = api.create_campaign(
        {
            "name": full_name,
            "alias": secrets.token_hex(5),
            "type": "position",
            "state": "active",
            "domain_id": settings.kt_domain_id,
            "group_id": settings.kt_group_id,
            "traffic_source_id": settings.kt_traffic_source_id,
        }
    )
    kt_id = int(kt["id"])
    api.create_stream(
        {
            "campaign_id": kt_id,
            "name": f"Google ({','.join(codes)})",
            "type": "regular",
            "position": 1,
            "state": "active",
            "schema": "redirect",
            "action_type": "http",
            "action_payload": settings.google_url,
            "filters": [{"name": "country", "mode": "accept", "payload": codes}],
        }
    )
    api.create_stream(
        {
            "campaign_id": kt_id,
            "name": "Offers",
            "type": "default",
            "position": 2,
            "state": "active",
            "schema": "landings",
            "action_type": "http",
            "offers": [{"offer_id": offer_id, "share": 100, "state": "active"}],
        }
    )
    campaign = Campaign(kt_id=kt_id, name=full_name, alias=kt.get("alias"), geo=",".join(codes))
    db.add(campaign)
    db.flush()
    fetch_campaign(db, api, kt_id, names=names)
    return campaign


# ---------------------------------------------------------------- fetch


def get_or_import(db: Session, api: KeitaroAPI, kt_id: int) -> Campaign:
    campaign = db.scalar(select(Campaign).where(Campaign.kt_id == kt_id))
    if campaign is None:
        kt = api.get_campaign(kt_id)
        campaign = Campaign(kt_id=kt_id, name=kt.get("name", str(kt_id)), alias=kt.get("alias"))
        db.add(campaign)
        db.flush()
    return campaign


def fetch_campaign(db: Session, api: KeitaroAPI, kt_id: int, names: dict[int, str] | None = None) -> Campaign:
    """«Fetch streams from KT»: Keitaro — источник правды для опубликованного состояния.

    * неопубликованный черновик сливается с Keitaro (см. _sync_offers);
    * оффер, который мы знали, но которого больше нет в Keitaro, остаётся у нас
      со статусом removed (серый, с кнопкой Bring back);
    * закрепления (pins) и архив живут только у нас и сохраняются.
    """
    campaign = get_or_import(db, api, kt_id)
    kt_streams = api.get_streams(kt_id)
    names = names if names is not None else offer_names(api)

    local = {s.kt_id: s for s in campaign.streams}
    seen = set()
    for ks in kt_streams:
        sid = int(ks["id"])
        seen.add(sid)
        stream = local.get(sid)
        if stream is None:
            stream = Stream(kt_id=sid, campaign=campaign)
            db.add(stream)
        stream.name = ks.get("name") or f"Stream {sid}"
        stream.position = int(ks.get("position") or 0)
        stream.kt_type = ks.get("type")
        stream.kt_schema = ks.get("schema")
        kt_offers = ks.get("offers") or []
        stream.has_offers = bool(kt_offers) or stream.has_offers or ks.get("schema") == "landings"
        if stream.has_offers:
            _sync_offers(db, stream, kt_offers, names)

    for sid, stream in local.items():
        if sid not in seen:  # поток удалили в Keitaro
            db.delete(stream)

    campaign.fetched_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.flush()
    db.refresh(campaign)
    return campaign


def _sync_offers(db: Session, stream: Stream, kt_offers: list[dict], names: dict[int, str]) -> None:
    """Слияние состояния из Keitaro с локальным черновиком.

    * опубликованное состояние (pub_*) всегда берётся из Keitaro;
    * офферы без локальных правок просто принимают состояние Keitaro;
    * локальные правки (добавления, удаления, закрепления) сохраняются;
    * удалённый в Keitaro оффер помечается removed, добавленный в Keitaro — появляется;
    * если Keitaro и черновик разошлись по одному офферу, в conflict пишется пояснение;
    * если в потоке остался черновик, доли пересчитываются с учётом закреплений.
    """
    kt = {}
    for ko in kt_offers:
        if ko.get("state", "active") == "active":
            kt[int(ko["offer_id"])] = int(ko.get("share") or 0)
    had_draft = is_draft(stream) or any(r.pinned for r in stream.offers)
    known = set()

    for row in list(stream.offers):
        known.add(row.offer_id)
        row.conflict = None
        row.offer_name = names.get(row.offer_id) or row.offer_name or f"Offer {row.offer_id}"
        local_changed = row.is_changed or row.pinned or (row.pinned != row.pub_pinned)
        old_status, old_share = row.pub_status, row.pub_share

        if row.offer_id in kt:
            new_share = kt[row.offer_id]
            kt_changed = old_status != ACTIVE or old_share != new_share
            row.pub_status, row.pub_share, row.removed_in_kt = ACTIVE, new_share, False
            if not local_changed:
                row.status, row.share = ACTIVE, new_share
                row.pub_pinned = row.pinned
            elif kt_changed and old_status is not None:
                if old_status == REMOVED:
                    row.conflict = "В Keitaro оффер вернули, оставлен ваш черновик"
                else:
                    row.conflict = f"В Keitaro доля менялась ({old_share}% → {new_share}%), оставлен ваш черновик"
            continue

        if old_status == ACTIVE:
            row.pub_status, row.pub_share, row.removed_in_kt = REMOVED, 0, True
            if local_changed and row.status == ACTIVE:
                row.conflict = "Удалён в Keitaro, пока вы правили черновик"
            row.status, row.share = REMOVED, 0
            row.pinned = row.pub_pinned = False
        # pub_status None — локальное добавление, ждёт Push; REMOVED — архив: ничего не меняем

    for oid, share in kt.items():
        if oid in known:
            continue
        row = StreamOffer(
            offer_id=oid, offer_name=names.get(oid, f"Offer {oid}"), status=ACTIVE, share=share,
            pub_status=ACTIVE, pub_share=share, pinned=False, pub_pinned=False, removed_in_kt=False,
        )
        if had_draft:
            row.conflict = "Добавлен в Keitaro, пока вы правили черновик"
        stream.offers.append(row)

    db.flush()
    if had_draft and is_draft(stream):
        _apply_recalc(stream)


class PushConflict(EditorError):
    def __init__(self, diff: dict):
        super().__init__("Поток в Keitaro изменился после последнего Fetch")
        self.diff = diff


def stream_diff(snapshot: dict[int, int], current: dict[int, int]) -> dict:
    return {
        "added_in_kt": sorted(set(current) - set(snapshot)),
        "removed_in_kt": sorted(set(snapshot) - set(current)),
        "share_changed": [
            {"offer_id": oid, "was": snapshot[oid], "now": current[oid]}
            for oid in sorted(set(snapshot) & set(current))
            if snapshot[oid] != current[oid]
        ],
    }


# ---------------------------------------------------------------- черновик


def _get_stream(db: Session, stream_id: int) -> Stream:
    stream = db.get(Stream, stream_id)
    if stream is None:
        raise EditorError("Поток не найден")
    if not stream.has_offers:
        raise EditorError("В этом потоке нет офферов, его не редактируем")
    return stream


def _get_offer(stream: Stream, offer_id: int) -> StreamOffer:
    for row in stream.offers:
        if row.offer_id == offer_id:
            return row
    raise EditorError(f"Оффера {offer_id} нет в потоке")


def _apply_recalc(stream: Stream) -> None:
    try:
        shares = recalc([Slot(r.id, r.is_active, r.pinned, r.share) for r in stream.offers])
    except ShareError as e:
        raise EditorError(str(e)) from e
    for r in stream.offers:
        r.share = shares[r.id]


def is_draft(stream: Stream) -> bool:
    return any(r.is_changed for r in stream.offers)


def add_offer(db: Session, api: KeitaroAPI, stream_id: int, offer_id: int) -> Stream:
    stream = _get_stream(db, stream_id)
    existing = next((r for r in stream.offers if r.offer_id == offer_id), None)
    if existing is not None:
        if existing.is_active:
            raise EditorError(f"Оффер {offer_id} уже есть в потоке")
        return bring_back(db, stream_id, offer_id)
    if offer_id <= 0:
        raise EditorError("Неверный ID оффера")
    # карточка оффера может быть скрыта правами пользователя Keitaro; добавляем по ID,
    # несуществующий ID Keitaro отклонит при Push
    names = offer_names(api)
    row = StreamOffer(offer_id=offer_id, offer_name=names.get(offer_id, f"Offer {offer_id}"), status=ACTIVE, share=0)
    stream.offers.append(row)
    db.flush()
    _apply_recalc(stream)
    db.flush()
    return stream


def remove_offer(db: Session, stream_id: int, offer_id: int) -> Stream:
    stream = _get_stream(db, stream_id)
    row = _get_offer(stream, offer_id)
    row.status = REMOVED
    row.pinned = False
    row.share = 0
    _apply_recalc(stream)
    db.flush()
    return stream


def bring_back(db: Session, stream_id: int, offer_id: int) -> Stream:
    stream = _get_stream(db, stream_id)
    row = _get_offer(stream, offer_id)
    row.status = ACTIVE
    row.pinned = False
    row.share = 0
    _apply_recalc(stream)
    db.flush()
    return stream


def pin_offer(db: Session, stream_id: int, offer_id: int, pinned: bool, share: int | None = None) -> Stream:
    stream = _get_stream(db, stream_id)
    row = _get_offer(stream, offer_id)
    if not row.is_active:
        raise EditorError("Нельзя закрепить удалённый оффер")
    old = (row.pinned, row.share)
    row.pinned = pinned
    if pinned and share is not None:
        row.share = int(share)
    try:
        _apply_recalc(stream)
    except EditorError:
        row.pinned, row.share = old
        raise
    db.flush()
    return stream


def cancel_draft(db: Session, stream_id: int) -> Stream:
    stream = _get_stream(db, stream_id)
    for row in list(stream.offers):
        if row.pub_status is None:
            stream.offers.remove(row)
            db.delete(row)
            continue
        row.share = row.pub_share or 0
        row.status = row.pub_status
        row.pinned = row.pub_pinned
        row.conflict = None
    db.flush()
    return stream


def push_stream(db: Session, api: KeitaroAPI, stream_id: int, force: bool = False) -> Stream:
    stream = _get_stream(db, stream_id)
    active = [r for r in stream.offers if r.is_active]
    try:
        validate_total([r.share for r in active])
    except ShareError as e:
        raise EditorError(str(e)) from e

    # защита от гонки: Keitaro должен совпадать с тем, что мы видели при последнем Fetch/Push
    if not force:
        current = {
            int(o["offer_id"]): int(o.get("share") or 0)
            for o in (api.get_stream(stream.kt_id).get("offers") or [])
            if o.get("state", "active") == "active"
        }
        snapshot = {r.offer_id: int(r.pub_share or 0) for r in stream.offers if r.pub_status == ACTIVE}
        if current != snapshot:
            raise PushConflict(stream_diff(snapshot, current))

    payload = [{"offer_id": r.offer_id, "share": r.share, "state": "active"} for r in active]
    result = api.update_stream_offers(stream.kt_id, payload)
    kt_shares = {int(o["offer_id"]): int(o.get("share") or 0) for o in (result or {}).get("offers", [])}

    for row in list(stream.offers):
        row.conflict = None
        if row.is_active:
            row.pub_status = ACTIVE
            row.pub_share = kt_shares.get(row.offer_id, row.share)
            row.share = row.pub_share
        elif row.pub_status is None:
            # добавили и тут же удалили — в Keitaro не был, хранить нечего
            stream.offers.remove(row)
            db.delete(row)
            continue
        else:
            row.pub_status = REMOVED
            row.pub_share = 0
        row.pub_pinned = row.pinned
        row.removed_in_kt = False
    db.flush()
    return stream


# ---------------------------------------------------------------- сериализация


def serialize_campaign(campaign: Campaign, settings: Settings) -> dict:
    return {
        "id": campaign.id,
        "kt_id": campaign.kt_id,
        "name": campaign.name,
        "geo": campaign.geo,
        "kt_url": settings.campaign_link(campaign.kt_id),
        "fetched_at": campaign.fetched_at.isoformat() if campaign.fetched_at else None,
        "streams": [serialize_stream(s) for s in sorted(campaign.streams, key=lambda s: s.position)],
    }


def serialize_stream(stream: Stream) -> dict:
    offers = stream.offers
    return {
        "id": stream.id,
        "kt_id": stream.kt_id,
        "name": stream.name,
        "position": stream.position,
        "type": stream.kt_type,
        "schema": stream.kt_schema,
        "has_offers": stream.has_offers,
        "draft": is_draft(stream) if stream.has_offers else False,
        "conflicts": sum(1 for r in offers if r.conflict),
        "total": sum(r.share for r in offers if r.is_active),
        "offers": [
            {
                "offer_id": r.offer_id,
                "name": r.offer_name,
                "share": r.share,
                "status": r.status,
                "pinned": r.pinned,
                "published_share": r.pub_share,
                "published_status": r.pub_status,
                "is_new": r.pub_status is None,
                "changed": r.is_changed,
                "removed_in_kt": r.removed_in_kt,
                "conflict": r.conflict,
            }
            for r in sorted(offers, key=lambda r: (r.status != ACTIVE, r.id))
        ],
    }


# ---------------------------------------------------------------- статистика


def campaign_stats(api: KeitaroAPI, kt_id: int, days: int = 7, today: date | None = None, tz: str = "Europe/Moscow") -> dict:
    """Клики/конверсии/доход по офферам и потокам кампании за `days` дней + дневной ряд кликов для тренда."""
    days = max(2, min(days, 90))
    today = today or datetime.now(ZoneInfo(tz)).date()
    start = today - timedelta(days=days - 1)
    day_list = [(start + timedelta(days=i)).isoformat() for i in range(days)]
    report = api.build_report(
        {
            "range": {"from": start.isoformat(), "to": today.isoformat(), "timezone": tz},
            "dimensions": ["stream_id", "offer_id", "day"],
            "measures": ["clicks", "conversions", "revenue"],
            "filters": [{"name": "campaign_id", "operator": "EQUALS", "expression": kt_id}],
            "limit": 10000,
        }
    )
    offers: dict[str, dict] = {}
    streams: dict[str, dict] = {}

    def bucket(store: dict, key: str) -> dict:
        return store.setdefault(
            key, {"clicks": 0, "conversions": 0, "revenue": 0.0, "today": 0, "series": dict.fromkeys(day_list, 0)}
        )

    for row in (report or {}).get("rows", []):
        day = str(row.get("day", ""))[:10]
        clicks = int(row.get("clicks") or 0)
        conv = int(row.get("conversions") or 0)
        rev = float(row.get("revenue") or 0)
        targets = [bucket(streams, str(int(row.get("stream_id") or 0)))]
        if int(row.get("offer_id") or 0):
            targets.append(bucket(offers, f"{int(row['stream_id'] or 0)}:{int(row['offer_id'])}"))
        for b in targets:
            b["clicks"] += clicks
            b["conversions"] += conv
            b["revenue"] += rev
            if day in b["series"]:
                b["series"][day] += clicks
            if day == today.isoformat():
                b["today"] += clicks

    def finish(b: dict) -> dict:
        series = list(b.pop("series").values())
        half = len(series) // 2
        prev, last = sum(series[:half]), sum(series[-half:])
        b["series"] = series
        b["cr"] = round(100 * b["conversions"] / b["clicks"], 2) if b["clicks"] else 0.0
        b["revenue"] = round(b["revenue"], 2)
        # тренд: вторая половина периода против первой, в процентах
        b["trend"] = None if prev == 0 else round(100 * (last - prev) / prev)
        b["trend_new"] = prev == 0 and last > 0
        return b

    return {
        "days": day_list,
        "offers": {k: finish(v) for k, v in offers.items()},
        "streams": {k: finish(v) for k, v in streams.items()},
    }
