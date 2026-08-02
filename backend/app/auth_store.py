"""User / org / membership persistence for multi-tenant auth.

Follows the same SQLAlchemy-Core pattern as the workflow store: tables +
``create_all`` on a shared engine (Postgres when configured, SQLite file
otherwise). Data ownership is by ORG — every workflow/credential/etc. will
carry an org_id (Phase 2). A new user gets a personal org on signup.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Optional

import sqlalchemy as sa

_metadata = sa.MetaData()

users_table = sa.Table(
    "auth_users",
    _metadata,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("email", sa.String(320), nullable=False, unique=True),
    sa.Column("name", sa.String, nullable=False, default=""),
    sa.Column("password_hash", sa.Text, nullable=True),  # null for SSO-only users
    sa.Column("auth_provider", sa.String(32), nullable=False, default="password"),
    sa.Column("created_at", sa.String(40), nullable=False),
)

orgs_table = sa.Table(
    "auth_orgs",
    _metadata,
    sa.Column("id", sa.String(64), primary_key=True),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("created_at", sa.String(40), nullable=False),
)

members_table = sa.Table(
    "auth_org_members",
    _metadata,
    sa.Column("org_id", sa.String(64), primary_key=True),
    sa.Column("user_id", sa.String(64), primary_key=True),
    sa.Column("role", sa.String(16), nullable=False, default="member"),  # owner|admin|member
    sa.Column("created_at", sa.String(40), nullable=False),
)

ROLES = ("owner", "admin", "member")


def _now() -> str:
    return datetime.now(UTC).isoformat()


class AuthStore:
    """Users, orgs, and memberships."""

    def __init__(self, engine: sa.Engine) -> None:
        self._engine = engine
        _metadata.create_all(engine)

    # ---- users ----------------------------------------------------------
    def create_user(
        self,
        email: str,
        name: str,
        password_hash: Optional[str],
        auth_provider: str = "password",
    ) -> dict[str, Any]:
        user = {
            "id": uuid.uuid4().hex,
            "email": email.strip().lower(),
            "name": name.strip(),
            "password_hash": password_hash,
            "auth_provider": auth_provider,
            "created_at": _now(),
        }
        with self._engine.begin() as conn:
            conn.execute(users_table.insert().values(**user))
        return user

    def get_user_by_email(self, email: str) -> Optional[dict[str, Any]]:
        with self._engine.begin() as conn:
            row = conn.execute(
                sa.select(users_table).where(users_table.c.email == email.strip().lower())
            ).mappings().first()
        return dict(row) if row else None

    def get_user(self, user_id: str) -> Optional[dict[str, Any]]:
        with self._engine.begin() as conn:
            row = conn.execute(
                sa.select(users_table).where(users_table.c.id == user_id)
            ).mappings().first()
        return dict(row) if row else None

    # ---- orgs + membership ---------------------------------------------
    def create_org(self, name: str) -> dict[str, Any]:
        org = {"id": uuid.uuid4().hex, "name": name.strip(), "created_at": _now()}
        with self._engine.begin() as conn:
            conn.execute(orgs_table.insert().values(**org))
        return org

    def add_member(self, org_id: str, user_id: str, role: str = "member") -> None:
        if role not in ROLES:
            raise ValueError(f"invalid role {role!r}")
        with self._engine.begin() as conn:
            conn.execute(
                members_table.insert().values(
                    org_id=org_id, user_id=user_id, role=role, created_at=_now()
                )
            )

    def list_user_orgs(self, user_id: str) -> list[dict[str, Any]]:
        """Orgs the user belongs to, with the user's role in each."""
        with self._engine.begin() as conn:
            rows = conn.execute(
                sa.select(orgs_table.c.id, orgs_table.c.name, members_table.c.role)
                .select_from(
                    members_table.join(orgs_table, members_table.c.org_id == orgs_table.c.id)
                )
                .where(members_table.c.user_id == user_id)
                .order_by(orgs_table.c.created_at)
            ).mappings().all()
        return [dict(r) for r in rows]

    def get_membership(self, org_id: str, user_id: str) -> Optional[dict[str, Any]]:
        with self._engine.begin() as conn:
            row = conn.execute(
                sa.select(members_table).where(
                    members_table.c.org_id == org_id,
                    members_table.c.user_id == user_id,
                )
            ).mappings().first()
        return dict(row) if row else None
