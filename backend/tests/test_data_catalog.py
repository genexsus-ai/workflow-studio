"""Tests for analytics data sources: registry, adapters, endpoints."""


def _seed_dataset(rows: list[dict], name: str = "events") -> None:
    from genxai.core.datasets import get_dataset_store

    get_dataset_store().append(name, rows)


def _sql_credential(client, tmp_path, name: str = "warehouse") -> str:
    """A SQL credential over a seeded SQLite database (same adapter path as PG)."""
    import sqlalchemy

    db = tmp_path / "wh.db"
    engine = sqlalchemy.create_engine(f"sqlite:///{db}")
    with engine.begin() as conn:
        conn.execute(sqlalchemy.text("CREATE TABLE orders (region TEXT, total REAL)"))
        conn.execute(
            sqlalchemy.text(
                "INSERT INTO orders VALUES ('east', 10), ('east', 30), ('west', 5)"
            )
        )
    engine.dispose()
    client.post(
        "/api/v1/credentials",
        json={
            "name": name,
            "connector_type": "postgres",
            "config": {"connection_string": f"sqlite:///{db}"},
        },
    )
    return name


def test_datasets_appear_as_implicit_sources(client):
    _seed_dataset([{"kind": "a"}])
    sources = client.get("/api/v1/analytics/sources").json()
    implicit = next(s for s in sources if s["id"] == "dataset:events")
    assert implicit["kind"] == "dataset"
    assert implicit["rows"] == 1

    rows = client.get("/api/v1/analytics/sources/dataset:events/rows").json()
    assert rows["total"] == 1


def test_register_sql_source_and_explore(client, tmp_path):
    credential = _sql_credential(client, tmp_path)

    tables = client.get(f"/api/v1/analytics/credentials/{credential}/tables").json()
    assert tables["tables"] == ["orders"]

    created = client.post(
        "/api/v1/analytics/sources",
        json={
            "name": "Orders",
            "kind": "sql",
            "config": {"credential": credential, "table": "orders"},
        },
    )
    assert created.status_code == 201
    source_id = created.json()["id"]

    schema = client.get(f"/api/v1/analytics/sources/{source_id}/schema").json()
    assert {c["name"] for c in schema} == {"region", "total"}

    rows = client.get(
        f"/api/v1/analytics/sources/{source_id}/rows?limit=2&offset=1"
    ).json()
    assert rows["total"] == 3
    assert len(rows["rows"]) == 2

    # Aggregation is pushed down as SQL GROUP BY
    aggregate = client.get(
        f"/api/v1/analytics/sources/{source_id}/aggregate"
        "?metric=sum&field=total&group_by=region"
    ).json()
    assert {e["group"]: e["value"] for e in aggregate} == {"east": 40.0, "west": 5.0}

    listing = client.get("/api/v1/analytics/sources").json()
    assert any(s["id"] == source_id for s in listing)

    assert (
        client.delete(f"/api/v1/analytics/sources/{source_id}").status_code == 204
    )
    assert client.get(f"/api/v1/analytics/sources/{source_id}/rows").status_code == 404


def test_register_validation_failures(client, tmp_path):
    credential = _sql_credential(client, tmp_path)

    missing_table = client.post(
        "/api/v1/analytics/sources",
        json={"name": "X", "kind": "sql", "config": {"credential": credential, "table": "nope"}},
    )
    assert missing_table.status_code == 422

    bad_credential = client.post(
        "/api/v1/analytics/sources",
        json={"name": "X", "kind": "sql", "config": {"credential": "ghost", "table": "orders"}},
    )
    assert bad_credential.status_code == 404

    bad_kind = client.post(
        "/api/v1/analytics/sources",
        json={"name": "X", "kind": "file", "config": {}},
    )
    assert bad_kind.status_code == 422


def test_dataset_sources_cannot_be_deleted_via_sources(client):
    _seed_dataset([{"a": 1}])
    response = client.delete("/api/v1/analytics/sources/dataset:events")
    assert response.status_code == 422


def test_analyze_over_sql_source(client, tmp_path, mock_llm):
    credential = _sql_credential(client, tmp_path)
    created = client.post(
        "/api/v1/analytics/sources",
        json={
            "name": "Orders",
            "kind": "sql",
            "config": {"credential": credential, "table": "orders"},
        },
    ).json()

    response = client.post(
        f"/api/v1/analytics/sources/{created['id']}/analyze",
        json={"question": "Which region leads?"},
    )
    assert response.status_code == 200
    assert response.json()["insight"] == "stub response"
    assert response.json()["total_rows"] == 3


