"""Translate workflow documents to executor format and run them."""

import logging
import os
from typing import Any

from app.config import get_settings
from app.schemas import (
    ATTACH_KINDS,
    NODE_TYPES,
    ValidationIssue,
    ValidationResult,
    WorkflowDoc,
)
from genxai.core.execution import ExecutionStore

logger = logging.getLogger(__name__)

_execution_store: ExecutionStore | None = None


def get_execution_store() -> ExecutionStore:
    global _execution_store
    if _execution_store is None:
        settings = get_settings()
        if settings.use_db_persistence:
            from app.exec_store import StudioExecutionStore

            try:
                _execution_store = StudioExecutionStore(settings.sync_database_url)
            except Exception:
                if settings.persistence_strict:
                    raise
                logger.exception(
                    "PERSISTENCE_BACKEND=postgres but the database is "
                    "unreachable — falling back to file persistence for runs"
                )
        if _execution_store is None:
            runs_dir = settings.data_dir / "runs"
            runs_dir.mkdir(parents=True, exist_ok=True)
            _execution_store = ExecutionStore(persistence_path=runs_dir)
    return _execution_store


def _agent_attachments(doc: WorkflowDoc) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Fold capability edges (attach=model/memory/tools) into per-agent overrides.

    Returns (agent_id -> overrides, ids of capability-source nodes to drop
    from the flow graph).
    """
    from genxai.tools.mcp_client import _sanitize_name

    node_by_id = {node.id: node for node in doc.nodes}
    overrides: dict[str, dict[str, Any]] = {}
    attached_ids: set[str] = set()

    for edge in doc.edges:
        if not edge.attach:
            continue
        source = node_by_id.get(edge.source)
        target = node_by_id.get(edge.target)
        if source is None or target is None or target.type != "agent":
            continue  # validate() reports these
        attached_ids.add(source.id)
        agent = overrides.setdefault(target.id, {"tools": []})
        if edge.attach == "model" and source.type == "model":
            if source.config.get("llm_model"):
                agent["llm_model"] = source.config["llm_model"]
            if source.config.get("temperature") is not None:
                agent["temperature"] = source.config["temperature"]
        elif edge.attach == "memory" and source.type == "memory":
            agent["memory_node"] = source
        elif edge.attach == "tools":
            if source.type == "tool":
                name = source.config.get("tool_name") or source.config.get("name")
                if name:
                    agent["tools"].append(name)
            elif source.type == "mcp":
                server = source.config.get("server")
                tool = source.config.get("tool")
                if server and tool:
                    agent["tools"].append(
                        f"mcp__{_sanitize_name(server)}__{_sanitize_name(tool)}"
                    )
    return overrides, attached_ids


def _apply_agent_overrides(
    doc: WorkflowDoc, node_id: str, config: dict[str, Any], overrides: dict[str, Any]
) -> None:
    if "llm_model" in overrides:
        config["llm_model"] = overrides["llm_model"]
    if "temperature" in overrides:
        config["temperature"] = overrides["temperature"]
    if overrides["tools"]:
        merged = list(config.get("tools") or []) + overrides["tools"]
        config["tools"] = list(dict.fromkeys(merged))
    memory_node = overrides.get("memory_node")
    if memory_node is not None:
        config["enable_memory"] = True
        if memory_node.config.get("persistent"):
            session = str(memory_node.config.get("session_key") or "default")
            identity = f"{doc.id or 'adhoc'}__{node_id}__{session}"
            safe = "".join(c if (c.isalnum() or c in "-_") else "_" for c in identity)
            base = get_settings().data_dir / "agent_memory" / safe
            config["memory"] = {"persistence_path": str(base), "memory_id": identity}


def _flow_team_attachments(doc: WorkflowDoc) -> tuple[dict[str, list[dict[str, Any]]], set[str]]:
    """Fold agent nodes attached to a flow node's Agents port into its team.

    Attached agents join the team AFTER any inline config agents, ordered by
    canvas row then column (top row first, left to right within a row) —
    so a lead agent placed above a worker row keeps agent-1 position.

    Returns (flow_id -> agent spec dicts, ids of attached agent nodes).
    """
    node_by_id = {node.id: node for node in doc.nodes}
    teams: dict[str, list[tuple[float, float, dict[str, Any]]]] = {}
    attached_ids: set[str] = set()

    for edge in doc.edges:
        if edge.attach != "agents":
            continue
        source = node_by_id.get(edge.source)
        target = node_by_id.get(edge.target)
        if source is None or target is None or source.type != "agent" or target.type != "flow":
            continue  # validate() reports these
        attached_ids.add(source.id)
        spec: dict[str, Any] = {
            "role": source.config.get("role") or source.label or source.id,
            "goal": source.config.get("goal", ""),
        }
        for key in ("backstory", "llm_model", "temperature", "tools"):
            if source.config.get(key) not in (None, "", []):
                spec[key] = source.config[key]
        teams.setdefault(target.id, []).append(
            (source.position.x, source.position.y, spec)
        )

    ordered = {
        flow_id: [spec for _x, _y, spec in sorted(members, key=lambda m: (m[1], m[0]))]
        for flow_id, members in teams.items()
    }
    return ordered, attached_ids


def translate(doc: WorkflowDoc) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Convert a WorkflowDoc into the node/edge dicts WorkflowExecutor accepts.

    Capability edges (attach set) never reach the executor: they are folded
    into the target agent's config, and their source nodes leave the flow.
    """
    agent_overrides, attached_ids = _agent_attachments(doc)
    flow_teams, team_agent_ids = _flow_team_attachments(doc)
    attached_ids = attached_ids | team_agent_ids
    # Trigger nodes declare automation (schedule/webhook); they are not
    # execution steps, so they and their edges never reach the executor.
    trigger_ids = {node.id for node in doc.nodes if node.type == "trigger"}
    nodes = []
    for node in doc.nodes:
        if node.type in ("model", "memory", "trigger") or node.id in attached_ids:
            continue
        if node.type == "connector":
            # Connector nodes execute through the connector_action tool, so
            # they inherit template interpolation and per-node results.
            config = {
                "tool_name": "connector_action",
                "tool_params": {
                    "connector": node.config.get("connector"),
                    "action": node.config.get("action"),
                    "credential": node.config.get("credential"),
                    "params": node.config.get("params") or {},
                },
            }
            for passthrough in ("execution", "for_each"):
                if node.config.get(passthrough):
                    config[passthrough] = node.config[passthrough]
            nodes.append({"id": node.id, "type": "tool", "config": config})
        elif node.type == "mcp":
            config = {
                "tool_name": "mcp_action",
                "tool_params": {
                    "server": node.config.get("server"),
                    "tool": node.config.get("tool"),
                    "params": node.config.get("params") or {},
                },
            }
            for passthrough in ("execution", "for_each"):
                if node.config.get(passthrough):
                    config[passthrough] = node.config[passthrough]
            nodes.append({"id": node.id, "type": "tool", "config": config})
        elif node.type == "filter":
            # Filter nodes run through the data_filter tool (n8n-style).
            config = {
                "tool_name": "data_filter",
                "tool_params": {
                    "items": node.config.get("items"),
                    "field": node.config.get("field") or None,
                    "operator": node.config.get("operator") or "is_not_empty",
                    "value": node.config.get("value"),
                    "keep": node.config.get("keep", True),
                },
            }
            for passthrough in ("execution", "for_each"):
                if node.config.get(passthrough):
                    config[passthrough] = node.config[passthrough]
            nodes.append({"id": node.id, "type": "tool", "config": config})
        elif node.type == "set_fields":
            # Set / Edit Fields nodes run through the data_set_fields tool.
            include_input = node.config.get("include_input", False)
            params: dict[str, Any] = {
                "fields": node.config.get("fields") or {},
                "keep_only_set": not include_input,
            }
            if include_input:
                params["base"] = "{{ input }}"
            config = {"tool_name": "data_set_fields", "tool_params": params}
            for passthrough in ("execution", "for_each"):
                if node.config.get(passthrough):
                    config[passthrough] = node.config[passthrough]
            nodes.append({"id": node.id, "type": "tool", "config": config})
        elif node.type == "datetime":
            # Date/Time formatter nodes run through the date_time tool.
            # Omit unset params so the tool's validator doesn't reject nulls.
            params = {"operation": node.config.get("operation") or "format"}
            for key in ("value", "format", "amount", "unit", "to"):
                val = node.config.get(key)
                if val not in (None, ""):
                    params[key] = val
            config = {"tool_name": "date_time", "tool_params": params}
            for passthrough in ("execution", "for_each"):
                if node.config.get(passthrough):
                    config[passthrough] = node.config[passthrough]
            nodes.append({"id": node.id, "type": "tool", "config": config})
        elif node.type == "agent":
            config = dict(node.config)
            if node.id in agent_overrides:
                _apply_agent_overrides(doc, node.id, config, agent_overrides[node.id])
            nodes.append({"id": node.id, "type": "agent", "config": config})
        elif node.type == "subworkflow":
            nodes.append(
                {
                    "id": node.id,
                    "type": "subgraph",
                    "config": {"workflow_id": node.config.get("workflow_id")},
                }
            )
        elif node.type == "flow":
            config = dict(node.config)
            if node.id in flow_teams:
                config["agents"] = list(config.get("agents") or []) + flow_teams[node.id]
            nodes.append({"id": node.id, "type": "flow", "config": config})
        else:
            nodes.append(
                {"id": node.id, "type": node.type, "config": dict(node.config)}
            )
    edges = []
    for edge in doc.edges:
        if edge.attach or edge.source in trigger_ids or edge.target in trigger_ids:
            continue
        edge_dict: dict[str, Any] = {"source": edge.source, "target": edge.target}
        if edge.condition:
            edge_dict["condition"] = edge.condition
        if edge.parallel:
            edge_dict["parallel"] = True
        edges.append(edge_dict)
    return nodes, edges


