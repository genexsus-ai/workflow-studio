"""Tests for the Analytics / Data Science first-run examples."""

from app.demo_seed import ANALYSIS_NAME, DATASET, SOURCE_NAME, seed_demo_data


def test_demo_seed_populates_all_three_apps(client):
    seed_demo_data()

    # Analytics: dataset appears as an implicit source with real rows
    sources = client.get("/api/v1/data/sources").json()
    dataset_source = next(s for s in sources if s["id"] == f"dataset:{DATASET}")
    assert dataset_source["rows"] == 12 * 4 * 3  # months x regions x products

    profile = client.get(f"/api/v1/data/sources/dataset:{DATASET}/profile").json()
    by_name = {c["name"]: c for c in profile["columns"]}
    assert by_name["revenue"]["type"] == "number"
    assert by_name["region"]["distinct"] == 4

    # Aggregation over the demo data behaves sensibly
    aggregate = client.get(
        f"/api/v1/data/sources/dataset:{DATASET}/aggregate"
        "?metric=sum&field=revenue&group_by=region"
    ).json()
    assert len(aggregate) == 4
    assert all(entry["value"] > 0 for entry in aggregate)

    # Federated example source works end to end
    federated = next(s for s in sources if s["name"] == SOURCE_NAME)
    rows = client.get(f"/api/v1/data/sources/{federated['id']}/rows").json()
    assert rows["total"] == 4
    assert rows["rows"][0]["revenue"] >= rows["rows"][-1]["revenue"]

    # Data Science: the example analysis has three pre-run cells
    analyses = client.get("/api/v1/datascience/analyses").json()
    example = next(a for a in analyses if a["name"] == ANALYSIS_NAME)
    detail = client.get(f"/api/v1/datascience/analyses/{example['id']}").json()
    assert len(detail["cells"]) == 3
    assert all(cell["status"] == "ok" for cell in detail["cells"])
    assert detail["cells"][0]["result_rows"][0]["revenue"] > 0
    trend = detail["cells"][1]
    assert [row["month"] for row in trend["result_rows"]][:2] == ["2026-01", "2026-02"]


def test_demo_seed_is_idempotent(client):
    seed_demo_data()
    seed_demo_data()

    sources = client.get("/api/v1/data/sources").json()
    assert sum(1 for s in sources if s["name"] == SOURCE_NAME) == 1
    dataset_source = next(s for s in sources if s["id"] == f"dataset:{DATASET}")
    assert dataset_source["rows"] == 144  # not duplicated

    analyses = client.get("/api/v1/datascience/analyses").json()
    assert sum(1 for a in analyses if a["name"] == ANALYSIS_NAME) == 1


def test_churn_dataset_supports_model_development(client):
    from app.demo_seed import CHURN_DATASET, seed_demo_data

    seed_demo_data()

    rows = client.get(f"/api/v1/data/sources/dataset:{CHURN_DATASET}/rows?limit=500").json()
    assert rows["total"] == 400
    labels = {row["churned"] for row in rows["rows"]}
    assert labels == {"yes", "no"}  # both classes present
    churn_rate = sum(1 for r in rows["rows"] if r["churned"] == "yes") / len(rows["rows"])
    assert 0.15 < churn_rate < 0.85  # not degenerate

    # A classifier actually learns the signal (noise keeps it below perfect)
    trained = client.post(
        "/api/v1/datascience/models/train",
        json={
            "name": "churn-demo",
            "source": f"dataset:{CHURN_DATASET}",
            "target": "churned",
            "model_type": "random_forest_classification",
        },
    )
    assert trained.status_code == 201, trained.text
    metrics = trained.json()["metrics"]
    assert metrics["accuracy"] >= 0.8
    # numeric features auto-selected; ids and plan strings excluded
    assert "tenure_months" in trained.json()["features"]
    assert "customer_id" not in trained.json()["features"]

    # Predictions materialize back into the catalog
    predicted = client.post(
        f"/api/v1/datascience/models/{trained.json()['id']}/predict",
        json={"source": f"dataset:{CHURN_DATASET}", "dataset": "churn_scored"},
    ).json()
    assert predicted["written"] == 400
    scored = client.get("/api/v1/datasets/churn_scored/rows?limit=2").json()
    assert scored["rows"][0]["predicted_churned"] in ("yes", "no")
