"""AuthN primitives: bcrypt password hashing + JWT (HS256) session tokens.

This is the documented development substitute for OAuth (spec §20): credentials are
verified server-side against seeded users and the signed token carries tenant/role —
roles are NEVER read from unverified request headers.
"""

import time
from dataclasses import dataclass

import bcrypt
import jwt

from relayiq.config import get_settings

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=10)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


@dataclass(frozen=True)
class TokenClaims:
    user_id: str
    tenant_id: str
    role: str
    email: str


def issue_token(claims: TokenClaims, ttl_seconds: int | None = None) -> str:
    settings = get_settings()
    now = int(time.time())
    payload = {
        "sub": claims.user_id,
        "tid": claims.tenant_id,
        "role": claims.role,
        "email": claims.email,
        "iat": now,
        "exp": now + (ttl_seconds or settings.jwt_ttl_seconds),
        "iss": "relayiq",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_token(token: str) -> TokenClaims | None:
    settings = get_settings()
    try:
        payload = jwt.decode(
            token, settings.jwt_secret, algorithms=[ALGORITHM], issuer="relayiq",
            options={"require": ["exp", "sub", "tid", "role"]},
        )
    except jwt.PyJWTError:
        return None
    return TokenClaims(
        user_id=payload["sub"], tenant_id=payload["tid"],
        role=payload["role"], email=payload.get("email", ""),
    )
