"""Tests for agent nodes attached to a flow node's Agents port."""


def _doc():
    return {
        "name": "Team Test",
        "nodes": [
            {"id": "start", "type": "input", "position": {"x": 0, "y": 0}, "config": {}},
            {
                "id": "team",
                "type": "flow",
                "position": {"x": 200, "y": 0},
                "config": {"flow_type": "critic_review", "agents": [], "params": {}},
            },
            {"id": "end", "type": "output", "position": {"x": 400, "y": 0}, "config": {}},
            # Attached team, deliberately declared right-to-left to prove
            # x-position ordering (writer at x=150 comes before critic at x=350).
            {
                "id": "critic",
                "type": "agent",
                "position": {"x": 350, "y": 150},
                "config": {"role": "Critic", "goal": "Review drafts"},
            },
            {
                "id": "writer",
                "type": "agent",
                "position": {"x": 150, "y": 150},
                "config": {"role": "Writer", "goal": "Draft the post", "temperature": 0.7},
            },
        ],
        "edges": [
            {"source": "start", "target": "team"},
            {"source": "team", "target": "end"},
            {"source": "critic", "target": "team", "attach": "agents"},
            {"source": "writer", "target": "team", "attach": "agents"},
        ],
    }


def test_attached_agents_fold_into_flow_team_in_x_order(client):
    from app.runner import translate
    from app.schemas import WorkflowDoc

    nodes, edges = translate(WorkflowDoc.model_validate(_doc()))

    ids = [node["id"] for node in nodes]
    assert ids == ["start", "team", "end"]  # attached agents leave the flow
    team = next(node for node in nodes if node["id"] == "team")
    roles = [agent["role"] for agent in team["config"]["agents"]]
    assert roles == ["Writer", "Critic"]  # x-ordered, not declaration-ordered
    assert team["config"]["agents"][0]["temperature"] == 0.7
    assert all(not edge.get("attach") for edge in edges)


def test_flow_with_attached_team_validates(client):
    result = client.post("/api/v1/workflows/validate", json=_doc()).json()
    assert result["valid"] is True, result["issues"]


def test_agents_attachment_must_target_flow(client):
    doc = _doc()
    doc["edges"][2]["target"] = "end"  # agents edge into an output node

    result = client.post("/api/v1/workflows/validate", json=doc).json()

    assert result["valid"] is False
    assert any("must target a flow node" in issue["message"] for issue in result["issues"])
