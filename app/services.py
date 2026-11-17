"""Бизнес-логика: создание кампании и редактор офферов."""

import re
import secrets
from datetime import datetime, timezone

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
    names = offer_names(api)
    if offer_id not in names:
        raise EditorError(f"Оффер {offer_id} не найден в Keitaro")

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

    * черновик (неопубликованные правки) сбрасывается;
    * оффер, который мы знали, но которого больше нет в Keitaro, не удаляется,
      а помечается removed (серый, с кнопкой Bring back);
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
    by_offer = {o.offer_id: o for o in stream.offers}
    in_kt = set()
    for ko in kt_offers:
        oid = int(ko["offer_id"])
        in_kt.add(oid)
        row = by_offer.get(oid)
        if row is None:
            row = StreamOffer(offer_id=oid)
            stream.offers.append(row)
        share = int(ko.get("share") or 0)
        row.offer_name = names.get(oid, row.offer_name or str(oid))
        row.pub_share = share
        row.pub_status = ACTIVE
        row.share = share
        row.status = ACTIVE
        row.removed_in_kt = False
        row.pub_pinned = row.pinned
    for oid, row in by_offer.items():
        if oid in in_kt:
            continue
        if row.pub_status is None:
            # добавлен в черновике и не опубликован — черновик сбрасывается
            stream.offers.remove(row)
            db.delete(row)
            continue
        if row.pub_status == ACTIVE:
            row.removed_in_kt = True
        row.pub_status = REMOVED
        row.pub_share = 0
        row.status = REMOVED
        row.share = 0
        row.pinned = row.pub_pinned = False


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
    names = offer_names(api)
    if offer_id not in names:
        raise EditorError(f"Оффер {offer_id} не найден в Keitaro")
    row = StreamOffer(offer_id=offer_id, offer_name=names[offer_id], status=ACTIVE, share=0)
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
    db.flush()
    return stream


def push_stream(db: Session, api: KeitaroAPI, stream_id: int) -> Stream:
    stream = _get_stream(db, stream_id)
    active = [r for r in stream.offers if r.is_active]
    try:
        validate_total([r.share for r in active])
    except ShareError as e:
        raise EditorError(str(e)) from e

    payload = [{"offer_id": r.offer_id, "share": r.share, "state": "active"} for r in active]
    result = api.update_stream_offers(stream.kt_id, payload)
    kt_shares = {int(o["offer_id"]): int(o.get("share") or 0) for o in (result or {}).get("offers", [])}

    for row in list(stream.offers):
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
            }
            for r in sorted(offers, key=lambda r: (r.status != ACTIVE, r.id))
        ],
    }
