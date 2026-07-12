"""End-to-end test: postgres connector through credentials + workflow nodes."""


def test_postgres_in_catalog(client):
    palette = client.get("/api/v1/palette").json()
    postgres = next(c for c in palette["connectors"] if c["type"] == "postgres")
    assert postgres["label"] == "PostgreSQL"
    assert postgres["credential_fields"][0]["name"] == "connection_string"
    assert {"query", "execute", "insert_rows", "list_tables"} <= set(
        postgres["actions"]
    )


def test_connector_extract_and_store_roundtrip(client, tmp_path):
    # SQLite URL exercises the same connector code path as PostgreSQL
    created = client.post(
        "/api/v1/credentials",
        json={
            "name": "warehouse",
            "connector_type": "postgres",
            "config": {"connection_string": f"sqlite:///{tmp_path}/wh.db"},
        },
    )
    assert created.status_code == 201

    doc = {
        "name": "DB flow",
        "nodes": [
            {"id": "start", "type": "input", "position": {"x": 0, "y": 0}, "config": {}},
            {
                "id": "create",
                "type": "connector",
                "position": {"x": 100, "y": 0},
                "config": {
                    "connector": "postgres",
                    "action": "execute",
                    "credential": "warehouse",
                    "params": {"sql": "CREATE TABLE items (title TEXT, score INTEGER)"},
                },
            },
            {
                "id": "load",
                "type": "connector",
                "position": {"x": 200, "y": 0},
                "config": {
                    "connector": "postgres",
                    "action": "insert_rows",
                    "credential": "warehouse",
                    "params": {"table": "items", "rows": "{{ input.items }}"},
                },
            },
            {
                "id": "extract",
                "type": "connector",
                "position": {"x": 300, "y": 0},
                "config": {
                    "connector": "postgres",
                    "action": "query",
                    "credential": "warehouse",
                    "params": {"sql": "SELECT title, score FROM items ORDER BY score DESC"},
                },
            },
        ],
        "edges": [
            {"source": "start", "target": "create"},
            {"source": "create", "target": "load"},
            {"source": "load", "target": "extract"},
        ],
    }
    workflow = client.post("/api/v1/workflows", json=doc).json()

    import time

    enabled = client.post(
        f"/api/v1/workflows/{workflow['id']}/automation",
        json={"webhook_enabled": True, "schedule_enabled": False, "interval_seconds": 300},
    ).json()
    run_id = client.post(
        f"/api/v1/hooks/{enabled['automation']['webhook_token']}",
        json={"items": [{"title": "a", "score": 1}, {"title": "b", "score": 9}]},
    ).json()["run_id"]

    record = {}
    for _ in range(100):
        record = client.get(f"/api/v1/runs/{run_id}").json()
        if record["status"] not in ("queued", "running"):
            break
        time.sleep(0.05)

    assert record["status"] == "success", record.get("error")
    results = record["result"]["node_results"]
    assert results["load"]["output"]["data"]["inserted"] == 2
    extracted = results["extract"]["output"]["data"]
    assert extracted["rows"] == [
        {"title": "b", "score": 9},
        {"title": "a", "score": 1},
    ]
