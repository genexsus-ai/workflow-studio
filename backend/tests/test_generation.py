"""Tests for NL workflow generation: emitter, catalog, and the API routes."""

import json

import pytest

_PLAN_JSON = json.dumps(
    {
        "name": "Ticket Triage",
        "description": "Classify and route tickets",
        "trigger": {"kind": "schedule", "config": {"interval_seconds": 600}},
        "steps": [
            {
                "id": "classify",
                "title": "Classifier",
                "description": "Classify the ticket",
                "kind": "agent",
                "capabilities": [],
                "depends_on": [],
            },
            {
                "id": "notify",
                "title": "Notify Slack",
                "description": "Post the result to Slack",
                "kind": "connector",
                "capabilities": ["slack.send_message"],
                "depends_on": ["classify"],
            },
        ],
        "open_questions": [],
    }
)


@pytest.fixture
def mock_planner_llm(monkeypatch):
    """LLM factory stub returning a fixed valid plan for every call."""
    from genxai.llm.base import LLMProvider, LLMResponse
    from genxai.llm.factory import LLMProviderFactory

    class StubProvider(LLMProvider):
        def __init__(self, model: str = "stub", **kwargs):
            super().__init__(model=model, temperature=0.0, max_tokens=None)

        async def generate(self, prompt, system_prompt=None, **kwargs):
            return LLMResponse(
                content=_PLAN_JSON,
                model=self.model,
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                finish_reason="stop",
            )

        async def generate_stream(self, prompt, system_prompt=None, **kwargs):
            yield _PLAN_JSON

    monkeypatch.setattr(
        LLMProviderFactory,
        "create_provider",
        lambda *args, **kwargs: StubProvider(model=kwargs.get("model", "stub")),
    )
    return StubProvider


def test_studio_catalog_includes_connector_actions(client):
    from app.generation import studio_capability_catalog

    catalog = studio_capability_catalog()

    assert "slack.send_message" in catalog.names("connector")
    assert "github.create_issue" in catalog.names("connector")
    # Flow patterns and registered studio tools ride along.
    assert "coordinator_worker" in catalog.names("flow")
    assert "web_scraper" in catalog.names("tool")


def test_workflow_to_doc_mapping_and_layout(client):
    from app.generation import workflow_to_doc

    workflow = {
        "name": "Demo",
        "description": "demo",
        "trigger": {"kind": "schedule", "config": {"interval_seconds": 600}},
        "agents": [],
        "graph": {
            "nodes": [
                {"id": "start", "type": "input"},
                {
                    "id": "classify",
                    "type": "agent",
                    "agent": "classify",
                    "config": {"role": "Classifier", "goal": "Classify"},
                },
                {
                    "id": "branch",
                    "type": "condition",
                    "config": {"condition": "category"},
                },
                {
                    "id": "notify",
                    "type": "tool",
                    "config": {
                        "tool_name": "slack.send_message",
                        "tool_params": {"channel": "#x"},
                    },
                },
                {"id": "end", "type": "output"},
            ],
            "edges": [
                {"from": "start", "to": "classify"},
                {"from": "classify", "to": "branch"},
                {"from": "branch", "to": "notify", "condition": "category == 'urgent'"},
                {"from": "notify", "to": "end"},
            ],
        },
    }

    doc = workflow_to_doc(workflow)

    types = {node.id: node.type for node in doc.nodes}
    assert types["branch"] == "decision"  # library name mapped to studio name
    assert types["notify"] == "connector"  # dotted capability became a connector node

    notify = next(node for node in doc.nodes if node.id == "notify")
    assert notify.config["connector"] == "slack"
    assert notify.config["action"] == "send_message"
    assert notify.config["params"] == {"channel": "#x"}

    # Layered layout: each hop moves right; siblings never overlap.
    by_id = {node.id: node.position for node in doc.nodes}
    assert by_id["start"].x < by_id["classify"].x < by_id["branch"].x < by_id["notify"].x
    positions = [(node.position.x, node.position.y) for node in doc.nodes]
    assert len(set(positions)) == len(positions)

    assert doc.automation.schedule_enabled is True
    assert doc.automation.interval_seconds == 600

    conditional = next(edge for edge in doc.edges if edge.target == "notify")
    assert conditional.condition == "category == 'urgent'"


def test_generate_endpoint_returns_valid_doc(client, mock_planner_llm):
    response = client.post(
        "/api/v1/workflows/generate",
        json={"prompt": "Classify tickets and notify slack", "crew": False},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    doc = payload["workflow"]
    assert doc["name"] == "Ticket Triage"
    node_types = {node["id"]: node["type"] for node in doc["nodes"]}
    assert node_types["classify"] == "agent"
    assert node_types["notify"] == "connector"
    assert payload["validation"]["valid"] in (True, False)
    # Generated doc passes the studio's own validate endpoint.
    validation = client.post("/api/v1/workflows/validate", json=doc)
    assert validation.status_code == 200


def test_generate_with_explicit_name_overrides_ai_name(client, mock_planner_llm):
    response = client.post(
        "/api/v1/workflows/generate",
        json={
            "prompt": "Classify tickets and notify slack",
            "crew": False,
            "name": "My Triage Pipeline",
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["workflow"]["name"] == "My Triage Pipeline"

    # Blank names fall back to the AI-chosen one.
    response = client.post(
        "/api/v1/workflows/generate",
        json={"prompt": "Classify tickets and notify slack", "crew": False, "name": "   "},
    )
    assert response.json()["workflow"]["name"] == "Ticket Triage"


def test_generation_recorded_and_acceptable(client, mock_planner_llm):
    response = client.post(
        "/api/v1/workflows/generate",
        json={"prompt": "Classify tickets and notify slack", "crew": False},
    )
    assert response.status_code == 200
    generation_id = response.json()["generation_id"]
    assert generation_id

    accept = client.post(f"/api/v1/workflows/generate/{generation_id}/accept")
    assert accept.status_code == 200
    assert accept.json() == {"accepted": True}

    # Unknown ids 404.
    assert client.post("/api/v1/workflows/generate/nope/accept").status_code == 404

    # Accepted plans are recalled for similar future prompts.
    from app.generation import get_generation_memory

    recalled = get_generation_memory().recall("classify my tickets")
    assert recalled and recalled[0].accepted is True


def test_refine_mode_passes_current_workflow(client, mock_planner_llm):
    current = {
        "name": "Existing Flow",
        "description": "",
        "nodes": [
            {"id": "start", "type": "input", "position": {"x": 0, "y": 0}, "config": {}},
            {"id": "end", "type": "output", "position": {"x": 100, "y": 0}, "config": {}},
        ],
        "edges": [{"source": "start", "target": "end"}],
    }
    response = client.post(
        "/api/v1/workflows/generate",
        json={
            "prompt": "add a summarization step",
            "crew": False,
            "current_workflow": current,
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["workflow"]["name"] == "Ticket Triage"


def test_generate_stream_emits_progress_then_complete(client, mock_planner_llm):
    with client.stream(
        "POST",
        "/api/v1/workflows/generate/stream",
        json={"prompt": "Classify tickets and notify slack", "crew": False},
    ) as response:
        assert response.status_code == 200
        events = []
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[len("data: ") :]))

    stages = [event.get("stage") for event in events if event["event"] == "progress"]
    assert "planning" in stages
    assert "compiled" in stages
    assert events[-1]["event"] == "complete"
    assert events[-1]["workflow"]["name"] == "Ticket Triage"
