"""Tests for workflow version history and error workflows."""

import time


def _calc_doc(name: str, expression: str) -> dict:
    return {
        "name": name,
        "nodes": [
            {
                "id": "calc",
                "type": "tool",
                "position": {"x": 0, "y": 0},
                "config": {
                    "tool_name": "calculator",
                    "tool_params": {"expression": expression},
                },
            },
        ],
        "edges": [],
    }


def _wait_done(client, run_id: str) -> dict:
    for _ in range(100):
        record = client.get(f"/api/v1/runs/{run_id}").json()
        if record["status"] not in ("queued", "running"):
            return record
        time.sleep(0.05)
    raise AssertionError("run did not finish")


# ------------------------------------------------------------------ versions


def test_updates_create_restorable_versions(client):
    created = client.post("/api/v1/workflows", json=_calc_doc("V1", "1 + 1")).json()
    workflow_id = created["id"]

    assert client.get(f"/api/v1/workflows/{workflow_id}/versions").json() == []

    v2 = _calc_doc("V2", "2 + 2")
    v2["id"] = workflow_id
    client.put(f"/api/v1/workflows/{workflow_id}", json=v2)
    v3 = _calc_doc("V3", "3 + 3")
    v3["id"] = workflow_id
    client.put(f"/api/v1/workflows/{workflow_id}", json=v3)

    versions = client.get(f"/api/v1/workflows/{workflow_id}/versions").json()
    assert [v["name"] for v in versions] == ["V2", "V1"]  # newest first

    # Fetch a specific version
    v1_doc = client.get(
        f"/api/v1/workflows/{workflow_id}/versions/{versions[1]['version']}"
    ).json()
    assert v1_doc["name"] == "V1"

    # Restore V1: it becomes current, and V3 lands in history
    restored = client.post(
        f"/api/v1/workflows/{workflow_id}/versions/{versions[1]['version']}/restore"
    ).json()
    assert restored["name"] == "V1"
    assert client.get(f"/api/v1/workflows/{workflow_id}").json()["name"] == "V1"
    names = [
        v["name"]
        for v in client.get(f"/api/v1/workflows/{workflow_id}/versions").json()
    ]
    assert names[0] == "V3"


def test_version_endpoints_404_for_unknown(client):
    assert client.get("/api/v1/workflows/nope/versions").status_code == 404
    created = client.post("/api/v1/workflows", json=_calc_doc("X", "1")).json()
    assert (
        client.get(f"/api/v1/workflows/{created['id']}/versions/20990101T000000000000").status_code
        == 404
    )


# ------------------------------------------------------------ error workflows


def test_failed_run_triggers_error_workflow(client):
    handler = client.post(
        "/api/v1/workflows",
        json=_calc_doc("Failure handler", "1 + 1"),
    ).json()

    failing = _calc_doc("Fragile", "{{ input.missing }} + 1")
    failing["automation"] = {
        "webhook_enabled": False,
        "schedule_enabled": False,
        "interval_seconds": 300,
        "error_workflow_id": handler["id"],
    }
    created = client.post("/api/v1/workflows", json=failing).json()
    enabled = client.post(
        f"/api/v1/workflows/{created['id']}/automation",
        json={
            "webhook_enabled": True,
            "schedule_enabled": False,
            "interval_seconds": 300,
            "error_workflow_id": handler["id"],
        },
    ).json()
    token = enabled["automation"]["webhook_token"]

    run_id = client.post(f"/api/v1/hooks/{token}", json={}).json()["run_id"]
    failed = _wait_done(client, run_id)
    assert failed["status"] == "error"

    # The handler run appears, triggered by the failure, with error context
    handler_run = None
    for _ in range(100):
        runs = client.get("/api/v1/runs").json()
        handler_run = next(
            (
                r
                for r in runs
                if r["workflow"] == "Failure handler"
                and str(r["metadata"].get("trigger", "")).startswith("error:")
            ),
            None,
        )
        if handler_run and handler_run["status"] not in ("queued", "running"):
            break
        time.sleep(0.05)

    assert handler_run is not None
    payload = handler_run["metadata"]["input"]
    assert payload["failed_run_id"] == run_id
    assert payload["workflow_name"] == "Fragile"
    assert "missing" in payload["error"]
    assert "calc" in payload["failed_nodes"]


def test_error_workflow_failure_does_not_cascade(client):
    # Handler that itself fails
    handler = client.post(
        "/api/v1/workflows", json=_calc_doc("Bad handler", "{{ input.nope }}")
    ).json()
    # Point the handler at itself as its own error workflow: if cascading
    # were allowed this would loop forever
    client.post(
        f"/api/v1/workflows/{handler['id']}/automation",
        json={
            "webhook_enabled": True,
            "schedule_enabled": False,
            "interval_seconds": 300,
            "error_workflow_id": handler["id"],
        },
    )

    failing = _calc_doc("Fragile2", "{{ input.missing }}")
    created = client.post("/api/v1/workflows", json=failing).json()
    enabled = client.post(
        f"/api/v1/workflows/{created['id']}/automation",
        json={
            "webhook_enabled": True,
            "schedule_enabled": False,
            "interval_seconds": 300,
            "error_workflow_id": handler["id"],
        },
    ).json()

    run_id = client.post(f"/api/v1/hooks/{enabled['automation']['webhook_token']}", json={}).json()[
        "run_id"
    ]
    _wait_done(client, run_id)

    # Give the handler time to run and fail
    deadline = time.time() + 3
    handler_runs: list = []
    while time.time() < deadline:
        runs = client.get("/api/v1/runs").json()
        handler_runs = [r for r in runs if r["workflow"] == "Bad handler"]
        if handler_runs and all(
            r["status"] not in ("queued", "running") for r in handler_runs
        ):
            break
        time.sleep(0.05)

    # Exactly one handler run: its own failure did not chain another
    assert len(handler_runs) == 1
    assert handler_runs[0]["status"] == "error"
