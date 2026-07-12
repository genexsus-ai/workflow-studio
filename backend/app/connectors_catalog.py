"""Catalog of connector integrations exposed as canvas nodes.

Each entry describes the credential fields needed to connect and the actions
(with parameter specs) the node can invoke. Execution goes through
ConnectorActionTool, which builds the connector from a stored credential,
calls the action, and closes the client.
"""

from __future__ import annotations

from typing import Any

from app.credentials import get_credential_store
from genxai.connectors import (
    Connector,
    EmailConnector,
    GitHubConnector,
    GoogleWorkspaceConnector,
    JiraConnector,
    NotionConnector,
    SlackConnector,
)
from genxai.tools.base import Tool, ToolCategory, ToolMetadata, ToolParameter

CONNECTOR_CLASSES: dict[str, type[Connector]] = {
    "email": EmailConnector,
    "slack": SlackConnector,
    "github": GitHubConnector,
    "jira": JiraConnector,
    "notion": NotionConnector,
    "google_workspace": GoogleWorkspaceConnector,
}

CONNECTOR_CATALOG: list[dict[str, Any]] = [
    {
        "type": "email",
        "label": "Email",
        "icon": "\u2709\ufe0f",
        "color": "#0891b2",
        "credential_fields": [
            {"name": "host", "example": "smtp.gmail.com"},
            {"name": "port", "example": "587"},
            {"name": "username", "example": "you@example.com"},
            {"name": "password", "secret": True},
            {"name": "from_email", "example": "you@example.com"},
        ],
        "actions": {
            "send_email": {
                "description": "Send an email via SMTP",
                "params": [
                    {"name": "to", "required": True, "example": "someone@example.com"},
                    {"name": "subject", "required": True, "example": "Daily digest"},
                    {"name": "body", "required": True},
                    {"name": "html", "required": False, "example": "false"},
                    {"name": "cc", "required": False},
                ],
            },
        },
    },
    {
        "type": "slack",
        "label": "Slack",
        "icon": "💬",
        "color": "#611f69",
        "credential_fields": [{"name": "bot_token", "secret": True}],
        "actions": {
            "send_message": {
                "description": "Post a message to a channel",
                "params": [
                    {"name": "channel", "required": True, "example": "#general"},
                    {"name": "text", "required": True, "example": "Hello from GenXAI"},
                ],
            },
            "post_ephemeral": {
                "description": "Send an ephemeral message to a user in a channel",
                "params": [
                    {"name": "channel", "required": True},
                    {"name": "user", "required": True},
                    {"name": "text", "required": True},
                ],
            },
            "list_channels": {
                "description": "List channels",
                "params": [{"name": "types", "required": False, "example": "public_channel"}],
            },
        },
    },
    {
        "type": "github",
        "label": "GitHub",
        "icon": "🐙",
        "color": "#24292f",
        "credential_fields": [{"name": "token", "secret": True}],
        "oauth_provider": "github",
        "actions": {
            "get_repo": {
                "description": "Fetch repository metadata",
                "params": [
                    {"name": "owner", "required": True},
                    {"name": "repo", "required": True},
                ],
            },
            "list_issues": {
                "description": "List repository issues",
                "params": [
                    {"name": "owner", "required": True},
                    {"name": "repo", "required": True},
                    {"name": "state", "required": False, "example": "open"},
                    {"name": "per_page", "required": False, "example": 30},
                ],
            },
            "create_issue": {
                "description": "Create an issue",
                "params": [
                    {"name": "owner", "required": True},
                    {"name": "repo", "required": True},
                    {"name": "title", "required": True},
                    {"name": "body", "required": False},
                ],
            },
        },
    },
    {
        "type": "jira",
        "label": "Jira",
        "icon": "📌",
        "color": "#0052cc",
        "credential_fields": [
            {"name": "base_url", "secret": False, "example": "https://you.atlassian.net"},
            {"name": "email", "secret": False},
            {"name": "api_token", "secret": True},
        ],
        "actions": {
            "get_project": {
                "description": "Fetch a project",
                "params": [{"name": "project_key", "required": True}],
            },
            "search_issues": {
                "description": "Search issues with JQL",
                "params": [
                    {"name": "jql", "required": True, "example": "project = PROJ"},
                    {"name": "max_results", "required": False, "example": 50},
                ],
            },
            "create_issue": {
                "description": "Create an issue (raw payload)",
                "params": [{"name": "payload", "required": True, "example": {"fields": {}}}],
            },
        },
    },
    {
        "type": "notion",
        "label": "Notion",
        "icon": "📝",
        "color": "#1f2328",
        "credential_fields": [{"name": "token", "secret": True}],
        "actions": {
            "get_page": {
                "description": "Fetch a page",
                "params": [{"name": "page_id", "required": True}],
            },
            "query_database": {
                "description": "Query a database",
                "params": [
                    {"name": "database_id", "required": True},
                    {"name": "payload", "required": False, "example": {}},
                ],
            },
            "create_page": {
                "description": "Create a page (raw payload)",
                "params": [{"name": "payload", "required": True, "example": {"parent": {}}}],
            },
        },
    },
    {
        "type": "google_workspace",
        "label": "Google Workspace",
        "icon": "📧",
        "color": "#0f9d58",
        "credential_fields": [{"name": "access_token", "secret": True}],
        "oauth_provider": "google",
        "actions": {
            "get_sheet": {
                "description": "Fetch spreadsheet metadata",
                "params": [{"name": "spreadsheet_id", "required": True}],
            },
            "append_sheet_values": {
                "description": "Append rows to a sheet",
                "params": [
                    {"name": "spreadsheet_id", "required": True},
                    {"name": "range_", "required": True, "example": "Sheet1!A1"},
                    {"name": "values", "required": True, "example": [["a", "b"]]},
                ],
            },
            "list_drive_files": {
                "description": "List Drive files",
                "params": [
                    {"name": "page_size", "required": False, "example": 10},
                    {"name": "query", "required": False},
                ],
            },
            "get_calendar_events": {
                "description": "List calendar events",
                "params": [
                    {"name": "calendar_id", "required": False, "example": "primary"},
                    {"name": "max_results", "required": False, "example": 10},
                ],
            },
        },
    },
]

