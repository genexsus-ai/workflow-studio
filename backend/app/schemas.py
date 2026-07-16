"""Pydantic models for workflow documents and API payloads."""

from typing import Any

from pydantic import BaseModel, Field

NODE_TYPES = (
    "trigger",
    "input",
    "output",
    "human",
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
ATTACH_KINDS = ("model", "memory", "tools", "agents")


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
    # Cron expression (crontab syntax); when set it wins over interval_seconds
    schedule_cron: str | None = None
    # IANA timezone the cron expression is evaluated in
    schedule_timezone: str = "UTC"
    # Workflow to run when a run of this workflow fails (n8n error workflow)
    error_workflow_id: str | None = None


class WorkflowVersionInfo(BaseModel):
    version: str
    saved_at: str
    name: str
    node_count: int = 0


class WorkflowDoc(BaseModel):
    id: str | None = None
    name: str = "Untitled workflow"
    description: str = ""
    nodes: list[NodeDoc] = Field(default_factory=list)
    edges: list[EdgeDoc] = Field(default_factory=list)
    automation: AutomationConfig = Field(default_factory=AutomationConfig)
    # Give all agents in a run a common SharedMemoryBus (framework feature)
    shared_memory: bool = False
    # Sample run input pinned by the user (n8n-style): used to prefill manual
    # runs and as the input for node tests when no explicit input is given
    pinned_input: dict[str, Any] | None = None


class WorkflowSummary(BaseModel):
    id: str
    name: str
    description: str = ""
    node_count: int = 0


class FeedbackCreate(BaseModel):
    message: str
    email: str | None = None
    category: str | None = None
    page: str | None = None


class RunRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)
    model_override: str | None = None


class HumanInputResponse(BaseModel):
    node_id: str
    response: Any = None


class DatasetAnalyzeRequest(BaseModel):
    question: str | None = None
    model: str | None = None
    # Run the verification crew (Fact-Checker + Judge) over the insight
    verify: bool = True


class DashboardReportRequest(BaseModel):
    # Optional emphasis for the dashboard (e.g. "revenue by region")
    focus: str | None = None


class SourceCreate(BaseModel):
    name: str
    kind: str  # "sql" | "file"
    config: dict[str, Any] = Field(default_factory=dict)


class AnalysisCreate(BaseModel):
    name: str
    sources: dict[str, str] = Field(default_factory=dict)  # alias -> source id


class AnalysisPatch(BaseModel):
    name: str | None = None
    sources: dict[str, str] | None = None


class CellAsk(BaseModel):
    question: str


class CellManual(BaseModel):
    sql: str
    question: str | None = None


class CellPatch(BaseModel):
    sql: str | None = None
    question: str | None = None


class MaterializeRequest(BaseModel):
    dataset: str
    mode: str = "replace"  # replace: dataset mirrors the source; append: history
    interval_seconds: int = 3600
    cron: str | None = None


class OAuthAppConfig(BaseModel):
    client_id: str
    client_secret: str


class OAuthStartRequest(BaseModel):
    credential_name: str
    scopes: list[str] | None = None


class NodeTestRequest(BaseModel):
    node_id: str
    input: dict[str, Any] = Field(default_factory=dict)
    # Upstream node outputs to seed state with; omitted = use the latest run's
    upstream: dict[str, Any] | None = None


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
    # When set, overrides the AI-chosen workflow name on the generated doc.
    name: str | None = None
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


class ScheduleReportRequest(BaseModel):
    interval_seconds: int = 86400
    cron: str | None = None
    slack_credential: str | None = None
    slack_channel: str | None = None


class ModelTrainRequest(BaseModel):
    name: str
    source: str
    target: str
    model_type: str
    features: list[str] | None = None


class ModelPredictRequest(BaseModel):
    source: str
    dataset: str | None = None
    mode: str = "replace"


class ExperimentCreate(BaseModel):
    objective: str
    source: str
    target: str | None = None
    human_gates: bool = False


class GateDecision(BaseModel):
    approve: bool
    note: str | None = None
