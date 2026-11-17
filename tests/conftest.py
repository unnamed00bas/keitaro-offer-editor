import itertools

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models  # noqa: F401
from app.config import Settings
from app.db import Base


class FakeKeitaro:
    """Имитация Admin API Keitaro в памяти."""

    def __init__(self):
        self.ids = itertools.count(1000)
        self.offers = [{"id": i, "name": f"Offer {i}", "state": "active"} for i in (11, 12, 13, 14, 15)]
        self.campaigns: dict[int, dict] = {}
        self.streams: dict[int, dict] = {}
        self.puts: list[tuple[int, list]] = []

    def list_offers(self):
        return self.offers

    def list_campaigns(self):
        return list(self.campaigns.values())

    def get_campaign(self, cid):
        return self.campaigns[cid]

    def create_campaign(self, payload):
        cid = next(self.ids)
        self.campaigns[cid] = {"id": cid, **payload}
        return self.campaigns[cid]

    def create_stream(self, payload):
        sid = next(self.ids)
        self.streams[sid] = {"id": sid, "filters": [], **payload, "offers": list(payload.get("offers", []))}
        return self.streams[sid]

    def get_streams(self, cid):
        return sorted((s for s in self.streams.values() if s["campaign_id"] == cid), key=lambda s: s["position"])

    def update_stream_offers(self, sid, offers):
        self.puts.append((sid, offers))
        self.streams[sid]["offers"] = [dict(o) for o in offers]
        return self.streams[sid]

    # helpers для тестов
    def kt_shares(self, sid):
        return {o["offer_id"]: o["share"] for o in self.streams[sid]["offers"]}


@pytest.fixture
def settings():
    return Settings(keitaro_api_key="x", database_url="sqlite://")


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def db(engine):
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()


@pytest.fixture
def kt():
    return FakeKeitaro()
