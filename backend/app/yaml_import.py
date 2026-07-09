"""Import genxai workflow YAML (the CLI's workflow DSL) into a WorkflowDoc.

The CLI (`genxai workflow run`) and this Studio both ultimately run on
WorkflowExecutor, but they read two different document shapes: the CLI's
YAML has flat node dicts (`{"id", "type", "tool_name": ...}`) and a
top-level `agents` list referenced by id, while the Studio's WorkflowDoc
nests everything per-node under `config` with agent fields inlined. This
module translates the former into the latter so an existing YAML workflow
(see examples/nocode/*.yaml) can be dragged onto the canvas and edited
visually.

`agents_ref` (loading agent definitions from a second YAML file) isn't
supported here: imports are pasted/uploaded text with no filesystem
context to resolve a relative path against. Inline the agents instead.
"""

from __future__ import annotations

from typing import Any

import yaml

from app.schemas import EdgeDoc, NodeDoc, WorkflowDoc
from genxai.core.graph.workflow_io import _validate_workflow_schema

_NODE_TYPE_MAP = {
    "start": "input",
    "end": "output",
    "condition": "decision",
    "subgraph": "subworkflow",
}


def _resolve_agent(node: dict[str, Any], agents_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Inline the referenced agent's fields into Studio agent-node config.

    The DSL's agent nodes reference a definition by id (`agent: <id>`,
    falling back to the node's own id) rather than carrying config inline;
    the Studio's agent nodes carry it inline, so we resolve the reference
    once at import time.
    """
    referenced = node.get("agent", node.get("id"))
    agent = agents_by_id.get(referenced, {})
    config: dict[str, Any] = {
        "role": agent.get("role", "Agent"),
        "goal": agent.get("goal", "Process tasks"),
        "backstory": agent.get("backstory", ""),
        "llm_model": agent.get("llm_model") or agent.get("llm") or "gpt-4",
        "temperature": agent.get("temperature", agent.get("llm_temperature", 0.7)),
        "tools": agent.get("tools", []),
    }
    if node.get("task"):
        config["task"] = node["task"]
    return config


def parse_workflow_yaml(text: str) -> WorkflowDoc:
    """Translate a genxai workflow YAML document into a Studio WorkflowDoc."""
    try:
        payload = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML: {exc}") from exc
    if not isinstance(payload, dict) or "workflow" not in payload:
        raise ValueError("Workflow YAML must contain a top-level 'workflow' mapping")

    workflow = payload["workflow"]
    if not isinstance(workflow, dict):
        raise ValueError("workflow must be a mapping")
    if workflow.get("agents_ref"):
        raise ValueError(
            "agents_ref is not supported when importing pasted/uploaded YAML — "
            "inline the referenced agents under workflow.agents instead"
        )

    _validate_workflow_schema(workflow)

    agents_by_id = {
        agent["id"]: agent for agent in workflow.get("agents", []) if isinstance(agent, dict)
    }
    graph = workflow["graph"]

    nodes: list[NodeDoc] = []
    for index, node in enumerate(graph["nodes"]):
        node_type = _NODE_TYPE_MAP.get(node["type"], node["type"])
        if node_type == "agent":
            config = _resolve_agent(node, agents_by_id)
        else:
            config = {k: v for k, v in node.items() if k not in {"id", "type", "agent"}}
            if node_type == "tool" and "tool_name" not in config and "tool" in config:
                config["tool_name"] = config.pop("tool")
        nodes.append(
            NodeDoc(
                id=node["id"],
                type=node_type,
                position={"x": 160.0 * (index % 5), "y": 140.0 * (index // 5)},
                config=config,
            )
        )

    edges = [
        EdgeDoc(
            source=edge["from"],
            target=edge["to"],
            condition=edge.get("condition"),
            parallel=bool(edge.get("parallel", False)),
        )
        for edge in graph.get("edges", [])
    ]

    memory = workflow.get("memory") if isinstance(workflow.get("memory"), dict) else {}

    return WorkflowDoc(
        id=None,
        name=workflow.get("name", "Imported workflow"),
        description=workflow.get("description", ""),
        nodes=nodes,
        edges=edges,
        shared_memory=bool(memory.get("shared", False)),
    )
