"""Data Science analyses: agent-driven questions over the data catalog.

An Analysis binds catalog sources under aliases; each Cell is a question.
The agent plans read-only DuckDB SQL from the sources' schemas and
profiles, the platform executes it via the catalog's federation machinery,
and the agent narrates the result. Cells store SQL, not data — rerunning
re-executes against current data.
"""

from __future__ import annotations

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

MAX_SNAPSHOT_ROWS = 200
MAX_AGENT_ATTEMPTS = 3
ALLOWED_CHART_TYPES = {"bar", "line", "table"}


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ------------------------------------------------------------------- storage


class AnalysisStore:
    """Analyses persisted as documents in the datasets database."""

    def __init__(self) -> None:
        self.db_path = get_settings().data_dir / "datasets.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analyses (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    sources TEXT NOT NULL,
                    cells TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def list(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, name, sources, cells, created_at, updated_at "
                "FROM analyses ORDER BY updated_at DESC"
            )
            summaries = []
            for row in cursor.fetchall():
                cells = json.loads(row[3])
                summaries.append(
                    {
                        "id": row[0],
                        "name": row[1],
                        "sources": json.loads(row[2]),
                        "cell_count": len(cells),
                        "created_at": row[4],
                        "updated_at": row[5],
                    }
                )
            return summaries

    def get(self, analysis_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, name, sources, cells, created_at, updated_at "
                "FROM analyses WHERE id = ?",
                (analysis_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "sources": json.loads(row[2]),
            "cells": json.loads(row[3]),
            "created_at": row[4],
            "updated_at": row[5],
        }

    def create(self, name: str, sources: dict[str, str]) -> dict[str, Any]:
        analysis = {
            "id": uuid.uuid4().hex,
            "name": name,
            "sources": sources,
            "cells": [],
            "created_at": _now(),
            "updated_at": _now(),
        }
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO analyses (id, name, sources, cells, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    analysis["id"],
                    name,
                    json.dumps(sources),
                    "[]",
                    analysis["created_at"],
                    analysis["updated_at"],
                ),
            )
        return analysis

    def save(self, analysis: dict[str, Any]) -> None:
        analysis["updated_at"] = _now()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE analyses SET name = ?, sources = ?, cells = ?, updated_at = ? "
                "WHERE id = ?",
                (
                    analysis["name"],
                    json.dumps(analysis["sources"]),
                    json.dumps(analysis["cells"], default=str),
                    analysis["updated_at"],
                    analysis["id"],
                ),
            )

    def delete(self, analysis_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM analyses WHERE id = ?", (analysis_id,)
            )
        return cursor.rowcount > 0


_store: AnalysisStore | None = None


def get_analysis_store() -> AnalysisStore:
    global _store
    if _store is None:
        _store = AnalysisStore()
    return _store


def reset_analysis_store() -> None:
    global _store
    _store = None


# ---------------------------------------------------------------- agent loop


def _sources_context(sources: dict[str, str]) -> str:
    """Schema + profile of each bound source, compact, for the planner."""
    blocks = []
    for alias, source_id in sources.items():
        source = resolve_source(source_id)
        if source is None:
            raise LookupError(f"Source '{source_id}' (alias {alias}) not found")
        adapter = get_adapter(source)
        schema = adapter.schema()
        profile = profile_source(adapter)
        stats = json.dumps(profile["columns"], default=str)[:2500]
        blocks.append(
            f"Table `{alias}` ({source['name']}, {profile['total_rows']} rows)\n"
            f"Columns: {json.dumps(schema)}\n"
            f"Column statistics: {stats}"
        )
    return "\n\n".join(blocks)


def _prior_cells_context(cells: list[dict[str, Any]], limit: int = 6) -> str:
    lines = []
    for cell in cells[-limit:]:
        if cell.get("status") != "ok":
            continue
        lines.append(
            f"- Q: {cell.get('question') or '(manual)'}\n"
            f"  SQL: {cell.get('sql', '')[:300]}\n"
            f"  Finding: {(cell.get('narrative') or '')[:300]}"
        )
    return "\n".join(lines) or "(none yet)"


