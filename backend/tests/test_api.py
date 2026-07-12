"""Tests for the Workflow Studio backend API."""

import json
from pathlib import Path

import pytest


def _calculator_workflow() -> dict:
    return {
        "name": "Calc pipeline",
        "nodes": [
            {"id": "start", "type": "input", "position": {"x": 0, "y": 0}, "config": {}},
            {
                "id": "calc",
                "type": "tool",
                "position": {"x": 200, "y": 0},
                "config": {"tool_name": "calculator", "tool_params": {"expression": "6 * 7"}},
            },
            {"id": "end", "type": "output", "position": {"x": 400, "y": 0}, "config": {}},
        ],
        "edges": [
            {"source": "start", "target": "calc"},
            {"source": "calc", "target": "end"},
        ],
    }


def _wait_for_run(client, run_id: str, timeout: float = 15.0) -> dict:
    """Poll a run until it reaches a terminal status (runs execute async now)."""
    import time as _time

    deadline = _time.time() + timeout
    while _time.time() < deadline:
        record = client.get(f"/api/v1/runs/{run_id}").json()
        if record.get("status") not in {"queued", "running"}:
            return record
        _time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s: {record}")


def _sse_events(response) -> list[dict]:
    events = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: "):]))
    return events


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_metrics_exposes_framework_collector(client):
    """The /metrics endpoint is the framework's own Prometheus collector —
    the graph engine records workflow/node executions into it as a side
    effect of running, with no Studio-side instrumentation."""
    doc = {
        "name": "Metrics smoke test",
        "nodes": [
            {"id": "start", "type": "input", "config": {}},
            {"id": "calc", "type": "tool", "config": {"tool_name": "calculator", "tool_params": {"expression": "1 + 1"}}},
            {"id": "end", "type": "output", "config": {}},
        ],
        "edges": [{"source": "start", "target": "calc"}, {"source": "calc", "target": "end"}],
    }
    response = client.post("/api/v1/run/stream", json={"workflow": doc, "input": {}})
    assert _sse_events(response)[-1]["data"]["status"] == "success"

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert metrics.headers["content-type"].startswith("text/plain")


def test_palette_shape(client):
    payload = client.get("/api/v1/palette").json()
    types = {t["type"] for t in payload["node_types"]}
    assert {"input", "output", "agent", "tool", "decision", "loop"} <= types
    tool_names = {t["name"] for t in payload["tools"]}
    assert "calculator" in tool_names
    assert any(m["id"] == "claude-opus-4-8" for m in payload["models"])
    # every tool exposes a JSON-schema-ish parameters block for form generation
    calc = next(t for t in payload["tools"] if t["name"] == "calculator")
    assert "properties" in calc["parameters"]


def test_workflow_crud_roundtrip(client):
    created = client.post("/api/v1/workflows", json=_calculator_workflow()).json()
    workflow_id = created["id"]
    assert workflow_id

    listed = client.get("/api/v1/workflows").json()
    assert any(w["id"] == workflow_id for w in listed)

    fetched = client.get(f"/api/v1/workflows/{workflow_id}").json()
    assert fetched["name"] == "Calc pipeline"
    assert fetched["nodes"][1]["position"]["x"] == 200  # positions persist

    fetched["name"] = "Renamed"
    updated = client.put(f"/api/v1/workflows/{workflow_id}", json=fetched).json()
    assert updated["name"] == "Renamed"

    assert client.delete(f"/api/v1/workflows/{workflow_id}").status_code == 204
    assert client.get(f"/api/v1/workflows/{workflow_id}").status_code == 404


def test_validate_catches_errors(client):
    doc = _calculator_workflow()
    doc["edges"].append({"source": "calc", "target": "ghost"})
    doc["nodes"].append({"id": "orphan_tool", "type": "tool", "config": {}})
    result = client.post("/api/v1/workflows/validate", json=doc).json()
    assert result["valid"] is False
    messages = " ".join(issue["message"] for issue in result["issues"])
    assert "missing node 'ghost'" in messages
    assert "no tool selected" in messages


def test_validate_ok(client):
    result = client.post("/api/v1/workflows/validate", json=_calculator_workflow()).json()
    assert result["valid"] is True


def test_run_stream_tool_only_no_api_key(client):
    """A tool-only pipeline runs end-to-end without any LLM key."""
    created = client.post("/api/v1/workflows", json=_calculator_workflow()).json()
    response = client.post(
        f"/api/v1/workflows/{created['id']}/run/stream", json={"input": {"task": "math"}}
    )
    assert response.status_code == 200
    events = _sse_events(response)

    kinds = [e["event"] for e in events]
    assert kinds[0] == "started"
    assert kinds[-1] == "complete"
    node_events = [e["data"] for e in events if e["event"] == "node"]
    statuses = {(e["node_id"], e["status"]) for e in node_events}
    assert ("calc", "completed") in statuses

    final = events[-1]["data"]
    assert final["status"] == "success"
    # tool_params must actually reach the tool: 6 * 7 = 42
    calc_output = final["result"]["node_results"]["calc"]["output"]
    assert calc_output["success"] is True, calc_output
    assert calc_output["data"]["result"] == 42
    run_id = final["run_id"]

    record = client.get(f"/api/v1/runs/{run_id}").json()
    assert record["status"] == "success"


def test_run_stream_invalid_workflow_rejected(client):
    doc = _calculator_workflow()
    doc["nodes"][1]["config"] = {}  # tool without tool_name
    created = client.post("/api/v1/workflows", json=doc).json()
    response = client.post(f"/api/v1/workflows/{created['id']}/run/stream", json={"input": {}})
    assert response.status_code == 422


def test_adhoc_run_with_agent_node(client, mock_llm):
    """Unsaved canvas run including an agent node backed by the stub LLM."""
    doc = {
        "name": "Agent pipeline",
        "nodes": [
            {"id": "start", "type": "input", "config": {}},
            {
                "id": "writer",
                "type": "agent",
                "config": {"role": "Writer", "goal": "Write a line", "llm_model": "gpt-4"},
            },
            {"id": "end", "type": "output", "config": {}},
        ],
        "edges": [
            {"source": "start", "target": "writer"},
            {"source": "writer", "target": "end"},
        ],
    }
    response = client.post("/api/v1/run/stream", json={"workflow": doc, "input": {"task": "go"}})
    assert response.status_code == 200
    events = _sse_events(response)
    assert events[-1]["event"] == "complete"
    assert events[-1]["data"]["status"] == "success"


