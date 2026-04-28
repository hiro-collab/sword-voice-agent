from __future__ import annotations

import ipaddress
import os
import secrets
from typing import Any, Mapping


AUTH_TOKEN_ENV = "SWORD_VOICE_AGENT_AUTH_TOKEN"


class AuthError(RuntimeError):
    pass


def resolve_auth_token(value: str | None = None) -> str:
    return (value or os.environ.get(AUTH_TOKEN_ENV, "")).strip()


def is_loopback_host(host: str) -> bool:
    normalized = (host or "").strip().strip("[]").lower()
    if normalized in {"localhost", "127.0.0.1", "::1"}:
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def require_auth_token_for_bind(host: str, auth_token: str, surface: str) -> None:
    if not is_loopback_host(host) and not auth_token:
        raise AuthError(
            f"{surface} auth token is required when binding outside loopback"
        )


def token_matches(expected: str, provided: str | None) -> bool:
    return bool(expected) and provided is not None and secrets.compare_digest(
        expected,
        provided,
    )


def token_from_headers(headers: Mapping[str, str], expected: str) -> str | None:
    header_token = headers.get("X-Sword-Agent-Token")
    if header_token:
        return header_token.strip()

    authorization = headers.get("Authorization", "")
    prefix = "Bearer "
    if authorization.startswith(prefix):
        return authorization[len(prefix) :].strip()
    return None


def headers_authorized(headers: Mapping[str, str], expected: str) -> bool:
    if not expected:
        return True
    return token_matches(expected, token_from_headers(headers, expected))


def token_from_payload(payload: Mapping[str, Any]) -> str | None:
    token = payload.get("auth_token", payload.get("token"))
    if isinstance(token, str):
        return token.strip()

    auth = payload.get("auth")
    if isinstance(auth, Mapping):
        nested = auth.get("token")
        if isinstance(nested, str):
            return nested.strip()
    return None


def payload_authorized(payload: Mapping[str, Any], expected: str) -> bool:
    if not expected:
        return True
    return token_matches(expected, token_from_payload(payload))


def strip_payload_auth(payload: Mapping[str, Any]) -> dict[str, Any]:
    sanitized = dict(payload)
    sanitized.pop("auth_token", None)
    sanitized.pop("token", None)
    if isinstance(sanitized.get("auth"), Mapping):
        sanitized = dict(sanitized)
        sanitized.pop("auth", None)
    return sanitized
