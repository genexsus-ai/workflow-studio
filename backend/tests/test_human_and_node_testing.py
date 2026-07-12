"""Tests for human-in-the-loop nodes and single-node testing."""

import json


def _human_doc(human_config: dict) -> dict:
    return {
        "name": "Approval flow",
        "nodes": [
            {"id": "start", "type": "input", "position": {"x": 0, "y": 0}, "config": {}},
            {
                "id": "gate",
                "type": "human",
                "position": {"x": 100, "y": 0},
                "config": human_config,
            },
            {"id": "end", "type": "output", "position": {"x": 200, "y": 0}, "config": {}},
        ],
        "edges": [
            {"source": "start", "target": "gate"},
            {"source": "gate", "target": "end"},
        ],
    }


def _stream_events(client, doc: dict, run_input: dict) -> list[dict]:
    events = []
    with client.stream(
        "POST", "/api/v1/run/stream", json={"workflow": doc, "input": run_input}
    ) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def test_palette_includes_human_node(client):
    palette = client.get("/api/v1/palette").json()
    human = next(t for t in palette["node_types"] if t["type"] == "human")
    field_names = [f["name"] for f in human["config_fields"]]
    assert "prompt" in field_names
    assert "timeout_seconds" in field_names
    assert "default_response" in field_names


def test_human_node_validates_and_translates(client):
    doc = _human_doc({"prompt": "Approve?"})
    assert client.post("/api/v1/workflows/validate", json=doc).json()["valid"] is True

    from app.runner import translate
    from app.schemas import WorkflowDoc

    nodes, edges = translate(WorkflowDoc.model_validate(doc))
    gate = next(node for node in nodes if node["id"] == "gate")
    assert gate["type"] == "human"
    assert gate["config"]["prompt"] == "Approve?"


def test_human_node_emits_event_and_times_out_to_default(client):
    doc = _human_doc(
        {"prompt": "Approve {{ input.doc }}?", "timeout_seconds": 0.3, "default_response": "auto"}
    )

    events = _stream_events(client, doc, {"doc": "budget.pdf"})

    kinds = [event["event"] for event in events]
    assert "human_input_required" in kinds
    ask = next(e for e in events if e["event"] == "human_input_required")
    assert ask["data"]["prompt"] == "Approve budget.pdf?"
    complete = next(e for e in events if e["event"] in ("complete", "error"))
    assert complete["event"] == "complete"
    gate_output = complete["data"]["result"]["node_results"]["gate"]["output"]
    assert gate_output["response"] == "auto"


def test_human_node_receives_live_response(client):
    # Start the run via the webhook endpoint (returns immediately; runs execute
    # on the app's background workers) — TestClient buffers SSE streams, so the
    # interactive loop is driven through the polling endpoints instead.
    import time

    doc = _human_doc({"prompt": "Approve?", "timeout_seconds": 10})
    created = client.post("/api/v1/workflows", json=doc).json()
    enabled = client.post(
        f"/api/v1/workflows/{created['id']}/automation",
        json={"webhook_enabled": True, "schedule_enabled": False, "interval_seconds": 300},
    ).json()
    token = enabled["automation"]["webhook_token"]

    run_id = client.post(f"/api/v1/hooks/{token}", json={}).json()["run_id"]

    pending: list = []
    for _ in range(100):
        pending = client.get(f"/api/v1/runs/{run_id}/pending-input").json()["pending"]
        if pending:
            break
        time.sleep(0.05)
    assert pending == [{"node_id": "gate", "prompt": "Approve?"}]

    delivered = client.post(
        f"/api/v1/runs/{run_id}/input",
        json={"node_id": "gate", "response": {"approved": True}},
    )
    assert delivered.status_code == 200

    record: dict = {}
    for _ in range(100):
        record = client.get(f"/api/v1/runs/{run_id}").json()
        if record["status"] not in ("queued", "running"):
            break
        time.sleep(0.05)
    assert record["status"] == "success"
    gate_output = record["result"]["node_results"]["gate"]["output"]
    assert gate_output["response"] == {"approved": True}

    # Answering the same node again is a conflict
    again = client.post(
        f"/api/v1/runs/{run_id}/input", json={"node_id": "gate", "response": "x"}
    )
    assert again.status_code == 409


def test_human_input_endpoints_reject_bad_targets(client):
    assert client.get("/api/v1/runs/nope/pending-input").status_code == 404
    assert (
        client.post(
            "/api/v1/runs/nope/input", json={"node_id": "gate", "response": "x"}
        ).status_code
        == 404
    )


def test_node_test_endpoint_runs_single_node(client):
    doc = {
        "name": "Calc flow",
        "nodes": [
            {"id": "start", "type": "input", "position": {"x": 0, "y": 0}, "config": {}},
            {
                "id": "calc",
                "type": "tool",
                "position": {"x": 100, "y": 0},
                "config": {
                    "tool_name": "calculator",
                    "tool_params": {"expression": "{{ input.a }} * 2"},
                },
            },
        ],
        "edges": [{"source": "start", "target": "calc"}],
    }
    created = client.post("/api/v1/workflows", json=doc).json()

    result = client.post(
        f"/api/v1/workflows/{created['id']}/test-node",
        json={"node_id": "calc", "input": {"a": 21}},
    ).json()

    assert result["status"] == "success"
    assert result["output"]["data"]["result"] == 42.0


