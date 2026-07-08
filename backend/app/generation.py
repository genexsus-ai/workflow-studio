"""Natural-language workflow generation for the Studio.

Bridges the genxai.builder generation pipelines (single-shot planner and the
planner→delegator→worker crew) into the Studio: builds a capability catalog
grounded in what THIS Studio instance can execute (registered tools including
connector/MCP action tools, plus the connector catalog's per-action entries),
runs generation, and emits the compiled library workflow as a canvas-ready
``WorkflowDoc`` with auto-layout positions and automation mapped from the
plan's trigger.
"""

from __future__ import annotations

import logging
from typing import Any

from app.connectors_catalog import CONNECTOR_CATALOG
from app.schemas import AutomationConfig, EdgeDoc, NodeDoc, Position, WorkflowDoc
from genxai.builder.catalog import CapabilityCatalog, build_capability_catalog
from genxai.builder.crew import crew_generate_workflow
from genxai.builder.generator import generate_workflow, refine_workflow
from genxai.builder.memory import GenerationMemory

logger = logging.getLogger(__name__)

_memory: GenerationMemory | None = None


def get_generation_memory() -> GenerationMemory:
    """Episodic memory of past generations, persisted in the data dir."""
    global _memory
    if _memory is None:
        from app.config import get_settings

        _memory = GenerationMemory(get_settings().data_dir / "generation_memory.jsonl")
    return _memory


DEFAULT_GENERATION_MODEL = "claude-sonnet-5"

_LAYOUT_X_START = 80.0
_LAYOUT_X_GAP = 280.0
_LAYOUT_Y_START = 80.0
_LAYOUT_Y_GAP = 150.0


def studio_capability_catalog() -> CapabilityCatalog:
    """Catalog of everything this Studio instance can execute.

    Tools come from the live ToolRegistry (the Studio bootstraps its curated
    tool set, and MCP server syncs register ``mcp__server__tool`` entries
    there too). Connector actions are added as a dedicated section named
    ``<connector>.<action>`` so the planner can reference them precisely.
    """
    connector_entries = []
    for connector in CONNECTOR_CATALOG:
        for action, spec in connector.get("actions", {}).items():
            params = spec.get("params", [])
            connector_entries.append(
                {
                    "name": f"{connector['type']}.{action}",
                    "description": spec.get("description", ""),
                    "parameters": {param["name"]: {} for param in params},
                    "required": [param["name"] for param in params if param.get("required")],
                }
            )
    return build_capability_catalog(extra_sections={"connector": connector_entries})


def _layout_positions(
    nodes: list[dict[str, Any]], edges: list[dict[str, Any]]
) -> dict[str, Position]:
    """Layered auto-layout: x by longest-path depth, y by index within layer."""
    incoming: dict[str, list[str]] = {node["id"]: [] for node in nodes}
    for edge in edges:
        if edge["to"] in incoming:
            incoming[edge["to"]].append(edge["from"])

    depths: dict[str, int] = {}

    def depth_of(node_id: str, seen: frozenset[str] = frozenset()) -> int:
        if node_id in depths:
            return depths[node_id]
        if node_id in seen:  # cycle guard (loop-back edges)
            return 0
        parents = incoming.get(node_id, [])
        value = (
            0 if not parents else 1 + max(depth_of(parent, seen | {node_id}) for parent in parents)
        )
        depths[node_id] = value
        return value

    layers: dict[int, list[str]] = {}
    for node in nodes:
        layers.setdefault(depth_of(node["id"]), []).append(node["id"])

    positions: dict[str, Position] = {}
    for depth, node_ids in layers.items():
        for index, node_id in enumerate(node_ids):
            positions[node_id] = Position(
                x=_LAYOUT_X_START + depth * _LAYOUT_X_GAP,
                y=_LAYOUT_Y_START + index * _LAYOUT_Y_GAP,
            )
    return positions


