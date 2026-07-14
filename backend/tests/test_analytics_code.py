"""Tests for the analytics code crew (Python Coder + Code Review agents)."""


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


def test_code_analysis_end_to_end(client, monkeypatch):
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

    response = client.post(
        "/api/v1/analytics/sources/dataset:orders/code",
        json={"request": "Total per region with a bar chart"},
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # Review loop ran: initial draft, one revision round, then approval
    assert len(calls["draft"]) == 2
    assert calls["draft"][1] == "print totals to stdout too"
    assert [v["verdict"] for v in body["review"]] == ["revise", "approve"]

    # Derived table materialized as a dataset (and thus a catalog source)
    assert body["datasets"] == {"region_totals": 2}
    rows = client.get("/api/v1/datasets/region_totals/rows").json()
    east = next(r for r in rows["rows"] if r["region"] == "east")
    assert east["total"] == 17
    sources = client.get("/api/v1/data/sources").json()
    assert any(s["id"] == "dataset:region_totals" for s in sources)

    # Figure stored and served; stdout captured; code echoed back
    assert len(body["figures"]) == 1
    png = client.get(f"/api/v1/files/{body['figures'][0]['id']}")
    assert png.status_code == 200
    assert png.content[:8].startswith(b"\x89PNG")
    assert "regions:" in body["stdout"]
    assert "read_parquet" in body["code"]


def test_code_analysis_self_repairs_failing_code(client, monkeypatch):
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

    response = client.post(
        "/api/v1/analytics/sources/dataset:orders/code",
        json={"request": "totals by region"},
    )
    assert response.status_code == 200, response.text
    assert attempts["n"] == 2
    assert response.json()["datasets"] == {"region_totals": 2}


def test_code_analysis_disallowed_imports_feed_back(client, monkeypatch):
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

    response = client.post(
        "/api/v1/analytics/sources/dataset:orders/code",
        json={"request": "totals by region"},
    )
    assert response.status_code == 200, response.text
    assert drafts["feedback"][1].startswith("Disallowed imports: requests")


def test_code_analysis_missing_source_404(client):
    response = client.post(
        "/api/v1/analytics/sources/dataset:nope/code",
        json={"request": "anything at all"},
    )
    assert response.status_code == 404
