"""Тонкий клиент Admin API Keitaro (заголовок Api-Key)."""

import time
from typing import Any, Protocol

import httpx

from app.config import Settings


class KeitaroError(RuntimeError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class KeitaroAPI(Protocol):
    def list_offers(self) -> list[dict]: ...
    def list_campaigns(self) -> list[dict]: ...
    def get_campaign(self, campaign_id: int) -> dict: ...
    def create_campaign(self, payload: dict) -> dict: ...
    def get_streams(self, campaign_id: int) -> list[dict]: ...
    def create_stream(self, payload: dict) -> dict: ...
    def update_stream_offers(self, stream_id: int, offers: list[dict]) -> dict: ...


class KeitaroClient:
    OFFERS_TTL = 300

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        if not settings.keitaro_api_key:
            raise KeitaroError("KEITARO_API_KEY не задан (см. .env.example)")
        self._http = httpx.Client(
            base_url=settings.api_url,
            headers={"Api-Key": settings.keitaro_api_key, "Accept": "application/json"},
            timeout=30,
            transport=transport,
        )
        self._offers_cache: tuple[float, list[dict]] | None = None

    def _call(self, method: str, path: str, **kw) -> Any:
        try:
            r = self._http.request(method, path, **kw)
        except httpx.HTTPError as e:
            raise KeitaroError(f"Keitaro недоступен: {e}") from e
        if r.status_code >= 400:
            raise KeitaroError(f"Keitaro {method} {path}: {r.status_code} {r.text[:300]}", r.status_code)
        return r.json() if r.content else None

    def list_offers(self) -> list[dict]:
        now = time.monotonic()
        if self._offers_cache and now - self._offers_cache[0] < self.OFFERS_TTL:
            return self._offers_cache[1]
        offers = self._call("GET", "/offers")
        self._offers_cache = (now, offers)
        return offers

    def list_campaigns(self) -> list[dict]:
        return self._call("GET", "/campaigns")

    def get_campaign(self, campaign_id: int) -> dict:
        return self._call("GET", f"/campaigns/{campaign_id}")

    def create_campaign(self, payload: dict) -> dict:
        return self._call("POST", "/campaigns", json=payload)

    def get_streams(self, campaign_id: int) -> list[dict]:
        return self._call("GET", f"/campaigns/{campaign_id}/streams")

    def create_stream(self, payload: dict) -> dict:
        return self._call("POST", "/streams", json=payload)

    def update_stream_offers(self, stream_id: int, offers: list[dict]) -> dict:
        # PUT заменяет весь список офферов потока целиком
        return self._call("PUT", f"/streams/{stream_id}", json={"offers": offers})
