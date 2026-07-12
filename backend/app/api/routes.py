"""REST + SSE routes for the Workflow Studio."""

import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, StreamingResponse

from app.automation import (
    ScheduleManager,
    apply_trigger_nodes,
    find_workflow_by_token,
    generate_webhook_token,
    verify_github_signature,
)
from app.config import get_settings
from app.connectors_catalog import CONNECTOR_CATALOG
from app.credentials import ConnectorConfigEntry, get_credential_store, safe_listing
from app.mcp_registry import (
    build_mcp_client,
    get_mcp_store,
    safe_mcp_listing,
    sync_agent_tools,
)
from app.palette import build_palette
from app.run_manager import get_run_manager
from app.runner import (
    cron_error,
    get_execution_store,
    test_single_node,
    timezone_error,
    validate,
)
from app.schemas import (
    AdhocRunRequest,
    AutomationConfig,
    CredentialCreate,
    DatasetAnalyzeRequest,
    GenerateRequest,
    HumanInputResponse,
    MaterializeRequest,
    MCPServerCreate,
    NodeTestRequest,
    OAuthAppConfig,
    OAuthStartRequest,
    SourceCreate,
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
async def create_workflow(doc: WorkflowDoc) -> WorkflowDoc:
    apply_trigger_nodes(doc)
    saved = get_store().create(doc)
    await _sync_schedule(saved)
    return saved


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
async def update_workflow(workflow_id: str, doc: WorkflowDoc) -> WorkflowDoc:
    apply_trigger_nodes(
        doc, existing=getattr(get_store().get(workflow_id), "automation", None)
    )
    updated = get_store().update(workflow_id, doc)
    if updated is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    await _sync_schedule(updated)
    return updated


async def _sync_schedule(doc: WorkflowDoc) -> None:
    """Keep the schedule manager in step with the doc's automation state.

    Best-effort: a save must not fail because the scheduler could not start
    (e.g. the optional apscheduler dependency is missing); the enabled flag
    is persisted either way and startup resume will retry.
    """
    if doc.id is None:
        return
    manager = get_schedule_manager()
    try:
        if doc.automation.schedule_enabled:
            await manager.enable(doc)
        else:
            await manager.disable(doc.id)
    except Exception as exc:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).warning(
            "Could not sync schedule for workflow %s: %s", doc.id, exc
        )


@router.get("/workflows/{workflow_id}/versions")
def list_workflow_versions(workflow_id: str) -> list[dict]:
    if get_store().get(workflow_id) is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    return [info.model_dump() for info in get_store().list_versions(workflow_id)]


@router.get("/workflows/{workflow_id}/versions/{version}", response_model=WorkflowDoc)
def get_workflow_version(workflow_id: str, version: str) -> WorkflowDoc:
    doc = get_store().get_version(workflow_id, version)
    if doc is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return doc


@router.post(
    "/workflows/{workflow_id}/versions/{version}/restore", response_model=WorkflowDoc
)
async def restore_workflow_version(workflow_id: str, version: str) -> WorkflowDoc:
    doc = get_store().restore_version(workflow_id, version)
    if doc is None:
        raise HTTPException(status_code=404, detail="Version not found")
    await _sync_schedule(doc)
    return doc


@router.delete("/workflows/{workflow_id}", status_code=204)
def delete_workflow(workflow_id: str) -> None:
    if not get_store().delete(workflow_id):
        raise HTTPException(status_code=404, detail="Workflow not found")


@router.post("/workflows/generate")
async def generate_workflow_endpoint(payload: GenerateRequest) -> dict:
    """Generate (or refine, when current_workflow is set) a draft WorkflowDoc."""
    from app.generation import generate_workflow_doc

    try:
        return await generate_workflow_doc(
            payload.prompt,
            model=payload.model,
            crew=payload.crew,
            workflow_name=payload.name,
            current_workflow=(
                payload.current_workflow.model_dump()
                if payload.current_workflow
                else None
            ),
        )
    except Exception as exc:  # pragma: no cover - provider/config failures
        raise HTTPException(
            status_code=422, detail=f"generation failed: {exc}"
        ) from exc


