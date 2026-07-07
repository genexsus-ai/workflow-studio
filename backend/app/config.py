"""Application settings for the Workflow Studio backend."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "GenXAI Workflow Studio"
    api_prefix: str = "/api/v1"
    cors_origins: str = "http://localhost:5173"

    openai_api_key: str | None = None
    anthropic_api_key: str | None = None

    # When set, /api/v1/* requests (except public /hooks/*) must send
    # this value in the X-Studio-Token header.
    studio_api_token: str | None = None

    data_dir: Path = BACKEND_ROOT / "data"


@lru_cache
def get_settings() -> Settings:
    return Settings()