def test_node_test_endpoint_rejects_non_flow_nodes(client):
    doc = _human_doc({"prompt": "x"})
    doc["nodes"].insert(
        0,
        {
            "id": "trig",
            "type": "trigger",
            "position": {"x": 0, "y": 0},
            "config": {"trigger_kind": "webhook"},
        },
    )
    created = client.post("/api/v1/workflows", json=doc).json()

    response = client.post(
        f"/api/v1/workflows/{created['id']}/test-node",
        json={"node_id": "trig", "input": {}},
    )
    assert response.status_code == 422


def test_for_each_runs_per_item_through_studio(client):
    doc = {
        "name": "Per-item flow",
        "nodes": [
            {"id": "start", "type": "input", "position": {"x": 0, "y": 0}, "config": {}},
            {
                "id": "double",
                "type": "tool",
                "position": {"x": 100, "y": 0},
                "config": {
                    "tool_name": "calculator",
                    "tool_params": {"expression": "{{ item }} * 2"},
                    "for_each": "{{ input.numbers }}",
                },
            },
        ],
        "edges": [{"source": "start", "target": "double"}],
    }
    created = client.post("/api/v1/workflows", json=doc).json()

    result = client.post(
        f"/api/v1/workflows/{created['id']}/test-node",
        json={"node_id": "double", "input": {"numbers": [5, 6]}},
    ).json()

    assert result["status"] == "success"
    assert result["output"]["count"] == 2
    assert [e["data"]["result"] for e in result["output"]["items"]] == [10.0, 12.0]


def test_pinned_input_used_by_node_test(client):
    doc = {
        "name": "Pinned flow",
        "pinned_input": {"a": 21},
        "nodes": [
            {"id": "start", "type": "input", "position": {"x": 0, "y": 0}, "config": {}},
            {
                "id": "calc",
                "type": "tool",
                "position": {"x": 100, "y": 0},
                "config": {
                    "tool_name": "calculator",
                    "tool_params": {"expression": "{{ input.a }} * 2"},
                },
            },
        ],
        "edges": [{"source": "start", "target": "calc"}],
    }
    created = client.post("/api/v1/workflows", json=doc).json()
    assert created["pinned_input"] == {"a": 21}

    # No explicit input and no prior run: pinned data supplies it
    result = client.post(
        f"/api/v1/workflows/{created['id']}/test-node",
        json={"node_id": "calc"},
    ).json()

    assert result["status"] == "success"
    assert result["output"]["data"]["result"] == 42.0


def test_retry_from_failure_replays_successes(client):
    import time

    doc = {
        "name": "Retry flow",
        "nodes": [
            {"id": "start", "type": "input", "position": {"x": 0, "y": 0}, "config": {}},
            {
                "id": "good",
                "type": "tool",
                "position": {"x": 100, "y": 0},
                "config": {
                    "tool_name": "calculator",
                    "tool_params": {"expression": "6 * 7"},
                },
            },
            {
                "id": "fragile",
                "type": "tool",
                "position": {"x": 200, "y": 0},
                "config": {
                    "tool_name": "calculator",
                    # Fails until 'threshold' is in the input
                    "tool_params": {"expression": "{{ good.data.result }} + {{ input.threshold }}"},
                },
            },
        ],
        "edges": [
            {"source": "start", "target": "good"},
            {"source": "good", "target": "fragile"},
        ],
    }
    created = client.post("/api/v1/workflows", json=doc).json()
    enabled = client.post(
        f"/api/v1/workflows/{created['id']}/automation",
        json={"webhook_enabled": True, "schedule_enabled": False, "interval_seconds": 300},
    ).json()
    token = enabled["automation"]["webhook_token"]

    def wait_done(run_id: str) -> dict:
        for _ in range(100):
            record = client.get(f"/api/v1/runs/{run_id}").json()
            if record["status"] not in ("queued", "running"):
                return record
            time.sleep(0.05)
        raise AssertionError("run did not finish")

    # First run fails at 'fragile' (threshold missing)
    failed_id = client.post(f"/api/v1/hooks/{token}", json={}).json()["run_id"]
    failed = wait_done(failed_id)
    assert failed["status"] == "error"
    assert failed["result"]["node_results"]["good"]["status"] == "completed"

    # Retrying an active run is rejected; retrying the failed one resumes it
    retried = client.post(f"/api/v1/runs/{failed_id}/retry")
    assert retried.status_code == 201
    retry_record = wait_done(retried.json()["run_id"])

    # Still fails (input unchanged) but 'good' was replayed, not re-executed
    assert retry_record["status"] == "error"
    events = retry_record["result"]["node_events"]
    replayed = [e["node_id"] for e in events if e.get("replayed")]
    assert "good" in replayed
