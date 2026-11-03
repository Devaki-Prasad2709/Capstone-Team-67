from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from dashboard import server
from dashboard.security import MUTATION_MARKER, operator_access, require_operator


MUTATION_ROUTES = {
    "/api/infrastructure/{action}",
    "/api/process/{name}/start",
    "/api/process/{name}/stop",
    "/api/social/alerts/{alert_id}/{action}",
    "/api/buildings/{building_source_id}/classification",
    "/api/scenario/{action}",
}


def request(*, host="public-example.ngrok.app", origin=None, authorization=None):
    headers = [(b"host", host.encode())]
    if origin:
        headers.append((b"origin", origin.encode()))
    if authorization:
        headers.append((b"authorization", authorization.encode()))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "path": "/api/scenario/start",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 50000),
            "server": (host, 443),
        }
    )


def test_every_mutation_route_has_the_operator_dependency():
    protected = set()
    for route in server.app.routes:
        if not ({"POST", "PUT", "PATCH", "DELETE"} & (getattr(route, "methods", set()) or set())):
            continue
        dependencies = getattr(getattr(route, "dependant", None), "dependencies", [])
        if any(dependency.call is require_operator for dependency in dependencies):
            protected.add(route.path)
    assert protected == MUTATION_ROUTES


def test_remote_anonymous_user_is_read_only_when_operator_token_exists(monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATOR_TOKEN", "correct-secret")
    remote = request()

    assert operator_access(remote)["mode"] == "read_only_observer"
    with pytest.raises(HTTPException) as error:
        require_operator(remote, authorization=None, mutation_marker=MUTATION_MARKER)
    assert error.value.status_code == 401
    assert error.value.detail == "Operator authentication required."


def test_remote_control_is_disabled_when_no_token_is_configured(monkeypatch):
    monkeypatch.delenv("DASHBOARD_OPERATOR_TOKEN", raising=False)
    with pytest.raises(HTTPException) as error:
        require_operator(request(), authorization=None, mutation_marker=MUTATION_MARKER)
    assert error.value.status_code == 503
    assert error.value.detail == "Remote operator access is not configured."


def test_valid_operator_token_and_same_origin_marker_authorize_mutation(monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATOR_TOKEN", "correct-secret")
    remote = request(
        origin="https://public-example.ngrok.app",
        authorization="Bearer correct-secret",
    )

    require_operator(
        remote,
        authorization="Bearer correct-secret",
        mutation_marker=MUTATION_MARKER,
    )
    assert operator_access(remote)["mode"] == "authenticated_operator"


def test_cross_site_or_unmarked_operator_request_is_rejected(monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATOR_TOKEN", "correct-secret")
    cross_site = request(origin="https://attacker.example")

    with pytest.raises(HTTPException) as origin_error:
        require_operator(
            cross_site,
            authorization="Bearer correct-secret",
            mutation_marker=MUTATION_MARKER,
        )
    assert origin_error.value.status_code == 403

    with pytest.raises(HTTPException) as marker_error:
        require_operator(
            request(), authorization="Bearer correct-secret", mutation_marker=None
        )
    assert marker_error.value.status_code == 403


def test_unconfigured_loopback_keeps_simple_development_mode(monkeypatch):
    monkeypatch.delenv("DASHBOARD_OPERATOR_TOKEN", raising=False)
    local = request(host="localhost:8000", origin="http://localhost:8000")

    require_operator(local, authorization=None, mutation_marker=MUTATION_MARKER)
    assert operator_access(local) == {
        "mode": "authenticated_operator",
        "operator": True,
        "authentication_configured": False,
        "local_development": True,
    }


def test_public_access_contract_never_returns_token_or_minio_credentials(monkeypatch):
    monkeypatch.setenv("DASHBOARD_OPERATOR_TOKEN", "correct-secret")
    payload = server.access_mode(request())
    serialized = str(payload).lower()

    assert payload["mode"] == "read_only_observer"
    assert "correct-secret" not in serialized
    assert "access_key" not in serialized
    assert "secret_key" not in serialized
    assert "password" not in serialized
