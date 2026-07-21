"""Node palette definitions served to the canvas frontend."""

from typing import Any

from app.connectors_catalog import CONNECTOR_CATALOG
from genxai.tools.registry import ToolRegistry

MODEL_OPTIONS = [
    {"id": "claude-opus-4-8", "label": "Claude Opus 4.8", "provider": "anthropic"},
    {"id": "claude-sonnet-5", "label": "Claude Sonnet 5", "provider": "anthropic"},
    {"id": "claude-haiku-4-5", "label": "Claude Haiku 4.5", "provider": "anthropic"},
    {"id": "gpt-4o", "label": "GPT-4o", "provider": "openai"},
    {"id": "gpt-4", "label": "GPT-4", "provider": "openai"},
]

# Multi-agent collaboration patterns (genxai.flows.FLOW_TYPES) exposed as
# Agent Team nodes. `params` describes each pattern's tunables so the panel
# can render proper inputs; `order_hint` explains what agent order means.
FLOW_PATTERNS: list[dict[str, Any]] = [
    {
        "id": "critic_review",
        "label": "Critic review",
        "description": "First agent drafts, second critiques; loops until accepted or max iterations.",
        "order_hint": "Agent 1 = generator, Agent 2 = critic.",
        "min_agents": 2,
        "params": [
            {
                "name": "max_iterations",
                "type": "number",
                "default": 3,
                "min": 1,
                "max": 10,
            },
        ],
    },
    {
        "id": "ensemble_voting",
        "label": "Ensemble voting",
        "description": "All agents answer independently; the majority answer wins.",
        "order_hint": "Order does not matter.",
        "min_agents": 2,
        "params": [],
    },
    {
        "id": "map_reduce",
        "label": "Map-reduce",
        "description": "All agents but the last work in parallel; the last agent combines their results.",
        "order_hint": "Last agent = reducer.",
        "min_agents": 2,
        "params": [],
    },
    {
        "id": "delegator_worker",
        "label": "Delegator / workers",
        "description": (
            "First agent routes typed work packets to the other agents; "
            "packets run in dependency waves and dependent packets receive "
            "upstream results."
        ),
        "order_hint": "Agent 1 = delegator, the rest = workers (addressed by role).",
        "min_agents": 2,
        "params": [],
    },
    {
        "id": "coordinator_worker",
        "label": "Coordinator / workers",
        "description": "First agent plans the work; the rest execute it in parallel.",
        "order_hint": "Agent 1 = coordinator, the rest = workers.",
        "min_agents": 2,
        "params": [],
    },
    {
        "id": "auction",
        "label": "Auction",
        "description": "Each agent bids on the task; the highest bidder executes it.",
        "order_hint": "Order does not matter.",
        "min_agents": 2,
        "params": [],
    },
    {
        "id": "p2p",
        "label": "Peer-to-peer rounds",
        "description": "Agents iterate in rounds until consensus, convergence, or quality threshold.",
        "order_hint": "Order does not matter.",
        "min_agents": 2,
        "params": [
            {"name": "max_rounds", "type": "number", "default": 5, "min": 1, "max": 20},
            {
                "name": "consensus_threshold",
                "type": "number",
                "default": 0.6,
                "min": 0,
                "max": 1,
            },
            {
                "name": "convergence_window",
                "type": "number",
                "default": 3,
                "min": 1,
                "max": 10,
            },
            {
                "name": "quality_threshold",
                "type": "number",
                "default": 0.85,
                "min": 0,
                "max": 1,
            },
        ],
    },
    {
        "id": "round_robin",
        "label": "Round robin",
        "description": "Agents run one after another in listed order.",
        "order_hint": "Agents run in listed order.",
        "min_agents": 1,
        "params": [],
    },
    {
        "id": "parallel",
        "label": "Parallel",
        "description": "All agents run concurrently on the same input.",
        "order_hint": "Order does not matter.",
        "min_agents": 1,
        "params": [],
    },
]

