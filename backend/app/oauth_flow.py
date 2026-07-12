"""OAuth2 authorization-code flow with PKCE and single-use state nonces.

The browser only ever sees the provider's consent screen; the code→token
exchange happens server-side and tokens go straight into the encrypted
credential store. Pending consents are held in memory with a TTL — a backend
restart aborts in-flight consents, which is acceptable.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from app.credentials import ConnectorConfigEntry, get_credential_store
from app.oauth_providers import OAUTH_PROVIDERS, OAuthProviderDef, oauth_app_name

logger = logging.getLogger(__name__)

STATE_TTL_SECONDS = 600


@dataclass
class PendingConsent:
    provider: str
    credential_name: str
    scopes: list[str]
    code_verifier: str
    created_at: float


_pending: dict[str, PendingConsent] = {}


def _prune_expired() -> None:
    cutoff = time.time() - STATE_TTL_SECONDS
    for state in [s for s, p in _pending.items() if p.created_at < cutoff]:
        _pending.pop(state, None)


def get_oauth_app(provider: str) -> dict[str, str] | None:
    """The deployment's registered OAuth app (client id/secret) or None."""
    entry = get_credential_store().get(oauth_app_name(provider))
    if entry is None:
        return None
    return {
        "client_id": str(entry.config.get("client_id", "")),
        "client_secret": str(entry.config.get("client_secret", "")),
    }


def save_oauth_app(provider: str, client_id: str, client_secret: str) -> None:
    get_credential_store().save(
        ConnectorConfigEntry(
            name=oauth_app_name(provider),
            connector_type=f"oauth_app:{provider}",
            config={"client_id": client_id, "client_secret": client_secret},
        )
    )


def delete_oauth_app(provider: str) -> bool:
    return get_credential_store().delete(oauth_app_name(provider))


def begin_consent(
    provider: str,
    credential_name: str,
    redirect_uri: str,
    scopes: list[str] | None = None,
) -> str:
    """Create a pending consent; returns the provider authorize URL."""
    definition = OAUTH_PROVIDERS[provider]
    app = get_oauth_app(provider)
    if app is None or not app["client_id"]:
        raise LookupError(
            f"No OAuth app registered for '{provider}' — add its client ID and "
            "secret first"
        )

    _prune_expired()
    state = secrets.token_urlsafe(32)
    code_verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    effective_scopes = scopes or definition.scopes
    _pending[state] = PendingConsent(
        provider=provider,
        credential_name=credential_name,
        scopes=effective_scopes,
        code_verifier=code_verifier,
        created_at=time.time(),
    )

    params = {
        "client_id": app["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(effective_scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        **definition.extra_auth_params,
    }
    return f"{definition.auth_url}?{urlencode(params)}"


async def exchange_code(
    code: str, verifier: str, definition: OAuthProviderDef,
    app: dict[str, str], redirect_uri: str,
) -> dict[str, Any]:
    """Server-side code→token exchange. Isolated so tests can stub it."""
    import httpx

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            definition.token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": app["client_id"],
                "client_secret": app["client_secret"],
                "code_verifier": verifier,
            },
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        return response.json()


async def complete_consent(state: str, code: str, redirect_uri: str) -> str:
    """Validate state, exchange the code, store the credential.

    Returns the credential name. Raises LookupError for unknown/expired
    state and ValueError when the provider returns no access token.
    """
    _prune_expired()
    pending = _pending.pop(state, None)
    if pending is None:
        raise LookupError("Unknown or expired OAuth state")

    definition = OAUTH_PROVIDERS[pending.provider]
    app = get_oauth_app(pending.provider)
    if app is None:
        raise LookupError(f"OAuth app for '{pending.provider}' was removed")

    tokens = await exchange_code(
        code, pending.code_verifier, definition, app, redirect_uri
    )
    access_token = tokens.get("access_token")
    if not access_token:
        raise ValueError(
            f"Provider returned no access token: {tokens.get('error', 'unknown error')}"
        )

    config: dict[str, Any] = {
        definition.token_field: access_token,
        "auth_kind": "oauth2",
        "provider": pending.provider,
        "scopes": pending.scopes,
    }
    if tokens.get("refresh_token"):
        config["refresh_token"] = tokens["refresh_token"]
    if tokens.get("expires_in"):
        config["expires_at"] = time.time() + float(tokens["expires_in"])

    get_credential_store().save(
        ConnectorConfigEntry(
            name=pending.credential_name,
            connector_type=definition.connector_type,
            config=config,
        )
    )
    logger.info(
        "OAuth credential '%s' stored for provider %s",
        pending.credential_name,
        pending.provider,
    )
    return pending.credential_name


def reset_pending() -> None:
    _pending.clear()
