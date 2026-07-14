"""Tests for the Python code-stage runtime."""

import pytest

from app.code_stage import check_imports, run_code_stage


def test_import_allowlist():
    assert check_imports("import pandas\nfrom sklearn.linear_model import X") == []
    assert check_imports("import os\nimport requests") == ["requests"]


@pytest.mark.anyio
async def test_code_stage_contract_roundtrip(client):
    code = (
        "import pandas as pd\n"
        "import json\n"
        "import matplotlib.pyplot as plt\n"
        "df = pd.read_parquet('data/data.parquet')\n"
        "df['double'] = df['n'] * 2\n"
        "df.to_parquet('out/datasets/doubled.parquet')\n"
        "plt.plot(df['n']); plt.savefig('out/figures/n.png')\n"
        "json.dump({'rows': len(df)}, open('out/metrics.json', 'w'))\n"
        "print('done')\n"
    )
    result = await run_code_stage(code, {"data": [{"n": 1}, {"n": 2}]})

    assert result["status"] == "ok", result.get("error")
    assert "done" in result["stdout"]
    assert result["metrics"] == {"rows": 2}
    assert len(result["figures"]) == 1
    assert result["datasets"]["doubled"][0]["double"] == 2


@pytest.mark.anyio
async def test_code_stage_rejects_disallowed_imports(client):
    result = await run_code_stage("import socket\n", {"data": [{"a": 1}]})
    assert result["status"] == "error"
    assert "Disallowed imports" in result["error"]


@pytest.mark.anyio
async def test_code_stage_timeout(client):
    result = await run_code_stage(
        "while True:\n    pass\n", {"data": [{"a": 1}]}, timeout=2
    )
    assert result["status"] == "error"
    assert "timed out" in result["error"]


@pytest.mark.anyio
async def test_code_stage_script_error_reported(client):
    result = await run_code_stage("raise ValueError('boom')\n", {"data": [{"a": 1}]})
    assert result["status"] == "error"
    assert "exited with code" in result["error"]
    assert "boom" in result["stdout"]
