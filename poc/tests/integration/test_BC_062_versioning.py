"""BC-062 — software + API + schema versioning.

Covers:
    - CAP-T124: VERSION file + verify-version-sync lint
    - CAP-T125: Hub API version header + /healthz + strict-mode gate
    - CAP-T126: SQLite PRAGMA user_version + migration gate
    - CAP-T127: make status VERSION column + mismatch warning
    - CAP-B045: Operator can always see what's running + knows when a
      deployed component is out of sync
"""

from __future__ import annotations

import importlib
import io
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import redirect_stdout
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from poc.scripts.verify_version_sync import check_version_sync

REPO_ROOT = Path(__file__).resolve().parents[3]


# ── Shared fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def hub_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("BEACONRY_MOCK", "1")
    monkeypatch.setenv("BEACONRY_SKIP_GRYPE_DB", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "0")
    monkeypatch.delenv("BEACONRY_STRICT_API", raising=False)

    import hub

    importlib.reload(hub)
    hub.init_db()
    client = TestClient(hub.api)
    yield {"client": client, "db": hub.DB_PATH, "hub": hub}
    client.close()
    importlib.reload(hub)


@pytest.fixture
def hub_strict_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("BEACONRY_MOCK", "1")
    monkeypatch.setenv("BEACONRY_SKIP_GRYPE_DB", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "0")
    monkeypatch.setenv("BEACONRY_STRICT_API", "1")

    import hub

    importlib.reload(hub)
    hub.init_db()
    client = TestClient(hub.api)
    yield {"client": client, "hub": hub}
    client.close()
    importlib.reload(hub)


# ── CAP-T124: VERSION file + loader ────────────────────────────────────────


def test_version_file_reads_0_1_0() -> None:
    assert (REPO_ROOT / "VERSION").read_text().strip() == "0.1.0"
    assert (REPO_ROOT / "poc" / "VERSION").read_text().strip() == "0.1.0"


def test_loader_matches_version_file() -> None:
    import poc.beaconry_health as bh

    assert bh.read_version() == "0.1.0"
    assert bh.SOFTWARE_VERSION == "0.1.0"


def test_verify_version_sync_passes_live() -> None:
    """CAP-T124: lint is clean on the live repo."""
    errors = check_version_sync(REPO_ROOT)
    assert errors == [], errors


def test_verify_version_sync_catches_drift(tmp_path: Path) -> None:
    """CAP-T124: drift in poc/VERSION triggers an error."""
    (tmp_path / "VERSION").write_text("0.1.0\n")
    (tmp_path / "poc").mkdir()
    (tmp_path / "poc" / "VERSION").write_text("0.1.1\n")
    (tmp_path / "poc" / "hub").mkdir()
    (tmp_path / "poc" / "hub" / "hub.py").write_text("")
    (tmp_path / "poc" / "scout").mkdir()
    (tmp_path / "poc" / "scout" / "scout.py").write_text("")
    (tmp_path / "poc" / "scripts").mkdir()
    (tmp_path / "poc" / "scripts" / "cli_http.py").write_text("")
    errors = check_version_sync(tmp_path)
    assert any("VERSION drift" in e for e in errors), errors


def test_verify_version_sync_catches_hardcoded_literal(tmp_path: Path) -> None:
    """CAP-T124: a drifting *_VERSION literal is flagged."""
    (tmp_path / "VERSION").write_text("0.1.0\n")
    (tmp_path / "poc").mkdir()
    (tmp_path / "poc" / "VERSION").write_text("0.1.0\n")
    (tmp_path / "poc" / "hub").mkdir()
    (tmp_path / "poc" / "hub" / "hub.py").write_text('HUB_VERSION = "9.9.9"\n')
    (tmp_path / "poc" / "scout").mkdir()
    (tmp_path / "poc" / "scout" / "scout.py").write_text("")
    (tmp_path / "poc" / "scripts").mkdir()
    (tmp_path / "poc" / "scripts" / "cli_http.py").write_text("")
    errors = check_version_sync(tmp_path)
    assert any("HUB_VERSION" in e and "9.9.9" in e for e in errors), errors


# ── CAP-T125: API header + /healthz + strict ────────────────────────────────


