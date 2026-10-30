"""BC-012 — build_router factory in beaconry_health.endpoints.

Covers:
    - CAP-T040: Shared `beaconry_health/` package provides FastAPI router factory
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from poc.beaconry_health.endpoints import build_router


def _mk_app(**router_kwargs: object) -> TestClient:
    api = FastAPI()
    api.include_router(build_router(**router_kwargs))  # type: ignore[arg-type]
    return TestClient(api)


def test_router_exposes_four_endpoints() -> None:
    client = _mk_app(
        livez_provider=lambda: "alive",
        readyz_providers=lambda: {"db": "ok"},
        startup_flag_getter=lambda: True,
        healthz_summary_getter=lambda: {"status": "ok", "version": "0.1.0", "checks": {}},
    )
    for path in ("/livez", "/readyz", "/startupz", "/healthz"):
        assert client.get(path).status_code == 200, f"{path} missing"


def test_livez_returns_200_on_any_input() -> None:
    client = _mk_app(
        livez_provider=lambda: "alive",
        readyz_providers=lambda: {"db": "unavailable"},  # dep broken; livez must not care
        startup_flag_getter=lambda: False,
        healthz_summary_getter=lambda: {"status": "degraded", "version": "0.1.0", "checks": {}},
    )
    assert client.get("/livez").status_code == 200


def test_readyz_503_names_failing_checks() -> None:
    client = _mk_app(
        livez_provider=lambda: "alive",
        readyz_providers=lambda: {"db": "ok", "grype_db": "missing", "grype_binary": "ok"},
        startup_flag_getter=lambda: True,
        healthz_summary_getter=lambda: {"status": "degraded", "version": "0.1.0", "checks": {}},
    )
    r = client.get("/readyz")
    assert r.status_code == 503
    body = r.json()
    assert "grype_db" in body.get("failed", [])
    assert "grype_binary" not in body.get("failed", [])
    assert "db" not in body.get("failed", [])


def test_readyz_200_when_all_providers_ok() -> None:
    client = _mk_app(
        livez_provider=lambda: "alive",
        readyz_providers=lambda: {"db": "ok", "grype_db": "ok"},
        startup_flag_getter=lambda: True,
        healthz_summary_getter=lambda: {"status": "ok", "version": "0.1.0", "checks": {}},
    )
    assert client.get("/readyz").status_code == 200


def test_startupz_gates_on_flag_getter() -> None:
    flag = [False]
    client = _mk_app(
        livez_provider=lambda: "alive",
        readyz_providers=lambda: {"db": "ok"},
        startup_flag_getter=lambda: flag[0],
        healthz_summary_getter=lambda: {"status": "ok", "version": "0.1.0", "checks": {}},
    )
    assert client.get("/startupz").status_code == 503
    flag[0] = True
    assert client.get("/startupz").status_code == 200


def test_healthz_always_200_with_summary_body() -> None:
    client = _mk_app(
        livez_provider=lambda: "alive",
        readyz_providers=lambda: {"db": "unavailable"},
        startup_flag_getter=lambda: True,
        healthz_summary_getter=lambda: {
            "status": "degraded",
            "version": "0.1.0",
            "checks": {"db": "unavailable", "telegram": "polling"},
        },
    )
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"]["db"] == "unavailable"
