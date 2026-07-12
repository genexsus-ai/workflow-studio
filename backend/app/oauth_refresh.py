"""Transparent OAuth token refresh at connector execution time.

One choke point — ``ensure_fresh(entry)`` — called before a connector runs:
static credentials pass through untouched, unexpired OAuth tokens are used
as-is, expired ones are refreshed (and rotated refresh tokens persisted).
A per-credential lock stops parallel branches from double-refreshing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.credentials import get_credential_store
from app.oauth_flow import get_oauth_app
from app.oauth_providers import OAUTH_PROVIDERS
from genxai.connectors.config_store import ConnectorConfigEntry

logger = logging.getLogger(__name__)

# Refresh when the token has less than this long to live
EXPIRY_MARGIN_SECONDS = 60

_locks: dict[str, asyncio.Lock] = {}


class CredentialNeedsReauth(RuntimeError):
    """Raised when a token can't be refreshed; the user must reconnect."""

    def __init__(self, credential_name: str, reason: str) -> None:
        super().__init__(
            f"Credential '{credential_name}' needs re-authorization "
            f"({reason}) — reconnect it in the Credentials panel"
        )
        self.credential_name = credential_name


async def refresh_request(
    token_url: str, refresh_token: str, app: dict[str, str]
) -> dict[str, Any]:
    """POST the refresh grant. Isolated so tests can stub it."""
    import httpx

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": app["client_id"],
                "client_secret": app["client_secret"],
            },
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        return response.json()


def refresh_request_sync(
    token_url: str, refresh_token: str, app: dict[str, str]
) -> dict[str, Any]:
    """Synchronous refresh grant (analytics adapters run in worker threads)."""
    import httpx

    response = httpx.post(
        token_url,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": app["client_id"],
            "client_secret": app["client_secret"],
        },
        headers={"Accept": "application/json"},
        timeout=20.0,
    )
    response.raise_for_status()
    return response.json()


def ensure_fresh_sync(entry: ConnectorConfigEntry) -> ConnectorConfigEntry:
    """Sync twin of ensure_fresh for non-async callers (analytics sources).

    No cross-call lock: a rare concurrent refresh is harmless (rotation is
    persisted last-writer-wins and both tokens are valid briefly).
    """
    config = entry.config
    if config.get("auth_kind") != "oauth2":
        return entry
    expires_at = config.get("expires_at")
    if not expires_at or float(expires_at) - time.time() > EXPIRY_MARGIN_SECONDS:
        return entry

    provider_key = str(config.get("provider") or "")
    definition = OAUTH_PROVIDERS.get(provider_key)
    refresh_token = config.get("refresh_token")
    app = get_oauth_app(provider_key) if definition else None
    if definition is None or not refresh_token or not (app or {}).get("client_id"):
        _mark_needs_reauth(entry)
        raise CredentialNeedsReauth(entry.name, "token expired and cannot refresh")

    try:
        tokens = refresh_request_sync(definition.token_url, str(refresh_token), app)
    except Exception as exc:
        _mark_needs_reauth(entry)
        raise CredentialNeedsReauth(entry.name, f"refresh rejected: {exc}") from exc

    access_token = tokens.get("access_token")
    if not access_token:
        _mark_needs_reauth(entry)
        raise CredentialNeedsReauth(entry.name, "provider returned no access token")

    config = dict(entry.config)
    config[definition.token_field] = access_token
    config.pop("needs_reauth", None)
    if tokens.get("refresh_token"):
        config["refresh_token"] = tokens["refresh_token"]
    if tokens.get("expires_in"):
        config["expires_at"] = time.time() + float(tokens["expires_in"])
    else:
        config.pop("expires_at", None)
    refreshed = ConnectorConfigEntry(
        name=entry.name, connector_type=entry.connector_type, config=config
    )
    get_credential_store().save(refreshed)
    return refreshed


async def ensure_fresh(entry: ConnectorConfigEntry) -> ConnectorConfigEntry:
    """Return the entry with a usable access token, refreshing if needed.

    No-op for static (non-OAuth) credentials. Raises CredentialNeedsReauth
    when refresh is impossible (no refresh token) or rejected (revoked).
    """
    config = entry.config
    if config.get("auth_kind") != "oauth2":
        return entry

    expires_at = config.get("expires_at")
    if not expires_at or float(expires_at) - time.time() > EXPIRY_MARGIN_SECONDS:
        return entry

    lock = _locks.setdefault(entry.name, asyncio.Lock())
    async with lock:
        # Another branch may have refreshed while we waited
        current = get_credential_store().get(entry.name) or entry
        expires_at = current.config.get("expires_at")
        if not expires_at or float(expires_at) - time.time() > EXPIRY_MARGIN_SECONDS:
            return current
        return await _refresh(current)


async def _refresh(entry: ConnectorConfigEntry) -> ConnectorConfigEntry:
    provider_key = str(entry.config.get("provider") or "")
    definition = OAUTH_PROVIDERS.get(provider_key)
    refresh_token = entry.config.get("refresh_token")
    if definition is None or not refresh_token:
        _mark_needs_reauth(entry)
        raise CredentialNeedsReauth(entry.name, "token expired with no refresh token")

    app = get_oauth_app(provider_key)
    if app is None or not app.get("client_id"):
        _mark_needs_reauth(entry)
        raise CredentialNeedsReauth(
            entry.name, f"no OAuth app registered for '{provider_key}'"
        )

    try:
        tokens = await refresh_request(definition.token_url, str(refresh_token), app)
    except Exception as exc:
        _mark_needs_reauth(entry)
        raise CredentialNeedsReauth(entry.name, f"refresh rejected: {exc}") from exc

    access_token = tokens.get("access_token")
    if not access_token:
        _mark_needs_reauth(entry)
        raise CredentialNeedsReauth(
            entry.name, str(tokens.get("error") or "provider returned no access token")
        )

    config = dict(entry.config)
    config[definition.token_field] = access_token
    config.pop("needs_reauth", None)
    if tokens.get("refresh_token"):
        # Some providers (Google, Microsoft) rotate refresh tokens on use
        config["refresh_token"] = tokens["refresh_token"]
    if tokens.get("expires_in"):
        config["expires_at"] = time.time() + float(tokens["expires_in"])
    else:
        config.pop("expires_at", None)

    refreshed = ConnectorConfigEntry(
        name=entry.name, connector_type=entry.connector_type, config=config
    )
    get_credential_store().save(refreshed)
    logger.info("Refreshed OAuth token for credential '%s'", entry.name)
    return refreshed


def _mark_needs_reauth(entry: ConnectorConfigEntry) -> None:
    config = dict(entry.config)
    config["needs_reauth"] = True
    get_credential_store().save(
        ConnectorConfigEntry(
            name=entry.name, connector_type=entry.connector_type, config=config
        )
    )