@router.post("/workflows/generate/{generation_id}/accept")
def accept_generation(generation_id: str) -> dict:
    """Mark a generated draft as accepted (the user kept/saved it).

    Accepted drafts are weighted up when the planner recalls past plans for
    similar future requests.
    """
    from app.generation import get_generation_memory

    if not get_generation_memory().mark_accepted(generation_id):
        raise HTTPException(status_code=404, detail="Unknown generation id")
    return {"accepted": True}


@router.post("/workflows/generate/stream")
async def generate_workflow_stream(payload: GenerateRequest) -> StreamingResponse:
    """Generate a draft workflow, streaming per-stage progress as SSE.

    Emits {"event": "progress", "stage": ..., ...} events while the crew
    works, then {"event": "complete", ...payload} or {"event": "error"}.
    """
    import asyncio

    from app.generation import generate_workflow_doc

    queue: asyncio.Queue = asyncio.Queue()

    def on_event(stage: str, data: dict) -> None:
        # Generation runs on this same event loop, so a direct put keeps
        # progress events ordered ahead of the final "complete" event.
        queue.put_nowait({"event": "progress", "stage": stage, **data})

    async def _generate() -> None:
        try:
            result = await generate_workflow_doc(
                payload.prompt,
                model=payload.model,
                crew=payload.crew,
                on_event=on_event,
                workflow_name=payload.name,
                current_workflow=(
                    payload.current_workflow.model_dump()
                    if payload.current_workflow
                    else None
                ),
            )
            await queue.put({"event": "complete", **result})
        except Exception as exc:  # pragma: no cover - surfaced to the stream
            await queue.put({"event": "error", "message": str(exc)})

    task = asyncio.create_task(_generate())

    async def _stream():
        try:
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event, default=str)}\n\n"
                if event.get("event") in {"complete", "error"}:
                    break
        finally:
            task.cancel()

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
async def run_workflow_stream(
    workflow_id: str, payload: RunRequest
) -> StreamingResponse:
    doc = get_store().get(workflow_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Workflow not found")
    result = validate(doc)
    if not result.valid:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Workflow is invalid",
                "issues": [i.model_dump() for i in result.issues],
            },
        )
    run_id = get_run_manager().submit(
        doc, payload.input, trigger="manual", model_override=payload.model_override
    )
    return _sse(run_id)


@router.post("/run/stream")
async def run_adhoc_stream(payload: AdhocRunRequest) -> StreamingResponse:
    """Run an unsaved workflow document directly (used by the canvas Run button)."""
    result = validate(payload.workflow)
    if not result.valid:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Workflow is invalid",
                "issues": [i.model_dump() for i in result.issues],
            },
        )
    run_id = get_run_manager().submit(
        payload.workflow,
        payload.input,
        trigger="manual",
        model_override=payload.model_override,
    )
    return _sse(run_id)


def _latest_run_context(workflow_name: str) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    """Upstream node outputs + input from this workflow's most recent run."""
    candidates = [
        record
        for record in getattr(get_execution_store(), "_records", {}).values()
        if record.workflow == workflow_name
        and (record.result or {}).get("node_results")
    ]
    if not candidates:
        return {}, {}, None
    latest = max(candidates, key=lambda r: r.started_at or "")
    upstream = {
        node_id: entry.get("output")
        for node_id, entry in latest.result["node_results"].items()
    }
    run_input = (latest.metadata or {}).get("input") or {}
    return upstream, run_input, latest.run_id


