"""One-time migration: import file-based Studio data into the SQL database.

Copies workflows (data/workflows/*.json), their version snapshots
(data/versions/<id>/*.json), run records (data/runs/execution_*.json), and
the SQLite database (data/datasets.db: datasets/rows plus the sources,
analyses, analytics_reports, models and experiments tables) into the
database named by DATABASE_URL. Existing rows are left untouched,
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

    migrate_sqlite(engine, data_dir)


def migrate_sqlite(engine: sa.Engine, data_dir: Path) -> None:
    """Import data/datasets.db (datasets + the studio's app tables)."""
    import sqlite3

    sqlite_path = data_dir / "datasets.db"
    if not sqlite_path.exists():
        print("datasets.db: not present, nothing to migrate")
        return

    from app.dataset_store import StudioDatasetStore, datasets_table, rows_table

    StudioDatasetStore(engine)  # ensures ws_datasets / ws_dataset_rows exist
    src = sqlite3.connect(sqlite_path)

    migrated = skipped = 0
    with engine.begin() as conn:
        for name, created_at in src.execute(
            "SELECT name, created_at FROM datasets"
        ).fetchall():
            exists = conn.execute(
                sa.select(datasets_table.c.name).where(datasets_table.c.name == name)
            ).first()
            has_rows = conn.execute(
                sa.select(rows_table.c.id)
                .where(rows_table.c.dataset == name)
                .limit(1)
            ).first()
            if exists and has_rows:
                skipped += 1
                continue
            if not exists:
                conn.execute(
                    datasets_table.insert().values(name=name, created_at=created_at)
                )
            if not has_rows:
                payload = [
                    {"dataset": name, "created_at": row_created, "data": data}
                    for row_created, data in src.execute(
                        "SELECT created_at, data FROM rows WHERE dataset = ? "
                        "ORDER BY id",
                        (name,),
                    ).fetchall()
                ]
                if payload:
                    conn.execute(rows_table.insert(), payload)
            migrated += 1
    print(f"datasets:  {migrated} migrated, {skipped} already present")

    # The app-table store classes create their tables on first use.
    from app.analytics_code import get_report_store
    from app.data_catalog import get_source_registry
    from app.datascience import get_analysis_store
    from app.experiments import get_experiment_store
    from app.ml import get_model_registry

    get_source_registry(), get_report_store(), get_analysis_store()
    get_experiment_store(), get_model_registry()

    for table in ("sources", "analyses", "analytics_reports", "models", "experiments"):
        present = src.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name = ?",
            (table,),
        ).fetchone()
        if not present:
            print(f"{table}: not in datasets.db, skipped")
            continue
        columns = [row[1] for row in src.execute(f"PRAGMA table_info({table})")]
        rows = src.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
        placeholders = ", ".join(["%s"] * len(columns))
        migrated = 0
        with engine.begin() as conn:
            for row in rows:
                result = conn.exec_driver_sql(
                    f"INSERT INTO {table} ({', '.join(columns)}) "
                    f"VALUES ({placeholders}) ON CONFLICT DO NOTHING",
                    tuple(row),
                )
                migrated += result.rowcount
        print(f"{table}: {migrated} migrated, {len(rows) - migrated} already present")
    src.close()


if __name__ == "__main__":
    main()
