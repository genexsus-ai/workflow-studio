"""Tests for multi-agent experiments (v2-P1: plan -> explore -> clean)."""

import time


def _seed_orders(rows=None) -> None:
    from genxai.core.datasets import get_dataset_store

    get_dataset_store().append(
        "orders",
        rows
        or [
            {"region": "east", "total": 10},
            {"region": "east", "total": 10},  # duplicate to clean
            {"region": "west", "total": None},  # null to clean
            {"region": "west", "total": 5},
        ],
    )


def _stub_crew(monkeypatch, review_script=None):
    """Stub the four agent functions; returns a call log."""
    import app.experiments as exp

    calls = {"plan": 0, "explore": 0, "clean": [], "review": 0}
    reviews = review_script or []

    async def plan_stage(objective, target, context):
        calls["plan"] += 1
        assert "orders" in context or "Columns" in context
        return {"task_type": "descriptive", "target": target,
                "exploration_focus": "nulls", "cleaning_focus": "dedupe"}

    async def draft_exploration(plan, context, feedback=None):
        calls["explore"] += 1
        return [
            {"purpose": "row count", "sql": "SELECT COUNT(*) AS n FROM data"},
            {"purpose": "null totals",
             "sql": "SELECT COUNT(*) AS nulls FROM data WHERE total IS NULL"},
        ]

    async def draft_cleaning(plan, context, exploration_summary, feedback=None):
        calls["clean"].append(feedback)
        if feedback and "zero rows" in feedback:
            return {"intent": "fixed", "sql": "SELECT DISTINCT region, total FROM data WHERE total IS NOT NULL"}
        return {"intent": "dedupe and drop null totals",
                "sql": "SELECT DISTINCT region, total FROM data WHERE total IS NOT NULL"}

    async def review_artifact(kind, artifact, plan, context):
        calls["review"] += 1
        if reviews:
            return reviews.pop(0)
        return {"verdict": "approve", "reason": "looks right"}

    monkeypatch.setattr(exp, "plan_stage", plan_stage)
    monkeypatch.setattr(exp, "draft_exploration", draft_exploration)
    monkeypatch.setattr(exp, "draft_cleaning", draft_cleaning)
    monkeypatch.setattr(exp, "review_artifact", review_artifact)
    return calls


def _wait_done(client, experiment_id: str) -> dict:
    for _ in range(200):
        experiment = client.get(
            f"/api/v1/datascience/experiments/{experiment_id}"
        ).json()
        if experiment["status"] not in ("queued", "running"):
            return experiment
        time.sleep(0.05)
    raise AssertionError("experiment did not finish")


def test_experiment_pipeline_end_to_end(client, monkeypatch):
    _seed_orders()
    calls = _stub_crew(monkeypatch)

    created = client.post(
        "/api/v1/datascience/experiments",
        json={"objective": "Understand order quality", "source": "dataset:orders"},
    )
    assert created.status_code == 201
    experiment = _wait_done(client, created.json()["id"])

    assert experiment["status"] == "ok"
    stages = {s["name"]: s for s in experiment["stages"]}
    assert stages["plan"]["status"] == "ok"
    assert stages["plan"]["artifact"]["task_type"] == "descriptive"

    explore = stages["explore"]["artifact"]["queries"]
    assert explore[0]["rows"] == [{"n": 4}]
    assert explore[1]["rows"] == [{"nulls": 1}]

    clean = stages["clean"]["artifact"]
    assert clean["row_count"] == 2  # DISTINCT collapses the dup; null row dropped
    assert clean["dataset"].endswith("_clean")

    # The cleaned dataset is a real catalog dataset
    rows = client.get(f"/api/v1/datasets/{clean['dataset']}/rows").json()
    assert rows["total"] == 2
    assert all(row["total"] is not None for row in rows["rows"])

    # Review gated both artifacts
    assert calls["review"] >= 2
    assert stages["clean"]["verdicts"][0]["verdict"] == "approve"

    listing = client.get("/api/v1/datascience/experiments").json()
    assert listing[0]["stages_done"] == 3


def test_reviewer_revision_feeds_back(client, monkeypatch):
    _seed_orders()
    calls = _stub_crew(
        monkeypatch,
        review_script=[
            {"verdict": "approve", "reason": "exploration fine"},
            {"verdict": "revise", "reason": "cleaning drops the target"},
            {"verdict": "approve", "reason": "fixed"},
        ],
    )

    created = client.post(
        "/api/v1/datascience/experiments",
        json={"objective": "x", "source": "dataset:orders"},
    ).json()
    experiment = _wait_done(client, created["id"])

    assert experiment["status"] == "ok"
    clean_stage = next(s for s in experiment["stages"] if s["name"] == "clean")
    assert [v["verdict"] for v in clean_stage["verdicts"]] == ["revise", "approve"]
    # Second cleaning draft received the reviewer's reason as feedback
    assert calls["clean"][1] and "target" in calls["clean"][1]


def test_experiment_failure_recorded(client, monkeypatch):
    import app.experiments as exp

    _seed_orders()
    _stub_crew(monkeypatch)

    async def bad_plan(objective, target, context):
        raise RuntimeError("No LLM API key configured")

    monkeypatch.setattr(exp, "plan_stage", bad_plan)

    created = client.post(
        "/api/v1/datascience/experiments",
        json={"objective": "x", "source": "dataset:orders"},
    ).json()
    experiment = _wait_done(client, created["id"])

    assert experiment["status"] == "error"
    assert "API key" in experiment["error"]
    plan_stage_record = next(s for s in experiment["stages"] if s["name"] == "plan")
    assert plan_stage_record["status"] == "error"


def test_experiment_rerun_resets_and_completes(client, monkeypatch):
    _seed_orders()
    _stub_crew(monkeypatch)
    created = client.post(
        "/api/v1/datascience/experiments",
        json={"objective": "x", "source": "dataset:orders"},
    ).json()
    first = _wait_done(client, created["id"])
    assert first["status"] == "ok"

    rerun = client.post(f"/api/v1/datascience/experiments/{created['id']}/rerun")
    assert rerun.status_code == 202
    second = _wait_done(client, created["id"])
    assert second["status"] == "ok"


def test_experiment_validation(client):
    missing = client.post(
        "/api/v1/datascience/experiments",
        json={"objective": "x", "source": "ghost"},
    )
    assert missing.status_code == 404

    empty = client.post(
        "/api/v1/datascience/experiments",
        json={"objective": "  ", "source": "dataset:orders"},
    )
    assert empty.status_code == 422
