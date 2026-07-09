"""MCP server registry and the tool that executes MCP nodes.

Registered MCP servers are stored like credentials (JSON on disk, encrypted
at rest when GENXAI_CONNECTOR_CONFIG_KEY is set). Each entry is either a
stdio server (command + args + env) or an HTTP server (url + headers).

Note: stdio servers execute the configured command on the backend host —
only register commands you trust, exactly as with Claude Desktop or n8n.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import get_settings
from genxai.connectors.config_store import ConnectorConfigStore
from genxai.tools.base import Tool, ToolCategory, ToolMetadata, ToolParameter
from genxai.tools.mcp_client import MCPToolClient

logger = logging.getLogger(__name__)

_store: ConnectorConfigStore | None = None


def get_mcp_store() -> ConnectorConfigStore:
    global _store
    if _store is None:
        settings = get_settings()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        _store = ConnectorConfigStore(path=settings.data_dir / "mcp_servers.json")
    return _store


def reset_mcp_store() -> None:
    global _store
    _store = None


def safe_mcp_listing() -> list[dict[str, Any]]:
    """Server names + transport + display target; env/header values omitted."""
    entries = []
    for entry in get_mcp_store().list().values():
        config = entry.config
        entries.append(
            {
                "name": entry.name,
                "transport": entry.connector_type,
                "target": config.get("command") or config.get("url") or "",
            }
        )
    return entries


def build_mcp_client(name: str) -> MCPToolClient:
    entry = get_mcp_store().get(name)
    if entry is None:
        raise ValueError(f"MCP server '{name}' is not registered")
    config = entry.config
    if entry.connector_type == "mcp_stdio":
        return MCPToolClient(
            command=config.get("command"),
            args=config.get("args") or [],
            env=config.get("env") or None,
        )
    return MCPToolClient(url=config.get("url"), headers=config.get("headers") or None)


class MCPActionTool(Tool):
    """Invokes one tool on a registered MCP server (used by MCP nodes)."""

    def __init__(self) -> None:
        super().__init__(
            metadata=ToolMetadata(
                name="mcp_action",
                description="Call a tool on a registered MCP (Model Context Protocol) server",
                category=ToolCategory.SYSTEM,
                tags=["mcp", "integration"],
            ),
            parameters=[
                ToolParameter(
                    name="server",
                    type="string",
                    description="Name of the registered MCP server",
                    required=True,
                ),
                ToolParameter(
                    name="tool", type="string", description="Tool name on that server", required=True
                ),
                ToolParameter(
                    name="params",
                    type="object",
                    description="Tool arguments",
                    required=False,
                    default={},
                ),
            ],
        )

    async def _execute(self, **kwargs: Any) -> Any:
        client = build_mcp_client(kwargs["server"])
        result = await client.call_tool(kwargs["tool"], kwargs.get("params") or {})
        if result["is_error"]:
            raise RuntimeError(f"MCP tool '{kwargs['tool']}' returned an error: {result['text']}")
        return result


async def sync_agent_tools() -> list[str]:
    """(Re)register every MCP server's tools as agent-usable proxy tools.

    Called at startup and after server add/delete: agents can then include
    e.g. "mcp__demo-tools__add" in their tool list like any built-in tool.
    """
    from genxai.tools.mcp_client import load_mcp_agent_tools
    from genxai.tools.registry import ToolRegistry

    entries = get_mcp_store().list()

    # Drop proxies whose server no longer exists (or that will be refreshed)
    for tool in list(ToolRegistry.list_all()):
        tags = tool.metadata.tags or []
        if "mcp" in tags and tool.metadata.name.startswith("mcp__"):
            ToolRegistry.unregister(tool.metadata.name)

    registered: list[str] = []
    for name in entries:
        try:
            client = build_mcp_client(name)
            for tool in await load_mcp_agent_tools(client, name):
                ToolRegistry.register(tool)
                registered.append(tool.metadata.name)
        except Exception as exc:  # noqa: BLE001 - keep other servers alive
            logger.warning("Could not load agent tools from MCP server '%s': %s", name, exc)
    if registered:
        logger.info("Registered %d MCP agent tool(s): %s", len(registered), ", ".join(registered))
    return registered