NODE_TYPE_DEFS: list[dict[str, Any]] = [
    {
        "type": "trigger",
        "label": "Trigger",
        "description": (
            "Starts the workflow automatically. Saving a workflow with a "
            "trigger node configures its automation (schedule, webhook, or "
            "hosted form); the trigger itself is not an execution step."
        ),
        "color": "#ef4444",
        "config_fields": [
            {
                "name": "trigger_kind",
                "type": "select",
                "required": True,
                "default": "schedule",
                "options": ["manual", "schedule", "webhook", "form"],
            },
            {
                "name": "interval_seconds",
                "type": "number",
                "required": False,
                "default": 3600,
                "min": 30,
            },
            {
                "name": "cron",
                "type": "string",
                "required": False,
                "placeholder": "0 9 * * 1-5 — cron expression, overrides interval",
            },
            {
                "name": "timezone",
                "type": "string",
                "required": False,
                "placeholder": "America/New_York — IANA timezone for cron (default UTC)",
            },
            {
                "name": "webhook_provider",
                "type": "select",
                "required": False,
                "default": "generic",
                "options": ["generic", "github"],
            },
            {
                "name": "webhook_event_filter",
                "type": "string",
                "required": False,
                "placeholder": "issues.opened (GitHub webhooks only)",
            },
            {
                "name": "webhook_secret",
                "type": "password",
                "required": False,
                "placeholder": "HMAC secret — same as in GitHub webhook settings",
            },
            {
                "name": "form_title",
                "type": "string",
                "required": False,
                "placeholder": "Title shown on the hosted form",
            },
            {
                "name": "form_description",
                "type": "string",
                "required": False,
                "placeholder": "Shown under the form title",
            },
        ],
    },
    {
        "type": "input",
        "label": "Input",
        "description": "Workflow entry point; receives the run input.",
        "color": "#0ea5e9",
        "config_fields": [],
    },
    {
        "type": "output",
        "label": "Output",
        "description": "Workflow exit point; captures the final state.",
        "color": "#22c55e",
        "config_fields": [],
    },
    {
        "type": "human",
        "label": "Human approval",
        "description": (
            "Pauses the run until a person responds in the Run panel (or via "
            "the API). Later nodes can reference {{ <id>.response }}."
        ),
        "color": "#f59e0b",
        "config_fields": [
            {
                "name": "prompt",
                "type": "string",
                "required": False,
                "placeholder": "Approve {{ input.doc }}? — templates allowed",
            },
            {
                "name": "timeout_seconds",
                "type": "number",
                "required": False,
                "min": 1,
                "placeholder": "wait forever if empty",
            },
            {
                "name": "default_response",
                "type": "string",
                "required": False,
                "placeholder": "used when the timeout expires",
            },
        ],
    },
    {
        "type": "agent",
        "label": "Agent",
        "description": "An LLM agent with a role, goal, and optional tools.",
        "color": "#8b5cf6",
        "config_fields": [
            {
                "name": "role",
                "type": "string",
                "required": True,
                "placeholder": "Research Analyst",
            },
            {
                "name": "goal",
                "type": "text",
                "required": True,
                "placeholder": "Summarize the input",
            },
            {
                "name": "task",
                "type": "text",
                "required": False,
                "placeholder": "Supports {{ node_id.data.result }} expressions",
            },
            {"name": "backstory", "type": "text", "required": False},
            {
                "name": "llm_model",
                "type": "model_select",
                "required": False,
                "default": "claude-opus-4-8",
            },
            {
                "name": "temperature",
                "type": "number",
                "required": False,
                "default": 0.7,
                "min": 0,
                "max": 1,
            },
            {
                "name": "tools",
                "type": "tool_multiselect",
                "required": False,
                "default": [],
            },
        ],
    },
    {
        "type": "tool",
        "label": "Tool",
        "description": "Runs a single tool with fixed parameters.",
        "color": "#f59e0b",
        "config_fields": [
            {"name": "tool_name", "type": "tool_select", "required": True},
            {"name": "tool_params", "type": "json", "required": False, "default": {}},
        ],
    },
    {
        "type": "connector",
        "label": "Connector",
        "description": "Call an integration: Slack, GitHub, Jira, Notion, Google Workspace.",
        "color": "#f97316",
        "config_fields": [
            {"name": "connector", "type": "connector_select", "required": True},
            {"name": "action", "type": "action_select", "required": True},
            {"name": "credential", "type": "credential_select", "required": True},
            {"name": "params", "type": "json", "required": False, "default": {}},
        ],
    },
    {
        "type": "mcp",
        "label": "MCP Tool",
        "description": "Call a tool on any MCP (Model Context Protocol) server.",
        "color": "#7c3aed",
        "config_fields": [
            {"name": "server", "type": "mcp_server_select", "required": True},
            {"name": "tool", "type": "mcp_tool_select", "required": True},
            {"name": "params", "type": "json", "required": False, "default": {}},
        ],
    },
    {
        "type": "filter",
        "label": "Filter",
        "description": "Keep only the list items that match a condition (n8n-style).",
        "color": "#0ea5e9",
        "config_fields": [
            {
                "name": "items",
                "type": "string",
                "required": True,
                "placeholder": "{{ fetch.data.result }} — the list to filter",
            },
            {
                "name": "field",
                "type": "string",
                "required": False,
                "placeholder": "field to test on each item, e.g. status (blank = the item itself)",
            },
            {
                "name": "operator",
                "type": "select",
                "required": True,
                "default": "equals",
                "options": [
                    "equals",
                    "not_equals",
                    "greater_than",
                    "less_than",
                    "greater_or_equal",
                    "less_or_equal",
                    "contains",
                    "not_contains",
                    "starts_with",
                    "ends_with",
                    "is_empty",
                    "is_not_empty",
                    "is_true",
                    "is_false",
                ],
            },
            {
                "name": "value",
                "type": "string",
                "required": False,
                "placeholder": "value to compare against",
            },
            {
                "name": "keep",
                "type": "boolean",
                "required": False,
                "default": True,
            },
        ],
    },
    {
        "type": "decision",
        "label": "Decision",
        "description": "Routes flow based on a condition key in workflow state.",
        "color": "#ec4899",
        "config_fields": [
            {
                "name": "condition",
                "type": "string",
                "required": True,
                "placeholder": "state key to test",
            },
        ],
    },
    {
        "type": "loop",
        "label": "Loop",
        "description": "Repeats a body (tool/agent) until a condition or limit.",
        "color": "#14b8a6",
        "config_fields": [
            {"name": "condition", "type": "string", "required": False},
            {
                "name": "max_iterations",
                "type": "number",
                "required": False,
                "default": 5,
                "min": 1,
                "max": 100,
            },
        ],
    },
    {
        "type": "flow",
        "label": "Agent Team",
        "description": "Runs a multi-agent collaboration pattern (critic review, voting, map-reduce, ...) as one step.",
        "color": "#d946ef",
        "config_fields": [
            {
                "name": "flow_type",
                "type": "flow_select",
                "required": True,
                "default": "critic_review",
            },
            {"name": "agents", "type": "agent_list", "required": True, "default": []},
            {
                "name": "task",
                "type": "text",
                "required": False,
                "placeholder": "Team task; supports {{ input.topic }} expressions",
            },
            {"name": "state", "type": "json", "required": False, "default": {}},
        ],
    },
    {
        "type": "subworkflow",
        "label": "Subworkflow",
        "description": "Runs another saved workflow as a single step.",
        "color": "#6366f1",
        "config_fields": [
            {"name": "workflow_id", "type": "workflow_select", "required": True},
        ],
    },
    {
        "type": "model",
        "label": "Chat Model",
        "description": "Attach to an agent's Model port to choose its LLM.",
        "color": "#10b981",
        "attachment": "model",
        "config_fields": [
            {
                "name": "llm_model",
                "type": "model_select",
                "required": True,
                "default": "claude-opus-4-8",
            },
            {
                "name": "temperature",
                "type": "number",
                "required": False,
                "default": 0.7,
                "min": 0,
                "max": 1,
            },
        ],
    },
    {
        "type": "memory",
        "label": "Memory",
        "description": "Attach to an agent's Memory port so it remembers across runs.",
        "color": "#eab308",
        "attachment": "memory",
        "config_fields": [
            {
                "name": "persistent",
                "type": "boolean",
                "required": False,
                "default": True,
            },
            {
                "name": "session_key",
                "type": "string",
                "required": False,
                "placeholder": "default",
            },
        ],
    },
]


def _agent_presets() -> list[dict[str, Any]]:
    """Reusable role agents from the genxai agent library, picker-ready."""
    from genxai.agents.library import AGENT_LIBRARY

    return [
        {
            "name": name,
            "role": spec["role"],
            "goal": spec["goal"],
            "backstory": spec.get("backstory", ""),
            "temperature": spec.get("llm_temperature", 0.7),
            "tools": spec.get("tools", []),
        }
        for name, spec in sorted(AGENT_LIBRARY.items())
    ]


def build_palette() -> dict[str, Any]:
    tools = []
    for tool in ToolRegistry.list_all():
        schema = tool.get_schema()
        tools.append(
            {
                "name": schema["name"],
                "description": schema["description"],
                "category": schema.get("category"),
                "parameters": schema.get("parameters", {}),
            }
        )
    return {
        "node_types": NODE_TYPE_DEFS,
        "tools": sorted(tools, key=lambda t: t["name"]),
        "models": MODEL_OPTIONS,
        "connectors": CONNECTOR_CATALOG,
        "flows": FLOW_PATTERNS,
        "agent_presets": _agent_presets(),
    }
