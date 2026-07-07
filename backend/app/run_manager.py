"""Queue-backed workflow execution.

All runs — manual, webhook, scheduled — go through a single in-process
queue with worker tasks, which decouples execution from HTTP connections:

- Closing the browser mid-run no longer cancels the run; SSE clients are
  *subscribers* to a run's event stream, not its owner.
- Per-node results and node events are persisted into the ExecutionRecord
  (size-capped), so run details survive restarts and are inspectable later.
- Each run's workflow snapshot + input are stored in record metadata,
  enabling re-runs and restart recovery.
- Runs can be cancelled while queued or running.

On startup, runs left in "queued"/"running" by a previous process are
marked "interrupted" (they can be re-run explicitly; auto-resubmitting
could double-charge LLM calls, so we don't).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from app.runner import _apply_api_keys, get_execution_store, resolve_subgraphs, translate
from app.schemas import WorkflowDoc

from genxai.core.graph.executor import execute_workflow_async

logger = logging.getLogger(__name__)

MAX_PERSISTED_OUTPUT_CHARS = 20_000
MAX_PERSISTED_EVENTS = 500


class _Job:
    def __init__(self, run_id: str, doc: WorkflowDoc, input_data: dict[str, Any], model_override: str | None):
        self.run_id = run_id
        self.doc = doc
        self.input_data = input_data
        self.model_override = model_override
        self.cancelled = False
        self.cancel_event = asyncio.Event()
        self.buffer: list[dict[str, Any]] = []  # events so far (for late subscribers)
        self.done = False


def _cap(value: Any) -> Any:
    """Cap a node output for persistence; keeps small values verbatim."""
    try:
        encoded = json.dumps(value, default=str)
    except Exception:
        return {"repr": str(value)[:MAX_PERSISTED_OUTPUT_CHARS]}
    if len(encoded) <= MAX_PERSISTED_OUTPUT_CHARS:
        return value
    return {
        "truncated": True,
        "preview": encoded[:MAX_PERSISTED_OUTPUT_CHARS],
        "full_size_chars": len(encoded),
    }


class RunManager:
    """Owns the run queue, worker pool, subscribers, and run lifecycle."""

    def __init__(self, workers: int = 4) -> None:
        self.worker_count = workers
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._jobs: dict[str, _Job] = {}
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._workers: list[asyncio.Task] = []
        self._started = False

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        for index in range(self.worker_count):
            self._workers.append(asyncio.create_task(self._worker(), name=f"run-worker-{index}"))
        logger.info("RunManager started with %d workers", self.worker_count)

    async def shutdown(self) -> None:
        for task in list(self._running_tasks.values()) + self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, *self._running_tasks.values(), return_exceptions=True)
        self._workers.clear()
        self._running_tasks.clear()
        self._started = False

    def recover_stale_runs(self) -> int:
        """Mark runs left over from a previous process as interrupted."""
        store = get_execution_store()
        stale = 0
        for record in list(getattr(store, "_records", {}).values()):
            if record.status in {"queued", "running"}:
                store.update(
                    record.run_id,
                    status="interrupted",
                    error="Backend restarted while this run was in progress",
                    completed=True,
                )
                stale += 1
        if stale:
            logger.warning("Marked %d stale run(s) as interrupted", stale)
        return stale

    # ------------------------------------------------------------ submission

    def submit(
        self,
        doc: WorkflowDoc,
        input_data: dict[str, Any],
        trigger: str = "manual",
        model_override: str | None = None,
    ) -> str:
        """Create a run record, enqueue it, return its run_id."""
        store = get_execution_store()
        run_id = store.generate_run_id()
        store.create(
            run_id,
            workflow=doc.name,
            status="queued",
            metadata={
                "trigger": trigger,
                "input": input_data,
                "workflow_snapshot": doc.model_dump(mode="json"),
                "model_override": model_override,
            },
        )
        self._jobs[run_id] = _Job(run_id, doc, input_data, model_override)
        self._queue.put_nowait(run_id)
        return run_id

    def rerun(self, run_id: str) -> str | None:
        """Submit a fresh run from a past run's stored snapshot."""
        record = get_execution_store().get(run_id)
        if record is None:
            return None
        snapshot = (record.metadata or {}).get("workflow_snapshot")
        if not snapshot:
            return None
        doc = WorkflowDoc.model_validate(snapshot)
        input_data = (record.metadata or {}).get("input") or {}
        model_override = (record.metadata or {}).get("model_override")
        trigger = f"rerun:{run_id[:8]}"
        return self.submit(doc, input_data, trigger=trigger, model_override=model_override)

    # ---------------------------------------------------------- subscribers

    def subscribe(self, run_id: str) -> asyncio.Queue:
        """Attach to a run's event stream; buffered events are replayed."""
        queue: asyncio.Queue = asyncio.Queue()
        job = self._jobs.get(run_id)
        if job is not None:
            for event in job.buffer:
                queue.put_nowait(event)
            if not job.done:
                self._subscribers.setdefault(run_id, []).append(queue)
        else:
            # Run predates this process (or is unknown): synthesize from record
            record = get_execution_store().get(run_id)
            if record is None:
                queue.put_nowait({"event": "error", "data": {"error": "Unknown run", "run_id": run_id}})
            else:
                terminal = "complete" if record.status == "success" else "error"
                queue.put_nowait(
                    {"event": terminal, "data": {"status": record.status, "run_id": run_id, "result": record.result}}
                )
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        subscribers = self._subscribers.get(run_id)
        if subscribers and queue in subscribers:
            subscribers.remove(queue)

    def _publish(self, job: _Job, event: dict[str, Any]) -> None:
        job.buffer.append(event)
        if len(job.buffer) > MAX_PERSISTED_EVENTS:
            job.buffer.pop(0)
        for queue in self._subscribers.get(job.run_id, []):
            queue.put_nowait(event)

    # -------------------------------------------------------------- control

    async def cancel(self, run_id: str) -> bool:
        job = self._jobs.get(run_id)
        if job is None or job.done:
            return False
        job.cancelled = True
        # Signals the racing waiter in _execute; deterministic even if the
        # engine's internals are mid-startup and would swallow a raw
        # task.cancel() (e.g. inside anyio subprocess bootstrap).
        job.cancel_event.set()
        return True

    # -------------------------------------------------------------- workers

    async def _worker(self) -> None:
        while True:
            run_id = await self._queue.get()
            job = self._jobs.get(run_id)
            if job is None:
                continue
            if job.cancelled:
                self._finish(job, status="cancelled", error="Cancelled before start")
                continue
            task = asyncio.create_task(self._execute(job))
            self._running_tasks[run_id] = task
            try:
                await task
            except asyncio.CancelledError:
                self._finish(job, status="cancelled", error="Cancelled while running")
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("Run %s crashed", run_id)
                self._finish(job, status="error", error=str(exc))
            finally:
                self._running_tasks.pop(run_id, None)

    async def _execute(self, job: _Job) -> None:
        _apply_api_keys()
        store = get_execution_store()
        store.update(job.run_id, status="running")
        self._publish(job, {"event": "started", "data": {"run_id": job.run_id}})

        nodes, edges = translate(job.doc)
        node_events: list[dict[str, Any]] = []

        async def on_node_event(event: dict[str, Any]) -> None:
            node_events.append(event)
            self._publish(job, {"event": "node", "data": event})

        exec_task = asyncio.create_task(
            execute_workflow_async(
                nodes=nodes,
                edges=edges,
                input_data=job.input_data,
                model_override=job.model_override,
                event_callback=on_node_event,
                shared_memory=job.doc.shared_memory,
                subgraphs=resolve_subgraphs(job.doc) or None,
            )
        )
        cancel_waiter = asyncio.create_task(job.cancel_event.wait())
        try:
            done, _ = await asyncio.wait(
                {exec_task, cancel_waiter}, return_when=asyncio.FIRST_COMPLETED
            )
        except asyncio.CancelledError:
            # _execute itself was cancelled (e.g. shutdown): don't orphan the
            # engine task — cancel it and give it a short grace period.
            exec_task.cancel()
            await asyncio.wait({exec_task}, timeout=5)
            raise
        finally:
            cancel_waiter.cancel()

        if exec_task not in done:
            # Cancel requested: stop the engine (best effort, bounded — a
            # swallowed cancellation must not pin a worker for the node's
            # full duration) and record the cancellation.
            exec_task.cancel()
            await asyncio.wait({exec_task}, timeout=5)
            raise asyncio.CancelledError

        result = exec_task.result()

        status = result.get("status", "error")
        node_results = (result.get("result") or {}).get("node_results") or {}
        persisted_results = {
            node_id: {**entry, "output": _cap(entry.get("output"))}
            for node_id, entry in node_results.items()
        }
        store.update(
            job.run_id,
            status=status,
            error=result.get("error"),
            result={
                "nodes_executed": result.get("nodes_executed"),
                "node_results": persisted_results,
                "node_events": node_events[-MAX_PERSISTED_EVENTS:],
            },
            completed=True,
        )
        terminal = "complete" if status == "success" else "error"
        self._publish(job, {"event": terminal, "data": {**result, "run_id": job.run_id}})
        job.done = True
        self._subscribers.pop(job.run_id, None)

    def _finish(self, job: _Job, status: str, error: str | None) -> None:
        get_execution_store().update(job.run_id, status=status, error=error, completed=True)
        self._publish(job, {"event": "error", "data": {"run_id": job.run_id, "status": status, "error": error}})
        job.done = True
        self._subscribers.pop(job.run_id, None)


_manager: RunManager | None = None


def get_run_manager() -> RunManager:
    global _manager
    if _manager is None:
        _manager = RunManager()
    return _manager


def reset_run_manager() -> None:
    global _manager
    _manager = None
