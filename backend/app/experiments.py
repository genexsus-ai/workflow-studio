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
import uuid
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings
from app.studio_db import studio_connect, try_execute
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

# Candidate selection tiebreak: when CV means are within one std of the
# best, the model with the lower rank here wins (interpretable > ensemble).
_MODEL_SIMPLICITY = {
    "linear_regression": 0,
    "logistic_regression": 0,
    "random_forest_regression": 1,
    "random_forest_classification": 1,
}
EXPLORE_RESULT_ROWS = 20
STAGE_NAMES = ("plan", "explore", "clean", "features", "model", "viz", "report")


def _now() -> str:
    return datetime.now(UTC).isoformat()


# ------------------------------------------------------------------- storage


class ExperimentStore:
    def __init__(self) -> None:
        self.db_path = get_settings().data_dir / "datasets.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with studio_connect(self.db_path) as conn:
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
                    human_gates INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            # no-op when the column already exists
            try_execute(
                conn,
                "ALTER TABLE experiments ADD COLUMN human_gates INTEGER NOT NULL DEFAULT 0",
            )

    def list(self) -> list[dict[str, Any]]:
        with studio_connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, objective, source_id, target, status, error, stages, "
                "human_gates, created_at, updated_at FROM experiments "
                "ORDER BY created_at DESC"
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
                    "human_gates": bool(row[7]),
                    "created_at": row[8],
                    "updated_at": row[9],
                }
            )
        return summaries

    def get(self, experiment_id: str) -> dict[str, Any] | None:
        with studio_connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, objective, source_id, target, status, error, stages, "
                "human_gates, created_at, updated_at FROM experiments WHERE id = ?",
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
            "human_gates": bool(row[7]),
            "created_at": row[8],
            "updated_at": row[9],
        }

    def create(
        self,
        objective: str,
        source_id: str,
        target: str | None,
        human_gates: bool = False,
    ) -> dict[str, Any]:
        experiment = {
            "id": uuid.uuid4().hex,
            "objective": objective,
            "source_id": source_id,
            "target": target,
            "human_gates": human_gates,
            "status": "queued",
            "error": None,
            "stages": [
                {"name": name, "status": "pending", "artifact": None, "verdicts": []}
                for name in STAGE_NAMES
            ],
            "created_at": _now(),
            "updated_at": _now(),
        }
        with studio_connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO experiments (id, objective, source_id, target, status, "
                "error, stages, human_gates, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    experiment["id"],
                    objective,
                    source_id,
                    target,
                    "queued",
                    None,
                    json.dumps(experiment["stages"]),
                    1 if human_gates else 0,
                    experiment["created_at"],
                    experiment["updated_at"],
                ),
            )
        return experiment

    def save(self, experiment: dict[str, Any]) -> None:
        experiment["updated_at"] = _now()
        with studio_connect(self.db_path) as conn:
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
        with studio_connect(self.db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM experiments WHERE id = ?", (experiment_id,)
            )
        return cursor.rowcount > 0


_store: ExperimentStore | None = None
_running: dict[str, asyncio.Task] = {}
_gates: dict[str, asyncio.Future] = {}


def get_experiment_store() -> ExperimentStore:
    global _store
    if _store is None:
        _store = ExperimentStore()
    return _store


def reset_experiment_store() -> None:
    global _store
    _store = None
    _running.clear()
    _gates.clear()


def resolve_gate(experiment_id: str, approve: bool, note: str | None = None) -> bool:
    """Answer a waiting human gate; returns False if nothing is waiting."""
    future = _gates.pop(experiment_id, None)
    if future is None or future.done():
        return False
    future.set_result({"approve": approve, "note": note})
    return True


