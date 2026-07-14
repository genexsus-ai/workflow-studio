"""Studio-owned SQL persistence for run records (PERSISTENCE_BACKEND=postgres).

Subclasses the in-memory ExecutionStore the run manager already uses, but
persists every record into the Studio's own ``ws_runs`` table instead of
JSON files (or the framework's SQL table). All records are preloaded at
startup so run history survives restarts and stays listable via _records.
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from genxai.core.execution import ExecutionStore
from genxai.core.execution.metadata import ExecutionRecord

_metadata = sa.MetaData()

runs_table = sa.Table(
    "ws_runs",
    _metadata,
    sa.Column("run_id", sa.String(64), primary_key=True),
    sa.Column("workflow", sa.String, nullable=False),
    sa.Column("status", sa.String(32), nullable=False),
    sa.Column("started_at", sa.String(40), nullable=False),
    sa.Column("completed_at", sa.String(40)),
    sa.Column("metadata", sa.Text, nullable=True),
    sa.Column("error", sa.Text, nullable=True),
    sa.Column("result", sa.Text, nullable=True),
)


class StudioExecutionStore(ExecutionStore):
    """ExecutionStore persisting to the Studio database's ws_runs table."""

    def __init__(self, url: str) -> None:
        super().__init__()
        self._studio_engine = sa.create_engine(url, pool_pre_ping=True)
        _metadata.create_all(self._studio_engine)
        self._load_from_db()

    def _load_from_db(self) -> None:
        with self._studio_engine.begin() as conn:
            rows = conn.execute(sa.select(runs_table)).mappings().all()
        for row in rows:
            record = ExecutionRecord(
                run_id=row["run_id"],
                workflow=row["workflow"],
                status=row["status"],
                started_at=row["started_at"],
                completed_at=row["completed_at"],
                metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                error=row["error"],
                result=json.loads(row["result"]) if row["result"] else None,
            )
            self._records[record.run_id] = record

    def _persist(self, record: ExecutionRecord) -> None:
        values = {
            "run_id": record.run_id,
            "workflow": record.workflow,
            "status": record.status,
            "started_at": record.started_at,
            "completed_at": record.completed_at,
            "metadata": json.dumps(record.metadata, default=str),
            "error": record.error,
            "result": (
                json.dumps(record.result, default=str)
                if record.result is not None
                else None
            ),
        }
        with self._studio_engine.begin() as conn:
            exists = conn.execute(
                sa.select(runs_table.c.run_id).where(
                    runs_table.c.run_id == record.run_id
                )
            ).first()
            if exists:
                conn.execute(
                    runs_table.update()
                    .where(runs_table.c.run_id == record.run_id)
                    .values(**values)
                )
            else:
                conn.execute(runs_table.insert().values(**values))

    def delete(self, run_id: str) -> bool:
        existed = self._records.pop(run_id, None) is not None
        with self._studio_engine.begin() as conn:
            result = conn.execute(
                runs_table.delete().where(runs_table.c.run_id == run_id)
            )
        return existed or bool(result.rowcount)

    def close(self) -> None:
        # __del__ on the base class calls close(); the engine may not exist
        # if __init__ failed while connecting.
        engine = getattr(self, "_studio_engine", None)
        if engine is not None:
            engine.dispose()