@router.post("/workflows/{workflow_id}/test-node")
async def test_node(workflow_id: str, payload: NodeTestRequest) -> dict:
    """Run a single node in isolation, seeding upstream data from the last run."""
    doc = get_store().get(workflow_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Workflow not found")

    upstream = payload.upstream
    input_data = payload.input
    source_run_id = None
    if upstream is None:
        upstream, last_input, source_run_id = _latest_run_context(doc.name)
        if not input_data:
            # Deliberately pinned sample data beats incidental last-run input
            input_data = doc.pinned_input or last_input

    try:
        result = await test_single_node(doc, payload.node_id, input_data, upstream)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    node_results = (result.get("result") or {}).get("node_results") or {}
    return {
        "status": result.get("status"),
        "node_id": payload.node_id,
        "output": (node_results.get(payload.node_id) or {}).get("output"),
        "error": result.get("error"),
        "upstream_from_run": source_run_id,
    }


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

    cron = (config.schedule_cron or "").strip() or None
    config.schedule_cron = cron
    config.schedule_timezone = (config.schedule_timezone or "").strip() or "UTC"
    if config.schedule_enabled:
        if cron:
            problem = cron_error(cron)
            if problem:
                raise HTTPException(
                    status_code=422, detail=f"Invalid cron expression: {problem}"
                )
        problem = timezone_error(config.schedule_timezone)
        if problem:
            raise HTTPException(status_code=422, detail=problem)

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
            if not verify_github_signature(
                automation.webhook_secret, raw_body, signature
            ):
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


# ----------------------------------------------------------------- datasets


@router.get("/datasets")
def list_datasets() -> list[dict]:
    from genxai.core.datasets import get_dataset_store

    return get_dataset_store().list_datasets()


@router.get("/datasets/{name}/rows")
def get_dataset_rows(name: str, limit: int = 50, offset: int = 0) -> dict:
    from genxai.core.datasets import get_dataset_store

    try:
        return get_dataset_store().rows(
            name, limit=max(1, min(limit, 500)), offset=max(0, offset)
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/datasets/{name}/aggregate")
def aggregate_dataset(
    name: str,
    metric: str = "count",
    field: str | None = None,
    group_by: str | None = None,
) -> list[dict]:
    from genxai.core.datasets import get_dataset_store

    try:
        return get_dataset_store().aggregate(
            name, metric=metric, field=field, group_by=group_by
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/datasets/{name}", status_code=204)
def delete_dataset(name: str) -> None:
    from genxai.core.datasets import get_dataset_store

    try:
        deleted = get_dataset_store().delete_dataset(name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Dataset not found")


async def _analyze_rows(
    name: str, sample: dict, question: str | None, model_override: str | None
) -> dict:
    """LLM analysis of a rows sample: patterns, anomalies, suggestions."""
    from app.generation import DEFAULT_GENERATION_MODEL, _resolve_model_and_key
    from genxai.llm.factory import LLMProviderFactory

    if sample["total"] == 0 or not sample["rows"]:
        raise HTTPException(status_code=404, detail="Source is empty or missing")

    model, api_key = _resolve_model_and_key(model_override or DEFAULT_GENERATION_MODEL)
    if api_key is None:
        raise HTTPException(
            status_code=409,
            detail="No LLM API key configured — set OPENAI_API_KEY or "
            "ANTHROPIC_API_KEY in the backend .env",
        )

    columns = sorted(
        {key for row in sample["rows"] for key in row if not key.startswith("_")}
    )
    effective_question = question or (
        "What patterns, outliers, and actionable insights do you see?"
    )
    prompt = (
        f"Data source '{name}': {sample['total']} rows total; columns: "
        f"{', '.join(columns) or '(none)'}.\n"
        f"Sample of {len(sample['rows'])} rows as JSON:\n{json.dumps(sample['rows'], default=str)[:12000]}\n\n"
        f"Question: {effective_question}\n"
        "Answer with 3-6 concise bullet points grounded ONLY in this data. "
        "Note explicitly that this is a sample if that limits any conclusion."
    )
    provider = LLMProviderFactory.create_provider(model=model, api_key=api_key)
    response = await provider.generate(
        prompt, system_prompt="You are a careful data analyst."
    )
    return {
        "dataset": name,
        "model": model,
        "sampled_rows": len(sample["rows"]),
        "total_rows": sample["total"],
        "insight": response.content,
    }


@router.post("/datasets/{name}/analyze")
async def analyze_dataset(name: str, payload: DatasetAnalyzeRequest) -> dict:
    from genxai.core.datasets import get_dataset_store

    sample = get_dataset_store().rows(name, limit=50)
    return await _analyze_rows(name, sample, payload.question, payload.model)


# ---------------------------------------------------------- analytics sources


def _resolve_source_or_404(source_id: str) -> tuple[dict, "Any"]:
    from app.analytics_sources import get_adapter, resolve_source

    source = resolve_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    try:
        return source, get_adapter(source)
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/analytics/sources")
def list_analytics_sources() -> list[dict]:
    from app.analytics_sources import list_all_sources

    return list_all_sources()


@router.post("/analytics/sources", status_code=201)
def create_analytics_source(payload: SourceCreate) -> dict:
    from app.analytics_sources import FileAdapter, SQLAdapter, get_source_registry

    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name required")

    if payload.kind == "sql":
        credential = str(payload.config.get("credential") or "")
        table = str(payload.config.get("table") or "") or None
        sql = str(payload.config.get("sql") or "") or None
        if not credential or bool(table) == bool(sql):
            raise HTTPException(
                status_code=422,
                detail="config.credential plus exactly one of config.table / config.sql required",
            )
        # Fail fast: adapter construction + a schema probe validate everything
        try:
            adapter = SQLAdapter(credential, table=table, sql=sql)
            schema = adapter.schema()
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=422, detail=f"Cannot read source: {exc}"
            ) from exc
        if not schema:
            raise HTTPException(status_code=422, detail="Source has no columns")
        config: dict = {"credential": credential}
        if table:
            config["table"] = table
        else:
            config["sql"] = adapter.sql
        return get_source_registry().create(name, "sql", config)

    if payload.kind == "file":
        file_id = str(payload.config.get("file_id") or "")
        format_ = str(payload.config.get("format") or "")
        sheet = payload.config.get("sheet") or None
        if not file_id or format_ not in ("xlsx", "csv"):
            raise HTTPException(
                status_code=422,
                detail="config.file_id and config.format (xlsx|csv) required",
            )
        try:
            adapter = FileAdapter(file_id, format_, sheet)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=422, detail=f"Cannot parse file: {exc}"
            ) from exc
        if adapter.rows(1, 0)["total"] == 0:
            raise HTTPException(status_code=422, detail="File contains no rows")
        config = {"file_id": file_id, "format": format_}
        if sheet:
            config["sheet"] = str(sheet)
        if payload.config.get("file_name"):
            config["file_name"] = str(payload.config["file_name"])
        return get_source_registry().create(name, "file", config)

    if payload.kind == "gsheet":
        from app.analytics_sources import GSheetAdapter

        credential = str(payload.config.get("credential") or "")
        spreadsheet_id = str(payload.config.get("spreadsheet_id") or "")
        range_ = str(payload.config.get("range") or "")
        if not credential or not spreadsheet_id or not range_:
            raise HTTPException(
                status_code=422,
                detail="config.credential, config.spreadsheet_id and config.range required",
            )
        try:
            GSheetAdapter(credential, spreadsheet_id, range_)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=422, detail=f"Cannot read sheet: {exc}"
            ) from exc
        return get_source_registry().create(
            name,
            "gsheet",
            {
                "credential": credential,
                "spreadsheet_id": spreadsheet_id,
                "range": range_,
            },
        )

    if payload.kind == "duckdb":
        from app.analytics_sources import FederatedAdapter

        sql = str(payload.config.get("sql") or "")
        sources = payload.config.get("sources") or {}
        if not sql or not isinstance(sources, dict):
            raise HTTPException(
                status_code=422,
                detail="config.sql and config.sources (alias -> source id) required",
            )
        try:
            FederatedAdapter(sql, {str(k): str(v) for k, v in sources.items()})
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=422, detail=f"Cannot run federated query: {exc}"
            ) from exc
        return get_source_registry().create(
            name, "duckdb", {"sql": sql, "sources": sources}
        )

    if payload.kind == "s3":
        from app.analytics_sources import s3_file_adapter

        credential = str(payload.config.get("credential") or "")
        bucket = str(payload.config.get("bucket") or "")
        key = str(payload.config.get("key") or "")
        format_ = str(payload.config.get("format") or "")
        if not format_:
            format_ = "xlsx" if key.lower().endswith(".xlsx") else "csv"
        sheet = payload.config.get("sheet") or None
        if not credential or not bucket or not key or format_ not in ("xlsx", "csv"):
            raise HTTPException(
                status_code=422,
                detail="config.credential, config.bucket, config.key and a csv/xlsx key required",
            )
        try:
            adapter = s3_file_adapter(credential, bucket, key, format_, sheet)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                status_code=422, detail=f"Cannot read s3://{bucket}/{key}: {exc}"
            ) from exc
        if adapter.rows(1, 0)["total"] == 0:
            raise HTTPException(status_code=422, detail="Object contains no rows")
        config = {
            "credential": credential,
            "bucket": bucket,
            "key": key,
            "format": format_,
        }
        if sheet:
            config["sheet"] = str(sheet)
        return get_source_registry().create(name, "s3", config)

    raise HTTPException(
        status_code=422,
        detail="kind must be 'sql', 'file', 'gsheet', 'duckdb', or 's3'",
    )


