"""FastAPI dependencies: DB session, authenticated principal, role checks, tenant scoping."""

from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from relayiq.db import get_db
from relayiq.enums import Role
from relayiq.logging_setup import tenant_id_var
from relayiq.models import User
from relayiq.security import decode_token

bearer = HTTPBearer(auto_error=False)

# Role hierarchy: each role implies the ones below it for read purposes; write scopes
# are checked explicitly per endpoint group.
ROLE_ORDER = [Role.ANALYST.value, Role.REVIEWER.value, Role.OPERATOR.value, Role.ADMIN.value]


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    role: str
    email: str

    def at_least(self, role: str) -> bool:
        try:
            return ROLE_ORDER.index(self.role) >= ROLE_ORDER.index(role)
        except ValueError:
            return False


def current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token")
    claims = decode_token(credentials.credentials)
    if claims is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or expired token")
    user = db.get(User, claims.user_id)
    if user is None or not user.is_active or user.tenant_id != claims.tenant_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "unknown or inactive user")
    # Defense in depth: role comes from the DB row, not just the token payload.
    principal = Principal(
        user_id=user.id, tenant_id=user.tenant_id, role=user.role, email=user.email
    )
    tenant_id_var.set(principal.tenant_id)
    request.state.principal = principal
    return principal


def require_role(minimum: str):
    def _dep(principal: Principal = Depends(current_principal)) -> Principal:
        if not principal.at_least(minimum):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"requires {minimum} role or higher",
            )
        return principal

    return _dep


require_analyst = require_role(Role.ANALYST.value)
require_reviewer = require_role(Role.REVIEWER.value)
require_operator = require_role(Role.OPERATOR.value)
require_admin = require_role(Role.ADMIN.value)
