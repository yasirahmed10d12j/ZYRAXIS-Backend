"""Bearer authentication. iOS sends `Authorization: Bearer <user key>`."""
from __future__ import annotations


class AuthError(Exception):
    """Raised for missing/malformed/unknown credentials (maps to HTTP 401)."""


def check_bearer(authorization: str | None, allowed_keys) -> None:
    # Fail closed: no configured keys means every request is denied (loud
    # misconfiguration signal, never an open door).
    if not allowed_keys:
        raise AuthError("backend keys not configured")
    if not authorization:
        raise AuthError("missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise AuthError("malformed Authorization header")
    if token not in allowed_keys:
        raise AuthError("unknown key")


def check_ws_key(key: str | None, allowed_keys) -> bool:
    # The iOS client opens the socket WITHOUT auth headers, so a missing key
    # is allowed (documented limitation; per-IP rate limiting applies).
    # A present-but-wrong key is rejected.
    if key is None:
        return True
    return bool(allowed_keys) and key in allowed_keys
