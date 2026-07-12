"""Credential storage for connector nodes.

Backed by the framework's ConnectorConfigStore (JSON on disk; encrypted at
rest when the GENXAI_CONNECTOR_CONFIG_KEY environment variable holds a
Fernet key). Secrets are write-only through the API: list/read responses
never include config values.
"""

from __future__ import annotations

from app.config import get_settings
from genxai.connectors.config_store import ConnectorConfigEntry, ConnectorConfigStore

_store: ConnectorConfigStore | None = None


def get_credential_store() -> ConnectorConfigStore:
    global _store
    if _store is None:
        settings = get_settings()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        _store = ConnectorConfigStore(path=settings.data_dir / "credentials.json")
    return _store


def reset_credential_store() -> None:
    global _store
    _store = None


def safe_listing() -> list[dict]:
    """Credential names and types only — never the secret values.

    Reserved entries (OAuth app registrations) are internal and excluded.
    """
    from app.oauth_providers import OAUTH_APP_PREFIX

    return [
        {
            "name": entry.name,
            "connector_type": entry.connector_type,
            "auth_kind": entry.config.get("auth_kind", "token"),
        }
        for entry in get_credential_store().list().values()
        if not entry.name.startswith(OAUTH_APP_PREFIX)
    ]


__all__ = [
    "ConnectorConfigEntry",
    "get_credential_store",
    "reset_credential_store",
    "safe_listing",
]