def resolve_subgraphs(doc: WorkflowDoc) -> dict[str, dict[str, Any]]:
    """Load + translate every workflow referenced by a subworkflow node.

    Returns the {workflow_id: {nodes, edges}} map the engine's subgraph
    nodes resolve against. One level deep: nested subworkflow nodes inside
    a referenced workflow are not resolved.
    """
    from app.api.routes import get_store

    store = get_store()
    subgraphs: dict[str, dict[str, Any]] = {}
    for node in doc.nodes:
        if node.type != "subworkflow":
            continue
        workflow_id = node.config.get("workflow_id")
        if not workflow_id or workflow_id in subgraphs:
            continue
        referenced = store.get(workflow_id)
        if referenced is None:
            logger.warning(
                "Subworkflow node '%s' references missing workflow '%s'",
                node.id,
                workflow_id,
            )
            continue
        sub_nodes, sub_edges = translate(referenced)
        subgraphs[workflow_id] = {"nodes": sub_nodes, "edges": sub_edges}
    return subgraphs


async def test_single_node(
    doc: WorkflowDoc,
    node_id: str,
    input_data: dict[str, Any],
    upstream_results: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute one node of a workflow in isolation (n8n-style node testing).

    The node runs with ``input_data`` as the run input and ``upstream_results``
    ({node_id: output}) seeded into state, so its ``{{ other_node.* }}``
    templates resolve exactly as they would mid-run.
    """
    from genxai.core.graph.executor import execute_workflow_async

    _apply_api_keys()
    nodes, _ = translate(doc)
    target = next((node for node in nodes if node["id"] == node_id), None)
    if target is None:
        raise ValueError(
            f"Node '{node_id}' is not an executable flow step (triggers and "
            "attached capability nodes cannot be tested in isolation)"
        )
    return await execute_workflow_async(
        nodes=[target],
        edges=[],
        input_data=input_data,
        extra_state=dict(upstream_results or {}),
        subgraphs=resolve_subgraphs(doc) or None,
    )


def cron_error(expression: str) -> str | None:
    """Why a crontab expression is invalid, or None if it parses."""
    try:
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        return None
    try:
        CronTrigger.from_crontab(expression)
    except ValueError as exc:
        return str(exc) or "invalid cron expression"
    return None


def timezone_error(name: str) -> str | None:
    """Why an IANA timezone name is invalid, or None if it resolves."""
    from zoneinfo import ZoneInfo

    try:
        ZoneInfo(name)
    except Exception:
        return f"unknown timezone '{name}' (use an IANA name like 'America/New_York')"
    return None


def validate(doc: WorkflowDoc) -> ValidationResult:
    """Structural validation without executing anything."""
    issues: list[ValidationIssue] = []
    node_ids = {node.id for node in doc.nodes}

    if not doc.nodes:
        issues.append(ValidationIssue(level="error", message="Workflow has no nodes"))

    seen: set[str] = set()
    for node in doc.nodes:
        if node.id in seen:
            issues.append(
                ValidationIssue(
                    level="error",
                    message=f"Duplicate node id '{node.id}'",
                    node_id=node.id,
                )
            )
        seen.add(node.id)
        if node.type not in NODE_TYPES:
            issues.append(
                ValidationIssue(
                    level="error",
                    message=f"Unknown node type '{node.type}'",
                    node_id=node.id,
                )
            )
        if node.type == "trigger" and node.config.get("trigger_kind") not in {
            "schedule",
            "webhook",
        }:
            issues.append(
                ValidationIssue(
                    level="error",
                    message="Trigger node needs trigger_kind 'schedule' or 'webhook'",
                    node_id=node.id,
                )
            )
        if node.type == "trigger" and node.config.get("trigger_kind") == "schedule":
            cron = str(node.config.get("cron") or "").strip()
            problem = cron_error(cron) if cron else None
            if problem:
                issues.append(
                    ValidationIssue(
                        level="error",
                        message=f"Invalid cron expression '{cron}': {problem}",
                        node_id=node.id,
                    )
                )
            tz = str(node.config.get("timezone") or "").strip()
            tz_problem = timezone_error(tz) if tz else None
            if tz_problem:
                issues.append(
                    ValidationIssue(level="error", message=tz_problem, node_id=node.id)
                )
        if node.type == "agent" and not node.config.get("role"):
            issues.append(
                ValidationIssue(
                    level="warning", message="Agent node has no role", node_id=node.id
                )
            )
        if node.type == "tool" and not (
            node.config.get("tool_name") or node.config.get("name")
        ):
            issues.append(
                ValidationIssue(
                    level="error",
                    message="Tool node has no tool selected",
                    node_id=node.id,
                )
            )
        if node.type == "mcp":
            for field in ("server", "tool"):
                if not node.config.get(field):
                    issues.append(
                        ValidationIssue(
                            level="error",
                            message=f"MCP node is missing '{field}'",
                            node_id=node.id,
                        )
                    )
        if node.type == "connector":
            for field in ("connector", "action", "credential"):
                if not node.config.get(field):
                    issues.append(
                        ValidationIssue(
                            level="error",
                            message=f"Connector node is missing '{field}'",
                            node_id=node.id,
                        )
                    )
        if node.type == "flow":
            if not node.config.get("flow_type"):
                issues.append(
                    ValidationIssue(
                        level="error",
                        message="Flow node has no pattern selected",
                        node_id=node.id,
                    )
                )
            agents = node.config.get("agents") or []
            attached_team = sum(
                1
                for edge in doc.edges
                if edge.attach == "agents" and edge.target == node.id
            )
            if (not isinstance(agents, list) or not agents) and attached_team == 0:
                issues.append(
                    ValidationIssue(
                        level="error",
                        message="Flow node needs at least one agent (inline or attached)",
                        node_id=node.id,
                    )
                )
            else:
                for index, spec in enumerate(agents):
                    if not isinstance(spec, dict) or not spec.get("role"):
                        issues.append(
                            ValidationIssue(
                                level="warning",
                                message=f"Flow agent #{index + 1} has no role",
                                node_id=node.id,
                            )
                        )
        if node.type == "subworkflow":
            workflow_id = node.config.get("workflow_id")
            if not workflow_id:
                issues.append(
                    ValidationIssue(
                        level="error",
                        message="Subworkflow node has no workflow selected",
                        node_id=node.id,
                    )
                )
            elif doc.id and workflow_id == doc.id:
                issues.append(
                    ValidationIssue(
                        level="error",
                        message="Subworkflow node cannot reference its own workflow",
                        node_id=node.id,
                    )
                )

    node_type = {node.id: node.type for node in doc.nodes}
    attach_counts: dict[tuple[str, str], int] = {}
    for edge in doc.edges:
        for endpoint in (edge.source, edge.target):
            if endpoint not in node_ids:
                issues.append(
                    ValidationIssue(
                        level="error",
                        message=f"Edge references missing node '{endpoint}'",
                    )
                )
        if edge.attach:
            if edge.attach not in ATTACH_KINDS:
                issues.append(
                    ValidationIssue(
                        level="error",
                        message=f"Unknown attachment kind '{edge.attach}'",
                    )
                )
                continue
            required_target = "flow" if edge.attach == "agents" else "agent"
            if node_type.get(edge.target) != required_target:
                issues.append(
                    ValidationIssue(
                        level="error",
                        message=(
                            f"'{edge.attach}' attachment edge must target "
                            f"a {required_target} node, not '{edge.target}'"
                        ),
                    )
                )
            expected = {
                "model": ("model",),
                "memory": ("memory",),
                "tools": ("tool", "mcp"),
                "agents": ("agent",),
            }[edge.attach]
            if node_type.get(edge.source) not in expected:
                issues.append(
                    ValidationIssue(
                        level="error",
                        message=(
                            f"'{edge.attach}' attachment needs a {' or '.join(expected)} node "
                            f"as source, got '{node_type.get(edge.source)}'"
                        ),
                        node_id=edge.source,
                    )
                )
            if edge.attach in ("model", "memory"):
                key = (edge.target, edge.attach)
                attach_counts[key] = attach_counts.get(key, 0) + 1
                if attach_counts[key] == 2:
                    issues.append(
                        ValidationIssue(
                            level="error",
                            message=f"Agent '{edge.target}' has more than one {edge.attach} attachment",
                            node_id=edge.target,
                        )
                    )

    # Capability nodes live outside the flow: they may only have attachment edges
    attached_sources = {edge.source for edge in doc.edges if edge.attach}
    for edge in doc.edges:
        if edge.attach:
            continue
        if node_type.get(edge.target) == "trigger":
            issues.append(
                ValidationIssue(
                    level="error",
                    message="Trigger nodes start the workflow — nothing can connect into them",
                    node_id=edge.target,
                )
            )
        for endpoint in (edge.source, edge.target):
            if node_type.get(endpoint) in ("model", "memory"):
                issues.append(
                    ValidationIssue(
                        level="error",
                        message=f"'{node_type[endpoint]}' node '{endpoint}' cannot be part of the flow — attach it to an agent's port",
                        node_id=endpoint,
                    )
                )
            elif endpoint in attached_sources:
                issues.append(
                    ValidationIssue(
                        level="error",
                        message=f"Node '{endpoint}' is attached to another node's port and cannot also be a flow step",
                        node_id=endpoint,
                    )
                )

    flow_nodes = [
        n
        for n in doc.nodes
        if n.type not in ("model", "memory") and n.id not in attached_sources
    ]
    targets = {edge.target for edge in doc.edges if not edge.attach}
    has_entry = any(n.id not in targets for n in flow_nodes) or any(
        n.type == "input" for n in flow_nodes
    )
    if flow_nodes and not has_entry:
        issues.append(
            ValidationIssue(
                level="error", message="No entry point (every node has incoming edges)"
            )
        )

    return ValidationResult(
        valid=not any(i.level == "error" for i in issues), issues=issues
    )


def _apply_api_keys() -> None:
    """Expose configured API keys as env vars for the genxai runtime."""
    settings = get_settings()
    if settings.openai_api_key:
        os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key)
    if settings.anthropic_api_key:
        os.environ.setdefault("ANTHROPIC_API_KEY", settings.anthropic_api_key)