_ALLOWED_ACTIONS = {
    entry["type"]: set(entry["actions"]) for entry in CONNECTOR_CATALOG
}


class ConnectorActionTool(Tool):
    """Runs one connector action using a stored credential.

    This bridges connectors into the graph engine's existing tool-node
    execution path, so connector nodes get template interpolation and
    per-node results for free.
    """

    def __init__(self) -> None:
        super().__init__(
            metadata=ToolMetadata(
                name="connector_action",
                description="Invoke an action on a connected integration (Slack, GitHub, Jira, Notion, Google Workspace)",
                category=ToolCategory.COMMUNICATION,
                tags=["connector", "integration"],
            ),
            parameters=[
                ToolParameter(
                    name="connector",
                    type="string",
                    description="Connector type",
                    required=True,
                    enum=sorted(CONNECTOR_CLASSES),
                ),
                ToolParameter(
                    name="action", type="string", description="Action to invoke", required=True
                ),
                ToolParameter(
                    name="credential",
                    type="string",
                    description="Name of the stored credential to use",
                    required=True,
                ),
                ToolParameter(
                    name="params",
                    type="object",
                    description="Action parameters",
                    required=False,
                    default={},
                ),
            ],
        )

    async def _execute(self, **kwargs: Any) -> Any:
        connector_type: str = kwargs["connector"]
        action: str = kwargs["action"]
        credential_name: str = kwargs["credential"]
        params: dict[str, Any] = kwargs.get("params") or {}

        if action not in _ALLOWED_ACTIONS.get(connector_type, set()):
            raise ValueError(
                f"Action '{action}' is not available on connector '{connector_type}'"
            )

        entry = get_credential_store().get(credential_name)
        if entry is None:
            raise ValueError(f"Credential '{credential_name}' not found")
        if entry.connector_type != connector_type:
            raise ValueError(
                f"Credential '{credential_name}' is for '{entry.connector_type}', "
                f"not '{connector_type}'"
            )

        # OAuth credentials: refresh the access token if it's about to expire
        from app.oauth_providers import OAUTH_META_KEYS
        from app.oauth_refresh import ensure_fresh

        entry = await ensure_fresh(entry)
        connector_kwargs = {
            key: value
            for key, value in entry.config.items()
            if key not in OAUTH_META_KEYS
        }

        connector_class = CONNECTOR_CLASSES[connector_type]
        connector = connector_class(
            connector_id=f"studio-{credential_name}", **connector_kwargs
        )
        await connector.validate_config()
        try:
            method = getattr(connector, action)
            return await method(**params)
        finally:
            await connector._stop()
