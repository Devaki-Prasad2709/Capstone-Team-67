"""Operator authentication and cross-site mutation protection for the dashboard."""

from __future__ import annotations

import hmac
import os
from urllib.parse import urlsplit

from fastapi import Header, HTTPException, Request


LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}
MUTATION_MARKER = "DisasterScope-Operator"


def _request_host(request: Request) -> str:
    raw = request.headers.get("host", "").strip().lower()
    if raw.startswith("["):
        return raw.split("]", 1)[0] + "]"
    return raw.split(":", 1)[0]


def _is_loopback(request: Request) -> bool:
    return _request_host(request) in LOOPBACK_HOSTS


def _configured_token() -> str:
    return os.getenv("DASHBOARD_OPERATOR_TOKEN", "").strip()


def _presented_token(authorization: str | None) -> str:
    scheme, _, value = (authorization or "").partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def operator_access(request: Request, authorization: str | None = None) -> dict:
    configured = _configured_token()
    presented = _presented_token(authorization or request.headers.get("authorization"))
    if configured and presented and hmac.compare_digest(configured, presented):
        return {
            "mode": "authenticated_operator",
            "operator": True,
            "authentication_configured": True,
            "local_development": _is_loopback(request),
        }
    if not configured and _is_loopback(request):
        return {
            "mode": "authenticated_operator",
            "operator": True,
            "authentication_configured": False,
            "local_development": True,
        }
    return {
        "mode": "read_only_observer",
        "operator": False,
        "authentication_configured": bool(configured),
        "local_development": _is_loopback(request),
    }


def require_operator(
    request: Request,
    authorization: str | None = Header(default=None),
    mutation_marker: str | None = Header(default=None, alias="X-Dashboard-Request"),
) -> None:
    """Authorize a mutation and reject form/cross-origin browser submissions."""
    if mutation_marker != MUTATION_MARKER:
        raise HTTPException(403, "Operator request verification failed.")

    origin = request.headers.get("origin")
    if origin:
        origin_host = (urlsplit(origin).netloc or "").lower()
        request_host = request.headers.get("host", "").lower()
        if not origin_host or not hmac.compare_digest(origin_host, request_host):
            raise HTTPException(403, "Cross-site operator requests are not allowed.")

    access = operator_access(request, authorization)
    if access["operator"]:
        return
    if not access["authentication_configured"]:
        raise HTTPException(503, "Remote operator access is not configured.")
    raise HTTPException(401, "Operator authentication required.", headers={"WWW-Authenticate": "Bearer"})
