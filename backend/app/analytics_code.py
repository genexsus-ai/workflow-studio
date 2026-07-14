"""Analytics dashboard crew: Planner, Python Coder, Code Review, Reporter.

The Dashboard Planning Agent (the manager) decides WHICH charts to
build; the Python Coder Agent implements them in pandas/matplotlib;
the Code Review Agent gates the script under the code-stage contract;
the platform executes it in the sandboxed code-stage runtime
(subprocess, scrubbed environment, import allowlist, timeout); the
Analytics Reporter narrates the result grounded in computed numbers.
Figures land in the file store; derived tables materialize into the
dataset store and become catalog sources; reports persist per source.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings
from app.data_catalog import get_adapter, profile_source, resolve_source

logger = logging.getLogger(__name__)

MAX_REVIEW_ROUNDS = 2
MAX_INPUT_ROWS = 50_000


def _source_context(source: dict[str, Any], adapter: Any) -> str:
    profile = profile_source(adapter)
    return (
        f"Input file ./data/data.parquet — a snapshot of source "
        f"'{source['name']}' ({profile['total_rows']} rows).\n"
        f"Columns: {json.dumps(adapter.schema())}\n"
        f"Column statistics: {json.dumps(profile['columns'], default=str)[:2500]}"
    )


async def draft_analysis_code(
    request: str, context: str, feedback: str | None = None
) -> str:
    """Python Coder Agent: a complete script under the code-stage contract."""
    from app.analyst import run_analyst

    revise = f"\nFeedback on your previous code — address it:\n{feedback}\n" if feedback else ""
    prompt = (
        f"Data:\n{context}\n{revise}\n"
        f"Task: {request}\n\n"
        "Write a complete Python script that:\n"
        "- reads ./data/data.parquet with pandas\n"
        "- performs the requested data manipulation / analysis\n"
        "- saves every figure as ./out/figures/<name>.png via matplotlib "
        "(never plt.show())\n"
        "- writes any derived table to ./out/datasets/<name>.csv or .parquet\n"
        "- prints the key numbers to stdout\n"
        "Allowed imports: pandas, numpy, matplotlib, scipy, sklearn, and the "
        "standard library. ./out/figures and ./out/datasets already exist.\n"
        "Reply with ONLY the Python code — no fences, no prose."
    )
    result = await run_analyst(
        prompt,
        role="Python Coder Agent",
        goal="Write correct, minimal pandas/matplotlib analysis scripts",
        backstory="Return only runnable Python. No explanations.",
    )
    code = result["output"].strip()
    return re.sub(r"^```(?:python)?\s*|\s*```$", "", code).strip()


async def review_analysis_code(
    code: str, request: str, context: str
) -> dict[str, str]:
    """Code Review Agent: approve or request revision, with reason."""
    from app.analyst import run_analyst
    from genxai.utils.structured import parse_json_loosely

    prompt = (
        f"You are reviewing a Python analysis script.\n"
        f"Task it must accomplish: {request}\n\n"
        f"Data context:\n{context[:2000]}\n\n"
        f"Code:\n{code[:4000]}\n\n"
        "IMPORTANT: the script reads ./data/data.parquet (that is CORRECT — "
        "there is no SQL table in code stages) and must write only under "
        "./out/. Request revision ONLY for real defects: certain crashes, "
        "nonexistent columns, writing outside ./out, or not performing the "
        "requested task. Do NOT demand stylistic or structural changes. "
        "When in doubt, approve.\n"
        "Reply with ONLY JSON:\n"
        '{"verdict": "approve"|"revise", "reason": "<one sentence>"}'
    )
    result = await run_analyst(
        prompt,
        role="Code Review Agent",
        goal="Catch incorrect or unsafe analysis code before it runs",
        backstory="Return only valid JSON. Be strict but practical.",
    )
    parsed = parse_json_loosely(result["output"])
    if not isinstance(parsed, dict) or parsed.get("verdict") not in ("approve", "revise"):
        return {"verdict": "approve",
                "reason": "reviewer output unparseable; defaulted to approve"}
    return {"verdict": str(parsed["verdict"]), "reason": str(parsed.get("reason", ""))}


def _dataset_name(stem: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", stem).strip("_").lower()
    return cleaned or "analysis_output"


async def run_code_analysis(source_id: str, request: str) -> dict[str, Any]:
    """Draft -> review loop -> sandboxed run (one self-repair) -> materialize.

    Returns {source, request, code, review, figures, datasets, stdout}.
    Raises for missing/empty sources and for code that still fails after
    the self-repair attempt.
    """
    from app.code_stage import check_imports, code_stages_enabled, run_code_stage
    from genxai.core.datasets import get_dataset_store

    if not code_stages_enabled():
        raise RuntimeError("Code stages are disabled (GENXAI_DISABLE_CODE_STAGES=1)")
    source = resolve_source(source_id)
    if source is None:
        raise LookupError(f"Source '{source_id}' not found")
    adapter = get_adapter(source)
    rows = adapter.rows(MAX_INPUT_ROWS, 0)["rows"]
    if not rows:
        raise LookupError(f"Source '{source_id}' is empty or missing")
    context = _source_context(source, adapter)

    code = await draft_analysis_code(request, context)
    verdicts: list[dict[str, str]] = []
    for round_index in range(MAX_REVIEW_ROUNDS + 1):
        disallowed = check_imports(code)
        if disallowed:
            problem = f"Disallowed imports: {', '.join(disallowed)}"
        else:
            review = await review_analysis_code(code, request, context)
            verdicts.append(review)
            problem = review["reason"] if review["verdict"] == "revise" else None
        if problem is None or round_index == MAX_REVIEW_ROUNDS:
            break
        code = await draft_analysis_code(request, context, feedback=problem)

    result = await run_code_stage(code, {"data": rows})
    if result["status"] != "ok":
        feedback = (
            f"Your code failed to run: {result.get('error')}\n"
            f"stdout tail:\n{(result.get('stdout') or '')[-1000:]}"
        )
        code = await draft_analysis_code(request, context, feedback=feedback)
        result = await run_code_stage(code, {"data": rows})
    if result["status"] != "ok":
        raise RuntimeError(f"Code analysis failed: {result.get('error')}")

    store = get_dataset_store()
    written: dict[str, int] = {}
    for stem, dataset_rows in (result.get("datasets") or {}).items():
        if not dataset_rows:
            continue
        name = _dataset_name(stem)
        written[name] = store.replace(name, dataset_rows)

    return {
        "source": source_id,
        "request": request,
        "code": code,
        "review": verdicts,
        "figures": result.get("figures") or [],
        "datasets": written,
        "metrics": result.get("metrics"),
        "stdout": (result.get("stdout") or "")[-2000:],
    }


# ------------------------------------------------------------ dashboard report


async def plan_dashboard(
    context: str, focus: str | None = None
) -> list[dict[str, Any]]:
    """Dashboard Planning Agent (the manager): decide WHICH charts to build.

    Returns 3-6 chart specs [{name, kind, purpose, columns}] the Python
    Coder Agent must then implement one-for-one.
    """
    from app.analyst import run_analyst
    from genxai.utils.structured import parse_json_loosely

    focus_line = f"Requested focus: {focus}\n" if focus else ""
    prompt = (
        f"{context[:2500]}\n{focus_line}\n"
        "Plan a compact analytics dashboard for this data. Choose 3-6 charts "
        "that together give the best overview: a trend over time if a "
        "time-like column exists, top categories, distributions, "
        "comparisons. Use ONLY columns that exist. Each chart must answer "
        "a distinct question.\n"
        "Reply with ONLY JSON:\n"
        '{"charts": [{"name": "<snake_case_slug>", '
        '"kind": "line"|"bar"|"hist"|"scatter"|"box"|"heatmap", '
        '"purpose": "<the question this chart answers>", '
        '"columns": ["<column>", ...]}, ...]}'
    )
    result = await run_analyst(
        prompt,
        role="Dashboard Planning Agent",
        goal="Plan the smallest set of charts that best explains the data",
        backstory="Return only valid JSON. No prose.",
    )
    parsed = parse_json_loosely(result["output"])
    raw_charts = parsed.get("charts") if isinstance(parsed, dict) else None
    charts: list[dict[str, Any]] = []
    for entry in raw_charts or []:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        charts.append(
            {
                "name": _dataset_name(str(entry["name"])),
                "kind": str(entry.get("kind", "")),
                "purpose": str(entry.get("purpose", "")),
                "columns": [str(col) for col in (entry.get("columns") or [])],
            }
        )
    if not charts:
        raise RuntimeError(
            f"Dashboard planner returned no charts: {result['output'][:200]}"
        )
    return charts[:6]


def _plan_to_request(plan: list[dict[str, Any]]) -> str:
    """The coder's brief: implement the planned charts one-for-one."""
    chart_lines = "\n".join(
        f"{index + 1}. {chart['name']} — {chart['kind']} chart: {chart['purpose']}"
        + (f" (columns: {', '.join(chart['columns'])})" if chart["columns"] else "")
        + f". Save as ./out/figures/{chart['name']}.png"
        for index, chart in enumerate(plan)
    )
    return (
        "Implement this dashboard plan EXACTLY — one PNG per planned chart, "
        f"clear title and labeled axes on each:\n{chart_lines}\n"
        "Also write the key summary numbers behind the charts as one flat "
        "JSON object to ./out/metrics.json and print a short plain-text "
        "summary to stdout."
    )


