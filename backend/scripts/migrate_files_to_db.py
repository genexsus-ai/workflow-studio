"""One-time migration: import file-based Studio data into the SQL database.

Copies workflows (data/workflows/*.json), their version snapshots
(data/versions/<id>/*.json), and run records (data/runs/execution_*.json)
into the database named by DATABASE_URL. Existing rows are left untouched,
so the script is safe to re-run. The source files are not modified.

Usage (from the backend root, with PERSISTENCE_BACKEND=postgres in .env):
    .venv/bin/python scripts/migrate_files_to_db.py
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy as sa

from app.config import get_settings
from app.db_store import DbWorkflowStore, versions_table, workflows_table
from app.schemas import WorkflowDoc


def main() -> None:
    settings = get_settings()
    if not settings.use_db_persistence:
        sys.exit(
            "PERSISTENCE_BACKEND=postgres and DATABASE_URL must be set in .env"
        )

    store = DbWorkflowStore(settings.sync_database_url)
    engine = store._engine
    data_dir = settings.data_dir

    migrated = skipped = 0
    for path in sorted((data_dir / "workflows").glob("*.json")):
        try:
            doc = WorkflowDoc.model_validate_json(path.read_text())
        except Exception as exc:
            print(f"  ! skipping unreadable {path.name}: {exc}")
            continue
        doc.id = doc.id or path.stem
        if store.get(doc.id) is not None:
            skipped += 1
            continue
        store.create(doc)
        migrated += 1
    print(f"workflows: {migrated} migrated, {skipped} already present")

    migrated = skipped = 0
    versions_root = data_dir / "versions"
    if versions_root.is_dir():
        with engine.begin() as conn:
            for version_dir in sorted(versions_root.iterdir()):
                if not version_dir.is_dir():
                    continue
                for snap in sorted(version_dir.glob("*.json")):
                    exists = conn.execute(
                        sa.select(versions_table.c.version).where(
                            versions_table.c.workflow_id == version_dir.name,
                            versions_table.c.version == snap.stem,
                        )
                    ).first()
                    if exists:
                        skipped += 1
                        continue
                    saved_at = (
                        datetime.strptime(snap.stem, "%Y%m%dT%H%M%S%f")
                        .replace(tzinfo=UTC)
                        .isoformat()
                    )
                    conn.execute(
                        versions_table.insert().values(
                            workflow_id=version_dir.name,
                            version=snap.stem,
                            saved_at=saved_at,
                            doc=snap.read_text(),
                        )
                    )
                    migrated += 1
    print(f"versions:  {migrated} migrated, {skipped} already present")

    from app.exec_store import StudioExecutionStore

    exec_store = StudioExecutionStore(settings.sync_database_url)
    already = set(exec_store._records)
    migrated = skipped = 0
    runs_dir = data_dir / "runs"
    if runs_dir.is_dir():
        for path in sorted(runs_dir.glob("execution_*.json")):
            try:
                data = json.loads(path.read_text())
            except Exception as exc:
                print(f"  ! skipping unreadable {path.name}: {exc}")
                continue
            run_id = data.get("run_id")
            if not run_id or run_id in already:
                skipped += 1
                continue
            record = exec_store.create(
                run_id,
                workflow=data.get("workflow", ""),
                status=data.get("status", "unknown"),
                metadata=data.get("metadata") or {},
            )
            record.started_at = data.get("started_at") or record.started_at
            record.completed_at = data.get("completed_at")
            record.error = data.get("error")
            record.result = data.get("result")
            exec_store._persist(record)
            migrated += 1
    exec_store.close()
    print(f"runs:      {migrated} migrated, {skipped} already present")


if __name__ == "__main__":
    main()
