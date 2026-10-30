"""BC-010 — four-endpoint health convention tests for the hub.

The restart-loop trap: /livez must NEVER depend on external systems. If the DB,
Telegram token, or any dependency fails, /livez stays 200 — restarting the
container would just reproduce the failure. Degradation goes to /healthz.

Covers:
    - CAP-T035: Hub implements the four-endpoint health convention
    - CAP-T038: Hub /livez and /readyz survive degraded external dependencies without restart-looping
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import hub
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    return TestClient(hub.api)


# ── /livez — the restart-loop trap guard ──────────────────────────────────────


def test_livez_returns_200_in_normal_state(client: TestClient) -> None:
    r = client.get("/livez")
    assert r.status_code == 200


def test_livez_survives_db_failure(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Trap guard: a dead DB must NOT fail /livez. Restarting won't fix the DB."""

    def _bomb(*a: object, **k: object) -> object:
        raise OSError("DB disk full")

    monkeypatch.setattr(sqlite3, "connect", _bomb)
    r = client.get("/livez")
    assert r.status_code == 200, "DB failure must not fail liveness (restart-loop trap)"


def test_livez_survives_dead_telegram(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Trap guard: a dead Telegram app must NOT fail /livez. Restart won't fix the token."""
    monkeypatch.setattr(hub, "tg_app", None, raising=False)
    r = client.get("/livez")
    assert r.status_code == 200


# ── /readyz — local prereqs ───────────────────────────────────────────────────


def test_readyz_returns_200_when_all_prereqs_ok(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Local prereqs: DB writable, grype binary, grype DB dir exists."""
    grype_dir = tmp_path / "grype-db"
    grype_dir.mkdir()
    (grype_dir / "vulnerability.db").write_text("x")
    monkeypatch.setenv("GRYPE_DB_CACHE_DIR", str(grype_dir))
    monkeypatch.setattr(hub, "_startup_complete", True, raising=False)
    monkeypatch.setattr(hub.shutil, "which", lambda _cmd: "/usr/local/bin/grype")

    r = client.get("/readyz")
    assert r.status_code == 200


def test_readyz_503_when_grype_db_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """/readyz must 503 when grype DB directory is missing or empty — body names the check."""
    monkeypatch.setenv("GRYPE_DB_CACHE_DIR", str(tmp_path / "nope"))
    monkeypatch.setattr(hub, "_startup_complete", True, raising=False)
    monkeypatch.setattr(hub.shutil, "which", lambda _cmd: "/usr/local/bin/grype")

    r = client.get("/readyz")
    assert r.status_code == 503
    body = r.json()
    assert "grype_db" in body.get("failed", []), f"body must name grype_db failure: {body}"


def test_readyz_503_when_grype_binary_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    grype_dir = tmp_path / "grype-db"
    grype_dir.mkdir()
    (grype_dir / "vulnerability.db").write_text("x")
    monkeypatch.setenv("GRYPE_DB_CACHE_DIR", str(grype_dir))
    monkeypatch.setattr(hub, "_startup_complete", True, raising=False)
    monkeypatch.setattr(hub.shutil, "which", lambda _cmd: None)

    r = client.get("/readyz")
    assert r.status_code == 503
    assert "grype_binary" in r.json().get("failed", [])


# ── /startupz — first-run gate ────────────────────────────────────────────────


def test_startupz_503_before_lifespan_completes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hub, "_startup_complete", False, raising=False)
    r = client.get("/startupz")
    assert r.status_code == 503


def test_startupz_200_after_lifespan_completes(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hub, "_startup_complete", True, raising=False)
    r = client.get("/startupz")
    assert r.status_code == 200


# ── /healthz — JSON summary ───────────────────────────────────────────────────


def test_healthz_json_shape(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    grype_dir = tmp_path / "grype-db"
    grype_dir.mkdir()
    (grype_dir / "vulnerability.db").write_text("x")
    monkeypatch.setenv("GRYPE_DB_CACHE_DIR", str(grype_dir))
    monkeypatch.setattr(hub, "_startup_complete", True, raising=False)
    monkeypatch.setattr(hub.shutil, "which", lambda _cmd: "/usr/local/bin/grype")

    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) >= {"status", "version", "checks"}, (
        f"healthz missing required keys: {body.keys()}"
    )
    assert body["status"] in ("ok", "degraded")
    assert set(body["checks"].keys()) >= {"db", "grype_db", "grype_binary", "telegram"}


def test_healthz_surfaces_telegram_degradation_without_503(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A dead Telegram shows as 'degraded' in /healthz but /healthz returns 200.
    Compose healthcheck targets /readyz, so this must not cascade to restart.
    """
    grype_dir = tmp_path / "grype-db"
    grype_dir.mkdir()
    (grype_dir / "vulnerability.db").write_text("x")
    monkeypatch.setenv("GRYPE_DB_CACHE_DIR", str(grype_dir))
    monkeypatch.setattr(hub, "_startup_complete", True, raising=False)
    monkeypatch.setattr(hub, "MOCK", False, raising=False)
    monkeypatch.setattr(hub, "tg_app", None, raising=False)
    monkeypatch.setattr(hub.shutil, "which", lambda _cmd: "/usr/local/bin/grype")

    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["checks"]["telegram"] in ("not_running", "degraded", "auth_failed")


def test_healthz_last_scan_ts_tracks_receive_scan(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    grype_dir = tmp_path / "grype-db"
    grype_dir.mkdir()
    (grype_dir / "vulnerability.db").write_text("x")
    monkeypatch.setenv("GRYPE_DB_CACHE_DIR", str(grype_dir))
    monkeypatch.setattr(hub, "_startup_complete", True, raising=False)
    monkeypatch.setattr(hub.shutil, "which", lambda _cmd: "/usr/local/bin/grype")
    monkeypatch.setattr(hub, "_last_scan_ts", 1_700_000_000, raising=False)

    r = client.get("/healthz")
    body = r.json()
    assert body["checks"]["last_scan_ts"] == 1_700_000_000


# ── /health alias ─────────────────────────────────────────────────────────────


def test_health_still_200_as_compat_alias(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Legacy /health kept returning 200 as a transitional alias for /readyz."""
    grype_dir = tmp_path / "grype-db"
    grype_dir.mkdir()
    (grype_dir / "vulnerability.db").write_text("x")
    monkeypatch.setenv("GRYPE_DB_CACHE_DIR", str(grype_dir))
    monkeypatch.setattr(hub, "_startup_complete", True, raising=False)
    monkeypatch.setattr(hub.shutil, "which", lambda _cmd: "/usr/local/bin/grype")

    r = client.get("/health")
    assert r.status_code == 200
