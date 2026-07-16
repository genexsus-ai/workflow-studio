"""User feedback: store every submission, email it when SMTP is configured.

Storage mirrors the rest of the studio: a ``ws_feedback`` table when
PERSISTENCE_BACKEND=postgres, otherwise a JSONL file under data_dir.
Emailing to ``feedback_email`` is best-effort — a missing SMTP config or a
send failure never loses the feedback, which is already persisted.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa

from app.config import get_settings
from app.studio_db import use_postgres

logger = logging.getLogger(__name__)

_metadata = sa.MetaData()

feedback_table = sa.Table(
    "ws_feedback",
    _metadata,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("created_at", sa.String(40), nullable=False),
    sa.Column("message", sa.Text, nullable=False),
    sa.Column("email", sa.String(320)),
    sa.Column("category", sa.String(40)),
    sa.Column("page", sa.String(200)),
    sa.Column("emailed", sa.Boolean, nullable=False, default=False),
)

_table_ready = False


def _feedback_path():
    return get_settings().data_dir / "feedback.jsonl"


def _ensure_table(engine: sa.Engine) -> None:
    global _table_ready
    if not _table_ready:
        _metadata.create_all(engine)
        _table_ready = True


def _store(record: dict[str, Any]) -> None:
    if use_postgres():
        from app.studio_db import get_studio_engine

        engine = get_studio_engine()
        _ensure_table(engine)
        with engine.begin() as conn:
            conn.execute(feedback_table.insert().values(**record))
    else:
        path = _feedback_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            handle.write(json.dumps(record, default=str) + "\n")


def _email_body(record: dict[str, Any]) -> str:
    return (
        f"New feedback from GenXAI Workflow Studio\n\n"
        f"Category: {record.get('category') or 'general'}\n"
        f"From: {record.get('email') or '(not provided)'}\n"
        f"Page: {record.get('page') or '-'}\n"
        f"When: {record['created_at']}\n\n"
        f"{record['message']}\n"
    )


def _send_ses_sync(settings: Any, record: dict[str, Any]) -> None:
    import boto3

    region = os.environ.get("AWS_REGION") or os.environ.get(
        "AWS_DEFAULT_REGION", "us-east-1"
    )
    client = boto3.client("ses", region_name=region)
    source = settings.feedback_from_email or settings.feedback_email
    kwargs: dict[str, Any] = {
        "Source": source,
        "Destination": {"ToAddresses": [settings.feedback_email]},
        "Message": {
            "Subject": {
                "Data": f"[Studio feedback] {record.get('category') or 'general'}"
            },
            "Body": {"Text": {"Data": _email_body(record)}},
        },
    }
    if record.get("email"):
        kwargs["ReplyToAddresses"] = [record["email"]]
    client.send_email(**kwargs)


async def _email(record: dict[str, Any]) -> bool:
    """Email the feedback to feedback_email; returns True if sent.

    Prefers AWS SES (using the standard AWS_* credentials in the
    environment); falls back to SMTP when configured. Best-effort — the
    feedback is already stored regardless.
    """
    import asyncio

    settings = get_settings()
    if not settings.feedback_email:
        return False

    if os.environ.get("AWS_ACCESS_KEY_ID"):
        try:
            await asyncio.to_thread(_send_ses_sync, settings, record)
            return True
        except Exception:
            logger.exception("Could not email feedback via SES (stored anyway)")
            # fall through to SMTP if that is configured

    if settings.feedback_smtp_host:
        from genxai.connectors import EmailConnector

        connector = EmailConnector(
            connector_id="studio-feedback",
            host=settings.feedback_smtp_host,
            port=settings.feedback_smtp_port,
            username=settings.feedback_smtp_username or "",
            password=settings.feedback_smtp_password or "",
            from_email=settings.feedback_from_email
            or settings.feedback_smtp_username
            or settings.feedback_email,
        )
        try:
            await connector.validate_config()
            await connector.send_email(
                to=settings.feedback_email,
                subject=f"[Studio feedback] {record.get('category') or 'general'}",
                body=_email_body(record),
            )
            return True
        except Exception:
            logger.exception("Could not email feedback via SMTP (stored anyway)")
        finally:
            await connector._stop()

    return False


async def submit_feedback(
    message: str,
    email: str | None = None,
    category: str | None = None,
    page: str | None = None,
) -> dict[str, Any]:
    """Persist a feedback submission and try to email it."""
    record = {
        "id": uuid.uuid4().hex,
        "created_at": datetime.now(UTC).isoformat(),
        "message": message.strip(),
        "email": (email or "").strip() or None,
        "category": (category or "general").strip() or "general",
        "page": (page or "").strip() or None,
        "emailed": False,
    }
    record["emailed"] = await _email(record)
    _store(record)
    return {"status": "received", "emailed": record["emailed"]}


def list_feedback(limit: int = 100) -> list[dict[str, Any]]:
    """Recent feedback, newest first (for an admin view)."""
    if use_postgres():
        from app.studio_db import get_studio_engine

        engine = get_studio_engine()
        _ensure_table(engine)
        with engine.begin() as conn:
            rows = conn.execute(
                sa.select(feedback_table)
                .order_by(feedback_table.c.created_at.desc())
                .limit(limit)
            ).mappings().all()
        return [dict(row) for row in rows]
    path = _feedback_path()
    if not path.exists():
        return []
    records = [json.loads(line) for line in path.read_text().splitlines() if line]
    return list(reversed(records))[:limit]
