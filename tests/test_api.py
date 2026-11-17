from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app import main
from app.config import get_settings
from app.db import get_db


def test_api_flow(engine, kt, settings):
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
    try:
        c = TestClient(main.app)
        assert c.get("/").status_code == 200
        assert [o["id"] for o in c.get("/api/offers?q=offer 1").json()] == [11, 12, 13, 14, 15]
        camp = c.post("/api/campaigns", json={"name": "api", "geo": "AU", "offer_id": 11}).json()
        assert camp["name"] == "test_pogodina_api"
        assert camp["kt_url"].endswith(f"/#!/campaigns/{camp['kt_id']}")
        sid = next(s["id"] for s in camp["streams"] if s["has_offers"])
        st = c.post(f"/api/streams/{sid}/offers", json={"offer_id": 12}).json()
        assert st["draft"] and [o["share"] for o in st["offers"]] == [50, 50]
        st = c.post(f"/api/streams/{sid}/push").json()
        assert not st["draft"]
        r = c.post(f"/api/streams/{sid}/offers", json={"offer_id": 12})
        assert r.status_code == 400
        assert c.get("/api/campaigns").json()[0]["in_editor"] is True
    finally:
        main.app.dependency_overrides.clear()
