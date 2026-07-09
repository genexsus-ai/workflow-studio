"""Tests for canvas trigger nodes: validation, translation, automation sync."""


def _doc(trigger_config: dict | None = None, extra_nodes: list | None = None) -> dict:
    nodes = []
    edges = []
    if trigger_config is not None:
        nodes.append(
            {
                "id": "trigger",
                "type": "trigger",
                "position": {"x": 0, "y": 0},
                "config": trigger_config,
            }
        )
        edges.append({"source": "trigger", "target": "start"})
    nodes.extend(
        [
            {
                "id": "start",
                "type": "input",
                "position": {"x": 100, "y": 0},
                "config": {},
            },
            {
                "id": "end",
                "type": "output",
                "position": {"x": 200, "y": 0},
                "config": {},
            },
        ]
    )
    if extra_nodes:
        nodes.extend(extra_nodes)
    edges.append({"source": "start", "target": "end"})
    return {"name": "Trigger Test", "nodes": nodes, "edges": edges}


def test_palette_includes_trigger_node(client):
    palette = client.get("/api/v1/palette").json()
    trigger_def = next(t for t in palette["node_types"] if t["type"] == "trigger")
    kinds = next(f for f in trigger_def["config_fields"] if f["name"] == "trigger_kind")
    assert kinds["options"] == ["schedule", "webhook"]


def test_trigger_node_validates_and_requires_kind(client):
    ok = client.post(
        "/api/v1/workflows/validate", json=_doc({"trigger_kind": "schedule"})
    ).json()
    assert ok["valid"] is True

    bad = client.post("/api/v1/workflows/validate", json=_doc({})).json()
    assert bad["valid"] is False
    assert any("trigger_kind" in issue["message"] for issue in bad["issues"])


def test_translate_skips_trigger_node_and_edges(client):
    from app.runner import translate
    from app.schemas import WorkflowDoc

    doc = WorkflowDoc.model_validate(_doc({"trigger_kind": "schedule"}))
    nodes, edges = translate(doc)

    assert [node["id"] for node in nodes] == ["start", "end"]
    assert edges == [{"source": "start", "target": "end"}]


def test_saving_schedule_trigger_node_enables_automation(client):
    response = client.post(
        "/api/v1/workflows",
        json=_doc({"trigger_kind": "schedule", "interval_seconds": 900}),
    )

    assert response.status_code == 201, response.text
    automation = response.json()["automation"]
    assert automation["schedule_enabled"] is True
    assert automation["interval_seconds"] == 900


def test_saving_webhook_trigger_node_provisions_token(client):
    response = client.post(
        "/api/v1/workflows",
        json=_doc(
            {
                "trigger_kind": "webhook",
                "webhook_provider": "github",
                "webhook_event_filter": "issues.opened",
            }
        ),
    )

    assert response.status_code == 201, response.text
    automation = response.json()["automation"]
    assert automation["webhook_enabled"] is True
    assert automation["webhook_token"]
    assert automation["webhook_provider"] == "github"
    assert automation["webhook_event_filter"] == "issues.opened"


def test_update_preserves_existing_webhook_token(client):
    created = client.post(
        "/api/v1/workflows", json=_doc({"trigger_kind": "webhook"})
    ).json()
    token = created["automation"]["webhook_token"]
    workflow_id = created["id"]

    updated_doc = _doc({"trigger_kind": "webhook", "webhook_provider": "generic"})
    updated_doc["id"] = workflow_id
    response = client.put(f"/api/v1/workflows/{workflow_id}", json=updated_doc)

    assert response.status_code == 200, response.text
    assert response.json()["automation"]["webhook_token"] == token


def test_saving_without_trigger_node_keeps_client_automation(client):
    doc = _doc(None)
    doc["automation"] = {"schedule_enabled": False, "interval_seconds": 300}
    response = client.post("/api/v1/workflows", json=doc)

    assert response.status_code == 201
    assert response.json()["automation"]["schedule_enabled"] is False
