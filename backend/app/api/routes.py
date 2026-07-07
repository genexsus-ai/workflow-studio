"""REST + SSE routes for the Workflow Studio."""

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.automation import (
    ScheduleManager,
    find_workflow_by_token,
    generate_webhook_token,
    verify_github_signature,
)
from app.config import get_settings
from app.connectors_catalog import CONNECTOR_CATALOG
from app.credentials import ConnectorConfigEntry, get_credential_store, safe_listing
from app.mcp_registry import build_mcp_client, get_mcp_store, safe_mcp_listing, sync_agent_tools
from app.palette import build_palette
from app.run_manager import get_run_manager
from app.runner import get_execution_store, validate
from app.schemas import (
    AdhocRunRequest,
    AutomationConfig,
    CredentialCreate,
    MCPServerCreate,
    RunRequest,
    ValidationResult,
    WorkflowDoc,
    WorkflowSummary,
)
from app.store import WorkflowStore
from app.yaml_import import parse_workflow_yaml

router = APIRouter()

_store: WorkflowStore | None = None
_schedule_manager: ScheduleManager | None = None


def get_store() -> WorkflowStore:
    global _store
    if _store is None:
        _store = WorkflowStore(get_settings().data_dir)
    return _store


def get_schedule_manager() -> ScheduleManager:
    global _schedule_manager
    if _schedule_manager is None:
        _schedule_manager = ScheduleManager(get_store())
    return _schedule_manager


@router.get("/palette")
def palette() -> dict:
    return build_palette()


@router.get("/workflows", response_model=list[WorkflowSummary])
def list_workflows() -> list[WorkflowSummary]:
    return get_store().list()


@router.post("/workflows", response_model=WorkflowDoc, status_code=201)
def create_workflow(doc: WorkflowDoc) -> WorkflowDoc:
    return get_store().create(doc)


