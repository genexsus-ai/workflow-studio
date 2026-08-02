"""Auth primitives: password hashing, JWT sessions, current-user dependency."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Optional

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException

from app.auth_store import AuthStore
from app.config import get_settings

_store: Optional[AuthStore] = None


def get_auth_store() -> AuthStore:
    global _store
    if _store is None:
        from app.studio_db import get_studio_engine, use_postgres

        if use_postgres():
            engine = get_studio_engine()
        else:
            import sqlalchemy as sa

            engine = sa.create_engine(
                f"sqlite:///{get_settings().data_dir / 'auth.db'}"
            )
        _store = AuthStore(engine)
    return _store


# ---- passwords ---------------------------------------------------------
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: Optional[str]) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


# ---- JWT sessions ------------------------------------------------------
def create_token(user_id: str) -> str:
    settings = get_settings()
    payload = {
        "sub": user_id,
        "iat": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(hours=settings.jwt_expiry_hours),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def _decode(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired session") from exc


async def get_current_user(
    authorization: Optional[str] = Header(default=None),
) -> dict[str, Any]:
    """FastAPI dependency: resolve the logged-in user from a Bearer JWT."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    payload = _decode(authorization.split(" ", 1)[1].strip())
    user = get_auth_store().get_user(payload.get("sub", ""))
    if user is None:
        raise HTTPException(status_code=401, detail="User no longer exists")
    return user


async def get_optional_user(
    authorization: Optional[str] = Header(default=None),
) -> Optional[dict[str, Any]]:
    """Like get_current_user but returns None instead of 401 when absent."""
    if not authorization:
        return None
    try:
        return await get_current_user(authorization)
    except HTTPException:
        return None
