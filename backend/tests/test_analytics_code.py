"""Tests for the analytics dashboard crew (Planner + Coder + Reviewer + Reporter)."""

import asyncio

import pytest

GOOD_CODE = """
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

df = pd.read_parquet("data/data.parquet")
totals = df.groupby("region", as_index=False)["total"].sum()
totals.to_csv("out/datasets/region_totals.csv", index=False)
totals.plot(kind="bar", x="region", y="total")
plt.savefig("out/figures/totals.png")
print("regions:", len(totals))
"""


def _seed_orders() -> None:
    from genxai.core.datasets import get_dataset_store

    get_dataset_store().append(
        "orders",
        [
            {"region": "east", "total": 10},
            {"region": "east", "total": 7},
            {"region": "west", "total": 5},
        ],
    )


# ------------------------------------------------- code crew (report engine)


def test_code_crew_review_loop_and_materialization(client, monkeypatch):
    import app.analytics_code as ac

    _seed_orders()
    calls = {"draft": [], "review": 0}

    async def draft(request, context, feedback=None):
        calls["draft"].append(feedback)
        assert "data.parquet" in context
        return GOOD_CODE

    async def review(code, request, context):
        calls["review"] += 1
        if calls["review"] == 1:
            return {"verdict": "revise", "reason": "print totals to stdout too"}
        return {"verdict": "approve", "reason": "correct"}

    monkeypatch.setattr(ac, "draft_analysis_code", draft)
    monkeypatch.setattr(ac, "review_analysis_code", review)

    body = asyncio.run(ac.run_code_analysis("dataset:orders", "totals by region"))

    # Review loop ran: initial draft, one revision round, then approval
    assert len(calls["draft"]) == 2
    assert calls["draft"][1] == "print totals to stdout too"
    assert [v["verdict"] for v in body["review"]] == ["revise", "approve"]

    # Derived table materialized as a dataset (and thus a catalog source)
    assert body["datasets"] == {"region_totals": 2}
    rows = client.get("/api/v1/datasets/region_totals/rows").json()
    east = next(r for r in rows["rows"] if r["region"] == "east")
    assert east["total"] == 17

    # Figure stored and served; stdout captured
    assert len(body["figures"]) == 1
    png = client.get(f"/api/v1/files/{body['figures'][0]['id']}")
    assert png.status_code == 200
    assert png.content[:8].startswith(b"\x89PNG")
    assert "regions:" in body["stdout"]


