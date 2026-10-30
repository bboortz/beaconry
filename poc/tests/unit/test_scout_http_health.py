"""BC-013 — scout's FastAPI health endpoints.

Scout exposes /livez /readyz /startupz /healthz on port 8001 via
`beaconry_health.build_router`, matching hub's shape. The restart-loop
trap guard: /livez must stay 200 even when the Docker socket is
unreachable — restarting scout won't fix a broken docker daemon.

Covers:
    - CAP-T043: Scout serves /livez /readyz /startupz /healthz via FastAPI + build_router
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from poc.scout import scout as scout_mod


@pytest.fixture
def client() -> TestClient:
    return TestClient(scout_mod.app)


def test_scout_router_exposes_four_endpoints(client: TestClient) -> None:
    for path in ("/livez", "/readyz", "/startupz", "/healthz"):
        r = client.get(path)
        assert r.status_code in (200, 503), f"{path}: unexpected {r.status_code}"


def test_livez_survives_docker_socket_failure(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Restart-loop trap: dead Docker socket must NOT fail /livez."""
    monkeypatch.setattr(scout_mod, "_docker_socket_ok", False, raising=False)
    r = client.get("/livez")
    assert r.status_code == 200


def test_readyz_reflects_docker_socket(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scout_mod, "_docker_socket_ok", True, raising=False)
    assert client.get("/readyz").status_code == 200

    monkeypatch.setattr(scout_mod, "_docker_socket_ok", False, raising=False)
    r = client.get("/readyz")
    assert r.status_code == 503
    assert "docker_socket" in r.json().get("failed", [])


def test_startupz_gates_on_started_flag(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scout_mod, "_started", False, raising=False)
    assert client.get("/startupz").status_code == 503

    monkeypatch.setattr(scout_mod, "_started", True, raising=False)
    assert client.get("/startupz").status_code == 200


def test_healthz_includes_scan_metrics(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scout_mod, "_docker_socket_ok", True, raising=False)
    monkeypatch.setattr(scout_mod, "_last_scan_ts", 1_700_000_000, raising=False)
    monkeypatch.setattr(scout_mod, "_images_seen", 11, raising=False)
    monkeypatch.setattr(scout_mod, "_bytes_posted", 4321, raising=False)
    monkeypatch.setattr(scout_mod, "_started", True, raising=False)

    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] in ("ok", "degraded")
    assert body["checks"]["last_scan_ts"] == 1_700_000_000
    assert body["checks"]["images_seen"] == 11
    assert body["checks"]["bytes_posted"] == 4321
    assert body["checks"]["docker_socket"] == "ok"


def test_healthz_marks_degraded_when_docker_socket_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(scout_mod, "_docker_socket_ok", False, raising=False)
    monkeypatch.setattr(scout_mod, "_started", True, raising=False)
    r = client.get("/healthz")
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"]["docker_socket"] == "unavailable"
