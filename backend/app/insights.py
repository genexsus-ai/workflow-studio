"""Run analytics: aggregate the execution store into an insights summary."""

from __future__ import annotations

import statistics
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any

from app.runner import get_execution_store

TERMINAL_FAILURE_STATUSES = {"error", "interrupted"}


def _parse_when(value: Any) -> datetime | None:
    """Parse a record timestamp; naive values are local time (they're stamped
    with datetime.now()), so daily buckets follow the server's calendar."""
    parsed: datetime | None = None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    if parsed is None:
        return None
    return parsed.astimezone()  # naive -> assume local; aware -> convert


def _duration_ms(record: Any) -> float | None:
    started = _parse_when(record.started_at)
    completed = _parse_when(record.completed_at)
    if started is None or completed is None:
        return None
    return max((completed - started).total_seconds() * 1000.0, 0.0)


def _normalize_trigger(raw: Any) -> str:
    trigger = str(raw or "manual")
    for prefix in ("retry:", "rerun:", "error:"):
        if trigger.startswith(prefix):
            return prefix.rstrip(":")
    return trigger


def compute_insights(days: int = 14) -> dict[str, Any]:
    """Aggregate run records from the last ``days`` days."""
    now = datetime.now().astimezone()
    cutoff = now - timedelta(days=days)
    records = [
        record
        for record in getattr(get_execution_store(), "_records", {}).values()
        if (_parse_when(record.started_at) or now) >= cutoff
    ]

    succeeded = sum(1 for r in records if r.status == "success")
    failed = sum(1 for r in records if r.status in TERMINAL_FAILURE_STATUSES)
    durations = [d for r in records if (d := _duration_ms(r)) is not None]

    # Runs per day, gaps filled so the chart has a continuous axis
    daily_counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {"succeeded": 0, "failed": 0, "other": 0}
    )
    for record in records:
        started = _parse_when(record.started_at)
        if started is None:
            continue
        day = started.date().isoformat()
        if record.status == "success":
            daily_counts[day]["succeeded"] += 1
        elif record.status in TERMINAL_FAILURE_STATUSES:
            daily_counts[day]["failed"] += 1
        else:
            daily_counts[day]["other"] += 1
    daily = []
    for offset in range(days - 1, -1, -1):
        day = (now - timedelta(days=offset)).date().isoformat()
        daily.append({"date": day, **daily_counts[day]})

    # Per-workflow rollup
    by_workflow: dict[str, list[Any]] = defaultdict(list)
    for record in records:
        by_workflow[record.workflow].append(record)
    workflows = []
    for name, group in by_workflow.items():
        group_durations = [d for r in group if (d := _duration_ms(r)) is not None]
        group_success = sum(1 for r in group if r.status == "success")
        last = max(
            (_parse_when(r.started_at) for r in group if _parse_when(r.started_at)),
            default=None,
        )
        workflows.append(
            {
                "name": name,
                "runs": len(group),
                "succeeded": group_success,
                "failed": sum(
                    1 for r in group if r.status in TERMINAL_FAILURE_STATUSES
                ),
                "success_rate": group_success / len(group) if group else 0.0,
                "avg_duration_ms": (
                    sum(group_durations) / len(group_durations)
                    if group_durations
                    else None
                ),
                "last_run_at": last.isoformat() if last else None,
            }
        )
    workflows.sort(key=lambda w: w["runs"], reverse=True)

    # Trigger breakdown
    trigger_counts: dict[str, int] = defaultdict(int)
    for record in records:
        trigger_counts[_normalize_trigger((record.metadata or {}).get("trigger"))] += 1
    triggers = sorted(
        ({"trigger": k, "runs": v} for k, v in trigger_counts.items()),
        key=lambda t: t["runs"],
        reverse=True,
    )

    # Slowest nodes across runs (by average recorded duration)
    node_durations: dict[tuple[str, str], list[float]] = defaultdict(list)
    for record in records:
        node_results = (record.result or {}).get("node_results") or {}
        for node_id, entry in node_results.items():
            duration = entry.get("duration_ms")
            if isinstance(duration, (int, float)):
                node_durations[(record.workflow, node_id)].append(float(duration))
    slowest_nodes = sorted(
        (
            {
                "workflow": workflow,
                "node_id": node_id,
                "avg_duration_ms": sum(values) / len(values),
                "runs": len(values),
            }
            for (workflow, node_id), values in node_durations.items()
        ),
        key=lambda n: n["avg_duration_ms"],
        reverse=True,
    )[:10]

    return {
        "days": days,
        "totals": {
            "runs": len(records),
            "succeeded": succeeded,
            "failed": failed,
            "other": len(records) - succeeded - failed,
            "success_rate": succeeded / len(records) if records else None,
            "median_duration_ms": (
                statistics.median(durations) if durations else None
            ),
        },
        "daily": daily,
        "workflows": workflows,
        "triggers": triggers,
        "slowest_nodes": slowest_nodes,
    }
