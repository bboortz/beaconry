"""BC-007 findings CLI — integration tests.

Covers:
    - CAP-B012: Operator reviews current findings state without re-running the pipeline
    - CAP-T025: Findings reporter reads SQLite, formats a table, honors STATUS filter
    - CAP-T052: `make findings` auto-routes to the running Compose hub's volume DB
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def _init_dev_db(data_dir: Path) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    db = data_dir / "beaconry.db"
    con = sqlite3.connect(db)
    con.execute("""
        CREATE TABLE IF NOT EXISTS findings (
            id           TEXT PRIMARY KEY,
            host         TEXT,
            cve_id       TEXT,
            package      TEXT,
            version      TEXT,
            fixed_version TEXT,
            severity     TEXT,
            cvss         REAL,
            source_type  TEXT,
            source_ref   TEXT,
            description  TEXT,
            found_at     INTEGER,
            status       TEXT DEFAULT 'open',
            tier         INTEGER DEFAULT 3
        )
    """)
    con.commit()
    con.close()
    return db


def _insert(db: Path, **row: object) -> None:
    con = sqlite3.connect(db)
    cols = ", ".join(row.keys())
    placeholders = ", ".join("?" * len(row))
    con.execute(f"INSERT INTO findings ({cols}) VALUES ({placeholders})", list(row.values()))
    con.commit()
    con.close()


def _row(**overrides: object) -> dict:
    defaults: dict[str, object] = {
        "host": "h",
        "package": "p",
        "version": "1",
        "fixed_version": "2",
        "source_type": "docker",
        "source_ref": "img:1",
        "description": "",
        "found_at": 1,
    }
    defaults.update(overrides)
    return defaults


def _row_with_tier(tier: int, **overrides: object) -> dict:
    return _row(tier=tier, **overrides)


def _run(data_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "DATA_DIR": str(data_dir),
        # BC-007 close-gap: force the local-file path so these tests don't
        # route to a live beaconry-hub container that may be running on the
        # host (compose-aware detection would otherwise shadow DATA_DIR).
        "BEACONRY_FINDINGS_LOCAL": "1",
    }
    return subprocess.run(
        [sys.executable, "-m", "poc.scripts.findings", *args],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
        timeout=15,
    )


def test_empty_db_friendly_message(tmp_path: Path) -> None:
    _init_dev_db(tmp_path)
    result = _run(tmp_path)
    assert result.returncode == 0
    assert "No findings" in result.stdout
    assert "make demo" in result.stdout


def test_prints_all_findings(tmp_path: Path) -> None:
    db = _init_dev_db(tmp_path)
    _insert(db, id="x1", cve_id="CVE-A", severity="Critical", cvss=9.8, status="open", **_row())
    _insert(db, id="x2", cve_id="CVE-B", severity="High", cvss=7.5, status="snoozed", **_row())
    result = _run(tmp_path)
    assert result.returncode == 0
    assert "CVE-A" in result.stdout
    assert "CVE-B" in result.stdout
    assert "CVE" in result.stdout and "Severity" in result.stdout and "Status" in result.stdout


def test_status_filter_narrows_output(tmp_path: Path) -> None:
    db = _init_dev_db(tmp_path)
    _insert(db, id="x1", cve_id="CVE-OPEN", severity="High", cvss=8.0, status="open", **_row())
    _insert(
        db,
        id="x2",
        cve_id="CVE-ACCEPTED",
        severity="Critical",
        cvss=9.0,
        status="accepted",
        **_row(),
    )
    result = _run(tmp_path, "--status", "accepted")
    assert result.returncode == 0
    assert "CVE-ACCEPTED" in result.stdout
    assert "CVE-OPEN" not in result.stdout


def test_sort_open_before_other_statuses(tmp_path: Path) -> None:
    db = _init_dev_db(tmp_path)
    _insert(
        db, id="x1", cve_id="CVE-ACC", severity="Critical", cvss=9.5, status="accepted", **_row()
    )
    _insert(db, id="x2", cve_id="CVE-OPEN", severity="High", cvss=7.5, status="open", **_row())
    result = _run(tmp_path)
    assert result.returncode == 0
    assert result.stdout.index("CVE-OPEN") < result.stdout.index("CVE-ACC")


def test_sort_within_status_uses_tier_then_cvss(tmp_path: Path) -> None:
    """BC-007 close-gaps: sort key is (status, tier, -cvss), not just (status, -cvss).
    Within the open group, tier-0 (KEV) must come before tier-2 even if tier-2 has
    higher cvss."""
    db = _init_dev_db(tmp_path)
    # Both open; tier-2 has higher cvss. Tier should win.
    _insert(
        db,
        id="x1",
        cve_id="CVE-TIER2-HIGH",
        severity="Critical",
        cvss=9.8,
        status="open",
        **_row_with_tier(2),
    )
    _insert(
        db,
        id="x2",
        cve_id="CVE-TIER0-LOW",
        severity="Critical",
        cvss=7.5,
        status="open",
        **_row_with_tier(0),
    )
    result = _run(tmp_path)
    assert result.returncode == 0
    assert result.stdout.index("CVE-TIER0-LOW") < result.stdout.index("CVE-TIER2-HIGH"), (
        "tier-0 (KEV) must come before tier-2 within the same status group even if "
        "tier-2 has higher cvss"
    )


def test_pure_read_does_not_modify_db(tmp_path: Path) -> None:
    db = _init_dev_db(tmp_path)
    _insert(db, id="x1", cve_id="CVE-X", severity="High", cvss=7.5, status="open", **_row())
    before = db.stat().st_mtime_ns
    result = _run(tmp_path)
    after = db.stat().st_mtime_ns
    assert result.returncode == 0
    assert before == after


def test_long_fields_truncate_with_ellipsis(tmp_path: Path) -> None:
    db = _init_dev_db(tmp_path)
    row = _row(
        source_ref="ghcr.io/some-org/very-long-image-name-here:v1.2.3-alpine-slim-build-123",
        package="a-package-with-a-rather-long-identifier-indeed",
    )
    _insert(db, id="x1", cve_id="CVE-LONG", severity="High", cvss=7.5, status="open", **row)
    result = _run(tmp_path)
    assert result.returncode == 0
    assert "…" in result.stdout
    for line in result.stdout.splitlines():
        # BC-011: Status column widened from 9→19 to fit "verification_failed" → 120 chars total.
        assert len(line) <= 120, f"line too long: {line!r}"


def test_unit_format_table_renders_header_and_rows() -> None:
    from poc.scripts.findings import format_table

    rows = [
        {
            "cve_id": "CVE-X",
            "severity": "High",
            "cvss": 7.5,
            "host": "h",
            "source_ref": "img:1",
            "package": "p",
            "status": "open",
        }
    ]
    output = format_table(rows)
    assert "CVE" in output
    assert "CVE-X" in output
    assert "7.5" in output


# ── BC-011: new statuses fixed_verified + verification_failed ────────────────


def test_bc011_status_filter_accepts_fixed_verified(tmp_path: Path) -> None:
    """BC-011 close-gap: --status fixed_verified must not be rejected by argparse."""
    db = _init_dev_db(tmp_path)
    _insert(
        db,
        id="bc11x1",
        cve_id="CVE-FV",
        severity="High",
        cvss=8.0,
        status="fixed_verified",
        **_row(),
    )
    _insert(db, id="bc11x2", cve_id="CVE-OPEN", severity="High", cvss=8.0, status="open", **_row())
    result = _run(tmp_path, "--status", "fixed_verified")
    assert result.returncode == 0, result.stderr
    assert "CVE-FV" in result.stdout
    assert "CVE-OPEN" not in result.stdout


def test_bc011_status_filter_accepts_verification_failed(tmp_path: Path) -> None:
    """BC-011 close-gap: --status verification_failed must not be rejected."""
    db = _init_dev_db(tmp_path)
    _insert(
        db,
        id="bc11x3",
        cve_id="CVE-VF",
        severity="Critical",
        cvss=9.1,
        status="verification_failed",
        **_row(),
    )
    result = _run(tmp_path, "--status", "verification_failed")
    assert result.returncode == 0, result.stderr
    assert "CVE-VF" in result.stdout


def test_bc011_sort_rank_puts_verification_failed_near_open(tmp_path: Path) -> None:
    """verification_failed is an actionable state — should sort before terminal ones."""
    db = _init_dev_db(tmp_path)
    _insert(
        db, id="bc11s1", cve_id="CVE-FIXED", severity="High", cvss=8.0, status="fixed", **_row()
    )
    _insert(
        db,
        id="bc11s2",
        cve_id="CVE-VERIFYFAIL",
        severity="High",
        cvss=8.0,
        status="verification_failed",
        **_row(),
    )
    result = _run(tmp_path)
    assert result.returncode == 0
    assert result.stdout.index("CVE-VERIFYFAIL") < result.stdout.index("CVE-FIXED"), (
        "verification_failed must sort before fixed — operator needs to see it first"
    )


def test_bc011_sort_rank_puts_fixed_verified_last(tmp_path: Path) -> None:
    """fixed_verified is the fully-settled terminal state — sort at the bottom."""
    db = _init_dev_db(tmp_path)
    _insert(db, id="bc11s3", cve_id="CVE-OPEN", severity="High", cvss=8.0, status="open", **_row())
    _insert(
        db,
        id="bc11s4",
        cve_id="CVE-FXV",
        severity="High",
        cvss=8.0,
        status="fixed_verified",
        **_row(),
    )
    result = _run(tmp_path)
    assert result.returncode == 0
    assert result.stdout.index("CVE-OPEN") < result.stdout.index("CVE-FXV")


# ── BC-007 close-gap: compose-volume routing ──────────────────────────────────


def test_running_compose_hub_detection_returns_false_when_container_absent(
    monkeypatch: object,
) -> None:
    """When no beaconry-hub container is running, we fall back to the local-dev DB."""
    from poc.scripts import findings

    # Point the detection at a container that cannot exist.
    monkeypatch.setattr(findings, "HUB_CONTAINER", "beaconry-hub-no-such-container-xyz")  # type: ignore[attr-defined]
    assert findings._running_compose_hub() is False


def test_beaconry_findings_local_env_forces_local_path(tmp_path: Path) -> None:
    """Operators can force the local-dev path even when a compose hub is running
    (useful when debugging the mock pipeline on a machine with a live stack)."""
    db = _init_dev_db(tmp_path)
    _insert(db, id="x1", cve_id="CVE-LOCAL", severity="High", cvss=7.5, status="open", **_row())

    env = {
        **os.environ,
        "DATA_DIR": str(tmp_path),
        "BEACONRY_FINDINGS_LOCAL": "1",
    }
    result = subprocess.run(
        [sys.executable, "-m", "poc.scripts.findings"],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
        env=env,
        timeout=15,
    )
    assert result.returncode == 0
    assert "CVE-LOCAL" in result.stdout, (
        "BEACONRY_FINDINGS_LOCAL=1 must take the local-file branch even if a hub is up"
    )
