from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[3]
JSON_DIR = ROOT_DIR / "json"
DATA_DIR = ROOT_DIR / "data"


def load_hardcode() -> dict:
    path = JSON_DIR / "hardcode.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    vworld_api_key: str = ""
    vworld_domain: str = "http://localhost"
    carto_api_key: str = ""
    cdse_client_id: str = ""
    cdse_client_secret: str = ""
    cdse_dem_instance: str = "COPERNICUS_90"
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    database_path: str = "data/sitedia_seocho.sqlite"
    clova_api_key: str = ""
    clova_base_url: str = "https://api.clovastudio.go.kr/api/v1"
    clova_model: str = "LLM42-Gemma4"
    clova_ssl_verify: bool = False
    seoul_open_api_key: str = ""
    admin_token: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def db_path(self) -> Path:
        path = Path(self.database_path)
        if not path.is_absolute():
            path = ROOT_DIR / path
        return path

    @property
    def has_vworld_key(self) -> bool:
        return bool(self.vworld_api_key.strip())

    @property
    def has_carto_key(self) -> bool:
        return bool(self.carto_api_key.strip())

    @property
    def has_cdse_key(self) -> bool:
        return bool(self.cdse_client_id.strip() and self.cdse_client_secret.strip())

    @property
    def has_clova_key(self) -> bool:
        return bool(self.clova_api_key.strip())

    @property
    def has_seoul_open_key(self) -> bool:
        return bool(self.seoul_open_api_key.strip())


def carto_voyager_url(z: int, x: int, y: int, key: str) -> str:
    """Carto Voyager 래스터 타일 URL. 키는 쿼리로만 붙인다."""
    template = load_hardcode().get(
        "CARTO_VOYAGER_TILE_URL",
        "https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png",
    )
    return f"{template.format(z=z, x=x, y=y)}?key={key}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
