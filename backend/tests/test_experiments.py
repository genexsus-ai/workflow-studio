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

    async def draft_features(plan, clean_context, feedback=None):
        calls["features"] = calls.get("features", 0) + 1
        return {"intent": "pass through with a flag",
                "sql": "SELECT *, CASE WHEN total >= 10 THEN 1 ELSE 0 END AS big FROM data"}

    async def draft_model_plan(plan, features_context, feedback=None):
        calls["model"] = calls.get("model", 0) + 1
        return {"approach": "spec", "model_type": "linear_regression",
                "features": None, "rationale": "linear data"}

    async def draft_visualization(plan, features_context, results_summary, feedback=None):
        calls["viz"] = calls.get("viz", 0) + 1
        return (
            "import pandas as pd\n"
            "import matplotlib.pyplot as plt\n"
            "df = pd.read_parquet('data/data.parquet')\n"
            "df.plot(kind='bar')\n"
            "plt.savefig('out/figures/overview.png')\n"
        )

    async def narrate_report(summary):
        calls["report"] = calls.get("report", 0) + 1
        return {"recommendation": "iterate", "report": "# Findings\nAll good."}

    monkeypatch.setattr(exp, "plan_stage", plan_stage)
    monkeypatch.setattr(exp, "draft_exploration", draft_exploration)
    monkeypatch.setattr(exp, "draft_cleaning", draft_cleaning)
    monkeypatch.setattr(exp, "review_artifact", review_artifact)
    monkeypatch.setattr(exp, "draft_features", draft_features)
    monkeypatch.setattr(exp, "draft_model_plan", draft_model_plan)
    monkeypatch.setattr(exp, "draft_visualization", draft_visualization)
    monkeypatch.setattr(exp, "narrate_report", narrate_report)
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
    assert listing[0]["stages_done"] == 7


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


# ------------------------------------------------------------------------ P2


def test_full_pipeline_with_model_and_report(client, monkeypatch):
    from genxai.core.datasets import get_dataset_store

    # y = 2x + 1 with a noise column: regression the spec path nails
    get_dataset_store().append(
        "points", [{"x": i, "noise": (i * 7) % 5, "y": 2 * i + 1} for i in range(60)]
    )
    calls = _stub_crew(monkeypatch)

    import app.experiments as exp

    async def regression_plan(objective, target, context):
        return {"task_type": "regression", "target": "y",
                "exploration_focus": "shape", "cleaning_focus": "none needed"}

    async def keep_all_clean(plan, context, exploration_summary, feedback=None):
        return {"intent": "no-op clean", "sql": "SELECT * FROM data"}

    async def passthrough_features(plan, clean_context, feedback=None):
        return {"intent": "keep numeric columns as-is", "sql": "SELECT * FROM data"}

    monkeypatch.setattr(exp, "plan_stage", regression_plan)
    monkeypatch.setattr(exp, "draft_cleaning", keep_all_clean)
    monkeypatch.setattr(exp, "draft_features", passthrough_features)

    created = client.post(
        "/api/v1/datascience/experiments",
        json={"objective": "Predict y", "source": "dataset:points", "target": "y"},
    ).json()
    experiment = _wait_done(client, created["id"])

    assert experiment["status"] == "ok", experiment.get("error")
    stages = {s["name"]: s for s in experiment["stages"]}

    model = stages["model"]["artifact"]
    assert model["approach"] == "spec"
    assert model["model_type"] == "linear_regression"
    assert model["cross_validation"]["mean"] > 0.99
    assert model["cross_validation"]["overfit_warning"] is False
    assert model["holdout_metrics"]["r2"] > 0.99

    # Predictions materialized as a dataset
    predictions = client.get(
        f"/api/v1/datasets/{model['predictions_dataset']}/rows?limit=3"
    ).json()
    assert predictions["total"] == 60
    assert "predicted_y" in predictions["rows"][0]

    # Model registered and visible in the Models rail
    models = client.get("/api/v1/datascience/models").json()
    assert any(m["name"] == model["model_name"] for m in models)

    # Viz produced a real figure served by the files endpoint
    viz = stages["viz"]["artifact"]
    assert len(viz["figures"]) >= 1
    figure = client.get(f"/api/v1/files/{viz['figures'][0]['id']}")
    assert figure.status_code == 200
    assert figure.content[:8].startswith(b"\x89PNG")

    # Report stage carries the Metric Performance Agent's verdict
    report = stages["report"]["artifact"]
    assert report["recommendation"] in ("ship", "iterate", "abandon")
    assert report["report"].startswith("#")
    assert calls["report"] == 1


