from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import services
from app.config import Settings, get_settings
from app.db import get_db
from app.keitaro import KeitaroAPI, KeitaroClient, KeitaroError
from app.models import Campaign, Stream

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Keitaro offer editor")
app.mount("/static", StaticFiles(directory=STATIC), name="static")

_client: KeitaroAPI | None = None


def get_api() -> KeitaroAPI:
    global _client
    if _client is None:
        _client = KeitaroClient(get_settings())
    return _client


@app.exception_handler(services.EditorError)
def editor_error(_, exc: services.EditorError):
    return JSONResponse(status_code=400, content={"detail": str(exc)})


@app.exception_handler(KeitaroError)
def keitaro_error(_, exc: KeitaroError):
    return JSONResponse(status_code=502, content={"detail": str(exc)})


class CampaignIn(BaseModel):
    name: str
    geo: str | list[str]
    offer_id: int


class OfferIn(BaseModel):
    offer_id: int


class PinIn(BaseModel):
    pinned: bool = True
    share: int | None = Field(default=None, ge=0, le=100)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
def config(settings: Settings = Depends(get_settings)):
    return {
        "prefix": settings.campaign_prefix,
        "domain_id": settings.kt_domain_id,
        "group_id": settings.kt_group_id,
        "traffic_source_id": settings.kt_traffic_source_id,
        "google_url": settings.google_url,
    }


@app.get("/api/offers")
def search_offers(q: str = "", limit: int = Query(20, le=100), api: KeitaroAPI = Depends(get_api)):
    q = q.strip().lower()
    out = []
    for o in api.list_offers():
        if o.get("state") not in (None, "active"):
            continue
        label = f"{o['id']} {o.get('name', '')}"
        if not q or q in label.lower():
            out.append({"id": o["id"], "name": o.get("name", "")})
        if len(out) >= limit:
            break
    return out


@app.get("/api/campaigns")
def list_campaigns(
    db: Session = Depends(get_db), api: KeitaroAPI = Depends(get_api), settings: Settings = Depends(get_settings)
):
    """Кампании с нашим префиксом из Keitaro + отметка, загружены ли они в редактор."""
    known = {c.kt_id for c in db.scalars(select(Campaign))}
    return [
        {
            "kt_id": c["id"],
            "name": c["name"],
            "in_editor": c["id"] in known,
            "kt_url": settings.campaign_link(c["id"]),
        }
        for c in api.list_campaigns()
        if str(c.get("name", "")).startswith(settings.campaign_prefix)
    ]


@app.post("/api/campaigns")
def create_campaign(
    body: CampaignIn,
    db: Session = Depends(get_db),
    api: KeitaroAPI = Depends(get_api),
    settings: Settings = Depends(get_settings),
):
    campaign = services.create_campaign(db, api, settings, body.name, body.geo, body.offer_id)
    db.commit()
    return services.serialize_campaign(campaign, settings)


@app.get("/api/campaigns/{kt_id}")
def get_campaign(kt_id: int, db: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    campaign = db.scalar(select(Campaign).where(Campaign.kt_id == kt_id))
    if campaign is None:
        raise HTTPException(404, "Кампания ещё не загружена: нажмите Fetch streams from KT")
    return services.serialize_campaign(campaign, settings)


@app.post("/api/campaigns/{kt_id}/fetch")
def fetch_campaign(
    kt_id: int,
    db: Session = Depends(get_db),
    api: KeitaroAPI = Depends(get_api),
    settings: Settings = Depends(get_settings),
):
    campaign = services.fetch_campaign(db, api, kt_id)
    db.commit()
    return services.serialize_campaign(campaign, settings)


def _stream_response(db: Session, stream: Stream) -> dict:
    db.commit()
    db.refresh(stream)
    return services.serialize_stream(stream)


@app.post("/api/streams/{stream_id}/offers")
def add_offer(stream_id: int, body: OfferIn, db: Session = Depends(get_db), api: KeitaroAPI = Depends(get_api)):
    return _stream_response(db, services.add_offer(db, api, stream_id, body.offer_id))


@app.post("/api/streams/{stream_id}/offers/{offer_id}/remove")
def remove_offer(stream_id: int, offer_id: int, db: Session = Depends(get_db)):
    return _stream_response(db, services.remove_offer(db, stream_id, offer_id))


@app.post("/api/streams/{stream_id}/offers/{offer_id}/bring-back")
def bring_back(stream_id: int, offer_id: int, db: Session = Depends(get_db)):
    return _stream_response(db, services.bring_back(db, stream_id, offer_id))


@app.post("/api/streams/{stream_id}/offers/{offer_id}/pin")
def pin_offer(stream_id: int, offer_id: int, body: PinIn, db: Session = Depends(get_db)):
    return _stream_response(db, services.pin_offer(db, stream_id, offer_id, body.pinned, body.share))


@app.post("/api/streams/{stream_id}/cancel")
def cancel(stream_id: int, db: Session = Depends(get_db)):
    return _stream_response(db, services.cancel_draft(db, stream_id))


@app.post("/api/streams/{stream_id}/push")
def push(stream_id: int, db: Session = Depends(get_db), api: KeitaroAPI = Depends(get_api)):
    return _stream_response(db, services.push_stream(db, api, stream_id))