async def _human_gate(
    experiment: dict[str, Any],
    stage: dict[str, Any],
    store: "ExperimentStore",
    question: str,
    preview: dict[str, Any],
) -> None:
    """Pause the pipeline until the user approves; raises on rejection."""
    if not experiment.get("human_gates"):
        return
    stage["gate"] = {
        "question": question,
        "preview": preview,
        "requested_at": _now(),
    }
    experiment["status"] = "waiting"
    store.save(experiment)

    future: asyncio.Future = asyncio.get_running_loop().create_future()
    _gates[experiment["id"]] = future
    decision = await future

    stage["gate"]["decided_at"] = _now()
    stage["gate"]["approved"] = bool(decision.get("approve"))
    if decision.get("note"):
        stage["gate"]["note"] = decision["note"]
    experiment["status"] = "running"
    store.save(experiment)
    if not decision.get("approve"):
        raise RuntimeError(
            f"Stopped at human gate ({stage['name']})"
            + (f": {decision.get('note')}" if decision.get("note") else "")
        )


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

    if "code" in kind:
        contract = (
            "IMPORTANT: this is a Python CODE stage. It reads inputs from "
            "./data/<alias>.parquet files (that is CORRECT — there is no SQL "
            "table in code stages) and writes outputs to ./out/. Request "
            "revision ONLY for real defects: certain crashes, nonexistent "
            "columns, target leakage, or writing outside ./out.\n"
        )
    else:
        contract = (
            "IMPORTANT: the data is exposed as a table literally named `data` — "
            "SQL referencing `data` is CORRECT; never ask for the source's "
            "display name to appear in SQL.\n"
            "Request revision ONLY for real defects: not read-only, referencing "
            "nonexistent columns, target leakage, or destructive data loss "
            "(dropping most rows, discarding the target).\n"
        )
    prompt = (
        f"You are reviewing a {kind} artifact for this experiment plan:\n"
        f"{json.dumps(plan)}\n\n"
        f"Data context:\n{context[:2000]}\n\n"
        f"Artifact:\n{json.dumps(artifact, default=str)[:4000]}\n\n"
        + contract +
        "Do NOT demand extra transformations, encodings, or stylistic "
        "changes — later stages handle those. When in doubt, approve.\n"
        "Reply with ONLY JSON:\n"
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



async def draft_features(
    plan: dict[str, Any], clean_context: str, feedback: str | None = None
) -> dict[str, str]:
    """Feature Engineering + Programming Agents: one SQL over the cleaned data."""
    from app.analyst import run_analyst
    from genxai.utils.structured import parse_json_loosely

    revise = f"\nFeedback on your previous draft — address it:\n{feedback}\n" if feedback else ""
    prompt = (
        f"Experiment plan: {json.dumps(plan)}\n\n"
        f"Cleaned data (table alias `data`):\n{clean_context}\n{revise}\n"
        "Write ONE read-only DuckDB SELECT over `data` that builds the model-"
        "ready feature table: derived ratios, buckets, date parts, useful "
        "encodings — numeric features where possible. The target column "
        f"'{plan.get('target') or '(none)'}' MUST appear in the output "
        "unchanged (same name, same values) — never drop, rename, or encode "
        "it. Reply with ONLY JSON:\n"
        '{"intent": "<features built and why>", "sql": "<SELECT ...>"}'
    )
    result = await run_analyst(
        prompt,
        role="Feature Engineering Agent",
        goal="Build the feature table the model will learn from",
        backstory="Return only valid JSON. No prose.",
    )
    parsed = parse_json_loosely(result["output"])
    if not isinstance(parsed, dict) or not parsed.get("sql"):
        raise ValueError(f"Feature engineer returned no SQL: {result['output'][:200]}")
    return {"intent": str(parsed.get("intent", "")), "sql": str(parsed["sql"])}


async def draft_model_plan(
    plan: dict[str, Any], features_context: str, feedback: str | None = None
) -> dict[str, Any]:
    """Model Algorithm Agent: pick spec (fast path) or Python code stage."""
    from app.analyst import run_analyst
    from genxai.utils.structured import parse_json_loosely

    revise = f"\nFeedback on your previous proposal — address it:\n{feedback}\n" if feedback else ""
    prompt = (
        f"Experiment plan: {json.dumps(plan)}\n\n"
        f"Feature table (available at ./data/data.parquet for code, alias "
        f"`data` conceptually):\n{features_context}\n{revise}\n"
        "Choose the modeling approach. Prefer approach \"spec\" (built-in "
        "primitives) unless the problem genuinely needs a custom pipeline.\n"
        "For \"spec\", propose 2-3 CANDIDATE model families suited to the "
        "task — the platform cross-validates every candidate and picks the "
        "best (preferring the simpler model on a statistical tie).\n"
        "Reply with ONLY JSON, one of:\n"
        '{"approach": "spec", "candidates": ["linear_regression"|"logistic_regression"|'
        '"random_forest_regression"|"random_forest_classification", ...], '
        '"features": [<columns>] or null, "rationale": "<why these candidates>"}\n'
        'or {"approach": "code", "code": "<python: read ./data/data.parquet with pandas, '
        "train sklearn, save ./out/model.joblib and ./out/metrics.json>\", "
        '"rationale": "<why>"}'
    )
    result = await run_analyst(
        prompt,
        role="Model Algorithm Agent",
        goal="Choose and specify the right model for the task and data",
        backstory="Return only valid JSON. No prose.",
    )
    parsed = parse_json_loosely(result["output"])
    if not isinstance(parsed, dict) or parsed.get("approach") not in ("spec", "code"):
        raise ValueError(f"Model agent returned no approach: {result['output'][:200]}")
    return parsed


async def draft_visualization(
    plan: dict[str, Any], features_context: str, results_summary: str,
    feedback: str | None = None,
) -> str:
    """Visualization Agent: matplotlib code producing ./out/figures/*.png."""
    from app.analyst import run_analyst

    revise = f"\nFeedback on your previous code — address it:\n{feedback}\n" if feedback else ""
    prompt = (
        f"Experiment plan: {json.dumps(plan)}\n\n"
        f"Feature data at ./data/data.parquet:\n{features_context}\n\n"
        f"Model results so far:\n{results_summary}\n{revise}\n"
        "Write a Python script (pandas + matplotlib, MPLBACKEND is Agg) that "
        "reads ./data/data.parquet and saves 1-3 insightful figures as PNG "
        "files into ./out/figures/ (the directory ALREADY EXISTS — do not "
        "create directories). E.g. target distribution, top feature "
        "relationships. Only use pandas/numpy/matplotlib. Reply with ONLY "
        "the Python code, no fences, no prose."
    )
    result = await run_analyst(
        prompt,
        role="Visualization Agent",
        goal="Show the data story behind the model in a few honest figures",
        backstory="Return only runnable Python source. No markdown fences.",
    )
    code = result["output"].strip()
    if code.startswith("```"):
        code = code.strip("`\n")
        if code.startswith("python"):
            code = code[len("python"):]
    return code.strip()


def _normalize_report_markdown(text: str) -> str:
    """Deterministic cleanup of narrator output into structured markdown.

    LLMs sometimes echo placeholder syntax (``<markdown: ...>``) or emit a
    single paragraph with ``**Section:**`` run-ins instead of headings.
    Strip wrappers, then — only when no real headings exist — promote the
    run-ins to ``## `` sections so the report always renders structured.
    """
    import re

    cleaned = text.strip()
    wrapped = re.match(r"^<\s*markdown:?\s*(.*)>\s*$", cleaned,
                       re.IGNORECASE | re.DOTALL)
    if wrapped:
        cleaned = wrapped.group(1).strip()
    cleaned = re.sub(r"^```(?:markdown|md)?\s*|```\s*$", "", cleaned)

    if not re.search(r"^#{1,3} ", cleaned, re.MULTILINE):
        cleaned = re.sub(
            r"\s*\*\*([^*\n]{2,60}?):\*\*\s*|\s*\*\*([^*\n]{2,60}?)\*\*:\s*",
            lambda m: f"\n\n## {m.group(1) or m.group(2)}\n\n",
            cleaned,
        ).strip()
    return cleaned


async def narrate_report(summary: dict[str, Any]) -> dict[str, str]:
    """Metric Performance Agent: the final verdict and report."""
    from app.analyst import run_analyst
    from genxai.utils.structured import parse_json_loosely

    prompt = (
        f"Experiment summary as JSON:\n{json.dumps(summary, default=str)[:9000]}\n\n"
        "Write the final report as GitHub-flavored markdown with EXACTLY "
        "these six '## ' sections, in this order, each holding 1-3 short "
        "paragraphs or a bullet list:\n"
        "## Objective\n"
        "## What the data showed\n"
        "## Data preparation\n"
        "## Model performance\n"
        "Explain the metrics in business terms here.\n"
        "## Risks & caveats\n"
        "## Next steps\n\n"
        "Use real newlines between sections. Do NOT wrap the markdown in "
        "any tag, fence, or angle brackets.\n"
        "Reply with ONLY JSON:\n"
        '{"recommendation": "ship"|"iterate"|"abandon", '
        '"report": "## Objective\\n..."}'
    )
    result = await run_analyst(
        prompt,
        role="Metric Performance Agent",
        goal="Judge the experiment honestly and explain it in business terms",
        backstory="Return only valid JSON. Be candid about weaknesses.",
    )
    parsed = parse_json_loosely(result["output"])
    if not isinstance(parsed, dict) or not parsed.get("report"):
        raise ValueError(f"Report agent returned nothing: {result['output'][:200]}")
    return {
        "recommendation": str(parsed.get("recommendation", "iterate")),
        "report": _normalize_report_markdown(str(parsed["report"])),
    }


# ------------------------------------------------------------------ pipeline


def _resolve_target_column(target: str, columns: list[str]) -> str | None:
    """Find the target in a column list, tolerating agent renames.

    Feature agents sometimes encode the target (churned -> churned_encoded)
    despite instructions; accept recognizable variants rather than failing.
    """
    if target in columns:
        return target
    lowered = {column.lower(): column for column in columns}
    for candidate in (target.lower(), f"{target.lower()}_encoded", f"{target.lower()}_enc"):
        if candidate in lowered:
            return lowered[candidate]
    for column in columns:
        if column.lower().startswith(target.lower() + "_"):
            return column
    return None


def _stage(experiment: dict[str, Any], name: str) -> dict[str, Any]:
    return next(s for s in experiment["stages"] if s["name"] == name)


# Display names for the deterministic performance section of the report.
_METRIC_LABELS = {
    "r2": "R²",
    "mae": "MAE",
    "mse": "MSE",
    "rmse": "RMSE",
    "accuracy": "Accuracy",
    "precision_weighted": "Precision (weighted)",
    "recall_weighted": "Recall (weighted)",
    "f1_weighted": "F1 (weighted)",
    "roc_auc": "ROC AUC",
}


def _performance_section(model_result: dict[str, Any]) -> str:
    """Markdown 'Final model performance' block appended to every report.

    Deterministic — the numbers come from the holdout/CV computations, not
    the narrator agent, so they are always present and always correct.
    """
    if not model_result or model_result.get("skipped"):
        return ""
    metrics = model_result.get("holdout") or model_result.get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        return ""

    lines = ["## Final model performance", ""]
    model_type = model_result.get("model_type")
    rows_note = ""
    if "test_rows" in metrics:
        rows_note = (
            f" — 80/20 holdout ({metrics.get('train_rows')} train / "
            f"{metrics['test_rows']} test rows)"
        )
    if model_type:
        lines.append(f"Model: **{model_type}**{rows_note}")
        lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    for key, value in metrics.items():
        if key in ("train_rows", "test_rows"):
            continue
        lines.append(f"| {_METRIC_LABELS.get(key, key)} | {value} |")

    cv = model_result.get("cv")
    if isinstance(cv, dict) and "mean" in cv:
        lines.append("")
        lines.append(
            f"Cross-validation ({cv.get('folds')}-fold {cv.get('metric')}): "
            f"{cv['mean']} ± {cv['std']}"
            + (" · ⚠ possible overfit" if cv.get("overfit_warning") else "")
        )
    candidates = model_result.get("candidates")
    if isinstance(candidates, list) and len(candidates) > 1:
        ranked = ", ".join(
            f"{entry['model_type']}={entry['cv_mean']}"
            + (" ✓" if entry.get("chosen") else "")
            for entry in candidates
        )
        lines.append(f"Candidates compared: {ranked}")
    return "\n".join(lines)


def _sources_context(source_id: str) -> str:
    source = resolve_source(source_id)
    if source is None:
        raise LookupError(f"Source '{source_id}' not found")
    adapter = get_adapter(source)
    profile = profile_source(adapter)
    return (
        f"Table `data` — the ONLY table name usable in SQL. It exposes the "
        f"source '{source['name']}' ({profile['total_rows']} rows).\n"
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



async def _run_model_stage(
    experiment: dict[str, Any],
    stage: dict[str, Any],
    plan: dict[str, Any],
    target: str | None,
    features_source: str,
    features_context: str,
) -> dict[str, Any]:
    """Model selection -> CV -> train -> holdout -> predictions dataset."""
    if plan.get("task_type") == "descriptive" or not target:
        stage["artifact"] = {
            "skipped": True,
            "reason": "Descriptive objective (no target column) — no model trained",
        }
        return {"skipped": True}

    from app import ml

    proposal = await draft_model_plan(plan, features_context)
    review = await review_artifact("model proposal", proposal, plan, features_context)
    stage["verdicts"].append(review)
    if review["verdict"] == "revise":
        proposal = await draft_model_plan(plan, features_context, feedback=review["reason"])
        stage["verdicts"].append(
            await review_artifact("model proposal", proposal, plan, features_context)
        )

    store = get_experiment_store()
    await _human_gate(
        experiment,
        stage,
        store,
        "Approve this modeling approach before training?",
        {
            "approach": proposal.get("approach"),
            "model_type": proposal.get("model_type"),
            "rationale": proposal.get("rationale"),
        },
    )

    if proposal.get("approach") == "code":
        from app.code_stage import run_code_stage

        code = str(proposal.get("code") or "")
        adapter_rows = _execute_sql("SELECT * FROM data", features_source)[1]
        result = await run_code_stage(code, {"data": adapter_rows})
        if result["status"] != "ok":
            raise RuntimeError(f"Model code stage failed: {result.get('error')}")
        metrics = result.get("metrics") or {}
        model_name = f"exp_{experiment['id'][:8]}_model"
        registered = None
        if result.get("model_file_id"):
            from app.ml import get_model_registry

            registered = {
                "id": uuid.uuid4().hex,
                "name": model_name,
                "model_type": str(metrics.get("model_type", "custom_code")),
                "source_id": features_source,
                "target": target,
                "features": list(metrics.get("features", [])),
                "metrics": metrics,
                "file_id": result["model_file_id"],
                "created_at": _now(),
            }
            get_model_registry().save(registered)
        stage["artifact"] = {
            "approach": "code",
            "rationale": proposal.get("rationale"),
            "code": code,
            "metrics": metrics,
            "model_name": model_name if registered else None,
            "stdout": result.get("stdout", "")[-1000:],
        }
        return {"approach": "code", "metrics": metrics}

    # Candidates: the agent proposes 2-3 model families; a bare model_type
    # (old contract) still works. Invalid names are dropped, not fatal —
    # unless nothing valid remains.
    raw_candidates = proposal.get("candidates") or (
        [proposal["model_type"]] if proposal.get("model_type") else []
    )
    candidates = list(dict.fromkeys(
        str(c) for c in raw_candidates if str(c) in ml.MODEL_TYPES
    ))[:3]
    if not candidates:
        raise RuntimeError(
            f"Model agent proposed no valid model type: {raw_candidates!r} "
            f"(valid: {', '.join(sorted(ml.MODEL_TYPES))})"
        )
    model_type = candidates[0]
    features = proposal.get("features") or None

    # Feature selection: rank by forest importance, propose a pruned set,
    # cross-validate BOTH sets, and keep the winner (ties favor fewer).
    importances = ml.rank_feature_importance(
        features_source, target, model_type, features
    )
    feature_selection: dict[str, Any] | None = None
    chosen_features = [entry["feature"] for entry in importances]
    cv = ml.cross_validate_spec(features_source, target, model_type, chosen_features)
    if len(importances) > 2:
        floor = 0.5 / len(importances)  # half of a uniform share
        pruned = [e["feature"] for e in importances if e["importance"] >= floor]
        if len(pruned) >= 2 and len(pruned) < len(importances):
            cv_pruned = ml.cross_validate_spec(
                features_source, target, model_type, pruned
            )
            keep_pruned = cv_pruned["mean"] >= cv["mean"] - cv["std"]
            feature_selection = {
                "importances": importances,
                "dropped": [f for f in chosen_features if f not in pruned],
                "cv_all_features": {"mean": cv["mean"], "std": cv["std"]},
                "cv_selected": {"mean": cv_pruned["mean"], "std": cv_pruned["std"]},
                "kept_selected_set": keep_pruned,
            }
            if keep_pruned:
                chosen_features = pruned
                cv = cv_pruned
    if feature_selection is None:
        feature_selection = {"importances": importances, "dropped": [],
                             "kept_selected_set": False}

    # Cross-validate every candidate on the chosen feature set and pick the
    # winner: best CV mean, but within one std of the best the SIMPLER
    # model wins (linear/logistic beat forests).
    comparison = [{"model_type": model_type, "cv": cv}]
    for candidate in candidates[1:]:
        comparison.append({
            "model_type": candidate,
            "cv": ml.cross_validate_spec(
                features_source, target, candidate, chosen_features
            ),
        })
    best = max(comparison, key=lambda entry: entry["cv"]["mean"])
    threshold = best["cv"]["mean"] - best["cv"]["std"]
    winner = min(
        (e for e in comparison if e["cv"]["mean"] >= threshold),
        key=lambda e: (_MODEL_SIMPLICITY.get(e["model_type"], 2),
                       -e["cv"]["mean"]),
    )
    model_type = winner["model_type"]
    cv = winner["cv"]
    candidate_table = [
        {
            "model_type": entry["model_type"],
            "cv_mean": entry["cv"]["mean"],
            "cv_std": entry["cv"]["std"],
            "overfit_warning": entry["cv"]["overfit_warning"],
            "chosen": entry is winner,
        }
        for entry in comparison
    ]
    scores = ", ".join(
        f"{e['model_type']}={e['cv']['mean']}" for e in comparison
    )
    if len(comparison) == 1:
        selection_note = f"single candidate: {model_type}"
    elif winner is best:
        selection_note = f"{model_type} won on CV mean ({scores})"
    else:
        selection_note = (
            f"{model_type} chosen: within one std of the best "
            f"({scores}) — simpler model preferred"
        )

    model_name = f"exp_{experiment['id'][:8]}_model"
    trained = ml.train_model(
        model_name, features_source, target, model_type, chosen_features
    )
    predictions = ml.predict_with_model(
        trained["id"], features_source, dataset=f"exp_{experiment['id'][:8]}_predictions"
    )
    stage["artifact"] = {
        "approach": "spec",
        "rationale": proposal.get("rationale"),
        "model_type": model_type,
        "model_name": model_name,
        "candidates": candidate_table,
        "selection_note": selection_note,
        "feature_selection": feature_selection,
        "features_used": chosen_features,
        "cross_validation": cv,
        "holdout_metrics": trained["metrics"],
        "figures": trained.get("figures") or [],
        "predictions_dataset": predictions["dataset"],
    }
    return {
        "approach": "spec",
        "model_type": model_type,
        "candidates": candidate_table,
        "selection_note": selection_note,
        "features_used": chosen_features,
        "cv": cv,
        "holdout": trained["metrics"],
    }


async def _run_viz_stage(
    experiment: dict[str, Any],
    stage: dict[str, Any],
    plan: dict[str, Any],
    features_source: str,
    features_context: str,
    model_result: dict[str, Any],
) -> None:
    """Visualization Agent -> reviewed code stage -> figures in file store."""
    from app.code_stage import check_imports, run_code_stage

    results_summary = json.dumps(model_result, default=str)[:1500]
    code = await draft_visualization(plan, features_context, results_summary)
    review = await review_artifact(
        "visualization code", {"code": code[:3000]}, plan, features_context
    )
    stage["verdicts"].append(review)
    if review["verdict"] == "revise" or check_imports(code):
        feedback = review["reason"] if review["verdict"] == "revise" else (
            f"Disallowed imports: {check_imports(code)}"
        )
        code = await draft_visualization(
            plan, features_context, results_summary, feedback=feedback
        )
        stage["verdicts"].append(
            await review_artifact(
                "visualization code", {"code": code[:3000]}, plan, features_context
            )
        )

    rows = _execute_sql("SELECT * FROM data", features_source)[1]
    result = await run_code_stage(code, {"data": rows})
    if result["status"] != "ok" or not result.get("figures"):
        # Self-repair: feed the failure back to the agent once
        failure = result.get("error") or "no figures were produced in out/figures/"
        code = await draft_visualization(
            plan,
            features_context,
            results_summary,
            feedback=(
                f"Your code failed: {failure}. stdout tail: "
                f"{result.get('stdout', '')[-500:]} — fix it. Remember: "
                "./out/figures/ already exists, save PNGs there."
            ),
        )
        result = await run_code_stage(code, {"data": rows})
    if result["status"] != "ok":
        raise RuntimeError(f"Visualization code failed: {result.get('error')}")
    if not result.get("figures"):
        raise RuntimeError("Visualization produced no figures")
    stage["artifact"] = {
        "code": code,
        "figures": result["figures"],
        "stdout": result.get("stdout", "")[-500:],
    }


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
        review_rounds = 0
        for _attempt in range(MAX_SQL_ATTEMPTS):
            draft = await draft_cleaning(
                plan, context, exploration_summary, feedback
            )
            review = await review_artifact("cleaning SQL", draft, plan, context)
            stage["verdicts"].append(review)
            if review["verdict"] == "revise":
                review_rounds += 1
                if review_rounds <= MAX_REVIEW_ROUNDS:
                    feedback = review["reason"]
                    last_error = f"reviewer requested revision: {review['reason']}"
                    continue
                # Review budget exhausted: proceed if the SQL actually runs,
                # recording the reservation instead of failing the experiment
                stage["verdicts"].append(
                    {
                        "verdict": "override",
                        "reason": "review rounds exhausted; proceeding because "
                        "the SQL validates and executes",
                    }
                )
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

            await _human_gate(
                experiment,
                stage,
                store,
                "Approve this cleaning transformation before it materializes?",
                {
                    "intent": draft["intent"],
                    "sql": draft["sql"],
                    "resulting_rows": len(rows),
                },
            )

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
            store.save(experiment)
            break
        else:
            raise RuntimeError(
                f"Cleaning failed after {MAX_SQL_ATTEMPTS} attempts: {last_error}"
            )

        clean_dataset = _stage(experiment, "clean")["artifact"]["dataset"]
        clean_source = f"dataset:{clean_dataset}"
        clean_context = _sources_context(clean_source)

        # --------------------------------------------------------- features
        stage = _stage(experiment, "features")
        stage["status"] = "running"
        store.save(experiment)
        feedback = None
        last_error = None
        review_rounds = 0
        for _attempt in range(MAX_SQL_ATTEMPTS):
            draft = await draft_features(plan, clean_context, feedback)
            review = await review_artifact("feature SQL", draft, plan, clean_context)
            stage["verdicts"].append(review)
            if review["verdict"] == "revise":
                review_rounds += 1
                if review_rounds <= MAX_REVIEW_ROUNDS:
                    feedback = review["reason"]
                    last_error = f"reviewer requested revision: {review['reason']}"
                    continue
                stage["verdicts"].append(
                    {
                        "verdict": "override",
                        "reason": "review rounds exhausted; proceeding because "
                        "the SQL validates and executes",
                    }
                )
            try:
                columns, rows = _execute_sql(draft["sql"], clean_source)
            except Exception as exc:
                feedback = f"The SQL failed to execute: {exc}"
                last_error = str(exc)
                continue
            if not rows:
                feedback = "The feature SQL returned zero rows."
                last_error = "features produced zero rows"
                continue
            expected_target = plan.get("target") or experiment["target"]
            resolved_target = (
                _resolve_target_column(str(expected_target), columns)
                if expected_target and plan.get("task_type") != "descriptive"
                else None
            )
            if expected_target and plan.get("task_type") != "descriptive" and not resolved_target:
                feedback = (
                    f"Your output LOST the target column '{expected_target}' — "
                    "keep it (or a clearly named encoding of it) in the SELECT."
                )
                last_error = f"features dropped the target column '{expected_target}'"
                continue
            features_dataset = f"exp_{experiment['id'][:8]}_features"
            from genxai.core.datasets import get_dataset_store

            written = get_dataset_store().replace(features_dataset, rows)
            stage["artifact"] = {
                "intent": draft["intent"],
                "sql": draft["sql"],
                "dataset": features_dataset,
                "columns": columns,
                "row_count": written,
                "target_column": resolved_target,
            }
            stage["status"] = "ok"
            store.save(experiment)
            break
        else:
            raise RuntimeError(
                f"Feature engineering failed after {MAX_SQL_ATTEMPTS} attempts: {last_error}"
            )

        features_source = f"dataset:{features_dataset}"
        features_context = _sources_context(features_source)

        # ------------------------------------------------------------ model
        stage = _stage(experiment, "model")
        stage["status"] = "running"
        store.save(experiment)
        target = (
            _stage(experiment, "features")["artifact"].get("target_column")
            or plan.get("target")
            or experiment["target"]
        )
        model_result = await _run_model_stage(
            experiment, stage, plan, target, features_source, features_context
        )
        stage["status"] = "ok"
        store.save(experiment)

        # -------------------------------------------------------------- viz
        stage = _stage(experiment, "viz")
        stage["status"] = "running"
        store.save(experiment)
        try:
            await _run_viz_stage(
                experiment, stage, plan, features_source, features_context,
                model_result,
            )
            stage["status"] = "ok"
        except Exception as exc:
            # Figures are valuable but not fatal — record and continue
            stage["status"] = "error"
            stage["error"] = str(exc)
        store.save(experiment)

        # ------------------------------------------------------------ report
        stage = _stage(experiment, "report")
        stage["status"] = "running"
        store.save(experiment)
        summary = {
            "objective": experiment["objective"],
            "plan": plan,
            "exploration": exploration_summary,
            "cleaning": _stage(experiment, "clean")["artifact"]["intent"],
            "cleaned_rows": _stage(experiment, "clean")["artifact"]["row_count"],
            "features": _stage(experiment, "features")["artifact"]["intent"],
            "feature_columns": _stage(experiment, "features")["artifact"]["columns"],
            "model": model_result,
            "figures": len((_stage(experiment, "viz")["artifact"] or {}).get("figures", [])),
        }
        report = await narrate_report(summary)
        performance = _performance_section(model_result)
        if performance:
            report["report"] = (
                str(report.get("report") or "").rstrip() + "\n\n" + performance
            )
        stage["artifact"] = report
        stage["status"] = "ok"
        experiment["status"] = "ok"
        store.save(experiment)
        return

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


# ---------------------------------------------------------- compare / export


def compare_experiments(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Side-by-side summary of two experiments."""

    def summarize(experiment: dict[str, Any]) -> dict[str, Any]:
        stages = {s["name"]: s for s in experiment["stages"]}
        model = (stages.get("model", {}).get("artifact") or {})
        report = (stages.get("report", {}).get("artifact") or {})
        return {
            "id": experiment["id"],
            "objective": experiment["objective"],
            "status": experiment["status"],
            "source_id": experiment["source_id"],
            "cleaned_rows": (stages.get("clean", {}).get("artifact") or {}).get("row_count"),
            "feature_columns": len(
                (stages.get("features", {}).get("artifact") or {}).get("columns") or []
            ),
            "model_type": model.get("model_type") or model.get("approach"),
            "cv_mean": (model.get("cross_validation") or {}).get("mean"),
            "cv_overfit": (model.get("cross_validation") or {}).get("overfit_warning"),
            "holdout_metrics": model.get("holdout_metrics") or model.get("metrics"),
            "recommendation": report.get("recommendation"),
            "updated_at": experiment["updated_at"],
        }

    return {"a": summarize(a), "b": summarize(b)}


def build_export_zip(experiment: dict[str, Any]) -> bytes:
    """Bundle the experiment as a runnable Python project (zip bytes)."""
    import io
    import zipfile

    stages = {s["name"]: s for s in experiment["stages"]}
    plan = stages.get("plan", {}).get("artifact") or {}
    clean = stages.get("clean", {}).get("artifact") or {}
    features = stages.get("features", {}).get("artifact") or {}
    model = stages.get("model", {}).get("artifact") or {}
    viz = stages.get("viz", {}).get("artifact") or {}
    report = stages.get("report", {}).get("artifact") or {}

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        readme = [
            f"# Experiment: {experiment['objective']}",
            "",
            f"Status: {experiment['status']}",
            f"Plan: {json.dumps(plan)}",
            "",
            "## How to run",
            "```",
            "pip install duckdb pandas pyarrow scikit-learn matplotlib joblib",
            "python run.py          # clean -> features -> (train if spec)",
            "python visualization.py  # figures into out/figures/",
            "```",
            "",
            "Files: data/source_sample.parquet (capped sample), sql/*.sql "
            "(the reviewed transformations), model/, visualization.py.",
        ]
        if report.get("report"):
            readme += ["", "## Final report", "", str(report["report"])]
        bundle.writestr("README.md", "\n".join(readme))

        explore = stages.get("explore", {}).get("artifact") or {}
        for index, query in enumerate(explore.get("queries") or [], 1):
            bundle.writestr(
                f"sql/01_exploration_{index}.sql",
                f"-- {query.get('purpose', '')}\n{query.get('sql', '')}\n",
            )
        if clean.get("sql"):
            bundle.writestr(
                "sql/02_clean.sql", f"-- {clean.get('intent', '')}\n{clean['sql']}\n"
            )
        if features.get("sql"):
            bundle.writestr(
                "sql/03_features.sql",
                f"-- {features.get('intent', '')}\n{features['sql']}\n",
            )

        if model.get("approach") == "spec":
            bundle.writestr(
                "model/spec.json",
                json.dumps(
                    {
                        "model_type": model.get("model_type"),
                        "target": plan.get("target") or experiment.get("target"),
                        "cross_validation": model.get("cross_validation"),
                        "holdout_metrics": model.get("holdout_metrics"),
                    },
                    indent=2,
                    default=str,
                ),
            )
        elif model.get("code"):
            bundle.writestr("model/train.py", str(model["code"]))
        if viz.get("code"):
            bundle.writestr("visualization.py", str(viz["code"]))

        # Capped source sample so the project runs offline
        try:
            from app.data_catalog import export_source, get_adapter, resolve_source

            source = resolve_source(experiment["source_id"])
            if source is not None:
                _, payload, _ = export_source(source, get_adapter(source), "parquet")
                bundle.writestr("data/source_sample.parquet", payload)
        except Exception as exc:  # pragma: no cover - defensive
            bundle.writestr("data/EXPORT_ERROR.txt", str(exc))

        target = plan.get("target") or experiment.get("target")
        run_py = f'''"""Re-run the experiment pipeline locally (generated by GenXAI)."""

import duckdb
from pathlib import Path

con = duckdb.connect()
con.execute("CREATE VIEW data AS SELECT * FROM read_parquet('data/source_sample.parquet')")

clean_sql = Path("sql/02_clean.sql").read_text().split("\\n", 1)[1]
con.execute(f"CREATE TABLE cleaned AS {{clean_sql}}")
con.execute("DROP VIEW data")
con.execute("CREATE VIEW data AS SELECT * FROM cleaned")

features_sql = Path("sql/03_features.sql").read_text().split("\\n", 1)[1]
con.execute(f"CREATE TABLE features AS {{features_sql}}")
Path("data").mkdir(exist_ok=True)
con.execute("COPY features TO 'data/data.parquet' (FORMAT PARQUET)")
print("features written to data/data.parquet:",
      con.execute("SELECT COUNT(*) FROM features").fetchone()[0], "rows")

TARGET = {json.dumps(target)}
SPEC = Path("model/spec.json")
if TARGET and SPEC.exists():
    import json as _json
    import pandas as pd
    from sklearn.model_selection import train_test_split

    spec = _json.loads(SPEC.read_text())
    df = pd.read_parquet("data/data.parquet").dropna()
    y = df[TARGET]
    X = df.drop(columns=[TARGET]).select_dtypes("number")
    X_tr, X_te, y_tr, y_te = train_test_split(X, y, test_size=0.2, random_state=42)
    from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
    from sklearn.linear_model import LinearRegression, LogisticRegression

    est = {{
        "linear_regression": LinearRegression(),
        "logistic_regression": LogisticRegression(max_iter=1000),
        "random_forest_regression": RandomForestRegressor(random_state=42),
        "random_forest_classification": RandomForestClassifier(random_state=42),
    }}[spec["model_type"]]
    est.fit(X_tr, y_tr)
    print("holdout score:", est.score(X_te, y_te))
'''
        bundle.writestr("run.py", run_py)

    return buffer.getvalue()
