"""Tests for dataset endpoints and the collect-to-dataset flow."""


def _seed_dataset(rows: list[dict], name: str = "sales") -> None:
    from genxai.core.datasets import get_dataset_store

    get_dataset_store().append(name, rows)


def test_dataset_listing_rows_and_aggregate(client):
    _seed_dataset(
        [
            {"region": "east", "amount": 10},
            {"region": "west", "amount": 5},
            {"region": "east", "amount": 30},
        ]
    )

    listing = client.get("/api/v1/datasets").json()
    assert listing[0]["name"] == "sales"
    assert listing[0]["rows"] == 3

    page = client.get("/api/v1/datasets/sales/rows?limit=2").json()
    assert page["total"] == 3
    assert len(page["rows"]) == 2
    assert page["rows"][0]["amount"] == 30  # newest first

    aggregate = client.get(
        "/api/v1/datasets/sales/aggregate?metric=sum&field=amount&group_by=region"
    ).json()
    assert {entry["group"]: entry["value"] for entry in aggregate} == {
        "east": 40.0,
        "west": 5.0,
    }

    assert (
        client.get("/api/v1/datasets/sales/aggregate?metric=median").status_code == 422
    )


def test_dataset_delete(client):
    _seed_dataset([{"a": 1}], name="temp")
    assert client.delete("/api/v1/datasets/temp").status_code == 204
    assert client.delete("/api/v1/datasets/temp").status_code == 404


def test_dataset_write_tool_via_workflow(client):
    doc = {
        "name": "Collector",
        "nodes": [
            {"id": "start", "type": "input", "position": {"x": 0, "y": 0}, "config": {}},
            {
                "id": "collect",
                "type": "tool",
                "position": {"x": 100, "y": 0},
                "config": {
                    "tool_name": "dataset_write",
                    "tool_params": {"dataset": "collected", "rows": "{{ input.items }}"},
                },
            },
        ],
        "edges": [{"source": "start", "target": "collect"}],
    }
    created = client.post("/api/v1/workflows", json=doc).json()

    result = client.post(
        f"/api/v1/workflows/{created['id']}/test-node",
        json={"node_id": "collect", "input": {"items": [{"t": "x"}, {"t": "y"}]}},
    ).json()

    assert result["status"] == "success"
    assert result["output"]["data"]["written"] == 2
    page = client.get("/api/v1/datasets/collected/rows").json()
    assert page["total"] == 2


def test_analyze_requires_data_and_key(client, monkeypatch):
    assert (
        client.post("/api/v1/datasets/none/analyze", json={}).status_code == 404
    )

    _seed_dataset([{"a": 1}], name="tiny")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    response = client.post("/api/v1/datasets/tiny/analyze", json={})
    assert response.status_code == 409
    assert "API key" in response.json()["detail"]


def test_analyze_returns_insight_with_stubbed_llm(client, monkeypatch, mock_llm):
    _seed_dataset(
        [{"region": "east", "amount": 10}, {"region": "west", "amount": 5}],
        name="sales",
    )

    response = client.post(
        "/api/v1/datasets/sales/analyze",
        json={"question": "Which region leads?"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["insight"] == "stub response"
    assert body["sampled_rows"] == 2
    assert body["total_rows"] == 2