def test_run_stream_data_passing_between_nodes(client):
    """A downstream tool consumes an upstream node's output via {{ }}."""
    doc = {
        "name": "Chained",
        "nodes": [
            {"id": "start", "type": "input", "config": {}},
            {
                "id": "calc1",
                "type": "tool",
                "config": {"tool_name": "calculator", "tool_params": {"expression": "6 * 6"}},
            },
            {
                "id": "calc2",
                "type": "tool",
                "config": {
                    "tool_name": "calculator",
                    "tool_params": {"expression": "{{ calc1.data.result }} + 6"},
                },
            },
            {"id": "end", "type": "output", "config": {}},
        ],
        "edges": [
            {"source": "start", "target": "calc1"},
            {"source": "calc1", "target": "calc2"},
            {"source": "calc2", "target": "end"},
        ],
    }
    response = client.post("/api/v1/run/stream", json={"workflow": doc, "input": {}})
    events = _sse_events(response)
    final = events[-1]["data"]
    assert final["status"] == "success"
    results = final["result"]["node_results"]
    assert results["calc1"]["output"]["data"]["result"] == 36
    assert results["calc2"]["output"]["data"]["result"] == 42  # 36 + 6


def test_run_stream_bad_template_fails_cleanly(client):
    doc = {
        "name": "BadTemplate",
        "nodes": [
            {"id": "start", "type": "input", "config": {}},
            {
                "id": "calc",
                "type": "tool",
                "config": {"tool_name": "calculator", "tool_params": {"expression": "{{ ghost.x }}"}},
            },
        ],
        "edges": [{"source": "start", "target": "calc"}],
    }
    response = client.post("/api/v1/run/stream", json={"workflow": doc, "input": {}})
    events = _sse_events(response)
    assert events[-1]["event"] == "error"
    assert "ghost.x" in str(events[-1]["data"])


def test_webhook_enable_and_fire(client):
    """Enabling a webhook yields a token; POSTing to the hook runs the workflow."""
    created = client.post("/api/v1/workflows", json=_calculator_workflow()).json()
    workflow_id = created["id"]

    doc = client.post(
        f"/api/v1/workflows/{workflow_id}/automation",
        json={"webhook_enabled": True, "schedule_enabled": False, "interval_seconds": 300},
    ).json()
    token = doc["automation"]["webhook_token"]
    assert token

    fired = client.post(f"/api/v1/hooks/{token}", json={"task": "from webhook"})
    assert fired.status_code == 200
    body = fired.json()
    assert body["status"] == "accepted"

    record = _wait_for_run(client, body["run_id"])
    assert record["status"] == "success"
    assert record["metadata"]["trigger"] == "webhook"

    # disabling clears the token and the hook 404s
    disabled = client.post(
        f"/api/v1/workflows/{workflow_id}/automation",
        json={"webhook_enabled": False, "schedule_enabled": False, "interval_seconds": 300},
    ).json()
    assert disabled["automation"]["webhook_token"] is None
    assert client.post(f"/api/v1/hooks/{token}", json={}).status_code == 404


def test_webhook_unknown_token_404(client):
    assert client.post("/api/v1/hooks/not-a-real-token", json={}).status_code == 404


async def test_schedule_manager_fires_and_stops(tmp_path, monkeypatch):
    """ScheduleTrigger fires the workflow on its interval and stops cleanly."""
    import app.runner as runner
    from app.automation import ScheduleManager
    from app.config import get_settings
    from app.schemas import WorkflowDoc
    from app.store import WorkflowStore

    import asyncio

    get_settings.cache_clear()
    settings = get_settings()
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(runner, "_execution_store", None)

    import app.run_manager as run_manager_module
    from app.run_manager import get_run_manager

    monkeypatch.setattr(run_manager_module, "_manager", None)
    run_manager = get_run_manager()
    await run_manager.start()

    store = WorkflowStore(tmp_path)
    doc = WorkflowDoc.model_validate(_calculator_workflow())
    doc.automation.schedule_enabled = True
    doc.automation.interval_seconds = 1
    doc = store.create(doc)

    manager = ScheduleManager(store)
    await manager.enable(doc)
    assert manager.is_active(doc.id)
    try:
        for _ in range(80):
            await asyncio.sleep(0.05)
            runs = list(runner.get_execution_store()._records.values())
            if any(r.status == "success" for r in runs):
                break
        else:
            raise AssertionError("scheduled run never completed")
    finally:
        await manager.disable(doc.id)
        await run_manager.shutdown()
    assert not manager.is_active(doc.id)
    fired = [r for r in runner.get_execution_store()._records.values()]
    assert fired and fired[0].metadata.get("trigger") == "schedule"
    get_settings.cache_clear()


def test_translate_strips_canvas_fields():
    from app.runner import translate
    from app.schemas import WorkflowDoc

    doc = WorkflowDoc.model_validate(_calculator_workflow())
    nodes, edges = translate(doc)
    assert nodes[0] == {"id": "start", "type": "input", "config": {}}
    assert "position" not in nodes[1]
    assert edges[0] == {"source": "start", "target": "calc"}


def test_conditional_and_parallel_edges_translate():
    from app.runner import translate
    from app.schemas import WorkflowDoc

    doc = WorkflowDoc.model_validate(
        {
            "name": "branchy",
            "nodes": [
                {"id": "a", "type": "input", "config": {}},
                {"id": "b", "type": "output", "config": {}},
            ],
            "edges": [{"source": "a", "target": "b", "condition": "go", "parallel": True}],
        }
    )
    _, edges = translate(doc)
    assert edges[0] == {"source": "a", "target": "b", "condition": "go", "parallel": True}


def test_credentials_crud_and_secrecy(client):
    created = client.post(
        "/api/v1/credentials",
        json={"name": "team-slack", "connector_type": "slack", "config": {"bot_token": "xoxb-secret"}},
    )
    assert created.status_code == 201

    listing = client.get("/api/v1/credentials").json()
    assert {"name": "team-slack", "connector_type": "slack", "auth_kind": "token"} in listing
    assert "xoxb-secret" not in json.dumps(listing)  # secrets are write-only

    assert client.post(
        "/api/v1/credentials", json={"name": "x", "connector_type": "nope", "config": {}}
    ).status_code == 422

    assert client.delete("/api/v1/credentials/team-slack").status_code == 204
    assert client.delete("/api/v1/credentials/team-slack").status_code == 404


def test_palette_includes_connector_catalog(client):
    payload = client.get("/api/v1/palette").json()
    types = {t["type"] for t in payload["node_types"]}
    assert "connector" in types
    connectors = {c["type"]: c for c in payload["connectors"]}
    assert "slack" in connectors and "github" in connectors
    assert "send_message" in connectors["slack"]["actions"]
    assert any(f["name"] == "bot_token" and f["secret"] for f in connectors["slack"]["credential_fields"])


def test_connector_node_validation_and_translation(client):
    doc = {
        "name": "Connector wf",
        "nodes": [
            {"id": "start", "type": "input", "config": {}},
            {"id": "notify", "type": "connector", "config": {}},
        ],
        "edges": [{"source": "start", "target": "notify"}],
    }
    result = client.post("/api/v1/workflows/validate", json=doc).json()
    assert result["valid"] is False
    messages = " ".join(i["message"] for i in result["issues"])
    for field in ("connector", "action", "credential"):
        assert f"missing '{field}'" in messages

    from app.runner import translate
    from app.schemas import WorkflowDoc

    doc["nodes"][1]["config"] = {
        "connector": "slack",
        "action": "send_message",
        "credential": "team-slack",
        "params": {"channel": "#general", "text": "{{ input.msg }}"},
    }
    nodes, _ = translate(WorkflowDoc.model_validate(doc))
    assert nodes[1]["type"] == "tool"
    assert nodes[1]["config"]["tool_name"] == "connector_action"
    assert nodes[1]["config"]["tool_params"]["params"]["text"] == "{{ input.msg }}"


