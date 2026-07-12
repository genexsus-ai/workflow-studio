"""Tests for binary data: file tools through the studio + download endpoint."""


def test_file_write_and_download_roundtrip(client):
    doc = {
        "name": "File flow",
        "nodes": [
            {
                "id": "write",
                "type": "tool",
                "position": {"x": 0, "y": 0},
                "config": {
                    "tool_name": "file_write",
                    "tool_params": {
                        "content": "hello file",
                        "name": "note.txt",
                        "media_type": "text/plain",
                    },
                },
            },
        ],
        "edges": [],
    }
    created = client.post("/api/v1/workflows", json=doc).json()

    result = client.post(
        f"/api/v1/workflows/{created['id']}/test-node",
        json={"node_id": "write", "input": {}},
    ).json()

    assert result["status"] == "success"
    ref = result["output"]["data"]["file"]
    assert ref["__genxai_file__"] is True
    assert ref["name"] == "note.txt"

    download = client.get(f"/api/v1/files/{ref['id']}")
    assert download.status_code == 200
    assert download.content == b"hello file"
    assert download.headers["content-type"].startswith("text/plain")
    assert "note.txt" in download.headers.get("content-disposition", "")


def test_download_unknown_file_404(client):
    assert client.get(f"/api/v1/files/{'b' * 64}").status_code == 404
    assert client.get("/api/v1/files/not-a-hash").status_code == 404


def test_file_tools_registered_in_palette(client):
    palette = client.get("/api/v1/palette").json()
    tool_names = {tool["name"] for tool in palette["tools"]}
    assert {"file_download", "file_write", "file_content"} <= tool_names


def test_excel_tools_registered_and_roundtrip(client):
    palette = client.get("/api/v1/palette").json()
    tool_names = {tool["name"] for tool in palette["tools"]}
    assert {"excel_read", "excel_write"} <= tool_names

    doc = {
        "name": "Excel flow",
        "nodes": [
            {"id": "start", "type": "input", "position": {"x": 0, "y": 0}, "config": {}},
            {
                "id": "write",
                "type": "tool",
                "position": {"x": 100, "y": 0},
                "config": {
                    "tool_name": "excel_write",
                    "tool_params": {"rows": "{{ input.rows }}", "name": "out.xlsx"},
                },
            },
        ],
        "edges": [{"source": "start", "target": "write"}],
    }
    created = client.post("/api/v1/workflows", json=doc).json()
    result = client.post(
        f"/api/v1/workflows/{created['id']}/test-node",
        json={"node_id": "write", "input": {"rows": [{"a": 1}, {"a": 2}]}},
    ).json()

    assert result["status"] == "success"
    ref = result["output"]["data"]["file"]
    download = client.get(f"/api/v1/files/{ref['id']}")
    assert download.status_code == 200
    assert download.content[:2] == b"PK"
    assert "out.xlsx" in download.headers.get("content-disposition", "")
