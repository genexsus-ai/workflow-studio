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
    HubSpotConnector,
    JiraConnector,
    NotionConnector,
    PostgresConnector,
    S3Connector,
    SlackConnector,
    WhatsAppConnector,
)
from genxai.tools.base import Tool, ToolCategory, ToolMetadata, ToolParameter

CONNECTOR_CLASSES: dict[str, type[Connector]] = {
    "email": EmailConnector,
    "slack": SlackConnector,
    "github": GitHubConnector,
    "jira": JiraConnector,
    "notion": NotionConnector,
    "google_workspace": GoogleWorkspaceConnector,
    "postgres": PostgresConnector,
    "s3": S3Connector,
    "whatsapp": WhatsAppConnector,
    "hubspot": HubSpotConnector,
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
        "oauth_provider": "slack",
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
        "type": "whatsapp",
        "label": "WhatsApp",
        "icon": "🟢",
        "color": "#25d366",
        "credential_fields": [
            {"name": "access_token", "secret": True},
            {"name": "phone_number_id", "example": "123456789012345"},
        ],
        "actions": {
            "send_message": {
                "description": "Send a text message (24h customer-service window)",
                "params": [
                    {"name": "to", "required": True, "example": "15551234567"},
                    {"name": "text", "required": True, "example": "Hello from GenXAI"},
                ],
            },
            "send_template": {
                "description": "Send an approved template (works outside the 24h window)",
                "params": [
                    {"name": "to", "required": True, "example": "15551234567"},
                    {"name": "template", "required": True, "example": "hello_world"},
                    {"name": "language", "required": False, "example": "en_US"},
                ],
            },
            "mark_read": {
                "description": "Mark an inbound message as read",
                "params": [{"name": "message_id", "required": True}],
            },
        },
    },
    {
        "type": "hubspot",
        "label": "HubSpot",
        "icon": "🧲",
        "color": "#ff7a59",
        "credential_fields": [
            {"name": "access_token", "secret": True},
        ],
        "actions": {
            "create_or_update_contact": {
                "description": "Upsert a contact by email",
                "params": [
                    {"name": "email", "required": True, "example": "lead@example.com"},
                    {"name": "firstname", "required": False},
                    {"name": "lastname", "required": False},
                    {"name": "phone", "required": False},
                ],
            },
            "search_contacts": {
                "description": "Free-text search over contacts",
                "params": [
                    {"name": "query", "required": True, "example": "jane"},
                    {"name": "limit", "required": False, "example": 10},
                ],
            },
            "find_contact_by_email": {
                "description": "Look up one contact by exact email",
                "params": [{"name": "email", "required": True}],
            },
            "create_deal": {
                "description": "Create a deal, optionally tied to a contact",
                "params": [
                    {"name": "dealname", "required": True, "example": "Villa inquiry — Jane"},
                    {"name": "amount", "required": False, "example": 250000},
                    {"name": "dealstage", "required": False, "example": "appointmentscheduled"},
                    {"name": "contact_id", "required": False},
                ],
            },
        },
    },
    {
        "type": "s3",
        "label": "AWS S3",
        "icon": "🪣",
        "color": "#e25444",
        "credential_fields": [
            {"name": "access_key_id", "example": "AKIA…"},
            {"name": "secret_access_key", "secret": True},
            {"name": "region", "example": "us-east-1"},
            {"name": "endpoint_url", "example": "leave empty for AWS; set for MinIO/R2"},
        ],
        "actions": {
            "list_objects": {
                "description": "List objects in a bucket",
                "params": [
                    {"name": "bucket", "required": True},
                    {"name": "prefix", "required": False, "example": "reports/"},
                    {"name": "max_keys", "required": False, "example": 100},
                ],
            },
            "get_object": {
                "description": "Download an object into the file store (returns a file ref)",
                "params": [
                    {"name": "bucket", "required": True},
                    {"name": "key", "required": True, "example": "reports/q2.xlsx"},
                ],
            },
            "put_object": {
                "description": "Upload a file reference or text content to a key",
                "params": [
                    {"name": "bucket", "required": True},
                    {"name": "key", "required": True},
                    {"name": "file", "required": False, "example": "{{ export.data.file }}"},
                    {"name": "content", "required": False},
                ],
            },
        },
    },
    {
        "type": "postgres",
        "label": "PostgreSQL",
        "icon": "🐘",
        "color": "#336791",
        "credential_fields": [
            {
                "name": "connection_string",
                "secret": True,
                "example": "postgresql://user:pass@host:5432/db",
            }
        ],
        "actions": {
            "query": {
                "description": "Run a read-only SELECT/WITH query",
                "params": [
                    {"name": "sql", "required": True, "example": "SELECT * FROM orders WHERE total > :min"},
                    {"name": "params", "required": False, "example": {"min": 100}},
                    {"name": "max_rows", "required": False, "example": 500},
                ],
            },
            "execute": {
                "description": "Run a write statement (INSERT/UPDATE/DELETE/DDL)",
                "params": [
                    {"name": "sql", "required": True, "example": "UPDATE orders SET status = :s WHERE id = :id"},
                    {"name": "params", "required": False, "example": {"s": "shipped", "id": 1}},
                ],
            },
            "insert_rows": {
                "description": "Bulk-insert a list of objects into a table",
                "params": [
                    {"name": "table", "required": True, "example": "articles"},
                    {"name": "rows", "required": True, "example": "{{ poll_feed.data.items }}"},
                ],
            },
            "list_tables": {
                "description": "List table names visible to this connection",
                "params": [],
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
            "get_sheet_values": {
                "description": "Read cell values from a sheet range",
                "params": [
                    {"name": "spreadsheet_id", "required": True},
                    {"name": "range_", "required": True, "example": "Sheet1!A1:F100"},
                ],
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
            "create_calendar_event": {
                "description": "Create a calendar event (schedule a follow-up)",
                "params": [
                    {"name": "summary", "required": True, "example": "Follow-up call with lead"},
                    {"name": "start", "required": True, "example": "2026-07-16T15:00:00"},
                    {"name": "end", "required": False, "example": "defaults to start + 30 min"},
                    {"name": "attendees", "required": False, "example": "agent@example.com"},
                    {"name": "description", "required": False},
                    {"name": "timezone", "required": False, "example": "UTC"},
                    {"name": "calendar_id", "required": False, "example": "primary"},
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
