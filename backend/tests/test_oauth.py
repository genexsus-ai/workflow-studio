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