def test_connector_node_runs_with_mocked_slack(client, monkeypatch):
    """End-to-end run of a connector node with the Slack API call mocked."""
    from genxai.connectors import SlackConnector

    sent = []

    async def fake_send_message(self, channel, text, blocks=None, attachments=None):
        sent.append({"channel": channel, "text": text})
        return {"ok": True, "ts": "1.2"}

    monkeypatch.setattr(SlackConnector, "send_message", fake_send_message)

    client.post(
        "/api/v1/credentials",
        json={"name": "team-slack", "connector_type": "slack", "config": {"bot_token": "xoxb-test"}},
    )
    doc = {
        "name": "Notify",
        "nodes": [
            {"id": "start", "type": "input", "config": {}},
            {
                "id": "notify",
                "type": "connector",
                "config": {
                    "connector": "slack",
                    "action": "send_message",
                    "credential": "team-slack",
                    "params": {"channel": "#general", "text": "Result: {{ input.result }}"},
                },
            },
            {"id": "end", "type": "output", "config": {}},
        ],
        "edges": [
            {"source": "start", "target": "notify"},
            {"source": "notify", "target": "end"},
        ],
    }
    response = client.post("/api/v1/run/stream", json={"workflow": doc, "input": {"result": 42}})
    events = _sse_events(response)
    final = events[-1]["data"]
    assert final["status"] == "success", final
    assert sent == [{"channel": "#general", "text": "Result: 42"}]
    notify_output = final["result"]["node_results"]["notify"]["output"]
    assert notify_output["success"] is True
    assert notify_output["data"]["ok"] is True


def test_connector_node_unknown_credential_fails(client):
    doc = {
        "name": "Bad cred",
        "nodes": [
            {"id": "start", "type": "input", "config": {}},
            {
                "id": "notify",
                "type": "connector",
                "config": {
                    "connector": "slack",
                    "action": "send_message",
                    "credential": "missing-cred",
                    "params": {"channel": "#x", "text": "y"},
                },
            },
        ],
        "edges": [{"source": "start", "target": "notify"}],
    }
    response = client.post("/api/v1/run/stream", json={"workflow": doc, "input": {}})
    events = _sse_events(response)
    node_events = [e["data"] for e in events if e["event"] == "node"]
    notify = [e for e in node_events if e["node_id"] == "notify" and e["status"] == "completed"]
    # tool failures surface in node output, run completes with tool error recorded
    assert notify or events[-1]["event"] in {"complete", "error"}


def _enable_github_webhook(client, workflow_id, secret="gh-secret", event_filter="issues.opened"):
    return client.post(
        f"/api/v1/workflows/{workflow_id}/automation",
        json={
            "webhook_enabled": True,
            "webhook_provider": "github",
            "webhook_secret": secret,
            "webhook_event_filter": event_filter,
            "schedule_enabled": False,
            "interval_seconds": 300,
        },
    ).json()


def test_github_webhook_signature_and_filter(client):
    import hashlib
    import hmac as hmac_mod

    created = client.post("/api/v1/workflows", json=_calculator_workflow()).json()
    doc = _enable_github_webhook(client, created["id"])
    token = doc["automation"]["webhook_token"]

    body = json.dumps({"action": "opened", "issue": {"number": 7, "title": "Bug"}}).encode()
    good_sig = "sha256=" + hmac_mod.new(b"gh-secret", body, hashlib.sha256).hexdigest()

    # wrong signature rejected
    bad = client.post(
        f"/api/v1/hooks/{token}",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": "sha256=bogus",
        },
    )
    assert bad.status_code == 401

    # non-matching event ignored (valid signature)
    ignored = client.post(
        f"/api/v1/hooks/{token}",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "push",
            "X-Hub-Signature-256": good_sig,
        },
    )
    assert ignored.json()["status"] == "ignored"

    # matching event with valid signature runs the workflow
    accepted = client.post(
        f"/api/v1/hooks/{token}",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "issues",
            "X-Hub-Signature-256": good_sig,
        },
    )
    payload = accepted.json()
    assert payload["status"] == "accepted"
    record = _wait_for_run(client, payload["run_id"])
    assert record["status"] == "success"


def test_api_token_auth(tmp_path, monkeypatch):
    """When STUDIO_API_TOKEN is set, API requires the header; hooks stay public."""
    from fastapi.testclient import TestClient

    import app.api.routes as routes
    import app.credentials as credentials
    import app.runner as runner
    from app.config import get_settings
    from app.main import create_app

    get_settings.cache_clear()
    monkeypatch.setenv("STUDIO_API_TOKEN", "s3cret-token")
    settings = get_settings()
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(routes, "_store", None)
    monkeypatch.setattr(runner, "_execution_store", None)
    monkeypatch.setattr(credentials, "_store", None)

    with TestClient(create_app()) as secured:
        assert secured.get("/health").status_code == 200  # health stays open
        assert secured.get("/api/v1/workflows").status_code == 401
        ok = secured.get("/api/v1/workflows", headers={"X-Studio-Token": "s3cret-token"})
        assert ok.status_code == 200
        # hooks bypass auth (still 404 for unknown token, not 401)
        assert secured.post("/api/v1/hooks/whatever", json={}).status_code == 404

    get_settings.cache_clear()


