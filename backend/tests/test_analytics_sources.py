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