@router.post("/workflows/import-yaml", response_model=WorkflowDoc, status_code=201)
def import_workflow_yaml(payload: dict[str, str]) -> WorkflowDoc:
    """Import a genxai workflow YAML document (the CLI's `workflow run`
    format) as a new saved workflow, ready to open and edit on the canvas."""
    try:
        doc = parse_workflow_yaml(payload.get("yaml", ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return get_store().create(doc)


@router.get("/workflows/{workflow_id}", response_model=WorkflowDoc)
def get_workflow(workflow_id: str) -> WorkflowDoc:
    doc = get_store().get(workflow_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return doc


@router.put("/workflows/{workflow_id}", response_model=WorkflowDoc)
def update_workflow(workflow_id: str, doc: WorkflowDoc) -> WorkflowDoc:
    updated = get_store().update(workflow_id, doc)
    if updated is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return updated


@router.delete("/workflows/{workflow_id}", status_code=204)
def delete_workflow(workflow_id: str) -> None:
    if not get_store().delete(workflow_id):
        raise HTTPException(status_code=404, detail="Workflow not found")


@router.post("/workflows/validate", response_model=ValidationResult)
def validate_workflow(doc: WorkflowDoc) -> ValidationResult:
    return validate(doc)


async def _run_event_stream(run_id: str):
    """SSE generator subscribed to a run. Disconnecting only unsubscribes —
    the run itself keeps executing on the worker pool."""
    manager = get_run_manager()
    queue = manager.subscribe(run_id)
    try:
        while True:
            event = await queue.get()
            yield f"data: {json.dumps(event, default=str)}\n\n"
            if event.get("event") in {"complete", "error"}:
                break
    finally:
        manager.unsubscribe(run_id, queue)


def _sse(run_id: str) -> StreamingResponse:
    return StreamingResponse(
        _run_event_stream(run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/workflows/{workflow_id}/run/stream")
async def run_workflow_stream(workflow_id: str, payload: RunRequest) -> StreamingResponse:
    doc = get_store().get(workflow_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    result = validate(doc)
    if not result.valid:
        raise HTTPException(
            status_code=422,
            detail={"message": "Workflow is invalid", "issues": [i.model_dump() for i in result.issues]},
        )
    run_id = get_run_manager().submit(doc, payload.input, trigger="manual", model_override=payload.model_override)
    return _sse(run_id)


@router.post("/run/stream")
async def run_adhoc_stream(payload: AdhocRunRequest) -> StreamingResponse:
    """Run an unsaved workflow document directly (used by the canvas Run button)."""
    result = validate(payload.workflow)
    if not result.valid:
        raise HTTPException(
            status_code=422,
            detail={"message": "Workflow is invalid", "issues": [i.model_dump() for i in result.issues]},
        )
    run_id = get_run_manager().submit(
        payload.workflow, payload.input, trigger="manual", model_override=payload.model_override
    )
    return _sse(run_id)


@router.post("/workflows/{workflow_id}/automation", response_model=WorkflowDoc)
async def update_automation(workflow_id: str, config: AutomationConfig) -> WorkflowDoc:
    """Enable/disable webhook and schedule activation for a workflow."""
    store = get_store()
    doc = store.get(workflow_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Workflow not found")

    if config.webhook_enabled and not config.webhook_token:
        config.webhook_token = doc.automation.webhook_token or generate_webhook_token()
    if not config.webhook_enabled:
        config.webhook_token = None

    doc.automation = config
    store.update(workflow_id, doc)

    manager = get_schedule_manager()
    if config.schedule_enabled:
        await manager.enable(doc)
    else:
        await manager.disable(workflow_id)
    return doc


@router.post("/hooks/{token}")
async def fire_webhook(token: str, request: Request) -> dict:
    """Public fire-by-URL endpoint: runs the workflow bound to this token.

    With webhook_provider="github", the X-Hub-Signature-256 header is
    verified against the configured secret and the optional event filter
    ("issues" or "issues.opened") gates which deliveries run the workflow.
    """
    store = get_store()
    doc = find_workflow_by_token(store, token)
    if doc is None:
        raise HTTPException(status_code=404, detail="Unknown webhook token")

    raw_body = await request.body()
    try:
        payload = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="Body must be JSON") from None
    if not isinstance(payload, dict):
        payload = {"body": payload}

    automation = doc.automation
    input_data: dict = payload

    if automation.webhook_provider == "github":
        if automation.webhook_secret:
            signature = request.headers.get("X-Hub-Signature-256")
            if not verify_github_signature(automation.webhook_secret, raw_body, signature):
                raise HTTPException(status_code=401, detail="Invalid signature")
        event = request.headers.get("X-GitHub-Event", "")
        action = payload.get("action")
        full_event = f"{event}.{action}" if action else event
        event_filter = automation.webhook_event_filter
        if event_filter and event_filter not in {event, full_event}:
            return {"status": "ignored", "event": full_event, "filter": event_filter}
        input_data = {"event": full_event, "payload": payload}

    run_id = get_run_manager().submit(doc, input_data, trigger="webhook")
    return {"status": "accepted", "run_id": run_id, "workflow_id": doc.id}


@router.get("/credentials")
def list_credentials() -> list[dict]:
    """Credential names and types only — secret values are write-only."""
    return safe_listing()


@router.post("/credentials", status_code=201)
def create_credential(payload: CredentialCreate) -> dict:
    if not payload.name or not payload.name.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=422, detail="Credential name must be alphanumeric/-/_")
    known = {entry["type"] for entry in CONNECTOR_CATALOG}
    if payload.connector_type not in known:
        raise HTTPException(status_code=422, detail=f"Unknown connector type '{payload.connector_type}'")
    store = get_credential_store()
    store.save(
        ConnectorConfigEntry(
            name=payload.name,
            connector_type=payload.connector_type,
            config=payload.config,
        )
    )
    return {"name": payload.name, "connector_type": payload.connector_type}


@router.delete("/credentials/{name}", status_code=204)
def delete_credential(name: str) -> None:
    if not get_credential_store().delete(name):
        raise HTTPException(status_code=404, detail="Credential not found")


@router.get("/mcp/servers")
def list_mcp_servers() -> list[dict]:
    return safe_mcp_listing()


@router.post("/mcp/servers", status_code=201)
async def create_mcp_server(payload: MCPServerCreate) -> dict:
    if not payload.name or not payload.name.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(status_code=422, detail="Server name must be alphanumeric/-/_")
    if payload.transport not in {"mcp_stdio", "mcp_http"}:
        raise HTTPException(status_code=422, detail="transport must be 'mcp_stdio' or 'mcp_http'")
    if payload.transport == "mcp_stdio" and not payload.config.get("command"):
        raise HTTPException(status_code=422, detail="stdio servers need a 'command'")
    if payload.transport == "mcp_http" and not payload.config.get("url"):
        raise HTTPException(status_code=422, detail="http servers need a 'url'")
    get_mcp_store().save(
        ConnectorConfigEntry(
            name=payload.name, connector_type=payload.transport, config=payload.config
        )
    )
    agent_tools = await sync_agent_tools()
    return {"name": payload.name, "transport": payload.transport, "agent_tools": agent_tools}


@router.delete("/mcp/servers/{name}", status_code=204)
async def delete_mcp_server(name: str) -> None:
    if not get_mcp_store().delete(name):
        raise HTTPException(status_code=404, detail="MCP server not found")
    await sync_agent_tools()


@router.get("/mcp/servers/{name}/tools")
async def list_mcp_server_tools(name: str) -> list[dict]:
    """Live tool discovery from the MCP server (used by the node inspector)."""
    try:
        client = build_mcp_client(name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    from genxai.tools.mcp_client import MCPClientError

    try:
        return await client.list_tools()
    except MCPClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/runs")
def list_runs() -> list[dict]:
    store = get_execution_store()
    records = getattr(store, "_records", {})
    runs = [record.to_dict() for record in records.values()]
    runs.sort(key=lambda r: r.get("started_at") or "", reverse=True)
    return runs[:100]


@router.get("/runs/{run_id}")
def get_run(run_id: str) -> dict:
    record = get_execution_store().get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return record.to_dict()


@router.get("/runs/{run_id}/stream")
async def stream_existing_run(run_id: str) -> StreamingResponse:
    """(Re)attach to a run's live event stream; finished runs replay a terminal event."""
    if get_execution_store().get(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return _sse(run_id)


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str) -> dict:
    if get_execution_store().get(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    cancelled = await get_run_manager().cancel(run_id)
    if not cancelled:
        raise HTTPException(status_code=409, detail="Run is not active")
    return {"status": "cancelling", "run_id": run_id}


@router.post("/runs/{run_id}/rerun", status_code=201)
def rerun_run(run_id: str) -> dict:
    new_run_id = get_run_manager().rerun(run_id)
    if new_run_id is None:
        raise HTTPException(
            status_code=404, detail="Run not found or has no stored workflow snapshot"
        )
    return {"status": "accepted", "run_id": new_run_id, "source_run_id": run_id}


@router.delete("/runs/{run_id}", status_code=204)
def delete_run(run_id: str) -> None:
    store = get_execution_store()
    record = store.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if record.status in {"queued", "running"}:
        raise HTTPException(
            status_code=409, detail="Cancel the run before deleting it"
        )
    store.delete(run_id)
