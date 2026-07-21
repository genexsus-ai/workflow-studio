"""Tests for the hosted form trigger (n8n "on form submission" style)."""

import time


def _wait_for_run(client, run_id: str, timeout: float = 15.0) -> dict:
    """Poll a run until it reaches a terminal status (runs execute async)."""
    deadline = time.time() + timeout
    record: dict = {}
    while time.time() < deadline:
        record = client.get(f"/api/v1/runs/{run_id}").json()
        if record.get("status") not in ("queued", "running"):
            return record
        time.sleep(0.1)
    return record


def _calc_doc() -> dict:
    return {
        "name": "Form Test",
        "nodes": [
            {"id": "start", "type": "input", "position": {"x": 0, "y": 0}, "config": {}},
            {
                "id": "calc",
                "type": "tool",
                "position": {"x": 100, "y": 0},
                "config": {"tool_name": "calculator", "tool_params": {"expression": "1 + 1"}},
            },
            {"id": "end", "type": "output", "position": {"x": 200, "y": 0}, "config": {}},
        ],
        "edges": [
            {"source": "start", "target": "calc"},
            {"source": "calc", "target": "end"},
        ],
    }


def _enable_form(client, workflow_id: str) -> str:
    response = client.post(
        f"/api/v1/workflows/{workflow_id}/automation",
        json={
            "webhook_enabled": False,
            "schedule_enabled": False,
            "interval_seconds": 300,
            "form_enabled": True,
            "form_title": "Test form",
            "form_description": "A form for tests",
            "form_fields": [
                {"name": "task", "label": "Task", "type": "text", "required": True},
                {"name": "amount", "label": "Amount", "type": "number"},
                {"name": "priority", "type": "select", "options": ["low", "high"]},
            ],
        },
    )
    assert response.status_code == 200
    token = response.json()["automation"]["form_token"]
    assert token
    return token


def test_form_enable_generates_token_and_disable_clears_it(client):
    workflow_id = client.post("/api/v1/workflows", json=_calc_doc()).json()["id"]
    token = _enable_form(client, workflow_id)

    # Re-enabling keeps the same token
    again = client.post(
        f"/api/v1/workflows/{workflow_id}/automation",
        json={
            "webhook_enabled": False,
            "schedule_enabled": False,
            "interval_seconds": 300,
            "form_enabled": True,
            "form_token": token,
            "form_fields": [{"name": "task"}],
        },
    )
    assert again.json()["automation"]["form_token"] == token

    # Disabling clears it
    off = client.post(
        f"/api/v1/workflows/{workflow_id}/automation",
        json={
            "webhook_enabled": False,
            "schedule_enabled": False,
            "interval_seconds": 300,
            "form_enabled": False,
        },
    )
    assert off.json()["automation"]["form_token"] is None


def test_form_page_renders_fields(client):
    workflow_id = client.post("/api/v1/workflows", json=_calc_doc()).json()["id"]
    token = _enable_form(client, workflow_id)

    page = client.get(f"/api/v1/forms/{token}")
    assert page.status_code == 200
    html = page.text
    assert "Test form" in html
    assert 'name="task"' in html and "required" in html
    assert 'type="number"' in html
    assert "<select" in html and "<option>low</option>" in html

    assert client.get("/api/v1/forms/not-a-token").status_code == 404


def test_form_submission_runs_workflow(client):
    workflow_id = client.post("/api/v1/workflows", json=_calc_doc()).json()["id"]
    token = _enable_form(client, workflow_id)

    # JSON client: accepted + run id, number coerced
    accepted = client.post(
        f"/api/v1/forms/{token}",
        json={"task": "add", "amount": "2.5", "priority": "high"},
    )
    assert accepted.status_code == 200
    body = accepted.json()
    assert body["status"] == "accepted" and body["run_id"]

    run = _wait_for_run(client, body["run_id"])
    assert run["metadata"]["trigger"] == "form"
    submitted = run["metadata"]["input"]
    assert submitted["task"] == "add"
    assert submitted["amount"] == 2.5
    assert submitted["priority"] == "high"
    # n8n-style submission metadata
    assert submitted["formMode"] == "production"
    assert "T" in submitted["submittedAt"]  # ISO-8601 timestamp

    # Browser form post: thank-you page
    browser = client.post(f"/api/v1/forms/{token}", data={"task": "hello"})
    assert browser.status_code == 200
    assert "Thanks" in browser.text
    # Let the queued run finish before teardown (async workers)
    runs = client.get("/api/v1/runs").json()
    for record in runs:
        if record.get("status") in ("queued", "running"):
            _wait_for_run(client, record["run_id"])


def test_manual_run_uses_test_form_mode(client):
    workflow_id = client.post("/api/v1/workflows", json=_calc_doc()).json()["id"]
    _enable_form(client, workflow_id)

    # Running a form-triggered workflow from the editor is the test path:
    # test-mode metadata is stamped, overriding any stale formMode.
    with client.stream(
        "POST",
        f"/api/v1/workflows/{workflow_id}/run/stream",
        json={"input": {"task": "add", "formMode": "production"}},
    ) as response:
        assert response.status_code == 200
        for _ in response.iter_lines():
            pass

    run = _wait_for_run(client, client.get("/api/v1/runs").json()[0]["run_id"])
    submitted = run["metadata"]["input"]
    assert submitted["task"] == "add"
    assert submitted["formMode"] == "test"
    assert "T" in submitted["submittedAt"]


def test_form_submission_validates_required_fields(client):
    workflow_id = client.post("/api/v1/workflows", json=_calc_doc()).json()["id"]
    token = _enable_form(client, workflow_id)

    # Missing required "task": JSON gets 422, browser gets the form back with an error
    assert client.post(f"/api/v1/forms/{token}", json={"amount": "1"}).status_code == 422
    browser = client.post(f"/api/v1/forms/{token}", data={"amount": "1"})
    assert browser.status_code == 422
    assert "Missing or invalid" in browser.text

    # Non-numeric value for a number field
    bad_number = client.post(
        f"/api/v1/forms/{token}", json={"task": "x", "amount": "not a number"}
    )
    assert bad_number.status_code == 422


def test_form_trigger_node_derives_automation(client):
    doc = _calc_doc()
    doc["nodes"].insert(
        0,
        {
            "id": "trigger",
            "type": "trigger",
            "position": {"x": -100, "y": 0},
            "config": {"trigger_kind": "form", "form_title": "From node"},
        },
    )
    doc["edges"].insert(0, {"source": "trigger", "target": "start"})
    created = client.post("/api/v1/workflows", json=doc).json()
    automation = created["automation"]
    assert automation["form_enabled"] is True
    assert automation["form_token"]
    assert automation["form_title"] == "From node"
