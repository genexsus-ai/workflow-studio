"""SQL persistence for workflow documents (PERSISTENCE_BACKEND=postgres).

Drop-in replacement for :class:`app.store.WorkflowStore` backed by a
SQLAlchemy engine. Workflows live in ``ws_workflows``; every update
snapshots the previous document into ``ws_workflow_versions`` (capped at
MAX_VERSIONS per workflow), matching the file store's restorable history.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa

from app.schemas import WorkflowDoc, WorkflowSummary, WorkflowVersionInfo
from app.store import MAX_VERSIONS

_metadata = sa.MetaData()

workflows_table = sa.Table(
    "ws_workflows",
    _metadata,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("description", sa.Text, nullable=False, default=""),
    sa.Column("node_count", sa.Integer, nullable=False, default=0),
    sa.Column("doc", sa.Text, nullable=False),
    sa.Column("updated_at", sa.String(40), nullable=False),
)

versions_table = sa.Table(
    "ws_workflow_versions",
    _metadata,
    sa.Column("workflow_id", sa.String(64), primary_key=True),
    sa.Column("version", sa.String(32), primary_key=True),
    sa.Column("saved_at", sa.String(40), nullable=False),
    sa.Column("doc", sa.Text, nullable=False),
)


class DbWorkflowStore:
    """Same interface as WorkflowStore, persisted to a SQL database."""

    def __init__(self, url: str) -> None:
        self._engine = sa.create_engine(url, pool_pre_ping=True)
        _metadata.create_all(self._engine)

    @staticmethod
    def _validate_id(workflow_id: str) -> str:
        safe = "".join(c for c in workflow_id if c.isalnum() or c in "-_")
        if not safe or safe != workflow_id:
            raise ValueError(f"Invalid workflow id: {workflow_id!r}")
        return workflow_id

    def list(self) -> list[WorkflowSummary]:
        with self._engine.begin() as conn:
            rows = conn.execute(
                sa.select(
                    workflows_table.c.id,
                    workflows_table.c.name,
                    workflows_table.c.description,
                    workflows_table.c.node_count,
                ).order_by(workflows_table.c.id)
            ).all()
        return [
            WorkflowSummary(
                id=r.id, name=r.name, description=r.description, node_count=r.node_count
            )
            for r in rows
        ]

    def get(self, workflow_id: str) -> WorkflowDoc | None:
        self._validate_id(workflow_id)
        with self._engine.begin() as conn:
            row = conn.execute(
                sa.select(workflows_table.c.doc).where(
                    workflows_table.c.id == workflow_id
                )
            ).first()
        if row is None:
            return None
        return WorkflowDoc.model_validate_json(row.doc)

    def create(self, doc: WorkflowDoc) -> WorkflowDoc:
        doc.id = doc.id or uuid.uuid4().hex
        self._validate_id(doc.id)
        with self._engine.begin() as conn:
            conn.execute(workflows_table.insert().values(**self._row(doc)))
        return doc

    def update(self, workflow_id: str, doc: WorkflowDoc) -> WorkflowDoc | None:
        self._validate_id(workflow_id)
        with self._engine.begin() as conn:
            current = conn.execute(
                sa.select(workflows_table.c.doc).where(
                    workflows_table.c.id == workflow_id
                )
            ).first()
            if current is None:
                return None
            self._snapshot(conn, workflow_id, current.doc)
            doc.id = workflow_id
            conn.execute(
                workflows_table.update()
                .where(workflows_table.c.id == workflow_id)
                .values(**self._row(doc))
            )
        return doc

    @staticmethod
    def _row(doc: WorkflowDoc) -> dict:
        return {
            "id": doc.id,
            "name": doc.name,
            "description": doc.description,
            "node_count": len(doc.nodes),
            "doc": doc.model_dump_json(indent=2),
            "updated_at": datetime.now(UTC).isoformat(),
        }

    # ------------------------------------------------------------- versions

    def _snapshot(self, conn: sa.Connection, workflow_id: str, doc_json: str) -> None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        conn.execute(
            versions_table.insert().values(
                workflow_id=workflow_id,
                version=stamp,
                saved_at=datetime.now(UTC).isoformat(),
                doc=doc_json,
            )
        )
        stale = conn.execute(
            sa.select(versions_table.c.version)
            .where(versions_table.c.workflow_id == workflow_id)
            .order_by(versions_table.c.version.desc())
            .offset(MAX_VERSIONS)
        ).scalars().all()
        if stale:
            conn.execute(
                versions_table.delete().where(
                    versions_table.c.workflow_id == workflow_id,
                    versions_table.c.version.in_(stale),
                )
            )

    def list_versions(self, workflow_id: str) -> list[WorkflowVersionInfo]:
        self._validate_id(workflow_id)
        with self._engine.begin() as conn:
            rows = conn.execute(
                sa.select(versions_table)
                .where(versions_table.c.workflow_id == workflow_id)
                .order_by(versions_table.c.version.desc())
            ).all()
        infos = []
        for row in rows:
            try:
                doc = WorkflowDoc.model_validate_json(row.doc)
            except Exception:
                continue
            infos.append(
                WorkflowVersionInfo(
                    version=row.version,
                    saved_at=row.saved_at,
                    name=doc.name,
                    node_count=len(doc.nodes),
                )
            )
        return infos

    def get_version(self, workflow_id: str, version: str) -> WorkflowDoc | None:
        self._validate_id(workflow_id)
        safe = "".join(c for c in version if c.isalnum())
        if not safe or safe != version:
            return None
        with self._engine.begin() as conn:
            row = conn.execute(
                sa.select(versions_table.c.doc).where(
                    versions_table.c.workflow_id == workflow_id,
                    versions_table.c.version == version,
                )
            ).first()
        if row is None:
            return None
        return WorkflowDoc.model_validate_json(row.doc)

    def restore_version(self, workflow_id: str, version: str) -> WorkflowDoc | None:
        """Make a historical version current (snapshotting what it replaces)."""
        doc = self.get_version(workflow_id, version)
        if doc is None:
            return None
        return self.update(workflow_id, doc)

    def delete(self, workflow_id: str) -> bool:
        self._validate_id(workflow_id)
        with self._engine.begin() as conn:
            result = conn.execute(
                workflows_table.delete().where(workflows_table.c.id == workflow_id)
            )
        return bool(result.rowcount)
