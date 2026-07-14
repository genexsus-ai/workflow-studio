"""Tests for verified insights (Analyst -> Fact-Checker -> Judge)."""


def _seed(client) -> None:
    from genxai.core.datasets import get_dataset_store

    get_dataset_store().append(
        "sales",
        [
            {"region": "east", "total": 10},
            {"region": "east", "total": 30},
            {"region": "west", "total": 5},
        ],
    )


def _stub_crew(monkeypatch, *, judge=None):
    import app.insight_crew as crew

    async def draft_insight(name, sample, profile, question, model):
        return {"insight": "- East revenue is 40\n- West revenue is 99", "model": "stub"}

    async def draft_verifications(insight, schema_context):
        return [
            {"claim": "East revenue is 40",
             "sql": "SELECT SUM(total) AS v FROM data WHERE region = 'east'"},
            {"claim": "West revenue is 99",
             "sql": "SELECT SUM(total) AS v FROM data WHERE region = 'west'"},
        ]

    async def judge_insight(insight, verifications):
        if judge:
            return judge(insight, verifications)
        # Default judge behaves like a real one: corrects the wrong claim
        east = verifications[0]["result"][0]["v"]
        west = verifications[1]["result"][0]["v"]
        return {
            "verdicts": [
                {"claim": "East revenue is 40", "verdict": "confirmed",
                 "note": f"query returned {east}"},
                {"claim": "West revenue is 99", "verdict": "corrected",
                 "note": f"actual value is {west}"},
            ],
            "final_insight": f"- East revenue is {east}\n- West revenue is {west}",
        }

    monkeypatch.setattr(crew, "draft_insight", draft_insight)
    monkeypatch.setattr(crew, "draft_verifications", draft_verifications)
    monkeypatch.setattr(crew, "judge_insight", judge_insight)


def test_verified_insight_executes_checks_and_corrects(client, monkeypatch):
    _seed(client)
    _stub_crew(monkeypatch)

    response = client.post(
        "/api/v1/analytics/sources/dataset:sales/analyze",
        json={"question": "Revenue by region?"},
    )
    assert response.status_code == 200
    body = response.json()

    # The judge corrected the wrong claim using the EXECUTED query result
    assert "West revenue is 5" in body["insight"]
    assert len(body["verifications"]) == 2
    east, west = body["verifications"]
    assert east["verdict"] == "confirmed"
    assert east["result"] == [{"v": 40}]
    assert west["verdict"] == "corrected"
    assert west["result"] == [{"v": 5}]


def test_bad_verification_sql_is_unverifiable_not_fatal(client, monkeypatch):
    import app.insight_crew as crew

    _seed(client)
    _stub_crew(monkeypatch)

    async def bad_checks(insight, schema_context):
        return [
            {"claim": "checkable", "sql": "SELECT SUM(total) AS v FROM data"},
            {"claim": "broken", "sql": "SELECT nope FROM missing"},
        ]

    monkeypatch.setattr(crew, "draft_verifications", bad_checks)

    async def lenient_judge(insight, verifications):
        return {"verdicts": [], "final_insight": insight}

    monkeypatch.setattr(crew, "judge_insight", lenient_judge)

    body = client.post(
        "/api/v1/analytics/sources/dataset:sales/analyze", json={}
    ).json()
    verdicts = {v["claim"]: v["verdict"] for v in body["verifications"]}
    assert verdicts["broken"] == "unverifiable"


def test_verify_false_uses_single_agent_path(client, monkeypatch, mock_llm):
    _seed(client)

    body = client.post(
        "/api/v1/analytics/sources/dataset:sales/analyze",
        json={"verify": False},
    ).json()
    assert body["insight"] == "stub response"
    assert "verifications" not in body
