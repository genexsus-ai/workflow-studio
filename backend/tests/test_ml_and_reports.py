"""Tests for Data Science P3: ML primitives and scheduled reports."""


def _seed_linear_dataset(name: str = "points", n: int = 60) -> None:
    from genxai.core.datasets import get_dataset_store

    rows = [{"x": i, "noise": (i * 7) % 5, "y": 2 * i + 1} for i in range(n)]
    get_dataset_store().append(name, rows)


def _seed_labeled_dataset(name: str = "labeled", n: int = 60) -> None:
    from genxai.core.datasets import get_dataset_store

    rows = [
        {"a": i, "b": n - i, "label": "high" if i >= n // 2 else "low"}
        for i in range(n)
    ]
    get_dataset_store().append(name, rows)


# ----------------------------------------------------------------------- ML


def test_train_linear_regression_and_predict_to_dataset(client):
    _seed_linear_dataset()

    trained = client.post(
        "/api/v1/datascience/models/train",
        json={
            "name": "line-fit",
            "source": "dataset:points",
            "target": "y",
            "model_type": "linear_regression",
        },
    )
    assert trained.status_code == 201, trained.text
    model = trained.json()
    assert model["features"] == ["x", "noise"]
    assert model["metrics"]["r2"] > 0.99  # y = 2x + 1 is perfectly linear

    listing = client.get("/api/v1/datascience/models").json()
    assert listing[0]["name"] == "line-fit"

    predicted = client.post(
        f"/api/v1/datascience/models/{model['id']}/predict",
        json={"source": "dataset:points", "dataset": "points_scored"},
    ).json()
    assert predicted["prediction_column"] == "predicted_y"
    assert predicted["written"] == 60

    rows = client.get("/api/v1/datasets/points_scored/rows?limit=5").json()
    sample = rows["rows"][0]
    assert abs(sample["predicted_y"] - sample["y"]) < 1.0

    # Predictions dataset is itself a catalog source
    sources = client.get("/api/v1/data/sources").json()
    assert any(s["id"] == "dataset:points_scored" for s in sources)


def test_train_classifier(client):
    _seed_labeled_dataset()
    trained = client.post(
        "/api/v1/datascience/models/train",
        json={
            "name": "hilo",
            "source": "dataset:labeled",
            "target": "label",
            "model_type": "random_forest_classification",
        },
    ).json()
    assert trained["metrics"]["accuracy"] >= 0.9


def test_train_validation(client):
    _seed_linear_dataset(name="pts2")
    bad_type = client.post(
        "/api/v1/datascience/models/train",
        json={"name": "x", "source": "dataset:pts2", "target": "y", "model_type": "svm"},
    )
    assert bad_type.status_code == 422

    empty_source = client.post(
        "/api/v1/datascience/models/train",
        json={"name": "x", "source": "dataset:ghost", "target": "y",
              "model_type": "linear_regression"},
    )
    assert empty_source.status_code == 422  # implicit dataset resolves but has no rows

    missing_source = client.post(
        "/api/v1/datascience/models/train",
        json={"name": "x", "source": "ghost", "target": "y",
              "model_type": "linear_regression"},
    )
    assert missing_source.status_code == 404


def test_model_delete(client):
    _seed_linear_dataset(name="pts3")
    model = client.post(
        "/api/v1/datascience/models/train",
        json={"name": "temp", "source": "dataset:pts3", "target": "y",
              "model_type": "linear_regression"},
    ).json()
    assert client.delete(f"/api/v1/datascience/models/{model['id']}").status_code == 204
    assert client.delete(f"/api/v1/datascience/models/{model['id']}").status_code == 404


def test_ml_tools_registered(client):
    palette = client.get("/api/v1/palette").json()
    tool_names = {tool["name"] for tool in palette["tools"]}
    assert {"model_train", "model_predict", "analysis_report", "source_query"} <= tool_names


# ------------------------------------------------------------------ reports


def _analysis_with_cell(client) -> dict:
    from genxai.core.datasets import get_dataset_store

    get_dataset_store().append("orders", [{"region": "east", "total": 10}])
    analysis = client.post(
        "/api/v1/datascience/analyses",
        json={"name": "Weekly revenue", "sources": {"orders": "dataset:orders"}},
    ).json()
    client.post(
        f"/api/v1/datascience/analyses/{analysis['id']}/cells/manual",
        json={
            "sql": "SELECT region, SUM(total) AS revenue FROM orders GROUP BY region",
            "question": "Revenue by region",
        },
    )
    return analysis


def test_analysis_report_tool_reruns_and_formats(client):
    analysis = _analysis_with_cell(client)
    from genxai.core.datasets import get_dataset_store

    get_dataset_store().append("orders", [{"region": "west", "total": 99}])

    doc = {
        "name": "Reporter",
        "nodes": [
            {"id": "start", "type": "input", "position": {"x": 0, "y": 0}, "config": {}},
            {
                "id": "report",
                "type": "tool",
                "position": {"x": 100, "y": 0},
                "config": {
                    "tool_name": "analysis_report",
                    "tool_params": {"analysis": analysis["id"], "rerun": True},
                },
            },
        ],
        "edges": [{"source": "start", "target": "report"}],
    }
    created = client.post("/api/v1/workflows", json=doc).json()
    result = client.post(
        f"/api/v1/workflows/{created['id']}/test-node",
        json={"node_id": "report", "input": {}},
    ).json()

    assert result["status"] == "success"
    output = result["output"]["data"]
    assert output["cells_ok"] == 1
    report = output["report"]
    assert "# Weekly revenue" in report
    assert "Revenue by region" in report
    assert "west" in report  # rerun picked up the new data


def test_schedule_report_generates_workflow(client):
    analysis = _analysis_with_cell(client)

    created = client.post(
        f"/api/v1/datascience/analyses/{analysis['id']}/schedule-report",
        json={"cron": "0 8 * * 1"},
    )
    assert created.status_code == 201
    workflow = client.get(f"/api/v1/workflows/{created.json()['workflow_id']}").json()
    assert workflow["automation"]["schedule_enabled"] is True
    assert workflow["automation"]["schedule_cron"] == "0 8 * * 1"
    assert [n["type"] for n in workflow["nodes"]] == ["trigger", "tool", "output"]

    with_slack = client.post(
        f"/api/v1/datascience/analyses/{analysis['id']}/schedule-report",
        json={"interval_seconds": 3600, "slack_credential": "team-slack",
              "slack_channel": "#reports"},
    ).json()
    workflow2 = client.get(f"/api/v1/workflows/{with_slack['workflow_id']}").json()
    assert [n["type"] for n in workflow2["nodes"]] == [
        "trigger", "tool", "connector", "output",
    ]
    notify = workflow2["nodes"][2]
    assert notify["config"]["params"]["text"] == "{{ report.data.report }}"