# --------------------------------------------------------------- file sources


def _xlsx_bytes(rows: list[dict], sheet: str = "Sheet1") -> bytes:
    import io

    import openpyxl

    workbook = openpyxl.Workbook()
    ws = workbook.active
    ws.title = sheet
    columns = list(rows[0].keys())
    ws.append(columns)
    for row in rows:
        ws.append([row.get(c) for c in columns])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_upload_csv_and_register_source(client):
    csv_data = b"region,total\neast,10\neast,30\nwest,5\n"
    uploaded = client.post(
        "/api/v1/files/upload",
        files={"file": ("orders.csv", csv_data, "text/csv")},
    )
    assert uploaded.status_code == 201
    body = uploaded.json()
    assert body["sheets"] is None

    created = client.post(
        "/api/v1/analytics/sources",
        json={
            "name": "Orders CSV",
            "kind": "file",
            "config": {"file_id": body["file"]["id"], "format": "csv"},
        },
    )
    assert created.status_code == 201
    source_id = created.json()["id"]

    schema = client.get(f"/api/v1/analytics/sources/{source_id}/schema").json()
    assert {"name": "total", "type": "number"} in schema  # CSV numerics coerced

    rows = client.get(f"/api/v1/analytics/sources/{source_id}/rows").json()
    assert rows["total"] == 3
    assert rows["rows"][0] == {"region": "east", "total": 10}

    aggregate = client.get(
        f"/api/v1/analytics/sources/{source_id}/aggregate"
        "?metric=avg&field=total&group_by=region"
    ).json()
    assert {e["group"]: e["value"] for e in aggregate} == {"east": 20.0, "west": 5.0}