@router.delete("/analytics/sources/{source_id}", status_code=204)
def delete_analytics_source(source_id: str) -> None:
    from app.analytics_sources import get_source_registry

    if source_id.startswith("dataset:"):
        raise HTTPException(
            status_code=422,
            detail="Datasets are managed on /datasets — delete the dataset itself",
        )
    if not get_source_registry().delete(source_id):
        raise HTTPException(status_code=404, detail="Source not found")


@router.get("/analytics/sources/{source_id}/schema")
def get_source_schema(source_id: str) -> list[dict]:
    _, adapter = _resolve_source_or_404(source_id)
    try:
        return adapter.schema()
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/analytics/sources/{source_id}/rows")
def get_source_rows(source_id: str, limit: int = 50, offset: int = 0) -> dict:
    _, adapter = _resolve_source_or_404(source_id)
    try:
        return adapter.rows(limit=max(1, min(limit, 500)), offset=max(0, offset))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/analytics/sources/{source_id}/aggregate")
def aggregate_source(
    source_id: str,
    metric: str = "count",
    field: str | None = None,
    group_by: str | None = None,
) -> list[dict]:
    _, adapter = _resolve_source_or_404(source_id)
    try:
        return adapter.aggregate(metric=metric, field=field, group_by=group_by)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/analytics/sources/{source_id}/analyze")