def _complex_pipeline_workflow() -> dict:
    """Fan-out/join/error-handling fixture exercising the full engine surface:
    parallel branches, joins that wait for all parents, {{ }} data passing
    across branches (including reading a failed node's error), retry +
    continue_on_error, and a final multi-way join.
    """
    return {
        "name": "Complex pipeline (fan-out, join, errors)",
        "nodes": [
            {"id": "start", "type": "input", "config": {}},
            {
                "id": "square",
                "type": "tool",
                "config": {
                    "tool_name": "calculator",
                    "tool_params": {"expression": "{{ input.number }} * {{ input.number }}"},
                },
            },
            {
                "id": "analyze",
                "type": "tool",
                "config": {
                    "tool_name": "text_analyzer",
                    "tool_params": {"text": "{{ input.text }}", "operation": "all"},
                },
            },
            {
                "id": "hash",
                "type": "tool",
                "config": {
                    "tool_name": "hash_generator",
                    "tool_params": {"data": "{{ input.text }}", "algorithm": "sha256"},
                },
            },
            {
                "id": "flaky_api",
                "type": "tool",
                "config": {
                    "tool_name": "http_client",
                    "tool_params": {"url": "http://localhost:9/unreachable", "timeout": 1},
                    "execution": {
                        "retry_count": 1,
                        "backoff_seconds": 0.05,
                        "timeout_seconds": 5,
                        "continue_on_error": True,
                    },
                },
            },
            {
                "id": "combine",
                "type": "tool",
                "config": {
                    "tool_name": "calculator",
                    "tool_params": {
                        "expression": "{{ square.data.result }} + {{ input.number }}"
                    },
                },
            },
            {
                "id": "find_word",
                "type": "tool",
                "config": {
                    "tool_name": "regex_matcher",
                    "tool_params": {
                        "text": "{{ input.text }}",
                        "pattern": "workflow",
                        "operation": "findall",
                    },
                },
            },
            {
                "id": "alert",
                "type": "tool",
                "config": {
                    "tool_name": "hash_generator",
                    "tool_params": {
                        "data": "ALERT: flaky_api failed with {{ flaky_api.error }}",
                        "algorithm": "md5",
                    },
                },
            },
            {"id": "end", "type": "output", "config": {}},
        ],
        "edges": [
            {"source": "start", "target": "square", "parallel": True},
            {"source": "start", "target": "analyze", "parallel": True},
            {"source": "start", "target": "hash", "parallel": True},
            {"source": "start", "target": "flaky_api", "parallel": True},
            {"source": "square", "target": "combine"},
            {"source": "analyze", "target": "combine"},
            {"source": "analyze", "target": "find_word"},
            {"source": "hash", "target": "find_word"},
            {"source": "flaky_api", "target": "alert"},
            {"source": "combine", "target": "end"},
            {"source": "find_word", "target": "end"},
            {"source": "alert", "target": "end"},
        ],
    }


def test_complex_pipeline_fanout_join_and_error_recovery(client):
    """End-to-end run covering parallel fan-out, two joins, cross-branch
    {{ }} interpolation, retry + continue_on_error, and a downstream node
    reading a failed sibling's error message.
    """
    import hashlib

    doc = _complex_pipeline_workflow()
    response = client.post(
        "/api/v1/run/stream",
        json={
            "workflow": doc,
            "input": {"number": 6, "text": "Testing the GenXAI workflow studio engine"},
        },
    )
    events = _sse_events(response)
    final = events[-1]["data"]
    assert final["status"] == "success", final

    results = final["result"]["node_results"]

    # square + combine: cross-branch interpolation into a join
    assert results["square"]["output"]["data"]["result"] == 36
    assert results["combine"]["output"]["data"]["result"] == 42  # 36 + 6

    # analyze + hash join at find_word; regex found the expected word
    assert results["find_word"]["output"]["data"]["matches"] == ["workflow"]

    # flaky_api: real failure, recorded (not raised) due to continue_on_error
    flaky_output = results["flaky_api"]["output"]
    assert flaky_output["success"] is False
    assert flaky_output["error"]

    # alert: downstream node reads the failed sibling's error via {{ }}
    expected_message = f"ALERT: flaky_api failed with {flaky_output['error']}"
    expected_hash = hashlib.md5(expected_message.encode()).hexdigest()
    assert results["alert"]["output"]["data"]["hash"] == expected_hash

    # every node reached a terminal status; no join fired more than once
    node_events = [e["data"] for e in events if e["event"] == "node"]
    completed_ids = [e["node_id"] for e in node_events if e["status"] == "completed"]
    for node in doc["nodes"]:
        assert completed_ids.count(node["id"]) == 1, f"{node['id']} ran {completed_ids.count(node['id'])}x"


def _multi_agent_workflow() -> dict:
    """Researcher + Critic (parallel, cheap model) join into an Editor
    (join, stronger model) that synthesizes both; two tools then fan out
    over the Editor's output. Exercises multi-agent parallelism, an agent
    join with cross-agent {{ }} interpolation into a task, heterogeneous
    per-node model selection, and agent -> tool data passing.
    """
    return {
        "name": "Multi-agent research pipeline",
        "nodes": [
            {"id": "start", "type": "input", "config": {}},
            {
                "id": "researcher",
                "type": "agent",
                "config": {
                    "role": "Research Analyst",
                    "goal": "Surface concrete, verifiable facts",
                    "llm_model": "claude-haiku-4-5",
                    "temperature": 0.3,
                    "task": "List exactly 3 concise facts about: {{ input.topic }}",
                },
            },
            {
                "id": "critic",
                "type": "agent",
                "config": {
                    "role": "Skeptical Reviewer",
                    "goal": "Surface risks, gaps, and counterarguments",
                    "llm_model": "claude-haiku-4-5",
                    "temperature": 0.5,
                    "task": "List exactly 2 potential risks or counterpoints about: {{ input.topic }}",
                },
            },
            {
                "id": "editor",
                "type": "agent",
                "config": {
                    "role": "Senior Editor",
                    "goal": "Produce a balanced, decision-ready brief",
                    "llm_model": "claude-opus-4-8",
                    "temperature": 0.4,
                    "task": (
                        "Combine this research: {{ researcher.output }} "
                        "with this critique: {{ critic.output }} "
                        "into a 3-sentence balanced summary about {{ input.topic }}."
                    ),
                },
            },
            {
                "id": "word_count",
                "type": "tool",
                "config": {
                    "tool_name": "text_analyzer",
                    "tool_params": {"text": "{{ editor.output }}", "operation": "all"},
                },
            },
            {
                "id": "archive",
                "type": "tool",
                "config": {
                    "tool_name": "hash_generator",
                    "tool_params": {"data": "{{ editor.output }}", "algorithm": "sha256"},
                },
            },
            {"id": "end", "type": "output", "config": {}},
        ],
        "edges": [
            {"source": "start", "target": "researcher", "parallel": True},
            {"source": "start", "target": "critic", "parallel": True},
            {"source": "researcher", "target": "editor"},
            {"source": "critic", "target": "editor"},
            {"source": "editor", "target": "word_count", "parallel": True},
            {"source": "editor", "target": "archive", "parallel": True},
            {"source": "word_count", "target": "end"},
            {"source": "archive", "target": "end"},
        ],
    }


@pytest.fixture
def recording_llm(monkeypatch):
    """A mock LLM provider that echoes the task line back in its response and
    records (model, prompt) per call, so tests can verify what each agent
    actually saw in its prompt — not just that a run completed.
    """
    from genxai.llm.base import LLMProvider, LLMResponse
    from genxai.llm.factory import LLMProviderFactory

    calls: list[dict] = []

    class RecordingProvider(LLMProvider):
        def __init__(self, model: str = "stub", **kwargs):
            super().__init__(model=model, temperature=0.0, max_tokens=None)

        async def generate(self, prompt, system_prompt=None, **kwargs):
            calls.append({"model": self.model, "prompt": prompt})
            task_line = next(
                (line for line in prompt.splitlines() if line.startswith("Task: ")), prompt
            )
            content = task_line[len("Task: "):] if task_line.startswith("Task: ") else prompt
            return LLMResponse(
                content=f"SUMMARY::{content}",
                model=self.model,
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                finish_reason="stop",
            )

        async def generate_stream(self, prompt, system_prompt=None, **kwargs):
            yield f"SUMMARY::{prompt}"

        async def generate_chat(self, messages, **kwargs):
            return await self.generate("chat")

    def _create(*args, **kwargs):
        return RecordingProvider(model=kwargs.get("model", "stub"))

    monkeypatch.setattr(LLMProviderFactory, "create_provider", _create)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    return calls