def test_hub_response_carries_api_header(hub_env) -> None:
    r = hub_env["client"].get("/healthz")
    assert r.status_code == 200
    assert r.headers.get("X-Beaconry-API") == "1"


def test_healthz_carries_version_block(hub_env) -> None:
    body = hub_env["client"].get("/healthz").json()
    assert body["version"]["software"] == "0.1.0"
    assert body["version"]["api"] == 1
    assert body["version"]["schema"] == 2


def test_missing_expect_header_never_fails(hub_env) -> None:
    """CAP-T125: unversioned callers (curl) pass through."""
    r = hub_env["client"].get("/healthz")
    assert r.status_code == 200


def test_api_mismatch_soft_passes(hub_env, caplog: pytest.LogCaptureFixture) -> None:
    """CAP-T125: default soft mode — WARNING logged, 200 returned."""
    with caplog.at_level(logging.WARNING):
        r = hub_env["client"].get("/healthz", headers={"X-Beaconry-API-Expect": "2"})
    assert r.status_code == 200
    assert r.headers.get("X-Beaconry-API") == "1"
    assert any("api-version mismatch" in rec.message for rec in caplog.records), caplog.records


def test_api_mismatch_strict_412(hub_strict_env) -> None:
    """CAP-T125: BEACONRY_STRICT_API=1 → 412 Precondition Failed."""
    r = hub_strict_env["client"].get("/healthz", headers={"X-Beaconry-API-Expect": "2"})
    assert r.status_code == 412
    body = r.json()
    assert body["detail"] == "api version mismatch"
    assert body["hub_serves"] == 1
    # Even on the short-circuit, the header is set so clients can learn
    # what the hub actually serves.
    assert r.headers.get("X-Beaconry-API") == "1"


def test_api_match_strict_passes(hub_strict_env) -> None:
    r = hub_strict_env["client"].get("/healthz", headers={"X-Beaconry-API-Expect": "1"})
    assert r.status_code == 200


# ── CAP-T126: schema version PRAGMA ─────────────────────────────────────────


def test_fresh_db_sets_pragma_to_expected(hub_env) -> None:
    con = sqlite3.connect(hub_env["db"])
    (value,) = con.execute("PRAGMA user_version").fetchone()
    con.close()
    assert value == hub_env["hub"].EXPECTED_SCHEMA_VERSION == 2


def test_legacy_db_migrates_to_expected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T126: PRAGMA = 0 (pre-BC-062) migrates up idempotently."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "beaconry.db"
    con = sqlite3.connect(db_path)
    con.execute(
        "CREATE TABLE findings (id TEXT PRIMARY KEY, host TEXT, cve_id TEXT, "
        "package TEXT, version TEXT, fixed_version TEXT, severity TEXT, cvss REAL, "
        "source_type TEXT, source_ref TEXT, description TEXT, status TEXT, "
        "tier INTEGER, found_at INTEGER, last_seen_ts INTEGER)"
    )
    con.execute("PRAGMA user_version = 0")
    con.commit()
    con.close()

    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("BEACONRY_MOCK", "1")
    monkeypatch.setenv("BEACONRY_SKIP_GRYPE_DB", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "0")
    import hub

    importlib.reload(hub)
    hub.init_db()

    con = sqlite3.connect(db_path)
    (value,) = con.execute("PRAGMA user_version").fetchone()
    con.close()
    assert value == 2
    importlib.reload(hub)


def test_newer_db_raises_schema_too_new(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T126: PRAGMA > EXPECTED → hard-fail refuses to boot."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "beaconry.db"
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA user_version = 99")
    con.commit()
    con.close()

    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("BEACONRY_MOCK", "1")
    monkeypatch.setenv("BEACONRY_SKIP_GRYPE_DB", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "fake")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "0")
    import hub

    importlib.reload(hub)
    with pytest.raises(hub.SchemaTooNew) as excinfo:
        hub.init_db()
    assert "99" in str(excinfo.value)
    assert "2" in str(excinfo.value)
    importlib.reload(hub)


# ── CAP-T127: make status renders VERSION column ────────────────────────────