async def analyze_source(source_id: str, payload: DatasetAnalyzeRequest) -> dict:
    source, adapter = _resolve_source_or_404(source_id)
    try:
        sample = adapter.rows(limit=50, offset=0)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _analyze_rows(source["name"], sample, payload.question, payload.model)


@router.post("/analytics/sources/{source_id}/materialize", status_code=201)
async def materialize_source(source_id: str, payload: MaterializeRequest) -> dict:
    """Generate a scheduled workflow that syncs a SQL source into a dataset."""
    from app.analytics_sources import resolve_source

    source = resolve_source(source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found")
    if source["kind"] != "sql":
        raise HTTPException(
            status_code=422,
            detail="Only database sources can be scheduled into datasets",
        )
    if payload.mode not in ("replace", "append"):
        raise HTTPException(status_code=422, detail="mode must be replace or append")
    dataset = payload.dataset.strip()
    from genxai.core.datasets import _validate_name

    try:
        _validate_name(dataset)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if payload.cron:
        problem = cron_error(payload.cron.strip())
        if problem:
            raise HTTPException(
                status_code=422, detail=f"Invalid cron expression: {problem}"
            )

    config = source["config"]
    sql = config.get("sql") or f"SELECT * FROM {config['table']}"  # noqa: S608 — table validated at registration
    trigger_config: dict = {"trigger_kind": "schedule"}
    if payload.cron:
        trigger_config["cron"] = payload.cron.strip()
    else:
        trigger_config["interval_seconds"] = max(60, payload.interval_seconds)

    doc = WorkflowDoc(
        name=f"Sync {source['name']} → {dataset}",
        description=(
            f"Generated by Analytics: materializes source '{source['name']}' "
            f"into dataset '{dataset}' ({payload.mode})"
        ),
        nodes=[
            {
                "id": "trigger",
                "type": "trigger",
                "label": "Schedule",
                "position": {"x": 40, "y": 160},
                "config": trigger_config,
            },
            {
                "id": "extract",
                "type": "connector",
                "label": f"Query {source['name']}",
                "position": {"x": 280, "y": 160},
                "config": {
                    "connector": "postgres",
                    "action": "query",
                    "credential": config["credential"],
                    "params": {"sql": sql, "max_rows": 1000},
                },
            },
            {
                "id": "collect",
                "type": "tool",
                "label": f"{payload.mode.title()} dataset",
                "position": {"x": 520, "y": 160},
                "config": {
                    "tool_name": "dataset_write",
                    "tool_params": {
                        "dataset": dataset,
                        "rows": "{{ extract.data.rows }}",
                        "mode": payload.mode,
                    },
                },
            },
            {
                "id": "end",
                "type": "output",
                "label": "Output",
                "position": {"x": 760, "y": 160},
                "config": {},
            },
        ],
        edges=[
            {"source": "extract", "target": "collect"},
            {"source": "collect", "target": "end"},
        ],
    )
    apply_trigger_nodes(doc)
    saved = get_store().create(doc)
    await _sync_schedule(saved)
    return {
        "workflow_id": saved.id,
        "workflow_name": saved.name,
        "dataset": dataset,
    }


@router.get("/analytics/credentials/{credential}/tables")
def get_credential_tables(credential: str) -> dict:
    from app.analytics_sources import list_credential_tables

    try:
        return {"tables": list_credential_tables(credential)}
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/insights")
def get_insights(days: int = 14) -> dict:
    """Aggregated run analytics for the Insights dashboard."""
    from app.insights import compute_insights

    return compute_insights(days=max(1, min(days, 90)))


@router.post("/files/upload", status_code=201)
async def upload_file(file: "UploadFile") -> dict:
    """Browser upload into the file store; xlsx uploads also report sheets."""
    from genxai.core.files import get_file_store

    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="Empty file")
    if len(data) > 50 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="File larger than 50 MB")
    name = file.filename or "upload"
    ref = get_file_store().save_bytes(
        data, name=name, media_type=file.content_type or None
    )

    sheets: list[str] | None = None
    if name.lower().endswith(".xlsx"):
        from app.analytics_sources import list_workbook_sheets

        try:
            sheets = list_workbook_sheets(ref["id"])
        except Exception as exc:
            raise HTTPException(
                status_code=422, detail=f"Not a readable .xlsx workbook: {exc}"
            ) from exc
    return {"file": ref, "sheets": sheets}


