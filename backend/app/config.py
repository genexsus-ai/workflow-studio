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

    # Secret for signing user-session JWTs. MUST be overridden in production
    # (via JWT_SECRET) — a stable value keeps sessions valid across restarts.
    jwt_secret: str = "dev-insecure-change-me"
    jwt_expiry_hours: int = 24 * 7

    # Externally reachable base URL of this backend — used to build the
    # OAuth redirect URI registered with providers.
    public_base_url: str = "http://localhost:8000"

    data_dir: Path = BACKEND_ROOT / "data"

    # Feedback: where user feedback is emailed. SMTP settings are optional —
    # without them feedback is still stored, just not emailed.
    feedback_email: str | None = None
    feedback_smtp_host: str | None = None
    feedback_smtp_port: int = 587
    feedback_smtp_username: str | None = None
    feedback_smtp_password: str | None = None
    feedback_from_email: str | None = None

    # Persistence: "files" keeps workflows/runs as JSON under data_dir;
    # "postgres" stores them in the database named by database_url.
    persistence_backend: str = "files"
    database_url: str | None = None
    # Strict mode (set in deployments): if the database is unreachable,
    # fail instead of silently falling back to file persistence — a pod
    # that "works" but writes to its ephemeral filesystem loses data on
    # every redeploy. Local dev keeps the friendly fallback.
    persistence_strict: bool = False

    @property
    def sync_database_url(self) -> str | None:
        """database_url normalized for sync SQLAlchemy (psycopg2).

        The .env URL may name the asyncpg driver and its ``ssl=`` query
        parameter; the backend uses sync engines, so translate both.
        """
        if not self.database_url:
            return None
        url = self.database_url.replace("+asyncpg", "+psycopg2")
        return url.replace("ssl=", "sslmode=") if "+psycopg2" in url else url

    @property
    def use_db_persistence(self) -> bool:
        return self.persistence_backend.lower() == "postgres" and bool(self.database_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()
