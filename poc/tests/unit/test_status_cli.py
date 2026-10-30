"""BC-010 — `make status` CLI output and exit code.

Covers:
    - CAP-B015: Operator always knows if beaconry itself is working
    - CAP-T039: `make status` prints an operator-readable summary of all beaconry services
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from poc.scripts import status as status_cli


def _hub_healthz_body(
    *, telegram: str = "polling", db: str = "ok", grype_db: str = "ok", last_scan_ts: int = 0
) -> dict:
    return {
        "status": "ok" if telegram == "polling" and db == "ok" and grype_db == "ok" else "degraded",
        "version": "0.1.0",
        "checks": {
            "db": db,
            "grype_db": grype_db,
            "grype_binary": "ok",
            "telegram": telegram,
            "last_scan_ts": last_scan_ts,
        },
    }


def _scout_healthz_body(
    *, status: str = "ok", last_scan_ts: int = 1, images_seen: int = 11, bytes_posted: int = 1024
) -> dict:
    return {
        "status": status,
        "last_scan_ts": last_scan_ts,
        "images_seen": images_seen,
        "bytes_posted": bytes_posted,
    }


def test_status_cli_prints_one_line_per_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(status_cli, "fetch_hub_healthz", lambda: _hub_healthz_body())
    (tmp_path / "scout.healthz.json").write_text(json.dumps(_scout_healthz_body()))
    monkeypatch.setattr(status_cli, "SCOUT_DATA_DIR", tmp_path)
    monkeypatch.setattr(status_cli, "SCOUT_CONTAINER", "test-nonexistent-container")

    exit_code = status_cli.main()
    out = capsys.readouterr().out

    assert "hub:" in out
    assert "scout:" in out
    assert "healthy" in out
    assert exit_code == 0


def test_status_cli_exits_nonzero_on_degraded_hub(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        status_cli, "fetch_hub_healthz", lambda: _hub_healthz_body(telegram="auth_failed")
    )
    (tmp_path / "scout.healthz.json").write_text(json.dumps(_scout_healthz_body()))
    monkeypatch.setattr(status_cli, "SCOUT_DATA_DIR", tmp_path)
    monkeypatch.setattr(status_cli, "SCOUT_CONTAINER", "test-nonexistent-container")

    exit_code = status_cli.main()
    out = capsys.readouterr().out

    assert exit_code != 0, "degraded hub must trigger non-zero exit"
    assert "degraded" in out.lower() or "auth_failed" in out


def test_status_cli_handles_unreachable_hub(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    def _raise(*_a: object, **_k: object) -> dict:
        raise ConnectionError("hub not up")

    monkeypatch.setattr(status_cli, "fetch_hub_healthz", _raise)
    (tmp_path / "scout.healthz.json").write_text(json.dumps(_scout_healthz_body()))
    monkeypatch.setattr(status_cli, "SCOUT_DATA_DIR", tmp_path)
    monkeypatch.setattr(status_cli, "SCOUT_CONTAINER", "test-nonexistent-container")

    exit_code = status_cli.main()
    out = capsys.readouterr().out

    assert exit_code != 0
    assert "hub:" in out
    assert "unreachable" in out.lower() or "down" in out.lower()


def test_status_cli_handles_missing_scout_sentinel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(status_cli, "fetch_hub_healthz", lambda: _hub_healthz_body())
    monkeypatch.setattr(status_cli, "SCOUT_DATA_DIR", tmp_path)
    monkeypatch.setattr(
        status_cli, "SCOUT_CONTAINER", "test-nonexistent-container"
    )  # no files written

    exit_code = status_cli.main()
    out = capsys.readouterr().out

    assert exit_code != 0
    assert "scout:" in out
    assert "unreachable" in out.lower() or "not_ready" in out.lower() or "down" in out.lower()
