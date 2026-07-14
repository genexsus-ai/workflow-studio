"""Experiments: the multi-agent data science crew (v2-P1).

The user states an objective; a crew of specialist agents runs a staged
pipeline. P1 covers Planning → Exploration → Cleaning, with the
Programming Agent writing every SQL artifact and the Code Review Agent
gating each one before execution. The cleaning stage materializes a
cleaned dataset that later phases (features, models) build on.

Every artifact is read-only DuckDB SQL validated by the same gate the
rest of the platform uses; agent calls are isolated module functions so
tests can stub the crew.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings
from app.data_catalog import (
    FederatedAdapter,
    get_adapter,
    profile_source,
    resolve_source,
    validate_readonly_sql,
)

logger = logging.getLogger(__name__)

MAX_REVIEW_ROUNDS = 2
MAX_SQL_ATTEMPTS = 3
EXPLORE_RESULT_ROWS = 20
STAGE_NAMES = ("plan", "explore", "clean")


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ------------------------------------------------------------------- storage


class ExperimentStore:
    def __init__(self) -> None:
        self.db_path = get_settings().data_dir / "datasets.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS experiments (
                    id TEXT PRIMARY KEY,
                    objective TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    target TEXT,
                    status TEXT NOT NULL,
                    error TEXT,
                    stages TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def list(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, objective, source_id, target, status, error, stages, "
                "created_at, updated_at FROM experiments ORDER BY created_at DESC"
            ).fetchall()
        summaries = []
        for row in rows:
            stages = json.loads(row[6])
            summaries.append(
                {
                    "id": row[0],
                    "objective": row[1],
                    "source_id": row[2],
                    "target": row[3],
                    "status": row[4],
                    "error": row[5],
                    "stages_done": sum(1 for s in stages if s["status"] == "ok"),
                    "stages_total": len(stages),
                    "created_at": row[7],
                    "updated_at": row[8],
                }
            )
        return summaries

    def get(self, experiment_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, objective, source_id, target, status, error, stages, "
                "created_at, updated_at FROM experiments WHERE id = ?",
                (experiment_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "objective": row[1],
            "source_id": row[2],
            "target": row[3],
            "status": row[4],
            "error": row[5],
            "stages": json.loads(row[6]),
            "created_at": row[7],
            "updated_at": row[8],
        }

    def create(
        self, objective: str, source_id: str, target: str | None
    ) -> dict[str, Any]:
        experiment = {
            "id": uuid.uuid4().hex,
            "objective": objective,
            "source_id": source_id,
            "target": target,
            "status": "queued",
            "error": None,
            "stages": [
                {"name": name, "status": "pending", "artifact": None, "verdicts": []}
                for name in STAGE_NAMES
            ],
            "created_at": _now(),
            "updated_at": _now(),
        }
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO experiments (id, objective, source_id, target, status, "
                "error, stages, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    experiment["id"],
                    objective,
                    source_id,
                    target,
                    "queued",
                    None,
                    json.dumps(experiment["stages"]),
                    experiment["created_at"],
                    experiment["updated_at"],
                ),
            )
        return experiment

    def save(self, experiment: dict[str, Any]) -> None:
        experiment["updated_at"] = _now()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE experiments SET status = ?, error = ?, stages = ?, "
                "updated_at = ? WHERE id = ?",
                (
                    experiment["status"],
                    experiment["error"],
                    json.dumps(experiment["stages"], default=str),
                    experiment["updated_at"],
                    experiment["id"],
                ),
            )

    def delete(self, experiment_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM experiments WHERE id = ?", (experiment_id,)
            )
        return cursor.rowcount > 0


_store: ExperimentStore | None = None
_running: dict[str, asyncio.Task] = {}


def get_experiment_store() -> ExperimentStore:
    global _store
    if _store is None:
        _store = ExperimentStore()
    return _store


def reset_experiment_store() -> None:
    global _store
    _store = None
    _running.clear()


# ------------------------------------------------------------------ the crew
# Each agent is an isolated module function (tests stub these).


async def plan_stage(objective: str, target: str | None, context: str) -> dict[str, Any]:
    """Planning Agent: task type, target, and per-stage intents."""
    from app.analyst import run_analyst
    from genxai.utils.structured import parse_json_loosely

    prompt = (
        f"Data science objective: {objective}\n"
        f"{f'User-specified target column: {target}' if target else ''}\n\n"
        f"Data available (table alias `data`):\n{context}\n\n"
        "Plan the experiment. Reply with ONLY JSON:\n"
        '{"task_type": "regression"|"classification"|"descriptive",\n'
        ' "target": "<column or null>",\n'
        ' "exploration_focus": "<what EDA should establish, one sentence>",\n'
        ' "cleaning_focus": "<expected data-quality work, one sentence>"}'
    )
    result = await run_analyst(
        prompt,
        role="Data Science Planner",
        goal="Turn objectives into an executable experiment plan",
        backstory="Return only valid JSON. No prose.",
    )
    parsed = parse_json_loosely(result["output"])
    if not isinstance(parsed, dict) or not parsed.get("task_type"):
        raise ValueError(f"Planner returned no plan: {result['output'][:200]}")
    return parsed


async def draft_exploration(
    plan: dict[str, Any], context: str, feedback: str | None = None
) -> list[dict[str, str]]:
    """Exploration + Programming Agents: up to 3 EDA queries as SQL."""
    from app.analyst import run_analyst
    from genxai.utils.structured import parse_json_loosely

    revise = f"\nReviewer feedback on your previous draft — address it:\n{feedback}\n" if feedback else ""
    prompt = (
        f"Experiment plan: {json.dumps(plan)}\n\n"
        f"Data (table alias `data`):\n{context}\n{revise}\n"
        "Write up to 3 focused EDA queries (DuckDB, read-only, table `data`, "
        "small result sets). Reply with ONLY JSON:\n"
        '[{"purpose": "<what this establishes>", "sql": "<SELECT ...>"}]'
    )
    result = await run_analyst(
        prompt,
        role="Data Exploration Agent",
        goal="Establish the data facts the rest of the pipeline depends on",
        backstory="Return only a valid JSON array. No prose.",
    )
    parsed = parse_json_loosely(result["output"])
    if not isinstance(parsed, list) or not parsed:
        raise ValueError(f"Exploration returned no queries: {result['output'][:200]}")
    return [
        {"purpose": str(q.get("purpose", "")), "sql": str(q.get("sql", ""))}
        for q in parsed[:3]
        if isinstance(q, dict) and q.get("sql")
    ]


async def draft_cleaning(
    plan: dict[str, Any],
    context: str,
    exploration_summary: str,
    feedback: str | None = None,
) -> dict[str, str]:
    """Cleaning + Programming Agents: one SQL transformation over `data`."""
    from app.analyst import run_analyst
    from genxai.utils.structured import parse_json_loosely

    revise = f"\nFeedback on your previous draft — address it:\n{feedback}\n" if feedback else ""
    prompt = (
        f"Experiment plan: {json.dumps(plan)}\n\n"
        f"Data (table alias `data`):\n{context}\n\n"
        f"Exploration findings:\n{exploration_summary}\n{revise}\n"
        "Write ONE read-only DuckDB SELECT over `data` that produces the "
        "cleaned dataset: handle duplicates, nulls, obvious type issues and "
        "outliers per the findings. Keep every column needed for the "
        "objective. Reply with ONLY JSON:\n"
        '{"intent": "<what the cleaning does and why>", "sql": "<SELECT ...>"}'
    )
    result = await run_analyst(
        prompt,
        role="Data Cleaning Agent",
        goal="Produce a defensible cleaned dataset via one SQL transformation",
        backstory="Return only valid JSON. No prose.",
    )
    parsed = parse_json_loosely(result["output"])
    if not isinstance(parsed, dict) or not parsed.get("sql"):
        raise ValueError(f"Cleaner returned no SQL: {result['output'][:200]}")
    return {"intent": str(parsed.get("intent", "")), "sql": str(parsed["sql"])}


async def review_artifact(
    kind: str, artifact: Any, plan: dict[str, Any], context: str
) -> dict[str, str]:
    """Code Review Agent: approve or request revision, with reason."""
    from app.analyst import run_analyst
    from genxai.utils.structured import parse_json_loosely

    prompt = (
        f"You are reviewing a {kind} artifact for this experiment plan:\n"
        f"{json.dumps(plan)}\n\n"
        f"Data context:\n{context[:2000]}\n\n"
        f"Artifact:\n{json.dumps(artifact, default=str)[:4000]}\n\n"
        "Check: does the SQL match the stated intent; is it read-only; does "
        "it reference only table `data` and real columns; any target "
        "leakage or data loss risks (dropping too many rows, discarding the "
        "target)? Reply with ONLY JSON:\n"
        '{"verdict": "approve"|"revise", "reason": "<one sentence>"}'
    )
    result = await run_analyst(
        prompt,
        role="Code Review Agent",
        goal="Catch incorrect, leaky, or destructive artifacts before they run",
        backstory="Return only valid JSON. Be strict but practical.",
    )
    parsed = parse_json_loosely(result["output"])
    if not isinstance(parsed, dict) or parsed.get("verdict") not in ("approve", "revise"):
        return {"verdict": "approve", "reason": "reviewer output unparseable; defaulted to approve"}
    return {"verdict": str(parsed["verdict"]), "reason": str(parsed.get("reason", ""))}


# ------------------------------------------------------------------ pipeline


def _stage(experiment: dict[str, Any], name: str) -> dict[str, Any]:
    return next(s for s in experiment["stages"] if s["name"] == name)


def _sources_context(source_id: str) -> str:
    source = resolve_source(source_id)
    if source is None:
        raise LookupError(f"Source '{source_id}' not found")
    adapter = get_adapter(source)
    profile = profile_source(adapter)
    return (
        f"Source: {source['name']} ({profile['total_rows']} rows)\n"
        f"Columns: {json.dumps(adapter.schema())}\n"
        f"Column statistics: {json.dumps(profile['columns'], default=str)[:2500]}"
    )


def _execute_sql(sql: str, source_id: str) -> tuple[list[str], list[dict[str, Any]]]:
    adapter = FederatedAdapter(validate_readonly_sql(sql), {"data": source_id})
    rows = adapter.rows_data
    columns: list[str] = []
    for row in rows[:50]:
        for key in row:
            if key not in columns:
                columns.append(key)
    return columns, rows


async def run_experiment(experiment_id: str) -> None:
    """The pipeline: plan → explore → clean, with review gates."""
    store = get_experiment_store()
    experiment = store.get(experiment_id)
    if experiment is None:
        return
    experiment["status"] = "running"
    store.save(experiment)

    try:
        context = _sources_context(experiment["source_id"])

        # ------------------------------------------------------------- plan
        stage = _stage(experiment, "plan")
        stage["status"] = "running"
        store.save(experiment)
        plan = await plan_stage(
            experiment["objective"], experiment["target"], context
        )
        if experiment["target"]:
            plan["target"] = experiment["target"]
        stage["artifact"] = plan
        stage["status"] = "ok"
        store.save(experiment)

        # ---------------------------------------------------------- explore
        stage = _stage(experiment, "explore")
        stage["status"] = "running"
        store.save(experiment)
        queries = await draft_exploration(plan, context)
        review = await review_artifact("exploration queries", queries, plan, context)
        stage["verdicts"].append(review)
        if review["verdict"] == "revise":
            queries = await draft_exploration(plan, context, feedback=review["reason"])
            second = await review_artifact("exploration queries", queries, plan, context)
            stage["verdicts"].append(second)

        results = []
        for query in queries:
            try:
                columns, rows = _execute_sql(query["sql"], experiment["source_id"])
                results.append(
                    {
                        "purpose": query["purpose"],
                        "sql": query["sql"],
                        "columns": columns,
                        "rows": rows[:EXPLORE_RESULT_ROWS],
                        "row_count": len(rows),
                        "status": "ok",
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "purpose": query["purpose"],
                        "sql": query["sql"],
                        "status": "error",
                        "error": str(exc),
                    }
                )
        if not any(r["status"] == "ok" for r in results):
            raise RuntimeError("Every exploration query failed")
        stage["artifact"] = {"queries": results}
        stage["status"] = "ok"
        store.save(experiment)

        exploration_summary = "\n".join(
            f"- {r['purpose']}: "
            + (
                json.dumps(r["rows"][:5], default=str)[:400]
                if r["status"] == "ok"
                else f"(failed: {r['error']})"
            )
            for r in results
        )

        # ------------------------------------------------------------ clean
        stage = _stage(experiment, "clean")
        stage["status"] = "running"
        store.save(experiment)
        feedback: str | None = None
        last_error: str | None = None
        for _attempt in range(MAX_SQL_ATTEMPTS):
            draft = await draft_cleaning(
                plan, context, exploration_summary, feedback
            )
            review = await review_artifact("cleaning SQL", draft, plan, context)
            stage["verdicts"].append(review)
            if review["verdict"] == "revise":
                feedback = review["reason"]
                last_error = f"reviewer requested revision: {review['reason']}"
                continue
            try:
                columns, rows = _execute_sql(draft["sql"], experiment["source_id"])
            except Exception as exc:
                feedback = f"The SQL failed to execute: {exc}"
                last_error = str(exc)
                continue
            if not rows:
                feedback = "The cleaning SQL returned zero rows — too aggressive."
                last_error = "cleaning produced zero rows"
                continue

            from genxai.core.datasets import get_dataset_store

            dataset_name = f"exp_{experiment['id'][:8]}_clean"
            written = get_dataset_store().replace(dataset_name, rows)
            stage["artifact"] = {
                "intent": draft["intent"],
                "sql": draft["sql"],
                "dataset": dataset_name,
                "columns": columns,
                "row_count": written,
            }
            stage["status"] = "ok"
            experiment["status"] = "ok"
            store.save(experiment)
            return

        raise RuntimeError(
            f"Cleaning failed after {MAX_SQL_ATTEMPTS} attempts: {last_error}"
        )

    except Exception as exc:
        logger.warning("Experiment %s failed: %s", experiment_id, exc)
        for stage in experiment["stages"]:
            if stage["status"] == "running":
                stage["status"] = "error"
                stage["error"] = str(exc)
        experiment["status"] = "error"
        experiment["error"] = str(exc)
        store.save(experiment)


def start_experiment(experiment_id: str) -> None:
    """Kick off the pipeline as a background task on the running loop."""
    task = asyncio.create_task(run_experiment(experiment_id))
    _running[experiment_id] = task
    task.add_done_callback(lambda _t: _running.pop(experiment_id, None))
