"""JSON-file persistence for workflow documents."""

import uuid
from pathlib import Path

from app.schemas import WorkflowDoc, WorkflowSummary


class WorkflowStore:
    """Stores each workflow as a JSON file under ``<root>/workflows/``."""

    def __init__(self, root: Path) -> None:
        self.workflows_dir = root / "workflows"
        self.workflows_dir.mkdir(parents=True, exist_ok=True)

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
        doc.id = workflow_id
        path.write_text(doc.model_dump_json(indent=2))
        return doc

    def delete(self, workflow_id: str) -> bool:
        path = self._path(workflow_id)
        if not path.exists():
            return False
        path.unlink()
        return True
