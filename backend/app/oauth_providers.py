"""OAuth2 provider registry: adding a provider is a data change, not code.

Each provider maps to one connector type and declares how the resulting
access token lands in that connector's credential config (``token_field``),
so connectors keep working unchanged whether a credential was typed in as a
static secret or minted by the OAuth flow.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OAuthProviderDef:
    label: str
    auth_url: str
    token_url: str
    scopes: list[str]
    connector_type: str
    # Credential config key the connector reads its token from
    token_field: str = "token"
    # Whether the provider issues refresh tokens (P2 wires actual refresh)
    issues_refresh_tokens: bool = False
    extra_auth_params: dict[str, str] = field(default_factory=dict)
    # Named scope sets users can pick from when connecting; "scopes" above
    # is the default when no preset is chosen
    scope_presets: dict[str, list[str]] = field(default_factory=dict)


OAUTH_PROVIDERS: dict[str, OAuthProviderDef] = {
    "github": OAuthProviderDef(
        label="GitHub",
        auth_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        scopes=["repo", "read:user"],
        connector_type="github",
        token_field="token",
        issues_refresh_tokens=False,
    ),
    "google": OAuthProviderDef(
        label="Google",
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive.readonly",
        ],
        connector_type="google_workspace",
        token_field="access_token",
        issues_refresh_tokens=True,
        # offline: issue a refresh token; consent: re-issue it on reconnects
        extra_auth_params={"access_type": "offline", "prompt": "consent"},
        scope_presets={
            "Sheets & Drive": [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive.readonly",
            ],
            "Sheets only": ["https://www.googleapis.com/auth/spreadsheets"],
            "Drive read-only": ["https://www.googleapis.com/auth/drive.readonly"],
        },
    ),
    "slack": OAuthProviderDef(
        label="Slack",
        auth_url="https://slack.com/oauth/v2/authorize",
        token_url="https://slack.com/api/oauth.v2.access",
        scopes=["chat:write", "channels:read"],
        connector_type="slack",
        token_field="bot_token",
        issues_refresh_tokens=False,
    ),
    # Microsoft 365 is deliberately absent: there is no Microsoft connector
    # in CONNECTOR_CLASSES yet, so a provider entry would mint credentials
    # nothing can use. Add it together with the connector.
}

# Credential config keys that are OAuth bookkeeping, not connector kwargs
OAUTH_META_KEYS = frozenset(
    {"auth_kind", "provider", "scopes", "refresh_token", "expires_at", "needs_reauth"}
)

# Reserved credential-store prefix for per-provider OAuth app registrations
OAUTH_APP_PREFIX = "__oauth_app__"


def oauth_app_name(provider: str) -> str:
    return f"{OAUTH_APP_PREFIX}{provider}"