def _doc_node(node: dict[str, Any], position: Position) -> NodeDoc:
    node_type = node["type"]
    config = dict(node.get("config", {}))

    if node_type == "condition":
        # Library DSL name → Studio node type.
        node_type = "decision"
    elif node_type == "tool":
        tool_name = config.get("tool_name", "")
        if "." in tool_name:
            # Connector action capability (e.g. "slack.send_message") →
            # a proper connector node the ConfigPanel can edit.
            connector, _, action = tool_name.partition(".")
            return NodeDoc(
                id=node["id"],
                type="connector",
                label=node["id"].replace("_", " ").title(),
                position=position,
                config={
                    "connector": connector,
                    "action": action,
                    "credential": "",
                    "params": config.get("tool_params") or {},
                },
            )

    label = config.get("role") or node["id"].replace("_", " ").title()
    return NodeDoc(
        id=node["id"],
        type=node_type,
        label=label,
        position=position,
        config=config,
    )


def _automation_from_trigger(trigger: dict[str, Any] | None) -> AutomationConfig:
    if not trigger:
        return AutomationConfig()
    kind = trigger.get("kind", "manual")
    config = trigger.get("config", {}) or {}
    if kind == "schedule":
        interval = int(config.get("interval_seconds", 3600))
        return AutomationConfig(schedule_enabled=True, interval_seconds=interval)
    if kind == "webhook":
        # Flag the intent; the token is provisioned when the user enables
        # automation on the saved workflow.
        return AutomationConfig(
            webhook_enabled=False,
            webhook_provider=config.get("provider", "generic"),
            webhook_event_filter=config.get("event_filter"),
        )
    return AutomationConfig()


def workflow_to_doc(workflow: dict[str, Any]) -> WorkflowDoc:
    """Convert a compiled library workflow dict into a canvas WorkflowDoc."""
    nodes = workflow["graph"]["nodes"]
    edges = workflow["graph"]["edges"]
    positions = _layout_positions(nodes, edges)

    doc_nodes = [_doc_node(node, positions[node["id"]]) for node in nodes]
    doc_edges = [
        EdgeDoc(
            source=edge["from"],
            target=edge["to"],
            condition=edge.get("condition"),
            parallel=bool(edge.get("parallel")),
        )
        for edge in edges
    ]
    return WorkflowDoc(
        name=workflow.get("name", "Generated workflow"),
        description=workflow.get("description", ""),
        nodes=doc_nodes,
        edges=doc_edges,
        automation=_automation_from_trigger(workflow.get("trigger")),
    )


async def generate_workflow_doc(
    prompt: str,
    *,
    model: str = DEFAULT_GENERATION_MODEL,
    crew: bool = False,
    on_event: Any = None,
    current_workflow: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate (or refine) a draft WorkflowDoc from a natural-language prompt.

    With ``current_workflow`` set, ``prompt`` is treated as a modification
    instruction for that document. Returns a payload with the doc plus
    generation provenance the UI can surface (open questions, reviewer
    verdict, warnings, generation_id for acceptance tracking).
    """
    from genxai.llm.factory import LLMProviderFactory

    catalog = studio_capability_catalog()
    generate_fn = crew_generate_workflow if crew else generate_workflow
    memory = get_generation_memory()

    provider = LLMProviderFactory.create_provider(model=model)
    try:
        if current_workflow is not None:
            result = await refine_workflow(
                prompt,
                current_workflow,
                llm_provider=provider,
                generate_fn=generate_fn,
                catalog=catalog,
                default_model=model,
                on_event=on_event,
                memory=memory,
            )
        else:
            result = await generate_fn(
                prompt,
                llm_provider=provider,
                catalog=catalog,
                default_model=model,
                on_event=on_event,
                memory=memory,
            )
    finally:
        await provider.aclose()

    doc = workflow_to_doc(result.workflow)

    from app.runner import validate

    validation = validate(doc)
    review = getattr(result, "review", None)
    return {
        "workflow": doc.model_dump(),
        "open_questions": [q.model_dump() for q in result.plan.open_questions],
        "review": review.model_dump() if review is not None else None,
        "warnings": list(getattr(result, "warnings", [])),
        "validation": validation.model_dump(),
        "llm_attempts": result.llm_attempts,
        "generation_id": result.generation_id,
    }
