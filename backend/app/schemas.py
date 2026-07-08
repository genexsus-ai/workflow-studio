"""Pydantic models for workflow documents and API payloads."""

from typing import Any

from pydantic import BaseModel, Field

NODE_TYPES = (
    "input",
    "output",
    "agent",
    "tool",
    "connector",
    "mcp",
    "decision",
    "loop",
    "model",
    "memory",
    "subworkflow",
    "flow",
)

# Agent capability ports (n8n-style sub-nodes): an edge with attach set hangs
# a capability node off an agent instead of being a flow step.
ATTACH_KINDS = ("model", "memory", "tools")


class Position(BaseModel):
    x: float = 0.0
    y: float = 0.0


class NodeDoc(BaseModel):
    id: str
    type: str
    label: str | None = None
    position: Position = Field(default_factory=Position)
    config: dict[str, Any] = Field(default_factory=dict)


class EdgeDoc(BaseModel):
    id: str | None = None
    source: str
    target: str
    condition: str | None = None
    parallel: bool = False
    attach: str | None = None  # one of ATTACH_KINDS: capability edge, not a flow edge


class AutomationConfig(BaseModel):
    webhook_enabled: bool = False
    webhook_token: str | None = None
    webhook_provider: str = "generic"  # "generic" | "github"
    webhook_secret: str | None = None  # HMAC secret for provider signatures
    webhook_event_filter: str | None = None  # e.g. "issues.opened" or "issues"
    schedule_enabled: bool = False
    interval_seconds: int = 300


class WorkflowDoc(BaseModel):
    id: str | None = None
    name: str = "Untitled workflow"
    description: str = ""
    nodes: list[NodeDoc] = Field(default_factory=list)
    edges: list[EdgeDoc] = Field(default_factory=list)
    automation: AutomationConfig = Field(default_factory=AutomationConfig)
    # Give all agents in a run a common SharedMemoryBus (framework feature)
    shared_memory: bool = False


class WorkflowSummary(BaseModel):
    id: str
    name: str
    description: str = ""
    node_count: int = 0


class RunRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    model_override: str | None = None


class MCPServerCreate(BaseModel):
    name: str
    transport: str  # "mcp_stdio" | "mcp_http"
    config: dict[str, Any] = Field(default_factory=dict)


class CredentialCreate(BaseModel):
    name: str
    connector_type: str
    config: dict[str, Any] = Field(default_factory=dict)


class GenerateRequest(BaseModel):
    prompt: str
    model: str = "claude-sonnet-5"
    crew: bool = True
    # When set, `prompt` is a modification instruction for this document
    # (refine mode) instead of a from-scratch request.
    current_workflow: "WorkflowDoc | None" = None


class AdhocRunRequest(BaseModel):
    workflow: WorkflowDoc
    input: dict[str, Any] = Field(default_factory=dict)
    model_override: str | None = None


class ValidationIssue(BaseModel):
    level: str  # "error" | "warning"
    message: str
    node_id: str | None = None


class ValidationResult(BaseModel):
    valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)
