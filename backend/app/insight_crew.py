"""Verified insights: the multi-agent crew behind Analytics' Ask AI.

Three agents, each earning its cost:
1. Analyst — drafts the insight from the profile + row sample (as before).
2. Fact-Checker — turns the insight's concrete claims into read-only SQL,
   which the platform EXECUTES against the source, so verification is
   computed fact, not another opinion.
3. Judge — confirms or corrects the insight claim-by-claim against the
   query results.

Agent calls are isolated module functions so tests can stub the crew.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.data_catalog import FederatedAdapter, validate_readonly_sql

logger = logging.getLogger(__name__)

MAX_VERIFICATION_QUERIES = 3


async def draft_insight(
    name: str,
    sample: dict[str, Any],
    profile: dict[str, Any],
    question: str | None,
    model: str | None,
) -> dict[str, Any]:
    """Analyst: the initial insight (same grounding as single-agent Ask AI)."""
    from app.analyst import run_analyst

    columns = sorted(
        {key for row in sample["rows"] for key in row if not key.startswith("_")}
    )
    effective_question = question or (
        "What patterns, outliers, and actionable insights do you see?"
    )
    prompt = (
        f"Data source '{name}': {sample['total']} rows total; columns: "
        f"{', '.join(columns) or '(none)'}.\n"
        f"Column statistics (over up to 10k rows):\n"
        f"{json.dumps(profile['columns'], default=str)[:4000]}\n\n"
        f"Sample of {len(sample['rows'])} rows as JSON:\n"
        f"{json.dumps(sample['rows'], default=str)[:10000]}\n\n"
        f"Question: {effective_question}\n"
        "Answer with 3-6 concise bullet points grounded ONLY in this data. "
        "Note explicitly that this is a sample if that limits any conclusion."
    )
    result = await run_analyst(prompt, model=model)
    return {"insight": result["output"], "model": result["model"]}


async def draft_verifications(
    insight: str, schema_context: str
) -> list[dict[str, str]]:
    """Fact-Checker: concrete claims -> read-only SQL checks (table `data`)."""
    from app.analyst import run_analyst
    from genxai.utils.structured import parse_json_loosely

    prompt = (
        f"An analyst made these claims about a table (alias `data`):\n"
        f"{insight}\n\n"
        f"Schema/statistics:\n{schema_context[:2500]}\n\n"
        f"Write up to {MAX_VERIFICATION_QUERIES} read-only DuckDB queries that "
        "verify the most load-bearing NUMERIC or comparative claims. Each "
        "query should return a small result that directly confirms or "
        "refutes one claim. Reply with ONLY JSON:\n"
        '[{"claim": "<the claim being checked>", "sql": "<SELECT ...>"}]'
    )
    result = await run_analyst(
        prompt,
        role="Fact-Checker",
        goal="Reduce insights to checkable queries",
        backstory="Return only a valid JSON array. No prose.",
    )
    parsed = parse_json_loosely(result["output"])
    if not isinstance(parsed, list):
        return []
    return [
        {"claim": str(v.get("claim", "")), "sql": str(v.get("sql", ""))}
        for v in parsed[:MAX_VERIFICATION_QUERIES]
        if isinstance(v, dict) and v.get("sql")
    ]


async def judge_insight(
    insight: str, verifications: list[dict[str, Any]]
) -> dict[str, Any]:
    """Judge: confirm/correct the insight against the executed results."""
    from app.analyst import run_analyst
    from genxai.utils.structured import parse_json_loosely

    prompt = (
        f"Original insight:\n{insight}\n\n"
        f"Verification queries and their ACTUAL executed results:\n"
        f"{json.dumps(verifications, default=str)[:6000]}\n\n"
        "For each verification, state whether the claim is confirmed or "
        "needs correction, and produce the final insight text (corrected "
        "where the data disagreed). Reply with ONLY JSON:\n"
        '{"verdicts": [{"claim": "...", "verdict": "confirmed"|"corrected"|"unverifiable", '
        '"note": "<one sentence>"}],\n'
        ' "final_insight": "<the corrected bullet-point insight>"}'
    )
    result = await run_analyst(
        prompt,
        role="Insight Judge",
        goal="Let computed facts override opinions",
        backstory="Return only valid JSON. Corrections must cite the numbers.",
    )
    parsed = parse_json_loosely(result["output"])
    if not isinstance(parsed, dict) or not parsed.get("final_insight"):
        return {"verdicts": [], "final_insight": insight}
    return {
        "verdicts": list(parsed.get("verdicts") or []),
        "final_insight": str(parsed["final_insight"]),
    }


async def analyze_with_verification(
    source: dict[str, Any],
    sample: dict[str, Any],
    profile: dict[str, Any],
    question: str | None,
    model: str | None,
) -> dict[str, Any]:
    """The full crew: Analyst -> Fact-Checker -> execute -> Judge."""
    drafted = await draft_insight(source["name"], sample, profile, question, model)

    schema_context = json.dumps(profile["columns"], default=str)
    checks = await draft_verifications(drafted["insight"], schema_context)

    executed: list[dict[str, Any]] = []
    for check in checks:
        entry: dict[str, Any] = {"claim": check["claim"], "sql": check["sql"]}
        try:
            adapter = FederatedAdapter(
                validate_readonly_sql(check["sql"]), {"data": source["id"]}
            )
            entry["result"] = adapter.rows_data[:10]
            entry["status"] = "ok"
        except Exception as exc:
            entry["status"] = "error"
            entry["error"] = str(exc)
        executed.append(entry)

    if any(entry["status"] == "ok" for entry in executed):
        judged = await judge_insight(drafted["insight"], executed)
    else:
        judged = {"verdicts": [], "final_insight": drafted["insight"]}

    verdict_by_claim = {
        str(v.get("claim", "")): v for v in judged["verdicts"] if isinstance(v, dict)
    }
    verifications = []
    for entry in executed:
        verdict = verdict_by_claim.get(entry["claim"], {})
        verifications.append(
            {
                "claim": entry["claim"],
                "sql": entry["sql"],
                "result": entry.get("result"),
                "verdict": (
                    "unverifiable"
                    if entry["status"] == "error"
                    else str(verdict.get("verdict", "confirmed"))
                ),
                "note": str(verdict.get("note", entry.get("error", ""))),
            }
        )

    return {
        "insight": judged["final_insight"],
        "draft_insight": drafted["insight"],
        "verifications": verifications,
        "model": drafted["model"],
    }