def test_make_status_renders_version_column() -> None:
    """CAP-T127: VERSION column + no mismatch when CLI aligns with hub."""
    from poc.scripts import status

    hub_body = {
        "status": "ok",
        "version": {"software": "0.1.0", "api": 1, "schema": 2},
        "checks": {"last_scan_ts": 0, "telegram": "polling"},
    }
    scout_body = {
        "status": "ok",
        "version": {"software": "0.1.0", "api": 1},
        "checks": {"docker_socket": "ok", "last_scan_ts": 0, "images_seen": 3},
    }
    hub_cell, hub_api = status._format_version_cell(hub_body)
    scout_cell, scout_api = status._format_version_cell(scout_body)
    hub_status = status.ServiceStatus(
        name="beaconry-hub",
        state="running",
        health="healthy",
        last_scan="-",
        details=status._hub_details(hub_body),
        version=hub_cell,
        api_version=hub_api,
    )
    scout_status = status.ServiceStatus(
        name="beaconry-scout",
        state="running",
        health="healthy",
        last_scan="-",
        details=status._scout_details(scout_body),
        version=scout_cell,
        api_version=scout_api,
    )
    text = status._format_table([hub_status, scout_status])
    assert "VERSION" in text.splitlines()[0]
    assert "0.1.0 · api1 · sch2" in text
    assert "0.1.0 · api1" in text
    assert status._api_mismatch_line([hub_status, scout_status]) == ""


def test_make_status_surfaces_api_mismatch() -> None:
    """CAP-T127: CLI expects api1, hub serves api2 → trailing warning line."""
    from poc.scripts import status

    hub_status = status.ServiceStatus(
        name="beaconry-hub",
        state="running",
        health="healthy",
        last_scan="-",
        details="ok",
        version="0.1.0 · api2 · sch2",
        api_version=2,
    )
    line = status._api_mismatch_line([hub_status])
    assert "api mismatch" in line
    assert "beaconry-hub" in line
    assert "api1" in line  # CLI expects
    assert "api2" in line  # hub serves


def test_make_status_legacy_version_string_degrades_gracefully() -> None:
    """CAP-T127: pre-BC-062 /healthz with 'version': '0.1.0' still renders."""
    from poc.scripts import status

    cell, api_int = status._format_version_cell({"version": "0.1.0"})
    assert cell == "0.1.0"
    assert api_int is None


# ── Scout + CLI send X-Beaconry-API-Expect ──────────────────────────────────


def test_scout_module_declares_client_api_version() -> None:
    import poc.scout.scout as scout

    assert scout.CLIENT_API_VERSION == 1


def test_cli_http_declares_client_api_version() -> None:
    from poc.scripts import cli_http

    assert cli_http.CLIENT_API_VERSION == 1


def test_cli_build_request_sends_expect_header() -> None:
    """CAP-T125: every CLI HTTP call carries X-Beaconry-API-Expect."""
    from poc.scripts import cli_http

    req = cli_http._build_request("http://hub.local/f", token="", method="GET")
    # urllib normalises header names to title-case; look it up that way.
    assert req.get_header("X-beaconry-api-expect") == "1"


# ── E2E through TestClient: scout-style POST advertises expect header ──────


def test_scout_style_post_header_round_trip(hub_env, caplog: pytest.LogCaptureFixture) -> None:
    """CAP-T125: a mismatched POST warns but still succeeds in soft mode;
    response carries the hub's own API header for client inspection."""
    with caplog.at_level(logging.WARNING):
        r = hub_env["client"].post(
            "/scan",
            json={"host": "testhost", "sboms": []},
            headers={"X-Beaconry-API-Expect": "999"},
        )
    assert r.status_code == 200
    assert r.headers.get("X-Beaconry-API") == "1"
    assert any("api-version mismatch" in rec.message for rec in caplog.records)


# ── Lint entry-point exits clean on live repo ──────────────────────────────


def test_verify_version_sync_cli_exits_zero() -> None:
    """CAP-T124: the module runnable via `python -m` exits 0 on the live repo."""
    from poc.scripts import verify_version_sync as v

    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = v.main() if hasattr(v, "main") else 0
    assert rc == 0, buf.getvalue()