async def plan_cell(
    question: str, sources_context: str, prior_context: str, error: str | None = None
) -> dict[str, Any]:
    """One LLM call: question -> {sql, chart}. Isolated so tests can stub it."""
    from app.generation import DEFAULT_GENERATION_MODEL, _resolve_model_and_key
    from genxai.llm.factory import LLMProviderFactory
    from genxai.utils.structured import parse_json_loosely

    model, api_key = _resolve_model_and_key(DEFAULT_GENERATION_MODEL)
    if api_key is None:
        raise RuntimeError(
            "No LLM API key configured — set OPENAI_API_KEY or ANTHROPIC_API_KEY"
        )

    retry_block = (
        f"\nYour previous SQL failed with this error — fix it:\n{error}\n"
        if error
        else ""
    )
    prompt = (
        "You are a data analyst writing DuckDB SQL.\n\n"
        f"Available tables:\n{sources_context}\n\n"
        f"Findings so far:\n{prior_context}\n\n"
        f"Question: {question}\n{retry_block}\n"
        "Reply with ONLY a JSON object:\n"
        '{"sql": "<one read-only SELECT/WITH DuckDB statement over the listed tables>",\n'
        ' "chart": {"type": "bar"|"line"|"table", "x": "<column>", "y": "<column>"} or null}\n'
        "Prefer small, focused result sets (aggregate; LIMIT 100)."
    )
    provider = LLMProviderFactory.create_provider(model=model, api_key=api_key)
    response = await provider.generate(
        prompt, system_prompt="Return only valid JSON. No prose, no code fences."
    )
    parsed = parse_json_loosely(response.content)
    if not isinstance(parsed, dict) or not parsed.get("sql"):
        raise ValueError(f"Planner returned no SQL: {response.content[:200]}")
    return {"sql": str(parsed["sql"]), "chart": parsed.get("chart"), "model": model}


async def narrate_cell(
    question: str, sql: str, columns: list[str], rows: list[dict[str, Any]]
) -> str:
    """Second LLM call: interpret the result. Isolated so tests can stub it."""
    from app.generation import DEFAULT_GENERATION_MODEL, _resolve_model_and_key
    from genxai.llm.factory import LLMProviderFactory

    model, api_key = _resolve_model_and_key(DEFAULT_GENERATION_MODEL)
    if api_key is None:
        raise RuntimeError("No LLM API key configured")
    prompt = (
        f"Question: {question}\n"
        f"SQL executed:\n{sql}\n"
        f"Result columns: {columns}\n"
        f"Result rows (up to 50 shown):\n{json.dumps(rows[:50], default=str)[:8000]}\n\n"
        "In 2-4 sentences: answer the question from this result, note one "
        "caveat if relevant, and suggest one natural follow-up question."
    )
    provider = LLMProviderFactory.create_provider(model=model, api_key=api_key)
    response = await provider.generate(
        prompt, system_prompt="You are a careful data analyst. Be concrete and brief."
    )
    return response.content.strip()


def _execute_sql(sql: str, sources: dict[str, str]) -> tuple[list[str], list[dict[str, Any]], int]:
    """Validate and run SQL over the bound sources; returns (columns, snapshot, total)."""
    adapter = FederatedAdapter(sql, sources)
    rows = adapter.rows_data
    columns: list[str] = []
    for row in rows[:50]:
        for key in row:
            if key not in columns:
                columns.append(key)
    return columns, rows[:MAX_SNAPSHOT_ROWS], len(rows)


def _normalize_chart(chart: Any, columns: list[str]) -> dict[str, Any] | None:
    if not isinstance(chart, dict):
        return None
    chart_type = str(chart.get("type") or "")
    if chart_type not in ALLOWED_CHART_TYPES or chart_type == "table":
        return None
    x, y = str(chart.get("x") or ""), str(chart.get("y") or "")
    if x not in columns or y not in columns:
        return None
    return {"type": chart_type, "x": x, "y": y}


