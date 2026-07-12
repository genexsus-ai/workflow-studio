"""Tests for the OAuth2 credential flow (P1: GitHub, PKCE + state)."""

from urllib.parse import parse_qs, urlparse


def _register_app(client) -> None:
    response = client.put(
        "/api/v1/oauth/apps/github",
        json={"client_id": "cid-123", "client_secret": "sec-456"},
    )
    assert response.status_code == 204


def test_providers_listing_reports_app_state(client):
    listing = client.get("/api/v1/oauth/providers").json()
    github = next(p for p in listing["providers"] if p["provider"] == "github")
    assert github["app_configured"] is False
    assert listing["redirect_uri"].endswith("/api/v1/oauth/callback")

    _register_app(client)
    listing = client.get("/api/v1/oauth/providers").json()
    github = next(p for p in listing["providers"] if p["provider"] == "github")
    assert github["app_configured"] is True


def test_oauth_app_never_appears_in_credentials(client):
    _register_app(client)
    names = [c["name"] for c in client.get("/api/v1/credentials").json()]
    assert not any(name.startswith("__oauth_app__") for name in names)


def test_start_requires_registered_app(client):
    response = client.post(
        "/api/v1/oauth/github/start", json={"credential_name": "my-github"}
    )
    assert response.status_code == 409
    assert "No OAuth app registered" in response.json()["detail"]


def test_start_builds_authorize_url_with_pkce_and_state(client):
    _register_app(client)
    response = client.post(
        "/api/v1/oauth/github/start", json={"credential_name": "my-github"}
    )
    assert response.status_code == 200

    url = urlparse(response.json()["authorize_url"])
    params = parse_qs(url.query)
    assert url.netloc == "github.com"
    assert params["client_id"] == ["cid-123"]
    assert params["code_challenge_method"] == ["S256"]
    assert params["state"][0]
    assert params["code_challenge"][0]
    assert "repo" in params["scope"][0]


def test_callback_rejects_unknown_state(client):
    response = client.get("/api/v1/oauth/callback?state=bogus&code=abc")
    assert response.status_code == 400
    assert "Unknown or expired" in response.text


def test_callback_reports_denied_consent(client):
    response = client.get("/api/v1/oauth/callback?error=access_denied")
    assert response.status_code == 400
    assert "denied" in response.text


def test_full_flow_stores_credential(client, monkeypatch):
    import app.oauth_flow as oauth_flow

    _register_app(client)
    start = client.post(
        "/api/v1/oauth/github/start", json={"credential_name": "my-github"}
    ).json()
    state = parse_qs(urlparse(start["authorize_url"]).query)["state"][0]

    exchanged: dict = {}

    async def fake_exchange(code, verifier, definition, app, redirect_uri):
        exchanged.update(code=code, verifier=verifier, client_id=app["client_id"])
        return {"access_token": "gho_testtoken", "token_type": "bearer"}

    monkeypatch.setattr(oauth_flow, "exchange_code", fake_exchange)

    response = client.get(f"/api/v1/oauth/callback?state={state}&code=authcode-1")
    assert response.status_code == 200
    assert "my-github" in response.text

    # Exchange received the PKCE verifier and app identity
    assert exchanged["code"] == "authcode-1"
    assert exchanged["verifier"]
    assert exchanged["client_id"] == "cid-123"

    # Credential is listed (without secrets) and marked oauth2
    creds = client.get("/api/v1/credentials").json()
    entry = next(c for c in creds if c["name"] == "my-github")
    assert entry["connector_type"] == "github"
    assert entry["auth_kind"] == "oauth2"

    # Stored config puts the token where the GitHub connector reads it
    from app.credentials import get_credential_store

    stored = get_credential_store().get("my-github")
    assert stored.config["token"] == "gho_testtoken"
    assert stored.config["auth_kind"] == "oauth2"

    # State is single-use
    replay = client.get(f"/api/v1/oauth/callback?state={state}&code=authcode-1")
    assert replay.status_code == 400


# ------------------------------------------------------------------ refresh


def _store_oauth_credential(client, monkeypatch, *, expires_in: float, provider="google"):
    """Seed an oauth2 credential directly in the store."""
    import time

    from app.credentials import get_credential_store
    from genxai.connectors.config_store import ConnectorConfigEntry

    get_credential_store().save(
        ConnectorConfigEntry(
            name="my-google",
            connector_type="google_workspace",
            config={
                "access_token": "old-token",
                "auth_kind": "oauth2",
                "provider": provider,
                "refresh_token": "refresh-1",
                "expires_at": time.time() + expires_in,
            },
        )
    )
    return get_credential_store().get("my-google")