def test_upload_xlsx_reports_sheets_and_registers(client):
    data = _xlsx_bytes(
        [{"item": "a", "qty": 2}, {"item": "b", "qty": 7}], sheet="Sales"
    )
    uploaded = client.post(
        "/api/v1/files/upload",
        files={
            "file": (
                "q2.xlsx",
                data,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    ).json()
    assert uploaded["sheets"] == ["Sales"]

    created = client.post(
        "/api/v1/analytics/sources",
        json={
            "name": "Q2",
            "kind": "file",
            "config": {
                "file_id": uploaded["file"]["id"],
                "format": "xlsx",
                "sheet": "Sales",
            },
        },
    ).json()

    rows = client.get(f"/api/v1/analytics/sources/{created['id']}/rows").json()
    assert rows["total"] == 2
    assert rows["rows"][1] == {"item": "b", "qty": 7}

    aggregate = client.get(
        f"/api/v1/analytics/sources/{created['id']}/aggregate?metric=sum&field=qty"
    ).json()
    assert aggregate[0]["value"] == 9.0


def test_file_source_validation(client):
    empty = client.post(
        "/api/v1/files/upload", files={"file": ("empty.csv", b"", "text/csv")}
    )
    assert empty.status_code == 422

    missing = client.post(
        "/api/v1/analytics/sources",
        json={"name": "X", "kind": "file", "config": {"file_id": "a" * 64, "format": "csv"}},
    )
    assert missing.status_code == 404

    bad_format = client.post(
        "/api/v1/analytics/sources",
        json={"name": "X", "kind": "file", "config": {"file_id": "a" * 64, "format": "pdf"}},
    )
    assert bad_format.status_code == 422


# ------------------------------------------------------------------------ P3


def test_custom_sql_source(client, tmp_path):
    credential = _sql_credential(client, tmp_path)

    created = client.post(
        "/api/v1/analytics/sources",
        json={
            "name": "Revenue by region",
            "kind": "sql",
            "config": {
                "credential": credential,
                "sql": "SELECT region, SUM(total) AS revenue FROM orders GROUP BY region",
            },
        },
    )
    assert created.status_code == 201
    source_id = created.json()["id"]

    rows = client.get(f"/api/v1/analytics/sources/{source_id}/rows").json()
    assert rows["total"] == 2
    revenue = {r["region"]: r["revenue"] for r in rows["rows"]}
    assert revenue == {"east": 40.0, "west": 5.0}

    # Aggregation over the wrapped query
    aggregate = client.get(
        f"/api/v1/analytics/sources/{source_id}/aggregate?metric=max&field=revenue"
    ).json()
    assert aggregate[0]["value"] == 40.0

    schema = client.get(f"/api/v1/analytics/sources/{source_id}/schema").json()
    assert {c["name"] for c in schema} == {"region", "revenue"}


def test_custom_sql_rejects_writes_and_multi_statements(client, tmp_path):
    credential = _sql_credential(client, tmp_path)

    for bad_sql in ("DELETE FROM orders", "SELECT 1; DROP TABLE orders"):
        response = client.post(
            "/api/v1/analytics/sources",
            json={
                "name": "X",
                "kind": "sql",
                "config": {"credential": credential, "sql": bad_sql},
            },
        )
        assert response.status_code == 422, bad_sql

    both = client.post(
        "/api/v1/analytics/sources",
        json={
            "name": "X",
            "kind": "sql",
            "config": {"credential": credential, "table": "orders", "sql": "SELECT 1"},
        },
    )
    assert both.status_code == 422


def test_materialize_generates_scheduled_sync_workflow(client, tmp_path):
    import time

    credential = _sql_credential(client, tmp_path)
    created = client.post(
        "/api/v1/analytics/sources",
        json={
            "name": "Orders",
            "kind": "sql",
            "config": {"credential": credential, "table": "orders"},
        },
    ).json()

    materialized = client.post(
        f"/api/v1/analytics/sources/{created['id']}/materialize",
        json={"dataset": "orders_snapshot", "mode": "replace", "interval_seconds": 3600},
    )
    assert materialized.status_code == 201
    workflow_id = materialized.json()["workflow_id"]

    doc = client.get(f"/api/v1/workflows/{workflow_id}").json()
    assert doc["automation"]["schedule_enabled"] is True
    assert doc["automation"]["interval_seconds"] == 3600
    node_types = [node["type"] for node in doc["nodes"]]
    assert node_types == ["trigger", "connector", "tool", "output"]

    # Run the generated workflow now (via webhook) and verify the dataset fills
    enabled = client.post(
        f"/api/v1/workflows/{workflow_id}/automation",
        json={
            "webhook_enabled": True,
            "schedule_enabled": False,
            "interval_seconds": 3600,
        },
    ).json()
    run_id = client.post(
        f"/api/v1/hooks/{enabled['automation']['webhook_token']}", json={}
    ).json()["run_id"]
    for _ in range(100):
        record = client.get(f"/api/v1/runs/{run_id}").json()
        if record["status"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    assert record["status"] == "success", record.get("error")

    snapshot = client.get("/api/v1/datasets/orders_snapshot/rows").json()
    assert snapshot["total"] == 3


def test_materialize_rejects_non_sql_sources(client):
    _seed_dataset([{"a": 1}])
    response = client.post(
        "/api/v1/analytics/sources/dataset:events/materialize",
        json={"dataset": "copy"},
    )
    assert response.status_code == 422


# ------------------------------------------------------------ deferred items


def test_federated_duckdb_source_joins_across_kinds(client, tmp_path):
    # Source 1: an internal dataset
    _seed_dataset(
        [
            {"region": "east", "manager": "ana"},
            {"region": "west", "manager": "bo"},
        ],
        name="regions",
    )
    # Source 2: a SQL table (sqlite)
    credential = _sql_credential(client, tmp_path)
    orders = client.post(
        "/api/v1/analytics/sources",
        json={
            "name": "Orders",
            "kind": "sql",
            "config": {"credential": credential, "table": "orders"},
        },
    ).json()

    created = client.post(
        "/api/v1/analytics/sources",
        json={
            "name": "Revenue by manager",
            "kind": "duckdb",
            "config": {
                "sql": (
                    "SELECT r.manager, SUM(o.total) AS revenue "
                    "FROM orders o JOIN regions r USING (region) "
                    "GROUP BY r.manager ORDER BY revenue DESC"
                ),
                "sources": {"orders": orders["id"], "regions": "dataset:regions"},
            },
        },
    )
    assert created.status_code == 201, created.text
    source_id = created.json()["id"]

    rows = client.get(f"/api/v1/analytics/sources/{source_id}/rows").json()
    assert rows["rows"] == [
        {"manager": "ana", "revenue": 40.0},
        {"manager": "bo", "revenue": 5.0},
    ]

    schema = client.get(f"/api/v1/analytics/sources/{source_id}/schema").json()
    assert {"name": "revenue", "type": "number"} in schema

    aggregate = client.get(
        f"/api/v1/analytics/sources/{source_id}/aggregate?metric=max&field=revenue"
    ).json()
    assert aggregate[0]["value"] == 40.0


def test_federated_validation(client):
    no_such = client.post(
        "/api/v1/analytics/sources",
        json={
            "name": "X",
            "kind": "duckdb",
            "config": {"sql": "SELECT * FROM t", "sources": {"t": "ghost"}},
        },
    )
    assert no_such.status_code == 404

    write = client.post(
        "/api/v1/analytics/sources",
        json={
            "name": "X",
            "kind": "duckdb",
            "config": {"sql": "DROP TABLE x", "sources": {}},
        },
    )
    assert write.status_code == 422


def test_gsheet_source_with_stubbed_api(client, monkeypatch):
    import app.data_catalog as sources_module

    client.post(
        "/api/v1/credentials",
        json={
            "name": "my-google",
            "connector_type": "google_workspace",
            "config": {"access_token": "tok", "auth_kind": "oauth2", "provider": "google"},
        },
    )

    def fake_fetch(credential, spreadsheet_id, range_):
        assert credential == "my-google"
        assert spreadsheet_id == "sheet123"
        return [
            ["region", "total"],
            ["east", 40],
            ["west", 5],
        ]

    monkeypatch.setattr(sources_module, "fetch_sheet_values", fake_fetch)
    sources_module._sheets_cache.clear()

    created = client.post(
        "/api/v1/analytics/sources",
        json={
            "name": "Q2 sheet",
            "kind": "gsheet",
            "config": {
                "credential": "my-google",
                "spreadsheet_id": "sheet123",
                "range": "Sheet1!A1:B3",
            },
        },
    )
    assert created.status_code == 201, created.text
    source_id = created.json()["id"]

    rows = client.get(f"/api/v1/analytics/sources/{source_id}/rows").json()
    assert rows["total"] == 2
    assert rows["rows"][0] == {"region": "east", "total": 40}

    aggregate = client.get(
        f"/api/v1/analytics/sources/{source_id}/aggregate"
        "?metric=sum&field=total&group_by=region"
    ).json()
    assert {e["group"]: e["value"] for e in aggregate} == {"east": 40.0, "west": 5.0}


def test_gsheet_requires_existing_credential(client):
    response = client.post(
        "/api/v1/analytics/sources",
        json={
            "name": "X",
            "kind": "gsheet",
            "config": {
                "credential": "ghost",
                "spreadsheet_id": "abc",
                "range": "A1:B2",
            },
        },
    )
    assert response.status_code == 404


def test_s3_source_with_stubbed_fetch(client, monkeypatch):
    import app.data_catalog as sources_module

    client.post(
        "/api/v1/credentials",
        json={
            "name": "my-s3",
            "connector_type": "s3",
            "config": {"access_key_id": "ak", "secret_access_key": "sk", "region": "us-east-1"},
        },
    )

    def fake_fetch(credential, bucket, key):
        assert credential == "my-s3"
        assert (bucket, key) == ("reports", "q2.csv")
        return b"region,total\neast,10\nwest,5\n"

    monkeypatch.setattr(sources_module, "fetch_s3_object", fake_fetch)
    sources_module._s3_cache.clear()

    created = client.post(
        "/api/v1/analytics/sources",
        json={
            "name": "Q2 (S3)",
            "kind": "s3",
            "config": {"credential": "my-s3", "bucket": "reports", "key": "q2.csv"},
        },
    )
    assert created.status_code == 201, created.text
    source_id = created.json()["id"]
    assert created.json()["config"]["format"] == "csv"  # inferred from key

    rows = client.get(f"/api/v1/analytics/sources/{source_id}/rows").json()
    assert rows["total"] == 2
    assert rows["rows"][0] == {"region": "east", "total": 10}

    aggregate = client.get(
        f"/api/v1/analytics/sources/{source_id}/aggregate?metric=sum&field=total"
    ).json()
    assert aggregate[0]["value"] == 15.0


def test_s3_source_requires_existing_credential(client):
    response = client.post(
        "/api/v1/analytics/sources",
        json={
            "name": "X",
            "kind": "s3",
            "config": {"credential": "ghost", "bucket": "b", "key": "k.csv"},
        },
    )
    assert response.status_code == 404


def test_s3_connector_in_catalog(client):
    palette = client.get("/api/v1/palette").json()
    s3 = next(c for c in palette["connectors"] if c["type"] == "s3")
    assert {"list_objects", "get_object", "put_object"} <= set(s3["actions"])
    field_names = {f["name"] for f in s3["credential_fields"]}
    assert {"access_key_id", "secret_access_key", "region"} <= field_names


# ------------------------------------------------------------- data catalog


def test_data_alias_routes(client):
    _seed_dataset([{"a": 1}])
    # Same handlers under both prefixes
    analytics = client.get("/api/v1/analytics/sources").json()
    data = client.get("/api/v1/data/sources").json()
    assert analytics == data
    rows = client.get("/api/v1/data/sources/dataset:events/rows").json()
    assert rows["total"] == 1


def test_sample_endpoint(client):
    _seed_dataset([{"n": i} for i in range(50)])
    sample = client.get("/api/v1/data/sources/dataset:events/sample?n=10").json()
    assert len(sample["rows"]) == 10
    assert sample["total"] == 50
    values = {row["n"] for row in sample["rows"]}
    assert values <= set(range(50))


def test_profile_endpoint(client):
    _seed_dataset(
        [
            {"region": "east", "total": 10},
            {"region": "east", "total": 30},
            {"region": "west", "total": None},
        ]
    )
    profile = client.get("/api/v1/data/sources/dataset:events/profile").json()
    assert profile["total_rows"] == 3
    by_name = {c["name"]: c for c in profile["columns"]}
    assert by_name["total"]["type"] == "number"
    assert by_name["total"]["nulls"] == 1
    assert by_name["total"]["min"] == 10.0
    assert by_name["total"]["max"] == 30.0
    assert by_name["total"]["mean"] == 20.0
    assert by_name["region"]["type"] == "string"
    assert by_name["region"]["top_values"][0]["value"] == "east"


def test_export_csv_and_parquet(client):
    _seed_dataset([{"region": "east", "total": 10}, {"region": "west", "total": 5}])

    csv_response = client.get("/api/v1/data/sources/dataset:events/export?format=csv")
    assert csv_response.status_code == 200
    assert "events.csv" in csv_response.headers["content-disposition"]
    lines = csv_response.text.strip().splitlines()
    assert lines[0].split(",")[0] in ("region", "total")
    assert len(lines) == 3

    parquet_response = client.get(
        "/api/v1/data/sources/dataset:events/export?format=parquet"
    )
    assert parquet_response.status_code == 200
    assert parquet_response.content[:4] == b"PAR1"  # parquet magic bytes

    # Parquet round-trips through duckdb
    import tempfile
    from pathlib import Path

    import duckdb

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "x.parquet"
        path.write_bytes(parquet_response.content)
        con = duckdb.connect()
        total = con.execute(
            f"SELECT SUM(total) FROM read_parquet('{path}')"
        ).fetchone()[0]
        con.close()
    assert total == 15

    assert (
        client.get("/api/v1/data/sources/dataset:events/export?format=xml").status_code
        == 422
    )


def test_source_query_tool_via_workflow(client):
    _seed_dataset([{"title": "hello"}, {"title": "world"}], name="articles")

    doc = {
        "name": "Reader",
        "nodes": [
            {"id": "start", "type": "input", "position": {"x": 0, "y": 0}, "config": {}},
            {
                "id": "read",
                "type": "tool",
                "position": {"x": 100, "y": 0},
                "config": {
                    "tool_name": "source_query",
                    "tool_params": {"source": "dataset:articles", "limit": 10},
                },
            },
        ],
        "edges": [{"source": "start", "target": "read"}],
    }
    created = client.post("/api/v1/workflows", json=doc).json()
    result = client.post(
        f"/api/v1/workflows/{created['id']}/test-node",
        json={"node_id": "read", "input": {}},
    ).json()

    assert result["status"] == "success"
    output = result["output"]["data"]
    assert output["total"] == 2
    assert {row["title"] for row in output["rows"]} == {"hello", "world"}

    # Lookup by display name also works
    result2 = client.post(
        f"/api/v1/workflows/{created['id']}/test-node",
        json={"node_id": "read", "input": {}, "upstream": {}},
    ).json()
    assert result2["status"] == "success"
