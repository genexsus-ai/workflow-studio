"""Analytics data sources: one adapter interface over datasets and SQL tables.

A *source* is a registered pointer to tabular data. Internal datasets are
implicit sources (always listed, nothing stored); SQL sources pair an
encrypted credential (the same ones the Postgres connector uses) with a
table name. Every kind answers the same three questions — schema, rows,
aggregate — which is exactly what the Analytics UI consumes.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from datetime import UTC, datetime
from typing import Any

from app.config import get_settings
from app.credentials import get_credential_store
from genxai.core.datasets import ALLOWED_METRICS, get_dataset_store

logger = logging.getLogger(__name__)

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
MAX_SQL_ROWS = 500
MAX_GROUPS = 50

_METRIC_SQL = {
    "count": "COUNT(*)",
    "sum": "SUM({field})",
    "avg": "AVG({field})",
    "min": "MIN({field})",
    "max": "MAX({field})",
}


def _validate_identifier(name: str, kind: str) -> str:
    if not _IDENTIFIER_RE.match(name or ""):
        raise ValueError(f"Invalid {kind} name {name!r}")
    return name


# ------------------------------------------------------------------ registry


class SourceRegistry:
    """Registered (non-implicit) sources, stored in the datasets database."""

    def __init__(self) -> None:
        self.db_path = get_settings().data_dir / "datasets.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    config TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

    def list(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT id, name, kind, config, created_at FROM sources "
                "ORDER BY created_at DESC"
            )
            return [
                {
                    "id": row[0],
                    "name": row[1],
                    "kind": row[2],
                    "config": json.loads(row[3]),
                    "created_at": row[4],
                }
                for row in cursor.fetchall()
            ]

    def get(self, source_id: str) -> dict[str, Any] | None:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id, name, kind, config, created_at FROM sources WHERE id = ?",
                (source_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "kind": row[2],
            "config": json.loads(row[3]),
            "created_at": row[4],
        }

    def create(self, name: str, kind: str, config: dict[str, Any]) -> dict[str, Any]:
        source = {
            "id": uuid.uuid4().hex,
            "name": name,
            "kind": kind,
            "config": config,
            "created_at": datetime.now(UTC).isoformat(),
        }
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO sources (id, name, kind, config, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    source["id"],
                    name,
                    kind,
                    json.dumps(config),
                    source["created_at"],
                ),
            )
        return source

    def delete(self, source_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        return cursor.rowcount > 0


_registry: SourceRegistry | None = None


def get_source_registry() -> SourceRegistry:
    global _registry
    if _registry is None:
        _registry = SourceRegistry()
    return _registry


def reset_source_registry() -> None:
    global _registry
    _registry = None


# ------------------------------------------------------------------ adapters


class DatasetAdapter:
    """Internal dataset store — wraps the existing DatasetStore calls."""

    def __init__(self, dataset: str) -> None:
        self.dataset = dataset

    def schema(self) -> list[dict[str, str]]:
        sample = get_dataset_store().rows(self.dataset, limit=50)["rows"]
        types: dict[str, str] = {}
        for row in sample:
            for key, value in row.items():
                if key.startswith("_"):
                    continue
                if isinstance(value, bool):
                    inferred = "boolean"
                elif isinstance(value, (int, float)):
                    inferred = "number"
                elif isinstance(value, (dict, list)):
                    inferred = "object"
                else:
                    inferred = "string"
                if key not in types or types[key] == "string":
                    types[key] = inferred
        return [{"name": name, "type": kind} for name, kind in types.items()]

    def rows(self, limit: int, offset: int) -> dict[str, Any]:
        return get_dataset_store().rows(self.dataset, limit=limit, offset=offset)

    def aggregate(
        self, metric: str, field: str | None, group_by: str | None
    ) -> list[dict[str, Any]]:
        return get_dataset_store().aggregate(
            self.dataset, metric=metric, field=field, group_by=group_by
        )


class SQLAdapter:
    """A table behind a stored credential; aggregation pushed down as SQL."""

    def __init__(self, credential: str, table: str) -> None:
        entry = get_credential_store().get(credential)
        if entry is None:
            raise LookupError(f"Credential '{credential}' not found")
        connection_string = entry.config.get("connection_string")
        if not connection_string:
            raise LookupError(
                f"Credential '{credential}' has no connection_string"
            )
        self.connection_string = str(connection_string)
        self.table = _validate_identifier(table, "table")

    def _engine(self) -> Any:
        from sqlalchemy import create_engine

        return create_engine(self.connection_string, pool_pre_ping=True)

    def schema(self) -> list[dict[str, str]]:
        from sqlalchemy import inspect

        engine = self._engine()
        try:
            columns = inspect(engine).get_columns(self.table)
        finally:
            engine.dispose()
        return [
            {"name": column["name"], "type": str(column["type"]).lower()}
            for column in columns
        ]

    def rows(self, limit: int, offset: int) -> dict[str, Any]:
        from sqlalchemy import text

        engine = self._engine()
        try:
            with engine.connect() as conn:
                total = conn.execute(
                    text(f"SELECT COUNT(*) FROM {self.table}")  # noqa: S608 — validated identifier
                ).scalar()
                result = conn.execute(
                    text(
                        f"SELECT * FROM {self.table} LIMIT :limit OFFSET :offset"  # noqa: S608
                    ),
                    {"limit": min(limit, MAX_SQL_ROWS), "offset": offset},
                )
                columns = list(result.keys())
                rows = [
                    {column: value for column, value in zip(columns, row)}
                    for row in result.fetchall()
                ]
        finally:
            engine.dispose()
        return {"rows": rows, "total": int(total or 0)}

    def aggregate(
        self, metric: str, field: str | None, group_by: str | None
    ) -> list[dict[str, Any]]:
        from sqlalchemy import text

        if metric not in ALLOWED_METRICS:
            raise ValueError(f"metric must be one of {ALLOWED_METRICS}")
        if metric != "count":
            if not field:
                raise ValueError(f"metric '{metric}' requires a field")
            _validate_identifier(field, "column")
        agg_sql = _METRIC_SQL[metric].format(field=field)

        engine = self._engine()
        try:
            with engine.connect() as conn:
                if group_by:
                    _validate_identifier(group_by, "column")
                    statement = (
                        f"SELECT {group_by} AS grp, {agg_sql} AS value, "  # noqa: S608
                        f"COUNT(*) AS rows_in_group FROM {self.table} "
                        f"GROUP BY {group_by} ORDER BY value DESC LIMIT {MAX_GROUPS}"
                    )
                    result = conn.execute(text(statement))
                    return [
                        {
                            "group": "—" if grp is None else str(grp),
                            "value": float(value or 0),
                            "rows": int(count),
                        }
                        for grp, value, count in result.fetchall()
                    ]
                statement = (
                    f"SELECT {agg_sql} AS value, COUNT(*) AS n FROM {self.table}"  # noqa: S608
                )
                value, count = conn.execute(text(statement)).fetchone()
                return [
                    {
                        "group": "all",
                        "value": float(value or 0),
                        "rows": int(count),
                    }
                ]
        finally:
            engine.dispose()


# ----------------------------------------------------------------- dispatch


def list_all_sources() -> list[dict[str, Any]]:
    """Registered sources plus one implicit source per internal dataset."""
    implicit = [
        {
            "id": f"dataset:{entry['name']}",
            "name": entry["name"],
            "kind": "dataset",
            "config": {"dataset": entry["name"]},
            "rows": entry["rows"],
            "last_written_at": entry["last_written_at"],
        }
        for entry in get_dataset_store().list_datasets()
    ]
    return implicit + get_source_registry().list()


def resolve_source(source_id: str) -> dict[str, Any] | None:
    if source_id.startswith("dataset:"):
        name = source_id.split(":", 1)[1]
        return {
            "id": source_id,
            "name": name,
            "kind": "dataset",
            "config": {"dataset": name},
        }
    return get_source_registry().get(source_id)


def get_adapter(source: dict[str, Any]) -> DatasetAdapter | SQLAdapter:
    kind = source["kind"]
    config = source["config"]
    if kind == "dataset":
        return DatasetAdapter(config["dataset"])
    if kind == "sql":
        return SQLAdapter(config["credential"], config["table"])
    raise ValueError(f"Unknown source kind '{kind}'")


def list_credential_tables(credential: str) -> list[str]:
    """Table names visible through a stored SQL credential (for the UI)."""
    from sqlalchemy import create_engine, inspect

    entry = get_credential_store().get(credential)
    if entry is None or not entry.config.get("connection_string"):
        raise LookupError(f"Credential '{credential}' not found or not a database")
    engine = create_engine(str(entry.config["connection_string"]), pool_pre_ping=True)
    try:
        return sorted(inspect(engine).get_table_names())
    finally:
        engine.dispose()
