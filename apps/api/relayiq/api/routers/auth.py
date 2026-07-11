"""Development login endpoint (documented OAuth substitute — spec §20)."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from relayiq.api.deps import Principal, current_principal
from relayiq.db import get_db
from relayiq.models import User
from relayiq.security import TokenClaims, issue_token, verify_password

router = APIRouter(prefix="/v1/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(min_length=8, max_length=200)

    @field_validator("email")
    @classmethod
    def _email_syntax(cls, v: str) -> str:
        from relayiq.canonical.normalize import is_valid_email_syntax

        if not is_valid_email_syntax(v):
            raise ValueError("invalid email address")
        return v.lower().strip()


class LoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 — token type label, not a secret
    role: str
    tenant_id: str
    user_id: str
    email: str


@router.post("/login", response_model=LoginOut)
def login(body: LoginIn, db: Session = Depends(get_db)) -> LoginOut:
    user = db.execute(
        select(User).where(User.email == body.email.lower())
    ).scalar_one_or_none()
    # Uniform error for unknown user vs bad password — no account enumeration.
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid credentials")
    token = issue_token(
        TokenClaims(user_id=user.id, tenant_id=user.tenant_id, role=user.role, email=user.email)
    )
    return LoginOut(
        access_token=token, role=user.role, tenant_id=user.tenant_id,
        user_id=user.id, email=user.email,
    )


class MeOut(BaseModel):
    user_id: str
    tenant_id: str
    role: str
    email: str


@router.get("/me", response_model=MeOut)
def me(principal: Principal = Depends(current_principal)) -> MeOut:
    return MeOut(**principal.__dict__)