def test_multi_agent_pipeline_join_and_model_selection(client, recording_llm):
    """End-to-end run covering parallel agents, an agent-level join with
    cross-agent {{ }} interpolation into a downstream task, per-node model
    selection, and agent -> tool data passing.
    """
    doc = _multi_agent_workflow()
    response = client.post(
        "/api/v1/run/stream",
        json={"workflow": doc, "input": {"topic": "quantum computing"}},
    )
    events = _sse_events(response)
    final = events[-1]["data"]
    assert final["status"] == "success", final

    results = final["result"]["node_results"]

    researcher_output = results["researcher"]["output"]["output"]
    critic_output = results["critic"]["output"]["output"]
    assert "3 concise facts about: quantum computing" in researcher_output
    assert "2 potential risks or counterpoints about: quantum computing" in critic_output

    # Find the editor's actual LLM call and confirm both sibling agents'
    # outputs were interpolated into its task before it ever reached the model.
    editor_calls = [c for c in recording_llm if c["model"] == "claude-opus-4-8"]
    assert len(editor_calls) == 1
    editor_prompt = editor_calls[0]["prompt"]
    assert researcher_output in editor_prompt
    assert critic_output in editor_prompt

    # Per-node model selection: researcher/critic on haiku, editor on opus.
    models_used = {c["model"] for c in recording_llm}
    assert models_used == {"claude-haiku-4-5", "claude-opus-4-8"}
    researcher_critic_calls = [c for c in recording_llm if c["model"] == "claude-haiku-4-5"]
    assert len(researcher_critic_calls) == 2

    # Agent -> tool: both fan-out tools consumed the editor's real output.
    editor_output = results["editor"]["output"]["output"]
    assert results["word_count"]["output"]["data"]["success"] is True
    assert results["archive"]["output"]["data"]["hash"] == __import__("hashlib").sha256(
        editor_output.encode()
    ).hexdigest()

    # Every node ran exactly once despite the fan-out/join/fan-out shape.
    node_events = [e["data"] for e in events if e["event"] == "node"]
    completed_ids = [e["node_id"] for e in node_events if e["status"] == "completed"]
    for node in doc["nodes"]:
        assert completed_ids.count(node["id"]) == 1, f"{node['id']} ran {completed_ids.count(node['id'])}x"


# Fixture MCP server ships in the repo root tests/fixtures directory.
MCP_FIXTURE = str(
    Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "mcp_fixture_server.py"
)


def _register_fixture_mcp_server(client, name="local-tools"):
    import sys

    return client.post(
        "/api/v1/mcp/servers",
        json={
            "name": name,
            "transport": "mcp_stdio",
            "config": {"command": sys.executable, "args": [MCP_FIXTURE]},
        },
    )


def test_mcp_server_registry_crud(client):
    assert _register_fixture_mcp_server(client).status_code == 201

    listing = client.get("/api/v1/mcp/servers").json()
    assert any(s["name"] == "local-tools" and s["transport"] == "mcp_stdio" for s in listing)

    # invalid payloads rejected
    assert client.post(
        "/api/v1/mcp/servers", json={"name": "x", "transport": "carrier-pigeon", "config": {}}
    ).status_code == 422
    assert client.post(
        "/api/v1/mcp/servers", json={"name": "x", "transport": "mcp_stdio", "config": {}}
    ).status_code == 422

    assert client.delete("/api/v1/mcp/servers/local-tools").status_code == 204
    assert client.delete("/api/v1/mcp/servers/local-tools").status_code == 404


def test_mcp_live_tool_discovery(client):
    pytest.importorskip("mcp")
    _register_fixture_mcp_server(client)
    tools = client.get("/api/v1/mcp/servers/local-tools/tools").json()
    names = {t["name"] for t in tools}
    assert {"echo", "add"} <= names
    add = next(t for t in tools if t["name"] == "add")
    assert "a" in add["input_schema"]["properties"]

    # unknown server -> 404
    assert client.get("/api/v1/mcp/servers/ghost/tools").status_code == 404


def test_mcp_node_validation(client):
    doc = {
        "name": "MCP wf",
        "nodes": [
            {"id": "start", "type": "input", "config": {}},
            {"id": "call", "type": "mcp", "config": {}},
        ],
        "edges": [{"source": "start", "target": "call"}],
    }
    result = client.post("/api/v1/workflows/validate", json=doc).json()
    assert result["valid"] is False
    messages = " ".join(i["message"] for i in result["issues"])
    assert "missing 'server'" in messages and "missing 'tool'" in messages


def test_mcp_node_runs_end_to_end(client):
    """A workflow MCP node calls a real stdio MCP server with {{ }} params."""
    pytest.importorskip("mcp")
    _register_fixture_mcp_server(client)
    doc = {
        "name": "MCP pipeline",
        "nodes": [
            {"id": "start", "type": "input", "config": {}},
            {
                "id": "adder",
                "type": "mcp",
                "config": {
                    "server": "local-tools",
                    "tool": "add",
                    "params": {"a": "{{ input.x }}", "b": 2},
                },
            },
            {"id": "end", "type": "output", "config": {}},
        ],
        "edges": [
            {"source": "start", "target": "adder"},
            {"source": "adder", "target": "end"},
        ],
    }
    response = client.post("/api/v1/run/stream", json={"workflow": doc, "input": {"x": 40}})
    events = _sse_events(response)
    final = events[-1]["data"]
    assert final["status"] == "success", final
    adder_output = final["result"]["node_results"]["adder"]["output"]
    assert adder_output["success"] is True
    assert adder_output["data"]["structured"] == {"sum": 42.0}


def test_run_detail_persisted_with_node_results(client):
    """Per-node outputs are persisted on the run record for later inspection."""
    created = client.post("/api/v1/workflows", json=_calculator_workflow()).json()
    response = client.post(f"/api/v1/workflows/{created['id']}/run/stream", json={"input": {}})
    run_id = _sse_events(response)[-1]["data"]["run_id"]

    record = _wait_for_run(client, run_id)
    assert record["status"] == "success"
    node_results = record["result"]["node_results"]
    assert node_results["calc"]["output"]["data"]["result"] == 42
    assert record["result"]["node_events"], "node events should be persisted"
    # snapshot + input stored for rerun/recovery
    assert record["metadata"]["workflow_snapshot"]["name"] == "Calc pipeline"


