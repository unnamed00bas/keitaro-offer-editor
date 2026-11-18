import itertools
import os

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
        self.report_rows: list[dict] = []
        self.reports: list[dict] = []

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

    def get_stream(self, sid):
        return self.streams[sid]

    def build_report(self, payload):
        self.reports.append(payload)
        return {"rows": self.report_rows, "total": len(self.report_rows)}

    def update_stream_offers(self, sid, offers):
        self.puts.append((sid, offers))
        self.streams[sid]["offers"] = [dict(o) for o in offers]
        return self.streams[sid]

    # helpers для тестов
    def kt_shares(self, sid):
        return {o["offer_id"]: o["share"] for o in self.streams[sid]["offers"]}


@pytest.fixture
def settings():
    return Settings(keitaro_api_key="x", database_url="sqlite://", auth_password="")


@pytest.fixture
def engine():
    """SQLite в памяти; TEST_DATABASE_URL=postgresql+psycopg://... гоняет те же тесты на Postgres."""
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        eng = create_engine(url)
        Base.metadata.drop_all(eng)
    else:
        eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()


@pytest.fixture
def kt():
    return FakeKeitaro()