@router.get("/files/{file_id}")
def download_file(file_id: str) -> "FileResponse":
    """Stream a stored workflow file (from file_download / file_write refs)."""
    from fastapi.responses import FileResponse

    from genxai.core.files import get_file_store

    store = get_file_store()
    try:
        path = store.open_path(file_id)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="File not found") from exc
    metadata = store.get_metadata(file_id) or {}
    return FileResponse(
        path,
        media_type=metadata.get("media_type", "application/octet-stream"),
        filename=metadata.get("name", file_id),
    )


# ------------------------------------------------------------------- oauth


def _oauth_redirect_uri() -> str:
    settings = get_settings()
    return f"{settings.public_base_url.rstrip('/')}{settings.api_prefix}/oauth/callback"


@router.get("/oauth/providers")
def list_oauth_providers() -> dict:
    """Available OAuth providers and whether an app is registered for each."""
    from app.oauth_flow import get_oauth_app
    from app.oauth_providers import OAUTH_PROVIDERS

    return {
        "redirect_uri": _oauth_redirect_uri(),
        "providers": [
            {
                "provider": key,
                "label": definition.label,
                "connector_type": definition.connector_type,
                "scopes": definition.scopes,
                "scope_presets": definition.scope_presets,
                "app_configured": bool((get_oauth_app(key) or {}).get("client_id")),
            }
            for key, definition in OAUTH_PROVIDERS.items()
        ],
    }


@router.put("/oauth/apps/{provider}", status_code=204)
def register_oauth_app(provider: str, payload: OAuthAppConfig) -> None:
    """Store the deployment's OAuth app (client id/secret) for a provider."""
    from app.oauth_flow import save_oauth_app
    from app.oauth_providers import OAUTH_PROVIDERS

    if provider not in OAUTH_PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider '{provider}'")
    if not payload.client_id or not payload.client_secret:
        raise HTTPException(status_code=422, detail="client_id and client_secret required")
    save_oauth_app(provider, payload.client_id, payload.client_secret)


@router.delete("/oauth/apps/{provider}", status_code=204)
def remove_oauth_app(provider: str) -> None:
    from app.oauth_flow import delete_oauth_app

    if not delete_oauth_app(provider):
        raise HTTPException(status_code=404, detail="No app registered")


