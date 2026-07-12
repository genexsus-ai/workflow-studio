"""Tests for the Data Science app: analyses, cells, agent loop."""


def _seed_dataset(rows: list[dict], name: str = "orders") -> None:
    from genxai.core.datasets import get_dataset_store

    get_dataset_store().append(name, rows)


def _analysis(client, sources=None) -> dict:
    return client.post(
        "/api/v1/datascience/analyses",
        json={"name": "Revenue study", "sources": sources or {}},
    ).json()


def test_analysis_crud(client):
    _seed_dataset([{"region": "east", "total": 10}])

    created = _analysis(client, {"orders": "dataset:orders"})
    assert created["sources"] == {"orders": "dataset:orders"}

    listing = client.get("/api/v1/datascience/analyses").json()
    assert listing[0]["name"] == "Revenue study"
    assert listing[0]["cell_count"] == 0

    patched = client.patch(
        f"/api/v1/datascience/analyses/{created['id']}",
        json={"name": "Renamed"},
    ).json()
    assert patched["name"] == "Renamed"

    assert (
        client.delete(f"/api/v1/datascience/analyses/{created['id']}").status_code
        == 204
    )
    assert (
        client.get(f"/api/v1/datascience/analyses/{created['id']}").status_code == 404
    )


def test_analysis_source_validation(client):
    bad = client.post(
        "/api/v1/datascience/analyses",
        json={"name": "X", "sources": {"t": "ghost"}},
    )
    assert bad.status_code == 404

    bad_alias = client.post(
        "/api/v1/datascience/analyses",
        json={"name": "X", "sources": {"not-an-identifier!": "dataset:orders"}},
    )
    assert bad_alias.status_code == 422


def test_manual_cell_and_rerun_reflects_new_data(client):
    _seed_dataset([{"region": "east", "total": 10}, {"region": "west", "total": 5}])
    analysis = _analysis(client, {"orders": "dataset:orders"})

    cell = client.post(
        f"/api/v1/datascience/analyses/{analysis['id']}/cells/manual",
        json={"sql": "SELECT region, SUM(total) AS rev FROM orders GROUP BY region ORDER BY rev DESC"},
    ).json()
    assert cell["status"] == "ok"
    assert cell["result_rows"][0] == {"region": "east", "rev": 10.0}
    assert cell["columns"] == ["region", "rev"]

    # New data arrives; rerun re-executes the stored SQL
    _seed_dataset([{"region": "west", "total": 100}])
    rerun = client.post(
        f"/api/v1/datascience/analyses/{analysis['id']}/cells/{cell['id']}/rerun"
    ).json()
    assert rerun["result_rows"][0] == {"region": "west", "rev": 105.0}


def test_manual_cell_rejects_writes(client):
    _seed_dataset([{"a": 1}])
    analysis = _analysis(client, {"orders": "dataset:orders"})
    cell = client.post(
        f"/api/v1/datascience/analyses/{analysis['id']}/cells/manual",
        json={"sql": "DROP TABLE orders"},
    ).json()
    assert cell["status"] == "error"
    assert "SELECT/WITH" in cell["error"]


def test_agent_cell_with_stubbed_llm(client, monkeypatch):
    import app.datascience as ds

    _seed_dataset([{"region": "east", "total": 10}, {"region": "west", "total": 5}])
    analysis = _analysis(client, {"orders": "dataset:orders"})

    async def fake_plan(question, sources_context, prior_context, error=None):
        assert "orders" in sources_context
        return {
            "sql": "SELECT region, SUM(total) AS rev FROM orders GROUP BY region",
            "chart": {"type": "bar", "x": "region", "y": "rev"},
            "model": "stub",
        }

    async def fake_narrate(question, sql, columns, rows):
        return "East leads with 10."

    monkeypatch.setattr(ds, "plan_cell", fake_plan)
    monkeypatch.setattr(ds, "narrate_cell", fake_narrate)

    cell = client.post(
        f"/api/v1/datascience/analyses/{analysis['id']}/cells",
        json={"question": "Which region leads?"},
    ).json()

    assert cell["status"] == "ok"
    assert cell["attempts"] == 1
    assert cell["narrative"] == "East leads with 10."
    assert cell["chart"] == {"type": "bar", "x": "region", "y": "rev"}
    assert {row["region"] for row in cell["result_rows"]} == {"east", "west"}

    # Cell persisted on the analysis
    stored = client.get(f"/api/v1/datascience/analyses/{analysis['id']}").json()
    assert len(stored["cells"]) == 1


def test_agent_cell_self_repairs_bad_sql(client, monkeypatch):
    import app.datascience as ds

    _seed_dataset([{"region": "east", "total": 10}])
    analysis = _analysis(client, {"orders": "dataset:orders"})

    attempts = []

    async def flaky_plan(question, sources_context, prior_context, error=None):
        attempts.append(error)
        if error is None:
            return {"sql": "SELECT nope FROM missing_table", "chart": None, "model": "stub"}
        return {"sql": "SELECT COUNT(*) AS n FROM orders", "chart": None, "model": "stub"}

    async def fake_narrate(question, sql, columns, rows):
        return "One row."

    monkeypatch.setattr(ds, "plan_cell", flaky_plan)
    monkeypatch.setattr(ds, "narrate_cell", fake_narrate)

    cell = client.post(
        f"/api/v1/datascience/analyses/{analysis['id']}/cells",
        json={"question": "How many orders?"},
    ).json()

    assert cell["status"] == "ok"
    assert cell["attempts"] == 2
    assert attempts[0] is None and attempts[1]  # second call carried the error
    assert cell["result_rows"] == [{"n": 1}]