def test_code_crew_self_repairs_failing_code(client, monkeypatch):
    import app.analytics_code as ac

    _seed_orders()
    attempts = {"n": 0}

    async def draft(request, context, feedback=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            return "import pandas as pd\nraise SystemExit(2)\n"
        assert feedback and "failed to run" in feedback
        return GOOD_CODE

    async def review(code, request, context):
        return {"verdict": "approve", "reason": "ok"}

    monkeypatch.setattr(ac, "draft_analysis_code", draft)
    monkeypatch.setattr(ac, "review_analysis_code", review)

    body = asyncio.run(ac.run_code_analysis("dataset:orders", "totals"))
    assert attempts["n"] == 2
    assert body["datasets"] == {"region_totals": 2}


def test_code_crew_disallowed_imports_feed_back(client, monkeypatch):
    import app.analytics_code as ac

    _seed_orders()
    drafts = {"feedback": []}

    async def draft(request, context, feedback=None):
        drafts["feedback"].append(feedback)
        if feedback is None:
            return "import requests\nprint('nope')\n"
        return GOOD_CODE

    async def review(code, request, context):
        return {"verdict": "approve", "reason": "ok"}

    monkeypatch.setattr(ac, "draft_analysis_code", draft)
    monkeypatch.setattr(ac, "review_analysis_code", review)

    asyncio.run(ac.run_code_analysis("dataset:orders", "totals"))
    assert drafts["feedback"][1].startswith("Disallowed imports: requests")


def test_code_crew_missing_source(client):
    import app.analytics_code as ac

    with pytest.raises(LookupError):
        asyncio.run(ac.run_code_analysis("dataset:nope", "anything"))


# ------------------------------------------------------------ dashboard report


DASHBOARD_CODE = """
import json
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

df = pd.read_parquet("data/data.parquet")
totals = df.groupby("region")["total"].sum()
for name in ("region_totals_bar", "totals_distribution"):
    totals.plot(kind="bar")
    plt.savefig(f"out/figures/{name}.png")
    plt.close()
with open("out/metrics.json", "w") as fh:
    json.dump({"regions": int(totals.size), "grand_total": int(totals.sum())}, fh)
print("grand total:", int(totals.sum()))
"""

PLAN = [
    {"name": "region_totals_bar", "kind": "bar",
     "purpose": "Which region sells most?", "columns": ["region", "total"]},
    {"name": "totals_distribution", "kind": "hist",
     "purpose": "How are order totals distributed?", "columns": ["total"]},
]


def _stub_dashboard_crew(monkeypatch):
    import app.analytics_code as ac

    seen = {"request": None}

    async def plan(context, focus=None):
        assert "data.parquet" in context
        return [dict(chart) for chart in PLAN]

    async def draft(request, context, feedback=None):
        seen["request"] = request
        return DASHBOARD_CODE

    async def review(code, request, context):
        return {"verdict": "approve", "reason": "solid"}

    async def narrate(source_name, context, metrics, stdout, figure_names, focus):
        assert metrics == {"regions": 2, "grand_total": 22}
        assert "grand total: 22" in stdout
        assert len(figure_names) == 2
        return "## Overview\nTwo regions.\n\n## Key findings\n- grand total 22"

    monkeypatch.setattr(ac, "plan_dashboard", plan)
    monkeypatch.setattr(ac, "draft_analysis_code", draft)
    monkeypatch.setattr(ac, "review_analysis_code", review)
    monkeypatch.setattr(ac, "narrate_dashboard", narrate)
    return seen


def test_dashboard_report_lifecycle(client, monkeypatch):
    _seed_orders()
    seen = _stub_dashboard_crew(monkeypatch)

    created = client.post(
        "/api/v1/analytics/sources/dataset:orders/report",
        json={"focus": "regional totals"},
    )
    assert created.status_code == 201, created.text
    report = created.json()

    # The coder was briefed with the manager's plan, chart by chart
    assert "Implement this dashboard plan EXACTLY" in seen["request"]
    assert "region_totals_bar" in seen["request"]
    assert "totals_distribution" in seen["request"]

    # The plan is part of the saved report
    assert [chart["name"] for chart in report["plan"]] == [
        "region_totals_bar", "totals_distribution"
    ]

    assert report["focus"] == "regional totals"
    assert report["report"].startswith("## Overview")
    assert report["metrics"]["grand_total"] == 22
    assert len(report["figures"]) == 2
    for figure in report["figures"]:
        png = client.get(f"/api/v1/files/{figure['id']}")
        assert png.status_code == 200
        assert png.content[:8].startswith(b"\x89PNG")

    # Persisted: listed (filtered by source) and retrievable by id
    listing = client.get(
        "/api/v1/analytics/reports?source=dataset:orders"
    ).json()
    assert [entry["id"] for entry in listing] == [report["id"]]
    assert listing[0]["figures"] == 2
    fetched = client.get(f"/api/v1/analytics/reports/{report['id']}").json()
    assert fetched["report"] == report["report"]
    assert fetched["plan"] == report["plan"]

    # And deletable
    assert client.delete(
        f"/api/v1/analytics/reports/{report['id']}"
    ).status_code == 204
    assert client.get(f"/api/v1/analytics/reports/{report['id']}").status_code == 404


def test_dashboard_plan_validation():
    import app.analytics_code as ac

    request = ac._plan_to_request(PLAN)
    assert "1. region_totals_bar — bar chart" in request
    assert "./out/figures/region_totals_bar.png" in request
    assert "metrics.json" in request


def test_python_analysis_endpoint_removed(client):
    response = client.post(
        "/api/v1/analytics/sources/dataset:orders/code",
        json={"request": "anything"},
    )
    assert response.status_code in (404, 405)