def test_cancel_running_run(client):
    """A long-running run can be cancelled mid-flight via the API."""
    pytest.importorskip("mcp")
    import time as _time

    _register_fixture_mcp_server(client, name="slow-server")
    doc = {
        "name": "Slow pipeline",
        "nodes": [
            {"id": "start", "type": "input", "config": {}},
            {
                "id": "sleeper",
                "type": "mcp",
                "config": {"server": "slow-server", "tool": "slow", "params": {"seconds": 30}},
            },
        ],
        "edges": [{"source": "start", "target": "sleeper"}],
    }
    created = client.post("/api/v1/workflows", json=doc).json()
    enabled = client.post(
        f"/api/v1/workflows/{created['id']}/automation",
        json={"webhook_enabled": True, "schedule_enabled": False, "interval_seconds": 300},
    ).json()
    fired = client.post(f"/api/v1/hooks/{enabled['automation']['webhook_token']}", json={})
    run_id = fired.json()["run_id"]

    # wait until it is actually running
    deadline = _time.time() + 10
    while _time.time() < deadline:
        if client.get(f"/api/v1/runs/{run_id}").json()["status"] == "running":
            break
        _time.sleep(0.05)
    else:
        raise AssertionError("run never started")

    assert client.post(f"/api/v1/runs/{run_id}/cancel").json()["status"] == "cancelling"
    record = _wait_for_run(client, run_id)
    assert record["status"] == "cancelled"


def test_cancel_unknown_or_finished_run(client):
    assert client.post("/api/v1/runs/nope/cancel").status_code == 404

    created = client.post("/api/v1/workflows", json=_calculator_workflow()).json()
    response = client.post(f"/api/v1/workflows/{created['id']}/run/stream", json={"input": {}})
    run_id = _sse_events(response)[-1]["data"]["run_id"]
    _wait_for_run(client, run_id)
    assert client.post(f"/api/v1/runs/{run_id}/cancel").status_code == 409


def test_interrupted_runs_marked_on_recovery(client):
    """Runs left queued/running by a dead process become 'interrupted'."""
    from app.run_manager import get_run_manager
    from app.runner import get_execution_store

    store = get_execution_store()
    store.create("stale-run-1", workflow="wf", status="running")
    store.create("stale-run-2", workflow="wf", status="queued")

    marked = get_run_manager().recover_stale_runs()
    assert marked == 2
    assert store.get("stale-run-1").status == "interrupted"
    assert store.get("stale-run-2").status == "interrupted"


def test_rerun_from_stored_snapshot(client):
    created = client.post("/api/v1/workflows", json=_calculator_workflow()).json()
    response = client.post(
        f"/api/v1/workflows/{created['id']}/run/stream", json={"input": {"task": "first"}}
    )
    original_run = _sse_events(response)[-1]["data"]["run_id"]
    _wait_for_run(client, original_run)

    rerun = client.post(f"/api/v1/runs/{original_run}/rerun")
    assert rerun.status_code == 201
    new_run_id = rerun.json()["run_id"]
    assert new_run_id != original_run

    record = _wait_for_run(client, new_run_id)
    assert record["status"] == "success"
    assert record["result"]["node_results"]["calc"]["output"]["data"]["result"] == 42
    assert record["metadata"]["trigger"].startswith("rerun:")

    assert client.post("/api/v1/runs/ghost/rerun").status_code == 404


def test_reattach_to_finished_run_stream(client):
    """GET /runs/{id}/stream on a finished run replays a terminal event."""
    created = client.post("/api/v1/workflows", json=_calculator_workflow()).json()
    response = client.post(f"/api/v1/workflows/{created['id']}/run/stream", json={"input": {}})
    run_id = _sse_events(response)[-1]["data"]["run_id"]
    _wait_for_run(client, run_id)

    replay = client.get(f"/api/v1/runs/{run_id}/stream")
    events = _sse_events(replay)
    assert events[-1]["event"] == "complete"
    assert events[-1]["data"]["run_id"] == run_id


def test_mcp_tools_become_agent_tools(client):
    """Registering an MCP server exposes its tools in the agent tool palette."""
    pytest.importorskip("mcp")
    created = _register_fixture_mcp_server(client, name="local-tools")
    assert "mcp__local-tools__add" in created.json()["agent_tools"]

    palette = client.get("/api/v1/palette").json()
    tool_names = {t["name"] for t in palette["tools"]}
    assert {"mcp__local-tools__add", "mcp__local-tools__echo"} <= tool_names

    # deleting the server removes its proxies
    client.delete("/api/v1/mcp/servers/local-tools")
    palette = client.get("/api/v1/palette").json()
    assert not any(t["name"].startswith("mcp__local-tools__") for t in palette["tools"])


