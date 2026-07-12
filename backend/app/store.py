"""JSON-file persistence for workflow documents."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from app.schemas import WorkflowDoc, WorkflowSummary, WorkflowVersionInfo

MAX_VERSIONS = 25


class WorkflowStore:
    """Stores each workflow as a JSON file under ``<root>/workflows/``.

    Every update snapshots the previous content under
    ``<root>/versions/<workflow_id>/`` (capped at MAX_VERSIONS), giving each
    workflow a restorable history.
    """

    def __init__(self, root: Path) -> None:
        self.workflows_dir = root / "workflows"
        self.workflows_dir.mkdir(parents=True, exist_ok=True)
        self.versions_dir = root / "versions"

    def _path(self, workflow_id: str) -> Path:
        safe = "".join(c for c in workflow_id if c.isalnum() or c in "-_")
        if not safe or safe != workflow_id:
            raise ValueError(f"Invalid workflow id: {workflow_id!r}")
        return self.workflows_dir / f"{safe}.json"

    def list(self) -> list[WorkflowSummary]:
        summaries = []
        for path in sorted(self.workflows_dir.glob("*.json")):
            try:
                doc = WorkflowDoc.model_validate_json(path.read_text())
            except Exception:
                continue
            summaries.append(
                WorkflowSummary(
                    id=doc.id or path.stem,
                    name=doc.name,
                    description=doc.description,
                    node_count=len(doc.nodes),
                )
            )
        return summaries

    def get(self, workflow_id: str) -> WorkflowDoc | None:
        path = self._path(workflow_id)
        if not path.exists():
            return None
        return WorkflowDoc.model_validate_json(path.read_text())

    def create(self, doc: WorkflowDoc) -> WorkflowDoc:
        doc.id = doc.id or uuid.uuid4().hex
        self._path(doc.id).write_text(doc.model_dump_json(indent=2))
        return doc

    def update(self, workflow_id: str, doc: WorkflowDoc) -> WorkflowDoc | None:
        path = self._path(workflow_id)
        if not path.exists():
            return None
        self._snapshot(workflow_id, path)
        doc.id = workflow_id
        path.write_text(doc.model_dump_json(indent=2))
        return doc

    # ------------------------------------------------------------- versions

    def _version_dir(self, workflow_id: str) -> Path:
        self._path(workflow_id)  # validates the id
        return self.versions_dir / workflow_id

    def _snapshot(self, workflow_id: str, current_path: Path) -> None:
        """Copy the current document into the version history, capped."""
        version_dir = self._version_dir(workflow_id)
        version_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        (version_dir / f"{stamp}.json").write_text(current_path.read_text())
        versions = sorted(version_dir.glob("*.json"))
        for stale in versions[:-MAX_VERSIONS]:
            stale.unlink()

    def list_versions(self, workflow_id: str) -> list[WorkflowVersionInfo]:
        version_dir = self._version_dir(workflow_id)
        if not version_dir.exists():
            return []
        infos = []
        for path in sorted(version_dir.glob("*.json"), reverse=True):
            try:
                doc = WorkflowDoc.model_validate_json(path.read_text())
            except Exception:
                continue
            stamp = path.stem
            saved_at = (
                datetime.strptime(stamp, "%Y%m%dT%H%M%S%f")
                .replace(tzinfo=UTC)
                .isoformat()
            )
            infos.append(
                WorkflowVersionInfo(
                    version=stamp,
                    saved_at=saved_at,
                    name=doc.name,
                    node_count=len(doc.nodes),
                )
            )
        return infos

    def get_version(self, workflow_id: str, version: str) -> WorkflowDoc | None:
        safe = "".join(c for c in version if c.isalnum())
        if not safe or safe != version:
            return None
        path = self._version_dir(workflow_id) / f"{version}.json"
        if not path.exists():
            return None
        return WorkflowDoc.model_validate_json(path.read_text())

    def restore_version(self, workflow_id: str, version: str) -> WorkflowDoc | None:
        """Make a historical version current (snapshotting what it replaces)."""
        doc = self.get_version(workflow_id, version)
        if doc is None:
            return None
        return self.update(workflow_id, doc)

    def delete(self, workflow_id: str) -> bool:
        path = self._path(workflow_id)
        if not path.exists():
            return False
        path.unlink()
        return True