@router.post("/oauth/{provider}/start")
def start_oauth(provider: str, payload: OAuthStartRequest) -> dict:
    """Begin the consent flow; the client opens authorize_url in a popup."""
    from app.oauth_flow import begin_consent
    from app.oauth_providers import OAUTH_PROVIDERS

    if provider not in OAUTH_PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider '{provider}'")
    if not payload.credential_name.strip():
        raise HTTPException(status_code=422, detail="credential_name required")
    try:
        authorize_url = begin_consent(
            provider,
            payload.credential_name.strip(),
            _oauth_redirect_uri(),
            scopes=payload.scopes,
        )
    except LookupError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"authorize_url": authorize_url}


@router.get("/oauth/callback")
async def oauth_callback(
    state: str = "", code: str = "", error: str = ""
) -> HTMLResponse:
    """Provider redirect target: finishes the exchange, then closes the popup."""
    from app.oauth_flow import complete_consent

    def _page(message: str, ok: bool) -> HTMLResponse:
        body = (
            f"<html><body style='font-family: sans-serif; padding: 2rem'>"
            f"<h3>{'✓' if ok else '✗'} {message}</h3>"
            f"<p>You can close this window.</p>"
            f"<script>setTimeout(() => window.close(), 1500)</script>"
            f"</body></html>"
        )
        return HTMLResponse(body, status_code=200 if ok else 400)

    if error:
        return _page(f"Authorization was denied: {error}", ok=False)
    if not state or not code:
        return _page("Missing state or code in callback", ok=False)
    try:
        name = await complete_consent(state, code, _oauth_redirect_uri())
    except LookupError as exc:
        return _page(str(exc), ok=False)
    except Exception as exc:
        return _page(f"Token exchange failed: {exc}", ok=False)
    return _page(f"Account connected — credential '{name}' saved", ok=True)


@router.get("/credentials")
def list_credentials() -> list[dict]:
    """Credential names and types only — secret values are write-only."""
    return safe_listing()


@router.post("/credentials", status_code=201)
def create_credential(payload: CredentialCreate) -> dict:
    if not payload.name or not payload.name.replace("-", "").replace("_", "").isalnum():
        raise HTTPException(
            status_code=422, detail="Credential name must be alphanumeric/-/_"
        )
    known = {entry["type"] for entry in CONNECTOR_CATALOG}
    if payload.connector_type not in known:
        raise HTTPException(
            status_code=422, detail=f"Unknown connector type '{payload.connector_type}'"
        )
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
        raise HTTPException(
            status_code=422, detail="Server name must be alphanumeric/-/_"
        )
    if payload.transport not in {"mcp_stdio", "mcp_http"}:
        raise HTTPException(
            status_code=422, detail="transport must be 'mcp_stdio' or 'mcp_http'"
        )
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
    return {
        "name": payload.name,
        "transport": payload.transport,
        "agent_tools": agent_tools,
    }


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


@router.get("/runs/{run_id}/pending-input")
def get_pending_input(run_id: str) -> dict:
    """Human nodes of this run currently waiting for a person's response."""
    if get_execution_store().get(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {"pending": get_run_manager().list_pending_input(run_id)}


@router.post("/runs/{run_id}/input")
def submit_human_input(run_id: str, payload: HumanInputResponse) -> dict:
    """Answer a waiting human node so the run can continue."""
    if get_execution_store().get(run_id) is None:
        raise HTTPException(status_code=404, detail="Run not found")
    delivered = get_run_manager().respond(run_id, payload.node_id, payload.response)
    if not delivered:
        raise HTTPException(
            status_code=409,
            detail=f"Node '{payload.node_id}' is not waiting for input on this run",
        )
    return {"status": "delivered", "run_id": run_id, "node_id": payload.node_id}


@router.post("/runs/{run_id}/retry", status_code=201)
def retry_run_from_failure(run_id: str) -> dict:
    """Re-run a failed run, resuming at the failed node (successes replay)."""
    record = get_execution_store().get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if record.status in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Run is still active")
    new_run_id = get_run_manager().retry_from_failure(run_id)
    if new_run_id is None:
        raise HTTPException(
            status_code=404, detail="Run has no stored workflow snapshot"
        )
    return {"status": "accepted", "run_id": new_run_id, "source_run_id": run_id}


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
        raise HTTPException(status_code=409, detail="Cancel the run before deleting it")
    store.delete(run_id)
