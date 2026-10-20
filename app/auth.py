import os
import uuid
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    user_id: str
    tenant_id: str
    roles: list[str]


def principal(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Principal:
    """Identity comes only from a verified token, never from request bodies or headers."""
    if creds is None:
        raise HTTPException(401, "missing bearer token")
    try:
        claims = jwt.decode(creds.credentials, os.environ["JWT_SECRET"], algorithms=["HS256"],
                            options={"require": ["sub", "tenant_id", "roles", "exp"]})
    except jwt.PyJWTError as e:
        raise HTTPException(401, f"invalid token: {e}")
    roles = claims["roles"]
    try:
        uuid.UUID(claims["tenant_id"]), uuid.UUID(claims["sub"])
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(401, "invalid token: tenant_id/sub must be UUIDs")
    if not isinstance(roles, list) or not all(isinstance(r, str) and r and "," not in r for r in roles):
        raise HTTPException(401, "invalid token: roles must be a list of non-empty strings")
    return Principal(claims["sub"], claims["tenant_id"], roles)