async def run_agent_cell(analysis: dict[str, Any], question: str) -> dict[str, Any]:
    """The full loop: plan -> validate -> execute (with retries) -> narrate."""
    sources_context = _sources_context(analysis["sources"])
    prior_context = _prior_cells_context(analysis["cells"])

    cell: dict[str, Any] = {
        "id": uuid.uuid4().hex,
        "question": question,
        "created_at": _now(),
    }
    error: str | None = None
    for attempt in range(MAX_AGENT_ATTEMPTS):
        try:
            plan = await plan_cell(question, sources_context, prior_context, error)
            sql = validate_readonly_sql(plan["sql"])
            columns, snapshot, total = _execute_sql(sql, analysis["sources"])
            narrative = await narrate_cell(question, sql, columns, snapshot)
            cell.update(
                sql=sql,
                columns=columns,
                result_rows=snapshot,
                row_count=total,
                chart=_normalize_chart(plan.get("chart"), columns),
                narrative=narrative,
                status="ok",
                error=None,
                attempts=attempt + 1,
                ran_at=_now(),
            )
            return cell
        except RuntimeError:
            raise  # missing API key: no point retrying
        except Exception as exc:
            error = str(exc)
            logger.info("Cell attempt %d failed: %s", attempt + 1, error)

    cell.update(
        sql=cell.get("sql"),
        status="error",
        error=error,
        attempts=MAX_AGENT_ATTEMPTS,
        ran_at=_now(),
    )
    return cell


def run_manual_cell(
    analysis: dict[str, Any], sql: str, question: str | None = None
) -> dict[str, Any]:
    """Execute user-written SQL as a cell (no LLM involved)."""
    cell: dict[str, Any] = {
        "id": uuid.uuid4().hex,
        "question": question or None,
        "created_at": _now(),
    }
    try:
        clean = validate_readonly_sql(sql)
        columns, snapshot, total = _execute_sql(clean, analysis["sources"])
        cell.update(
            sql=clean,
            columns=columns,
            result_rows=snapshot,
            row_count=total,
            chart=None,
            narrative=None,
            status="ok",
            error=None,
            ran_at=_now(),
        )
    except Exception as exc:
        cell.update(sql=sql, status="error", error=str(exc), ran_at=_now())
    return cell


def rerun_cell(analysis: dict[str, Any], cell: dict[str, Any]) -> dict[str, Any]:
    """Re-execute a cell's stored SQL against current data (keeps narrative)."""
    if not cell.get("sql"):
        cell["status"] = "error"
        cell["error"] = "Cell has no SQL to run"
        return cell
    try:
        columns, snapshot, total = _execute_sql(
            validate_readonly_sql(cell["sql"]), analysis["sources"]
        )
        cell.update(
            columns=columns,
            result_rows=snapshot,
            row_count=total,
            status="ok",
            error=None,
            ran_at=_now(),
        )
    except Exception as exc:
        cell.update(status="error", error=str(exc), ran_at=_now())
    return cell


MAX_MATERIALIZE_ROWS = 100_000


def materialize_cell(
    analysis: dict[str, Any],
    cell: dict[str, Any],
    dataset: str,
    mode: str = "replace",
) -> dict[str, Any]:
    """Write a cell's full (re-executed) result into a durable dataset."""
    from genxai.core.datasets import get_dataset_store

    if not cell.get("sql"):
        raise ValueError("Cell has no SQL to materialize")
    if mode not in ("replace", "append"):
        raise ValueError("mode must be replace or append")

    adapter = FederatedAdapter(
        validate_readonly_sql(cell["sql"]), analysis["sources"]
    )
    rows = adapter.rows_data[:MAX_MATERIALIZE_ROWS]
    store = get_dataset_store()
    if mode == "replace":
        written = store.replace(dataset, rows)
    else:
        written = store.append(dataset, rows)
    return {
        "dataset": dataset,
        "written": written,
        "total_rows": store.rows(dataset, limit=1)["total"],
    }


def rerun_all(analysis: dict[str, Any]) -> dict[str, Any]:
    """Re-execute every cell that has SQL, in order; returns the analysis."""
    for cell in analysis["cells"]:
        if cell.get("sql"):
            rerun_cell(analysis, cell)
    return analysis
