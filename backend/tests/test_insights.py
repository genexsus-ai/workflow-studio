"""Tests for the run analytics (insights) endpoint."""

import time


def _calc_doc(name: str, expression: str) -> dict:
    return {
        "name": name,
        "nodes": [
            {
                "id": "calc",
                "type": "tool",
                "position": {"x": 0, "y": 0},
                "config": {
                    "tool_name": "calculator",
                    "tool_params": {"expression": expression},
                },
            },
        ],
        "edges": [],
    }


def _run_via_webhook(client, workflow_id: str) -> str:
    enabled = client.post(
        f"/api/v1/workflows/{workflow_id}/automation",
        json={"webhook_enabled": True, "schedule_enabled": False, "interval_seconds": 300},
    ).json()
    run_id = client.post(
        f"/api/v1/hooks/{enabled['automation']['webhook_token']}", json={}
    ).json()["run_id"]
    for _ in range(100):
        record = client.get(f"/api/v1/runs/{run_id}").json()
        if record["status"] not in ("queued", "running"):
            return run_id
        time.sleep(0.05)
    raise AssertionError("run did not finish")


def test_insights_empty(client):
    insights = client.get("/api/v1/insights").json()
    assert insights["totals"]["runs"] == 0
    assert insights["totals"]["success_rate"] is None
    assert len(insights["daily"]) == 14  # gap-filled axis


def test_insights_aggregates_runs(client):
    good = client.post("/api/v1/workflows", json=_calc_doc("Good", "1 + 1")).json()
    bad = client.post(
        "/api/v1/workflows", json=_calc_doc("Bad", "{{ input.missing }}")
    ).json()

    _run_via_webhook(client, good["id"])
    _run_via_webhook(client, good["id"])
    _run_via_webhook(client, bad["id"])

    insights = client.get("/api/v1/insights?days=7").json()
    totals = insights["totals"]
    assert totals["runs"] == 3
    assert totals["succeeded"] == 2
    assert totals["failed"] == 1
    assert abs(totals["success_rate"] - 2 / 3) < 1e-6

    today = insights["daily"][-1]
    assert today["succeeded"] == 2
    assert today["failed"] == 1

    by_name = {w["name"]: w for w in insights["workflows"]}
    assert by_name["Good"]["runs"] == 2
    assert by_name["Good"]["success_rate"] == 1.0
    assert by_name["Bad"]["failed"] == 1

    triggers = {t["trigger"]: t["runs"] for t in insights["triggers"]}
    assert triggers.get("webhook") == 3

    assert any(
        n["node_id"] == "calc" and n["workflow"] == "Good"
        for n in insights["slowest_nodes"]
    )


def test_insights_days_clamped(client):
    assert client.get("/api/v1/insights?days=500").json()["days"] == 90
    assert client.get("/api/v1/insights?days=0").json()["days"] == 1
