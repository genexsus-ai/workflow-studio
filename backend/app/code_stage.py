"""Code stages: run agent-written Python under the experiment I/O contract.

Contract (see DATA_SCIENCE_APP_DESIGN.md):
- inputs materialize as ``./data/<alias>.parquet`` in a temp workdir
- the script writes to ``./out/``: ``figures/*.png`` (→ file store),
  ``datasets/*.parquet|csv`` (→ dataset store, collected by caller),
  ``model.joblib`` + ``metrics.json`` (→ model registry, by caller)
- subprocess isolation: scrubbed environment (data, never credentials),
  MPLBACKEND=Agg, wall-clock timeout, stdout tail capped
- a static import allowlist is enforced before execution; the Code Review
  Agent is the primary gate, this is the backstop

Disable code stages entirely with GENXAI_DISABLE_CODE_STAGES=1.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

from genxai.core.files import get_file_store

logger = logging.getLogger(__name__)

CODE_TIMEOUT_SECONDS = 180
STDOUT_TAIL_CHARS = 4000
MAX_FIGURES = 6
MAX_OUTPUT_ROWS = 100_000

ALLOWED_IMPORTS = {
    # data science stack
    "pandas", "numpy", "sklearn", "scipy", "matplotlib", "joblib", "pyarrow",
    # stdlib
    "json", "math", "statistics", "datetime", "itertools", "collections",
    "functools", "pathlib", "random", "re", "csv", "io", "typing",
}

_IMPORT_RE = re.compile(
    r"^\s*(?:import|from)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE
)


def code_stages_enabled() -> bool:
    return os.environ.get("GENXAI_DISABLE_CODE_STAGES") != "1"


def check_imports(code: str) -> list[str]:
    """Module names used by the code that are not on the allowlist."""
    return sorted(
        {m for m in _IMPORT_RE.findall(code) if m not in ALLOWED_IMPORTS}
    )


def _write_inputs(workdir: Path, inputs: dict[str, list[dict[str, Any]]]) -> None:
    """Materialize each input rowset as ./data/<alias>.parquet via DuckDB."""
    import duckdb

    from app.data_catalog import _duckdb_load

    data_dir = workdir / "data"
    data_dir.mkdir()
    con = duckdb.connect()
    try:
        for alias, rows in inputs.items():
            _duckdb_load(con, alias, rows)
            con.execute(
                f"COPY {alias} TO '{data_dir / (alias + '.parquet')}' (FORMAT PARQUET)"
            )
    finally:
        con.close()


def _collect_outputs(workdir: Path) -> dict[str, Any]:
    """Harvest the ./out contract into platform stores and JSON-safe refs."""
    out_dir = workdir / "out"
    collected: dict[str, Any] = {"figures": [], "datasets": {}, "metrics": None,
                                 "model_file_id": None}
    if not out_dir.exists():
        return collected

    store = get_file_store()
    figures_dir = out_dir / "figures"
    if figures_dir.exists():
        for figure in sorted(figures_dir.glob("*.png"))[:MAX_FIGURES]:
            ref = store.save_bytes(
                figure.read_bytes(), name=figure.name, media_type="image/png"
            )
            collected["figures"].append(ref)

    datasets_dir = out_dir / "datasets"
    if datasets_dir.exists():
        import duckdb

        con = duckdb.connect()
        try:
            for path in sorted(datasets_dir.iterdir()):
                if path.suffix not in (".parquet", ".csv"):
                    continue
                reader = (
                    f"read_parquet('{path}')"
                    if path.suffix == ".parquet"
                    else f"read_csv_auto('{path}')"
                )
                result = con.execute(
                    f"SELECT * FROM {reader} LIMIT {MAX_OUTPUT_ROWS}"
                )
                columns = [d[0] for d in result.description]
                rows = [dict(zip(columns, record)) for record in result.fetchall()]
                collected["datasets"][path.stem] = rows
        finally:
            con.close()

    metrics_path = out_dir / "metrics.json"
    if metrics_path.exists():
        try:
            collected["metrics"] = json.loads(metrics_path.read_text())
        except Exception as exc:
            collected["metrics"] = {"error": f"metrics.json unreadable: {exc}"}

    model_path = out_dir / "model.joblib"
    if model_path.exists():
        ref = store.save_bytes(
            model_path.read_bytes(),
            name="model.joblib",
            media_type="application/octet-stream",
        )
        collected["model_file_id"] = ref["id"]

    return collected


async def run_code_stage(
    code: str,
    inputs: dict[str, list[dict[str, Any]]],
    timeout: float = CODE_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """Execute a Python code stage under the contract; returns outputs.

    Result: {status, stdout, figures, datasets, metrics, model_file_id,
    error?}. Never raises for script failures — errors are data the
    pipeline (and reviewer feedback loop) can react to.
    """
    if not code_stages_enabled():
        return {"status": "error", "error": "Code stages are disabled "
                "(GENXAI_DISABLE_CODE_STAGES=1)"}
    disallowed = check_imports(code)
    if disallowed:
        return {
            "status": "error",
            "error": f"Disallowed imports: {', '.join(disallowed)} — allowed: "
            + ", ".join(sorted(ALLOWED_IMPORTS)),
        }

    with tempfile.TemporaryDirectory(prefix="genxai-code-") as tmp:
        workdir = Path(tmp)
        _write_inputs(workdir, inputs)
        (workdir / "out").mkdir()
        (workdir / "out" / "figures").mkdir()
        (workdir / "out" / "datasets").mkdir()
        script = workdir / "script.py"
        script.write_text(code)

        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(workdir),
            "MPLBACKEND": "Agg",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            str(script),
            cwd=str(workdir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return {
                "status": "error",
                "error": f"Code stage timed out after {timeout}s",
            }

        tail = stdout.decode(errors="replace")[-STDOUT_TAIL_CHARS:]
        outputs = _collect_outputs(workdir)
        if process.returncode != 0:
            return {
                "status": "error",
                "error": f"Script exited with code {process.returncode}",
                "stdout": tail,
                **outputs,
            }
        return {"status": "ok", "stdout": tail, **outputs}