class ReportStore:
    """Dashboard reports: one JSON payload per report in the datasets DB."""

    def __init__(self) -> None:
        self.db_path = get_settings().data_dir / "datasets.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS analytics_reports (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def list(self, source_id: str | None = None) -> list[dict[str, Any]]:
        query = (
            "SELECT id, source_id, payload, created_at FROM analytics_reports"
        )
        params: tuple = ()
        if source_id:
            query += " WHERE source_id = ?"
            params = (source_id,)
        query += " ORDER BY created_at DESC"
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(query, params).fetchall()
        summaries = []
        for row in rows:
            payload = json.loads(row[2])
            summaries.append(
                {
                    "id": row[0],
                    "source": row[1],
                    "focus": payload.get("focus"),
                    "figures": len(payload.get("figures") or []),
                    "created_at": row[3],
                }
            )
        return summaries

    def get(self, report_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT payload FROM analytics_reports WHERE id = ?",
                (report_id,),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, report: dict[str, Any]) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO analytics_reports (id, source_id, payload, created_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    report["id"],
                    report["source"],
                    json.dumps(report, default=str),
                    report["created_at"],
                ),
            )

    def delete(self, report_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM analytics_reports WHERE id = ?", (report_id,)
            )
        return cursor.rowcount > 0


_report_store: ReportStore | None = None