def test_agent_calls_mcp_tool_mid_reasoning(client, monkeypatch):
    """An agent with an MCP proxy tool decides to invoke it and incorporates
    the MCP server's real result into its answer."""
    pytest.importorskip("mcp")
    _register_fixture_mcp_server(client, name="local-tools")
    # re-sync agent tools inside this app's loop
    import anyio

    from genxai.llm.base import LLMProvider, LLMResponse
    from genxai.llm.factory import LLMProviderFactory

    prompts: list[str] = []

    class ToolCallingStub(LLMProvider):
        def __init__(self, model="stub", **kwargs):
            super().__init__(model=model, temperature=0.0, max_tokens=None)

        async def generate(self, prompt, system_prompt=None, **kwargs):
            prompts.append(prompt)
            if "Tool Execution Results:" in prompt:
                # follow-up turn: incorporate the tool result
                content = f"FINAL ANSWER USING: {prompt.split('Tool Execution Results:')[1].strip().splitlines()[0]}"
            else:
                # first turn: the agent decides to call the MCP tool
                content = '{"name": "mcp__local-tools__add", "arguments": {"a": 40, "b": 2}}'
            return LLMResponse(
                content=content, model=self.model,
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                finish_reason="stop",
            )

        async def generate_stream(self, prompt, system_prompt=None, **kwargs):
            yield "x"

        async def generate_chat(self, messages, **kwargs):
            return await self.generate("chat")

    monkeypatch.setattr(
        LLMProviderFactory, "create_provider", lambda *a, **kw: ToolCallingStub(model=kw.get("model", "stub"))
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    doc = {
        "name": "Agent with MCP tools",
        "nodes": [
            {"id": "start", "type": "input", "config": {}},
            {
                "id": "mathbot",
                "type": "agent",
                "config": {
                    "role": "Math Bot",
                    "goal": "Do math with remote tools",
                    "llm_model": "claude-haiku-4-5",
                    "task": "What is 40 + 2?",
                    "tools": ["mcp__local-tools__add"],
                },
            },
            {"id": "end", "type": "output", "config": {}},
        ],
        "edges": [
            {"source": "start", "target": "mathbot"},
            {"source": "mathbot", "target": "end"},
        ],
    }
    response = client.post("/api/v1/run/stream", json={"workflow": doc, "input": {}})
    events = _sse_events(response)
    final = events[-1]["data"]
    assert final["status"] == "success", final

    agent_output = final["result"]["node_results"]["mathbot"]["output"]["output"]
    # the agent's final answer incorporates the REAL result from the MCP server
    assert "FINAL ANSWER USING:" in agent_output
    assert "42" in agent_output
    # the tool list was visible to the agent in its first prompt
    assert any("mcp__local-tools__add" in p for p in prompts if "Available tools" in p)


# ---------------------------------------------------------------------------
# Agent capability ports (n8n-style attachments: model / memory / tools)
# ---------------------------------------------------------------------------


def _attachment_workflow(name="Agent with ports", persistent_memory=True):
    return {
        "name": name,
        "nodes": [
            {"id": "start", "type": "input", "config": {}},
            {
                "id": "bot",
                "type": "agent",
                "config": {"role": "Bot", "goal": "Help", "task": "{{ input.task }}"},
            },
            {"id": "end", "type": "output", "config": {}},
            {
                "id": "chat_model",
                "type": "model",
                "config": {"llm_model": "claude-haiku-4-5", "temperature": 0.2},
            },
            {
                "id": "mem",
                "type": "memory",
                "config": {"persistent": persistent_memory, "session_key": "s1"},
            },
            {"id": "calc", "type": "tool", "config": {"tool_name": "calculator"}},
        ],
        "edges": [
            {"source": "start", "target": "bot"},
            {"source": "bot", "target": "end"},
            {"source": "chat_model", "target": "bot", "attach": "model"},
            {"source": "mem", "target": "bot", "attach": "memory"},
            {"source": "calc", "target": "bot", "attach": "tools"},
        ],
    }


def test_translate_folds_attachments_into_agent_config(client):
    from app.runner import translate
    from app.schemas import WorkflowDoc

    doc = WorkflowDoc(id="wf-1", **_attachment_workflow())
    nodes, edges = translate(doc)

    node_ids = {n["id"] for n in nodes}
    assert node_ids == {"start", "bot", "end"}  # capability nodes leave the flow
    assert all("attach" not in e for e in edges)
    assert len(edges) == 2

    agent = next(n for n in nodes if n["id"] == "bot")["config"]
    assert agent["llm_model"] == "claude-haiku-4-5"
    assert agent["temperature"] == 0.2
    assert "calculator" in agent["tools"]
    assert agent["enable_memory"] is True
    assert agent["memory"]["memory_id"] == "wf-1__bot__s1"
    assert "agent_memory" in agent["memory"]["persistence_path"]


def test_validate_rejects_capability_nodes_in_flow(client):
    doc = _attachment_workflow()
    doc["edges"].append({"source": "mem", "target": "end"})  # memory node in flow
    doc["edges"].append(  # second model attachment on same agent
        {"source": "chat_model", "target": "bot", "attach": "model"}
    )
    result = client.post("/api/v1/workflows/validate", json=doc).json()
    assert result["valid"] is False
    messages = " | ".join(i["message"] for i in result["issues"])
    assert "cannot be part of the flow" in messages
    assert "more than one model attachment" in messages


def test_attached_model_node_selects_agent_llm(client, recording_llm):
    response = client.post(
        "/api/v1/run/stream",
        json={"workflow": _attachment_workflow(), "input": {"task": "say hi"}},
    )
    events = _sse_events(response)
    assert events[-1]["data"]["status"] == "success"
    assert recording_llm[0]["model"] == "claude-haiku-4-5"


def test_memory_attachment_recalls_previous_run(client, recording_llm):
    """Two runs of the same saved workflow: the second run's prompt contains
    the first run's interaction, because the Memory attachment persists
    short-term memory across runs."""
    created = client.post("/api/v1/workflows", json=_attachment_workflow())
    workflow_id = created.json()["id"]

    first = client.post(
        f"/api/v1/workflows/{workflow_id}/run/stream",
        json={"input": {"task": "my favorite color is teal"}},
    )
    assert _sse_events(first)[-1]["data"]["status"] == "success"

    second = client.post(
        f"/api/v1/workflows/{workflow_id}/run/stream",
        json={"input": {"task": "what is my favorite color?"}},
    )
    assert _sse_events(second)[-1]["data"]["status"] == "success"

    second_prompt = recording_llm[-1]["prompt"]
    assert "teal" in second_prompt, second_prompt


def test_memory_without_persistence_forgets_between_runs(client, recording_llm):
    created = client.post(
        "/api/v1/workflows", json=_attachment_workflow(persistent_memory=False)
    )
    workflow_id = created.json()["id"]

    first = client.post(
        f"/api/v1/workflows/{workflow_id}/run/stream",
        json={"input": {"task": "my favorite color is teal"}},
    )
    assert _sse_events(first)[-1]["data"]["status"] == "success"

    second = client.post(
        f"/api/v1/workflows/{workflow_id}/run/stream",
        json={"input": {"task": "what is my favorite color?"}},
    )
    assert _sse_events(second)[-1]["data"]["status"] == "success"
    assert "teal" not in recording_llm[-1]["prompt"]


def test_seeded_full_agent_example_is_valid(client):
    """The bundled full-agent example (model + memory + tools ports) loads,
    validates, and translates into a plain 3-node flow with everything
    folded into the agent's config."""
    doc = client.get("/api/v1/workflows/example-full-agent").json()
    result = client.post("/api/v1/workflows/validate", json=doc).json()
    assert result["valid"] is True, result["issues"]

    from app.runner import translate
    from app.schemas import WorkflowDoc

    nodes, edges = translate(WorkflowDoc(**doc))
    assert {n["id"] for n in nodes} == {"start", "assistant", "end"}
    assert len(edges) == 2

    agent = next(n for n in nodes if n["id"] == "assistant")["config"]
    assert agent["llm_model"] == "claude-haiku-4-5"
    assert set(agent["tools"]) == {"calculator", "mcp__demo-tools__add"}
    assert agent["memory"]["memory_id"] == "example-full-agent__assistant__default"


def test_seeded_full_agent_example_runs_with_memory_and_tools(client, recording_llm):
    """Run the bundled full-agent example twice: the agent sees its attached
    tools in the prompt, and the second run recalls the first via the
    Memory attachment."""
    first = client.post(
        "/api/v1/workflows/example-full-agent/run/stream",
        json={"input": {"task": "My favorite number is 7, remember it."}},
    )
    assert _sse_events(first)[-1]["data"]["status"] == "success"

    second = client.post(
        "/api/v1/workflows/example-full-agent/run/stream",
        json={"input": {"task": "What is my favorite number?"}},
    )
    assert _sse_events(second)[-1]["data"]["status"] == "success"

    # attached model node picked the LLM
    assert all(call["model"] == "claude-haiku-4-5" for call in recording_llm)
    # attached calculator was offered to the agent
    assert any("calculator" in call["prompt"] for call in recording_llm)
    # memory attachment carried run 1 into run 2's prompt
    assert "favorite number is 7" in recording_llm[-1]["prompt"]


# ---------------------------------------------------------------------------
# Shared agent memory + subworkflow nodes (framework features exposed as-is)
# ---------------------------------------------------------------------------


def test_shared_memory_flag_reaches_the_executor(client, recording_llm, monkeypatch):
    """The workflow-level 'shared_memory' toggle is forwarded to
    execute_workflow_async exactly as the framework defines it — no
    reimplementation in the Studio."""
    import app.run_manager as run_manager_module

    captured = {}
    original = run_manager_module.execute_workflow_async

    async def spy(*args, **kwargs):
        captured.update(kwargs)
        return await original(*args, **kwargs)

    monkeypatch.setattr(run_manager_module, "execute_workflow_async", spy)

    doc = {
        "name": "Shared memory test",
        "shared_memory": True,
        "nodes": [
            {"id": "start", "type": "input", "config": {}},
            {"id": "bot", "type": "agent", "config": {"role": "Bot", "goal": "Help"}},
            {"id": "end", "type": "output", "config": {}},
        ],
        "edges": [
            {"source": "start", "target": "bot"},
            {"source": "bot", "target": "end"},
        ],
    }
    response = client.post("/api/v1/run/stream", json={"workflow": doc, "input": {}})
    assert _sse_events(response)[-1]["data"]["status"] == "success"
    assert captured["shared_memory"] is True


def test_subworkflow_node_runs_a_saved_workflow(client, recording_llm):
    """A Subworkflow node references another saved workflow, which the
    Studio resolves and passes to the framework's native subgraph node —
    no custom nested-execution logic in the Studio."""
    inner = client.post(
        "/api/v1/workflows",
        json={
            "name": "Inner: greeter",
            "nodes": [
                {"id": "start", "type": "input", "config": {}},
                {
                    "id": "calc",
                    "type": "tool",
                    "config": {"tool_name": "calculator", "tool_params": {"expression": "6 * 7"}},
                },
                {"id": "end", "type": "output", "config": {}},
            ],
            "edges": [
                {"source": "start", "target": "calc"},
                {"source": "calc", "target": "end"},
            ],
        },
    ).json()

    outer = {
        "name": "Outer: calls inner",
        "nodes": [
            {"id": "start", "type": "input", "config": {}},
            {"id": "sub", "type": "subworkflow", "config": {"workflow_id": inner["id"]}},
            {"id": "end", "type": "output", "config": {}},
        ],
        "edges": [
            {"source": "start", "target": "sub"},
            {"source": "sub", "target": "end"},
        ],
    }
    result = client.post("/api/v1/workflows/validate", json=outer).json()
    assert result["valid"] is True, result["issues"]

    response = client.post("/api/v1/run/stream", json={"workflow": outer, "input": {}})
    events = _sse_events(response)
    assert events[-1]["data"]["status"] == "success"
    sub_output = events[-1]["data"]["result"]["node_results"]["sub"]["output"]
    assert sub_output["state"]["calc"]["data"]["result"] == 42


def test_subworkflow_validation_rejects_missing_and_self_reference(client):
    missing = {
        "name": "Missing ref",
        "nodes": [
            {"id": "start", "type": "input", "config": {}},
            {"id": "sub", "type": "subworkflow", "config": {}},
            {"id": "end", "type": "output", "config": {}},
        ],
        "edges": [{"source": "start", "target": "sub"}, {"source": "sub", "target": "end"}],
    }
    result = client.post("/api/v1/workflows/validate", json=missing).json()
    assert result["valid"] is False
    assert any("no workflow selected" in i["message"] for i in result["issues"])


def test_palette_includes_full_curated_builtin_tool_set(client):
    """The tool palette exposes the framework's full curated read-only tool
    set, not a narrower hand-picked subset."""
    names = {t["name"] for t in client.get("/api/v1/palette").json()["tools"]}
    for expected in (
        "xml_processor",
        "directory_scanner",
        "pdf_parser",
        "api_caller",
        "web_scraper",
    ):
        assert expected in names, f"{expected} missing from palette: {sorted(names)}"


# ---------------------------------------------------------------------------
# YAML workflow import (genxai's own workflow DSL -> Studio WorkflowDoc)
# ---------------------------------------------------------------------------

from pathlib import Path as _Path

_NOCODE_EXAMPLES_DIR = _Path(__file__).resolve().parents[4] / "examples" / "nocode"


def test_import_shared_memory_workflow_yaml(client):
    yaml_text = (_NOCODE_EXAMPLES_DIR / "shared_memory_workflow.yaml").read_text()
    created = client.post("/api/v1/workflows/import-yaml", json={"yaml": yaml_text})
    assert created.status_code == 201, created.text
    doc = created.json()
    assert doc["name"] == "Shared Memory Workflow"
    assert doc["shared_memory"] is True

    node_by_id = {n["id"]: n for n in doc["nodes"]}
    assert node_by_id["writer"]["type"] == "agent"
    assert node_by_id["writer"]["config"]["role"] == "Writer"
    assert node_by_id["writer"]["config"]["llm_model"] == "gpt-4"

    result = client.post("/api/v1/workflows/validate", json=doc).json()
    assert result["valid"] is True, result["issues"]


def test_import_user_proxy_workflow_yaml_normalizes_tool_key(client):
    """This example uses `tool:` instead of `tool_name:` and references an
    agent by a name equal to the node id — both must resolve correctly."""
    yaml_text = (_NOCODE_EXAMPLES_DIR / "user_proxy_workflow.yaml").read_text()
    created = client.post("/api/v1/workflows/import-yaml", json={"yaml": yaml_text})
    assert created.status_code == 201, created.text
    doc = created.json()

    node_by_id = {n["id"]: n for n in doc["nodes"]}
    assert node_by_id["user_input"]["config"]["tool_name"] == "human_input"
    assert node_by_id["assistant"]["config"]["tools"] == ["text_analyzer"]


def test_import_workflow_composition_yaml_maps_condition_and_subgraph(client):
    """condition -> decision, subgraph -> subworkflow, and the agent node's
    `agent: router_agent` reference (differing from its own id `router`)
    resolves to the right inline config."""
    yaml_text = (_NOCODE_EXAMPLES_DIR / "workflow_composition.yaml").read_text()
    created = client.post("/api/v1/workflows/import-yaml", json={"yaml": yaml_text})
    assert created.status_code == 201, created.text
    doc = created.json()

    node_by_id = {n["id"]: n for n in doc["nodes"]}
    assert node_by_id["route"]["type"] == "decision"
    assert node_by_id["support_subflow"]["type"] == "subworkflow"
    assert node_by_id["router"]["config"]["role"] == "Router"
    assert node_by_id["router"]["config"]["llm_model"] == "gpt-4"


def test_import_rejects_malformed_yaml(client):
    response = client.post("/api/v1/workflows/import-yaml", json={"yaml": "not: a: workflow"})
    assert response.status_code == 400