async def test_ensure_fresh_passes_static_credentials_through(client):
    from app.credentials import get_credential_store
    from app.oauth_refresh import ensure_fresh
    from genxai.connectors.config_store import ConnectorConfigEntry

    get_credential_store().save(
        ConnectorConfigEntry(
            name="pat", connector_type="github", config={"token": "ghp_static"}
        )
    )
    entry = get_credential_store().get("pat")

    result = await ensure_fresh(entry)

    assert result.config["token"] == "ghp_static"


async def test_ensure_fresh_skips_unexpired_tokens(client, monkeypatch):
    import app.oauth_refresh as oauth_refresh

    entry = _store_oauth_credential(client, monkeypatch, expires_in=3600)

    async def boom(*args, **kwargs):
        raise AssertionError("refresh_request must not be called")

    monkeypatch.setattr(oauth_refresh, "refresh_request", boom)
    result = await oauth_refresh.ensure_fresh(entry)

    assert result.config["access_token"] == "old-token"


async def test_ensure_fresh_refreshes_and_rotates(client, monkeypatch):
    import app.oauth_refresh as oauth_refresh

    client.put(
        "/api/v1/oauth/apps/google",
        json={"client_id": "gcid", "client_secret": "gsec"},
    )
    entry = _store_oauth_credential(client, monkeypatch, expires_in=10)

    calls = {}

    async def fake_refresh(token_url, refresh_token, app):
        calls.update(url=token_url, refresh_token=refresh_token, client_id=app["client_id"])
        return {
            "access_token": "new-token",
            "refresh_token": "refresh-2",
            "expires_in": 3600,
        }

    monkeypatch.setattr(oauth_refresh, "refresh_request", fake_refresh)
    result = await oauth_refresh.ensure_fresh(entry)

    assert result.config["access_token"] == "new-token"
    assert result.config["refresh_token"] == "refresh-2"  # rotation persisted
    assert calls["refresh_token"] == "refresh-1"
    assert calls["client_id"] == "gcid"

    # Persisted, not just returned
    from app.credentials import get_credential_store

    stored = get_credential_store().get("my-google")
    assert stored.config["access_token"] == "new-token"


async def test_ensure_fresh_failure_marks_needs_reauth(client, monkeypatch):
    import pytest

    import app.oauth_refresh as oauth_refresh
    from app.oauth_refresh import CredentialNeedsReauth

    client.put(
        "/api/v1/oauth/apps/google",
        json={"client_id": "gcid", "client_secret": "gsec"},
    )
    entry = _store_oauth_credential(client, monkeypatch, expires_in=10)

    async def rejected(*args, **kwargs):
        raise RuntimeError("invalid_grant")

    monkeypatch.setattr(oauth_refresh, "refresh_request", rejected)
    with pytest.raises(CredentialNeedsReauth, match="my-google"):
        await oauth_refresh.ensure_fresh(entry)

    listing = client.get("/api/v1/credentials").json()
    cred = next(c for c in listing if c["name"] == "my-google")
    assert cred["needs_reauth"] is True
    assert cred["provider"] == "google"


async def test_connector_execution_strips_oauth_meta_and_refreshes(client, monkeypatch):
    import app.connectors_catalog as catalog
    import app.oauth_refresh as oauth_refresh

    client.put(
        "/api/v1/oauth/apps/google",
        json={"client_id": "gcid", "client_secret": "gsec"},
    )
    _store_oauth_credential(client, monkeypatch, expires_in=10)

    async def fake_refresh(token_url, refresh_token, app):
        return {"access_token": "fresh-token", "expires_in": 3600}

    monkeypatch.setattr(oauth_refresh, "refresh_request", fake_refresh)

    received = {}

    class DummyConnector:
        def __init__(self, connector_id, **kwargs):
            received.update(kwargs)

        async def validate_config(self):
            return True

        async def get_sheet(self, **params):
            return {"ok": True}

        async def _stop(self):
            return None

    monkeypatch.setitem(catalog.CONNECTOR_CLASSES, "google_workspace", DummyConnector)

    tool = catalog.ConnectorActionTool()
    result = await tool._execute(
        connector="google_workspace",
        action="get_sheet",
        credential="my-google",
        params={"spreadsheet_id": "s1"},
    )

    assert result == {"ok": True}
    assert received["access_token"] == "fresh-token"  # refreshed before use
    # OAuth bookkeeping never reaches the connector constructor
    assert "auth_kind" not in received
    assert "refresh_token" not in received
    assert "expires_at" not in received
