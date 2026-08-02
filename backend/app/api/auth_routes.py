"""Authentication endpoints: signup, login, current user."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import (
    create_token,
    get_auth_store,
    get_current_user,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SignupRequest(BaseModel):
    email: str
    password: str = Field(min_length=8)
    name: str = ""
    org_name: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


def _auth_response(user: dict, token: str) -> dict:
    store = get_auth_store()
    return {
        "token": token,
        "user": {"id": user["id"], "email": user["email"], "name": user["name"]},
        "orgs": store.list_user_orgs(user["id"]),
    }


@router.post("/signup")
def signup(req: SignupRequest) -> dict:
    email = req.email.strip().lower()
    if not _EMAIL_RE.match(email):
        raise HTTPException(status_code=422, detail="Enter a valid email address")
    store = get_auth_store()
    if store.get_user_by_email(email) is not None:
        # Generic message — don't reveal which emails are registered.
        raise HTTPException(status_code=409, detail="Could not create account")

    user = store.create_user(
        email=email,
        name=req.name or email.split("@")[0],
        password_hash=hash_password(req.password),
        auth_provider="password",
    )
    # Every new user gets a personal org, and is its owner.
    org_name = (req.org_name or "").strip() or f"{user['name']}'s workspace"
    org = store.create_org(org_name)
    store.add_member(org["id"], user["id"], role="owner")

    return _auth_response(user, create_token(user["id"]))


@router.post("/login")
def login(req: LoginRequest) -> dict:
    store = get_auth_store()
    user = store.get_user_by_email(req.email)
    # Same error whether the email is unknown or the password is wrong.
    if user is None or not verify_password(req.password, user.get("password_hash")):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return _auth_response(user, create_token(user["id"]))


@router.get("/me")
def me(user: dict = Depends(get_current_user)) -> dict:
    store = get_auth_store()
    return {
        "user": {"id": user["id"], "email": user["email"], "name": user["name"]},
        "orgs": store.list_user_orgs(user["id"]),
    }