def get_report_store() -> ReportStore:
    global _report_store
    if _report_store is None:
        _report_store = ReportStore()
    return _report_store


async def narrate_dashboard(
    source_name: str,
    context: str,
    metrics: Any,
    stdout: str,
    figure_names: list[str],
    focus: str | None,
) -> str:
    """Analytics Reporter: structured markdown grounded in computed results."""
    from app.analyst import run_analyst
    from app.experiments import _normalize_report_markdown

    focus_line = f"Requested focus: {focus}\n" if focus else ""
    prompt = (
        f"Data source: {source_name}\n{focus_line}"
        f"{context[:2000]}\n\n"
        f"Computed metrics (metrics.json): {json.dumps(metrics, default=str)[:2000]}\n"
        f"Script output:\n{stdout[:2000]}\n"
        f"Charts generated: {', '.join(figure_names) or '(none)'}\n\n"
        "Write the dashboard narrative as GitHub-flavored markdown with "
        "EXACTLY these three '## ' sections:\n"
        "## Overview\n"
        "## Key findings\n"
        "Bullet points grounded ONLY in the computed metrics and script "
        "output above — never invent numbers.\n"
        "## What to watch\n\n"
        "Use real newlines. Do NOT wrap the markdown in any tag or fence. "
        "Reply with ONLY the markdown."
    )
    result = await run_analyst(
        prompt,
        role="Analytics Reporter",
        goal="Explain the dashboard honestly, grounded in computed numbers",
        backstory="Return only markdown. No preamble.",
    )
    return _normalize_report_markdown(result["output"])


async def run_dashboard_report(
    source_id: str, focus: str | None = None
) -> dict[str, Any]:
    """Planner picks the charts; the code crew builds them; the Reporter
    narrates; the store keeps it."""
    source = resolve_source(source_id)
    if source is None:
        raise LookupError(f"Source '{source_id}' not found")

    adapter = get_adapter(source)
    context = _source_context(source, adapter)
    plan = await plan_dashboard(context, focus)
    analysis = await run_code_analysis(source_id, _plan_to_request(plan))

    report_md = await narrate_dashboard(
        source["name"],
        context,
        analysis.get("metrics"),
        analysis.get("stdout") or "",
        [figure["name"] for figure in analysis["figures"]],
        focus,
    )

    report = {
        "id": uuid.uuid4().hex,
        "source": source_id,
        "source_name": source["name"],
        "focus": focus,
        "plan": plan,
        "report": report_md,
        "figures": analysis["figures"],
        "datasets": analysis["datasets"],
        "metrics": analysis.get("metrics"),
        "stdout": analysis.get("stdout") or "",
        "code": analysis["code"],
        "review": analysis["review"],
        "created_at": datetime.now(UTC).isoformat(),
    }
    get_report_store().save(report)
    return report
