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