def test_agent_cell_requires_api_key(client, monkeypatch):
    _seed_dataset([{"a": 1}])
    analysis = _analysis(client, {"orders": "dataset:orders"})
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    response = client.post(
        f"/api/v1/datascience/analyses/{analysis['id']}/cells",
        json={"question": "anything"},
    )
    assert response.status_code == 409


def test_cell_edit_and_delete(client):
    _seed_dataset([{"total": 10}])
    analysis = _analysis(client, {"orders": "dataset:orders"})
    cell = client.post(
        f"/api/v1/datascience/analyses/{analysis['id']}/cells/manual",
        json={"sql": "SELECT COUNT(*) AS n FROM orders"},
    ).json()

    edited = client.patch(
        f"/api/v1/datascience/analyses/{analysis['id']}/cells/{cell['id']}",
        json={"sql": "SELECT SUM(total) AS s FROM orders"},
    ).json()
    assert edited["result_rows"] == [{"s": 10.0}]

    assert (
        client.delete(
            f"/api/v1/datascience/analyses/{analysis['id']}/cells/{cell['id']}"
        ).status_code
        == 204
    )
    stored = client.get(f"/api/v1/datascience/analyses/{analysis['id']}").json()
    assert stored["cells"] == []


def test_cells_require_bound_sources(client):
    analysis = _analysis(client)
    response = client.post(
        f"/api/v1/datascience/analyses/{analysis['id']}/cells/manual",
        json={"sql": "SELECT 1"},
    )
    assert response.status_code == 422


# ------------------------------------------------------------------------ P2


def test_rerun_all_refreshes_every_cell(client):
    _seed_dataset([{"total": 10}])
    analysis = _analysis(client, {"orders": "dataset:orders"})
    for sql in ("SELECT COUNT(*) AS n FROM orders", "SELECT SUM(total) AS s FROM orders"):
        client.post(
            f"/api/v1/datascience/analyses/{analysis['id']}/cells/manual",
            json={"sql": sql},
        )

    _seed_dataset([{"total": 90}])  # data changes
    refreshed = client.post(
        f"/api/v1/datascience/analyses/{analysis['id']}/rerun"
    ).json()

    assert refreshed["cells"][0]["result_rows"] == [{"n": 2}]
    assert refreshed["cells"][1]["result_rows"] == [{"s": 100.0}]


def test_materialize_cell_creates_dataset(client):
    _seed_dataset(
        [{"region": "east", "total": 10}, {"region": "east", "total": 30},
         {"region": "west", "total": 5}]
    )
    analysis = _analysis(client, {"orders": "dataset:orders"})
    cell = client.post(
        f"/api/v1/datascience/analyses/{analysis['id']}/cells/manual",
        json={"sql": "SELECT region, SUM(total) AS revenue FROM orders GROUP BY region"},
    ).json()

    result = client.post(
        f"/api/v1/datascience/analyses/{analysis['id']}/cells/{cell['id']}/materialize",
        json={"dataset": "revenue_by_region", "mode": "replace"},
    )
    assert result.status_code == 201
    assert result.json()["written"] == 2

    # The materialized cell is now a first-class dataset (and catalog source)
    rows = client.get("/api/v1/datasets/revenue_by_region/rows").json()
    assert rows["total"] == 2
    revenue = {r["region"]: r["revenue"] for r in rows["rows"]}
    assert revenue == {"east": 40.0, "west": 5.0}

    sources = client.get("/api/v1/data/sources").json()
    assert any(s["id"] == "dataset:revenue_by_region" for s in sources)

    # replace mode keeps it a mirror on re-materialize
    client.post(
        f"/api/v1/datascience/analyses/{analysis['id']}/cells/{cell['id']}/materialize",
        json={"dataset": "revenue_by_region", "mode": "replace"},
    )
    assert client.get("/api/v1/datasets/revenue_by_region/rows").json()["total"] == 2


def test_materialize_validation(client):
    _seed_dataset([{"a": 1}])
    analysis = _analysis(client, {"orders": "dataset:orders"})
    cell = client.post(
        f"/api/v1/datascience/analyses/{analysis['id']}/cells/manual",
        json={"sql": "SELECT 1 AS x"},
    ).json()

    bad_name = client.post(
        f"/api/v1/datascience/analyses/{analysis['id']}/cells/{cell['id']}/materialize",
        json={"dataset": "../evil", "mode": "replace"},
    )
    assert bad_name.status_code == 422

    bad_mode = client.post(
        f"/api/v1/datascience/analyses/{analysis['id']}/cells/{cell['id']}/materialize",
        json={"dataset": "ok_name", "mode": "upsert"},
    )
    assert bad_mode.status_code == 422
