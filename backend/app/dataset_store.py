"""Postgres-backed dataset store (PERSISTENCE_BACKEND=postgres).

Same behavior as genxai's SQLite DatasetStore, persisted to the studio
database's ``ws_datasets`` / ``ws_dataset_rows`` tables. Installed as the
framework's dataset-store singleton at startup so every consumer — studio
routes and genxai's dataset workflow nodes alike — reads and writes the
shared database. genxai core keeps its SQLite default for non-studio use.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

import genxai.core.datasets as gx_datasets
from genxai.core.datasets import (
    MAX_SCAN_ROWS,
    DatasetStore,
    _validate_name,
    aggregate_rows,
)

_metadata = sa.MetaData()

datasets_table = sa.Table(
    "ws_datasets",
    _metadata,
    sa.Column("name", sa.String(64), primary_key=True),
    sa.Column("created_at", sa.String(40), nullable=False),
)

rows_table = sa.Table(
    "ws_dataset_rows",
    _metadata,
    sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
    sa.Column("dataset", sa.String(64), nullable=False),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Column("data", sa.Text, nullable=False),
    sa.Index("idx_ws_dataset_rows_dataset", "dataset", "created_at"),
)


class StudioDatasetStore(DatasetStore):
    """DatasetStore interface against the studio's Postgres tables."""

    def __init__(self, engine: sa.Engine) -> None:  # no super(): no SQLite file
        self._engine = engine
        _metadata.create_all(engine)

    def append(self, dataset: str, rows: list[dict[str, Any]]) -> int:
        _validate_name(dataset)
        clean: list[str] = []
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(
                    f"Dataset rows must be objects, got {type(row).__name__}"
                )
            clean.append(json.dumps(row, default=str))
        now = datetime.now(UTC).isoformat()
        with self._engine.begin() as conn:
            registered = conn.execute(
                sa.select(datasets_table.c.name).where(
                    datasets_table.c.name == dataset
                )
            ).first()
            if registered is None:
                conn.execute(
                    datasets_table.insert().values(name=dataset, created_at=now)
                )
            if clean:
                conn.execute(
                    rows_table.insert(),
                    [
                        {"dataset": dataset, "created_at": now, "data": data}
                        for data in clean
                    ],
                )
        return len(clean)

    def replace(self, dataset: str, rows: list[dict[str, Any]]) -> int:
        _validate_name(dataset)
        with self._engine.begin() as conn:
            conn.execute(
                rows_table.delete().where(rows_table.c.dataset == dataset)
            )
        return self.append(dataset, rows)

    def list_datasets(self) -> list[dict[str, Any]]:
        with self._engine.begin() as conn:
            rows = conn.execute(
                sa.select(
                    datasets_table.c.name,
                    datasets_table.c.created_at,
                    sa.func.count(rows_table.c.id).label("row_count"),
                    sa.func.max(rows_table.c.created_at).label("last_at"),
                )
                .select_from(
                    datasets_table.outerjoin(
                        rows_table, rows_table.c.dataset == datasets_table.c.name
                    )
                )
                .group_by(datasets_table.c.name, datasets_table.c.created_at)
                .order_by(sa.desc(sa.text("last_at")).nulls_last())
            ).all()
        return [
            {
                "name": name,
                "created_at": created_at,
                "rows": row_count,
                "last_written_at": last_at,
            }
            for name, created_at, row_count, last_at in rows
        ]

    def rows(
        self, dataset: str, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        """Newest-first page of rows plus the dataset's total count."""
        _validate_name(dataset)
        with self._engine.begin() as conn:
            total = conn.execute(
                sa.select(sa.func.count())
                .select_from(rows_table)
                .where(rows_table.c.dataset == dataset)
            ).scalar()
            page_rows = conn.execute(
                sa.select(rows_table.c.id, rows_table.c.created_at, rows_table.c.data)
                .where(rows_table.c.dataset == dataset)
                .order_by(rows_table.c.id.desc())
                .limit(limit)
                .offset(offset)
            ).all()
        page = [
            {"_id": row_id, "_created_at": created_at, **json.loads(data)}
            for row_id, created_at, data in page_rows
        ]
        return {"rows": page, "total": total}

    def aggregate(
        self,
        dataset: str,
        metric: str = "count",
        field: str | None = None,
        group_by: str | None = None,
    ) -> list[dict[str, Any]]:
        _validate_name(dataset)
        with self._engine.begin() as conn:
            raw = conn.execute(
                sa.select(rows_table.c.data)
                .where(rows_table.c.dataset == dataset)
                .order_by(rows_table.c.id.desc())
                .limit(MAX_SCAN_ROWS)
            ).scalars().all()
        raw_rows = [json.loads(data) for data in raw]
        return aggregate_rows(raw_rows, metric=metric, field=field, group_by=group_by)

    def delete_dataset(self, dataset: str) -> bool:
        _validate_name(dataset)
        with self._engine.begin() as conn:
            existed = conn.execute(
                sa.select(datasets_table.c.name).where(
                    datasets_table.c.name == dataset
                )
            ).first()
            conn.execute(rows_table.delete().where(rows_table.c.dataset == dataset))
            conn.execute(
                datasets_table.delete().where(datasets_table.c.name == dataset)
            )
        return existed is not None


def install_studio_dataset_store(engine: sa.Engine) -> StudioDatasetStore:
    """Make this store the singleton behind genxai's get_dataset_store()."""
    store = StudioDatasetStore(engine)
    gx_datasets._store = store
    return store