# ------------------------------------------------------------------------ P3


def _wait_status(client, experiment_id: str, statuses: tuple) -> dict:
    for _ in range(200):
        experiment = client.get(
            f"/api/v1/datascience/experiments/{experiment_id}"
        ).json()
        if experiment["status"] in statuses:
            return experiment
        time.sleep(0.05)
    raise AssertionError(f"never reached {statuses}: {experiment['status']}")


def test_human_gate_pauses_and_approval_continues(client, monkeypatch):
    _seed_orders()
    _stub_crew(monkeypatch)

    created = client.post(
        "/api/v1/datascience/experiments",
        json={"objective": "x", "source": "dataset:orders", "human_gates": True},
    ).json()

    waiting = _wait_status(client, created["id"], ("waiting",))
    clean_stage = next(s for s in waiting["stages"] if s["name"] == "clean")
    assert "cleaning transformation" in clean_stage["gate"]["question"]
    assert clean_stage["gate"]["preview"]["resulting_rows"] == 2

    resumed = client.post(
        f"/api/v1/datascience/experiments/{created['id']}/resume",
        json={"approve": True},
    )
    assert resumed.status_code == 200
    done = _wait_done(client, created["id"])
    assert done["status"] == "ok"
    gate = next(s for s in done["stages"] if s["name"] == "clean")["gate"]
    assert gate["approved"] is True


def test_human_gate_rejection_stops_pipeline(client, monkeypatch):
    _seed_orders()
    _stub_crew(monkeypatch)

    created = client.post(
        "/api/v1/datascience/experiments",
        json={"objective": "x", "source": "dataset:orders", "human_gates": True},
    ).json()
    _wait_status(client, created["id"], ("waiting",))

    client.post(
        f"/api/v1/datascience/experiments/{created['id']}/resume",
        json={"approve": False, "note": "wrong null handling"},
    )
    done = _wait_done(client, created["id"])
    assert done["status"] == "error"
    assert "human gate" in done["error"]
    assert "wrong null handling" in done["error"]

    # No dataset was materialized
    assert (
        client.get(f"/api/v1/datasets/exp_{created['id'][:8]}_clean/rows").json()["total"]
        == 0
    )


def test_resume_without_waiting_gate_conflicts(client, monkeypatch):
    _seed_orders()
    _stub_crew(monkeypatch)
    created = client.post(
        "/api/v1/datascience/experiments",
        json={"objective": "x", "source": "dataset:orders"},
    ).json()
    _wait_done(client, created["id"])
    response = client.post(
        f"/api/v1/datascience/experiments/{created['id']}/resume",
        json={"approve": True},
    )
    assert response.status_code == 409


def test_compare_experiments(client, monkeypatch):
    _seed_orders()
    _stub_crew(monkeypatch)
    first = client.post(
        "/api/v1/datascience/experiments",
        json={"objective": "first", "source": "dataset:orders"},
    ).json()
    second = client.post(
        "/api/v1/datascience/experiments",
        json={"objective": "second", "source": "dataset:orders"},
    ).json()
    _wait_done(client, first["id"])
    _wait_done(client, second["id"])

    comparison = client.get(
        f"/api/v1/datascience/experiments/{first['id']}/compare/{second['id']}"
    ).json()
    assert comparison["a"]["objective"] == "first"
    assert comparison["b"]["objective"] == "second"
    assert comparison["a"]["cleaned_rows"] == 2
    assert comparison["a"]["recommendation"] == "iterate"


def test_export_is_a_runnable_project(client, monkeypatch):
    import io
    import zipfile

    _seed_orders()
    _stub_crew(monkeypatch)
    created = client.post(
        "/api/v1/datascience/experiments",
        json={"objective": "exportable", "source": "dataset:orders"},
    ).json()
    _wait_done(client, created["id"])

    response = client.get(
        f"/api/v1/datascience/experiments/{created['id']}/export"
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"

    bundle = zipfile.ZipFile(io.BytesIO(response.content))
    names = set(bundle.namelist())
    assert {"README.md", "sql/02_clean.sql", "run.py",
            "visualization.py", "data/source_sample.parquet"} <= names
    clean_sql = bundle.read("sql/02_clean.sql").decode()
    assert "SELECT DISTINCT" in clean_sql
    readme = bundle.read("README.md").decode()
    assert "exportable" in readme and "Final report" in readme
    # The bundled sample parquet is genuine
    assert bundle.read("data/source_sample.parquet")[:4] == b"PAR1"
