"""Workflow automation: webhook tokens and schedule activation.

Webhooks give each workflow a fire-by-URL endpoint (n8n-style): enabling
generates a secret token; POSTing to /api/v1/hooks/{token} runs the workflow
with the request body as input.

Schedules run a workflow every N seconds using the framework's
ScheduleTrigger (APScheduler under the hood). Enabled schedules are resumed
on backend startup.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from typing import Any

from app.run_manager import get_run_manager
from app.schemas import WorkflowDoc
from app.store import WorkflowStore

from genxai.triggers.schedule import ScheduleTrigger
from genxai.triggers.webhook import WebhookTrigger

logger = logging.getLogger(__name__)


def generate_webhook_token() -> str:
    return secrets.token_urlsafe(24)


def verify_github_signature(secret: str, raw_body: bytes, signature: str | None) -> bool:
    """Verify GitHub's X-Hub-Signature-256 header (sha256=<hmac hexdigest>).

    GitHub's signature scheme matches the framework WebhookTrigger's
    "<alg>=<hexdigest>" format, so verification is delegated to it.
    """
    trigger = WebhookTrigger(trigger_id="github", secret=secret, hash_alg="sha256")
    return trigger.validate_signature(raw_body, signature)


def find_workflow_by_token(store: WorkflowStore, token: str) -> WorkflowDoc | None:
    if not token:
        return None
    for summary in store.list():
        doc = store.get(summary.id)
        if doc and doc.automation.webhook_enabled and doc.automation.webhook_token == token:
            return doc
    return None


class ScheduleManager:
    """Holds one live ScheduleTrigger per schedule-enabled workflow."""

    def __init__(self, store: WorkflowStore) -> None:
        self.store = store
        self._triggers: dict[str, ScheduleTrigger] = {}

    async def enable(self, doc: WorkflowDoc) -> None:
        assert doc.id is not None
        await self.disable(doc.id)
        interval = max(int(doc.automation.interval_seconds or 60), 1)
        trigger = ScheduleTrigger(
            trigger_id=f"studio-schedule-{doc.id}",
            interval_seconds=interval,
        )
        workflow_id = doc.id

        async def on_event(event: Any) -> None:
            current = self.store.get(workflow_id)
            if current is None:
                await self.disable(workflow_id)
                return
            logger.info("Scheduled run firing for workflow %s", workflow_id)
            get_run_manager().submit(current, dict(event.payload), trigger="schedule")

        trigger.on_event(on_event)
        await trigger.start()
        self._triggers[workflow_id] = trigger
        logger.info("Schedule enabled for workflow %s every %ss", workflow_id, interval)

    async def disable(self, workflow_id: str) -> None:
        trigger = self._triggers.pop(workflow_id, None)
        if trigger is not None:
            await trigger.stop()
            logger.info("Schedule disabled for workflow %s", workflow_id)

    def is_active(self, workflow_id: str) -> bool:
        return workflow_id in self._triggers

    async def resume_enabled(self) -> None:
        """Start triggers for every schedule-enabled workflow (called at startup)."""
        for summary in self.store.list():
            doc = self.store.get(summary.id)
            if doc and doc.automation.schedule_enabled:
                try:
                    await self.enable(doc)
                except Exception as exc:
                    logger.error("Could not resume schedule for %s: %s", summary.id, exc)

    async def shutdown(self) -> None:
        for workflow_id in list(self._triggers):
            await self.disable(workflow_id)
