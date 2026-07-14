"""Analytics code crew: Python Coder + Code Review agents.

The Python Coder Agent writes pandas/matplotlib code for data
manipulation and visualization over a catalog source; the Code Review
Agent gates it under the code-stage contract; the platform executes it
in the sandboxed code-stage runtime (subprocess, scrubbed environment,
import allowlist, timeout). Figures land in the file store; derived
tables materialize into the dataset store and become catalog sources.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

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
        "stdout": (result.get("stdout") or "")[-2000:],
    }
