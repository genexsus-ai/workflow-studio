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
}

# Reserved credential-store prefix for per-provider OAuth app registrations
OAUTH_APP_PREFIX = "__oauth_app__"


def oauth_app_name(provider: str) -> str:
    return f"{OAUTH_APP_PREFIX}{provider}"
