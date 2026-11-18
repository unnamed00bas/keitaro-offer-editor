from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    keitaro_admin_url: str = "https://tlgk.host/admin"
    keitaro_api_key: str = ""

    campaign_prefix: str = "test_pogodina_"
    kt_domain_id: int = 1813
    kt_group_id: int = 10332
    kt_traffic_source_id: int = 836
    google_url: str = "https://google.com"

    database_url: str = "sqlite:///./editor.db"

    # вход в саму обёртку (HTTP Basic); пустой пароль = вход выключен
    auth_username: str = "admin"
    auth_password: str = ""

    stats_days: int = 7
    stats_timezone: str = "Europe/Moscow"

    @property
    def api_url(self) -> str:
        return self.keitaro_admin_url.rstrip("/") + "_api/v1"

    def campaign_link(self, kt_id: int) -> str:
        return f"{self.keitaro_admin_url.rstrip('/')}/#!/campaigns/{kt_id}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
