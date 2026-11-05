"""BC-031 integration — scan reconciliation + regression detection.

Covers:
    - CAP-B021: Operator sees regression alerts — previously-fixed CVE that returns raises a loud alarm
    - CAP-T071: Scan reconciliation diffs current scan against DB open+fixed rows
    - CAP-T072: `regressed` DB status with distinct Telegram notification format
    - CAP-T073: Audit log carries finding_auto_fixed, regressed, fix_held_through_scan event types
    - CAP-T176: Counter-based auto-fix (BC-086 — fixture pins AUTO_FIX_THRESHOLD=1 to preserve BC-031 cadence)

Each test simulates a different container/finding regression scenario by
calling hub.reconcile_scan with crafted (scanned_ids) sets against a tmp DB.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import hub
import pytest

from poc.hub.executor.base import Finding

# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture
def recon_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated DB + audit log + reconcile enabled."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(hub, "DATA_DIR", data_dir)
    monkeypatch.setattr(hub, "DB_PATH", data_dir / "beaconry.db")
    monkeypatch.setattr(hub, "RECONCILE_ENABLED", True)
    # BC-086: these BC-031 tests predate the counter-based threshold and
    # assume immediate close on first absence. Set AUTO_FIX_THRESHOLD=1
    # to preserve that cadence; BC-086's own tests exercise the counter.
    monkeypatch.setattr(hub, "AUTO_FIX_THRESHOLD", 1)
    hub.init_db()
    return data_dir


def _finding(
    *,
    fid: str,
    host: str = "homelab",
    cve_id: str = "CVE-TEST",
    source_ref: str = "nginx:1.24",
    source_type: str = "docker",
    package: str = "libc",
    tier: int = 2,
    kev: bool = False,
) -> Finding:
    return Finding(
        id=fid,
        host=host,
        cve_id=cve_id,
        package=package,
        version="1.0",
        fixed_version="1.1",
        severity="High",
        cvss=8.0,
        source_type=source_type,
        source_ref=source_ref,
        description="fixture",
        tier=tier,
        kev=kev,
    )


def _audit_events(data_dir: Path) -> list[dict]:
    path = data_dir / "audit.log"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _read_status(data_dir: Path, fid: str) -> str | None:
    con = sqlite3.connect(data_dir / "beaconry.db")
    row = con.execute("SELECT status FROM findings WHERE id=?", (fid,)).fetchone()
    con.close()
    return row[0] if row else None


# ── Gherkin Scenario 1: Manual fix auto-verifies on next scan ─────────────────


def test_open_finding_absent_from_scan_auto_closes(recon_env: Path) -> None:
    """Operator ran manual fix externally; next scout cycle shows the finding
    is gone; hub transitions open → fixed_verified silently."""
    f = _finding(fid="aaaa000000000001", cve_id="AVG-REGRESS-01")
    hub.save_finding(f)
    assert _read_status(recon_env, f.id) == "open"

    # Simulate: scout rescans but the fix stuck — f.id not in scanned set.
    regressed, auto_closed, _ = hub.reconcile_scan("homelab", scanned_ids=set())

    assert regressed == []
    assert len(auto_closed) == 1 and auto_closed[0].id == f.id
    assert _read_status(recon_env, f.id) == "auto_fixed"

    events = _audit_events(recon_env)
    assert any(e["event"] == "finding_auto_fixed" and e["finding_id"] == f.id for e in events)


# ── Gherkin Scenario 2: Regression triggers a loud alarm ─────────────────────


def test_fixed_finding_reappears_triggers_regression(
    recon_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Operator fixed the finding 3 months ago. Something rolled it back.
    Scout rescans and matches the same CVE on the same container. Hub
    transitions fixed → regressed and the loud alarm fires."""
    f = _finding(fid="bbbb000000000002", cve_id="CVE-REGRESSION-01")
    hub.save_finding(f)
    hub.set_status(f.id, "fixed")  # also writes last_fixed_ts
    assert _read_status(recon_env, f.id) == "fixed"

    # Simulate the regression: scout sees the vuln again.
    regressed, auto_closed, _ = hub.reconcile_scan("homelab", scanned_ids={f.id})

    assert auto_closed == []
    assert len(regressed) == 1 and regressed[0].cve_id == "CVE-REGRESSION-01"
    assert _read_status(recon_env, f.id) == "regressed"

    events = _audit_events(recon_env)
    regression_event = next(e for e in events if e["event"] == "regressed")
    assert regression_event["finding_id"] == f.id
    assert regression_event["prev_status"] == "fixed"
    assert "last_fixed_ts" in regression_event
    assert "regressed_ts" in regression_event


def test_fixed_verified_reappears_also_triggers_regression(recon_env: Path) -> None:
    """Same rule applies to findings that reached fixed_verified via
    operator-triggered [Fix now] (BC-031 path — distinct from BC-086's
    auto_fixed path tested separately)."""
    f = _finding(fid="cccc000000000003", cve_id="CVE-REGRESSION-02")
    hub.save_finding(f)
    hub.set_status(f.id, "fixed_verified")

    regressed, _, _ = hub.reconcile_scan("homelab", scanned_ids={f.id})
    assert len(regressed) == 1
    assert _read_status(recon_env, f.id) == "regressed"


# ── Gherkin Scenario 4: Snoozed finding ignored by reconciliation ────────────


def test_snoozed_finding_still_in_scan_is_untouched(recon_env: Path) -> None:
    """Snoozed finding still appearing in scan stays snoozed. BC-086 only
    closes snoozed findings that disappear — present ones are untouched."""
    f = _finding(fid="dddd000000000004")
    hub.save_finding(f)
    hub.set_status(f.id, "snoozed")

    hub.reconcile_scan("homelab", scanned_ids={f.id})
    assert _read_status(recon_env, f.id) == "snoozed"


# BC-086 explicitly supersedes the old BC-031 rule that snoozed/accepted
# are ignored by reconcile. See `test_snoozed_closes_on_absent` +
# `test_accepted_closes_on_absent` in `test_BC_086_auto_fix.py`.


# ── Gherkin Scenario 5: Accepted finding still in scan is untouched ─────────


def test_accepted_finding_still_in_scan_is_untouched(recon_env: Path) -> None:
    """Accepted + present in scan: stays accepted. Absent case tested by BC-086."""
    f = _finding(fid="eeee000000000005")
    hub.save_finding(f)
    hub.set_status(f.id, "accepted")

    hub.reconcile_scan("homelab", scanned_ids={f.id})
    assert _read_status(recon_env, f.id) == "accepted"


# ── Gherkin Scenario 6: regressed → re-fix held → fixed_verified ────────────


def test_regressed_absent_from_scan_auto_closes_to_fixed_verified(
    recon_env: Path,
) -> None:
    """Re-fix cycle: finding was regressed; operator re-fixed externally;
    next scan doesn't see it → auto-close to fixed_verified."""
    f = _finding(fid="ffff000000000006")
    hub.save_finding(f)
    hub.set_status(f.id, "fixed")
    hub.reconcile_scan("homelab", scanned_ids={f.id})  # → regressed
    assert _read_status(recon_env, f.id) == "regressed"

    # Operator re-applies fix. Next scan doesn't see it.
    _, auto_closed, _ = hub.reconcile_scan("homelab", scanned_ids=set())
    assert len(auto_closed) == 1
    assert _read_status(recon_env, f.id) == "auto_fixed"


# ── Configuration: BEACONRY_RECONCILE_ENABLED=0 disables everything ──────────


def test_reconcile_disabled_is_a_noop(recon_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Users who find scan reconciliation surveillance-y can opt out entirely."""
    f = _finding(fid="1111000000000007")
    hub.save_finding(f)
    hub.set_status(f.id, "fixed")
    monkeypatch.setattr(hub, "RECONCILE_ENABLED", False)

    # Would normally trigger regression if enabled.
    regressed, auto_closed, _ = hub.reconcile_scan("homelab", scanned_ids={f.id})

    assert regressed == [] and auto_closed == []
    assert _read_status(recon_env, f.id) == "fixed"  # unchanged
    # And no audit events written.
    events = [
        e for e in _audit_events(recon_env) if e["event"] in ("finding_auto_fixed", "regressed")
    ]
    assert events == []


# ── Isolation: cross-host findings not touched ───────────────────────────────


def test_reconcile_scoped_to_host(recon_env: Path) -> None:
    """A scan from host-A must not affect host-B findings."""
    f_a = _finding(fid="aaaa010101010101", host="host-a")
    f_b = _finding(fid="bbbb010101010101", host="host-b")
    hub.save_finding(f_a)
    hub.save_finding(f_b)

    # host-a scan: both findings present at host-a would be a bug.
    # We pass only the host-a id but the function filters by host anyway.
    hub.reconcile_scan("host-a", scanned_ids=set())  # auto-close f_a

    assert _read_status(recon_env, f_a.id) == "auto_fixed"
    assert _read_status(recon_env, f_b.id) == "open"  # untouched


# ── Simulated 2-container "regression scenario" ──────────────────────────────


def test_simulated_regression_two_container_scenario(recon_env: Path) -> None:
    """Comprehensive scenario mirroring real operator workflow:

    1. Scan 1: 3 containers, 3 findings → status=open.
    2. Operator fixes 1, snoozes 1, accepts 1.
    3. Scan 2 (same inventory): all 3 containers still present with CVE.
       - fixed → regressed (loud alarm)
       - snoozed → unchanged
       - accepted → unchanged
    4. Scan 3 (operator re-applied fix on container A, removed container B,
       kept C): the fixed container no longer shows CVE → fixed_verified.
    """
    container_a = _finding(fid="a0000000000000a0", cve_id="CVE-A", source_ref="svc-a:latest")
    container_b = _finding(fid="b0000000000000b0", cve_id="CVE-B", source_ref="svc-b:latest")
    container_c = _finding(fid="c0000000000000c0", cve_id="CVE-C", source_ref="svc-c:latest")

    # Scan 1: all three findings arrive.
    for f in (container_a, container_b, container_c):
        hub.save_finding(f)

    # Operator triages.
    hub.set_status(container_a.id, "fixed")
    hub.set_status(container_b.id, "snoozed")
    hub.set_status(container_c.id, "accepted")

    # Scan 2: everything still present.
    all_ids = {container_a.id, container_b.id, container_c.id}
    regressed, auto_closed, _ = hub.reconcile_scan("homelab", scanned_ids=all_ids)

    assert len(regressed) == 1 and regressed[0].cve_id == "CVE-A"
    assert (
        auto_closed == []
    )  # snoozed + accepted + present → stay; only absent triggers BC-086 auto-close
    assert _read_status(recon_env, container_a.id) == "regressed"
    assert _read_status(recon_env, container_b.id) == "snoozed"
    assert _read_status(recon_env, container_c.id) == "accepted"

    # Scan 3: operator re-fixed A properly; B still snoozed; C still accepted.
    scan3_ids = {container_b.id, container_c.id}
    regressed, auto_closed, _ = hub.reconcile_scan("homelab", scanned_ids=scan3_ids)

    assert len(auto_closed) == 1 and auto_closed[0].cve_id == "CVE-A"
    assert regressed == []
    assert _read_status(recon_env, container_a.id) == "auto_fixed"
    assert _read_status(recon_env, container_b.id) == "snoozed"
    assert _read_status(recon_env, container_c.id) == "accepted"


# ── Notification body shape ──────────────────────────────────────────────────


def test_format_regression_includes_fix_date_and_fire_header(recon_env: Path) -> None:
    f = _finding(fid="1234567812345678", cve_id="CVE-FIRE-01")
    # 2026-10-15 ~ fixed timestamp
    body = hub.format_regression(f, last_fixed_ts=1792800000)
    assert body.startswith("🔥")
    assert "FIX REGRESSED" in body
    assert "CVE-FIRE-01" in body
    assert "2026" in body  # fix date rendered
    # Operator-visible invariant: body must NOT contain raw audit internals
    assert "last_fixed_ts" not in body


def test_format_regression_handles_missing_fix_date_gracefully(recon_env: Path) -> None:
    f = _finding(fid="2345678923456789", cve_id="CVE-FIRE-02")
    body = hub.format_regression(f, last_fixed_ts=0)
    assert "unknown" in body


# ── BC-031 close-gap: legacy-row backfill ────────────────────────────────────


def test_init_db_backfills_last_fixed_ts_from_found_at(recon_env: Path) -> None:
    """BC-031 close-gap: legacy fixed/fixed_verified rows that predate the
    last_fixed_ts column must get backfilled from `found_at` on next init_db,
    so operators don't get `regressed since unknown` on previously-fixed CVEs."""
    # Simulate a pre-BC-031 row: fixed finding with last_fixed_ts=0 but a
    # real found_at value. (In production this is what the ALTER TABLE
    # migration leaves behind for rows that existed before the column.)
    f = _finding(fid="legacy0000000001", cve_id="CVE-LEGACY-01")
    hub.save_finding(f)
    con = sqlite3.connect(recon_env / "beaconry.db")
    con.execute(
        "UPDATE findings SET status='fixed', last_fixed_ts=0, found_at=1700000000 WHERE id=?",
        (f.id,),
    )
    con.commit()
    con.close()

    # Rerun init_db — backfill should apply.
    hub.init_db()

    con = sqlite3.connect(recon_env / "beaconry.db")
    row = con.execute("SELECT last_fixed_ts FROM findings WHERE id=?", (f.id,)).fetchone()
    con.close()
    assert row[0] == 1700000000, (
        "legacy fixed row must have last_fixed_ts backfilled from found_at "
        "so regression notifications don't render as 'unknown'"
    )


def test_init_db_backfill_does_not_touch_already_populated_rows(recon_env: Path) -> None:
    """Idempotence: a fixed row with a legitimate last_fixed_ts (set by
    set_status on a modern Fix tap) must not be overwritten by the backfill."""
    f = _finding(fid="modern0000000001", cve_id="CVE-MODERN-01")
    hub.save_finding(f)
    hub.set_status(f.id, "fixed")  # writes last_fixed_ts to now()
    con = sqlite3.connect(recon_env / "beaconry.db")
    row = con.execute("SELECT last_fixed_ts FROM findings WHERE id=?", (f.id,)).fetchone()
    original_ts = row[0]
    con.close()
    assert original_ts > 0

    hub.init_db()  # backfill pass should not touch this row

    con = sqlite3.connect(recon_env / "beaconry.db")
    row = con.execute("SELECT last_fixed_ts FROM findings WHERE id=?", (f.id,)).fetchone()
    con.close()
    assert row[0] == original_ts


def test_init_db_backfill_does_not_touch_open_rows(recon_env: Path) -> None:
    """Guard: backfill only applies to fixed/fixed_verified status."""
    f = _finding(fid="openrow000000001", cve_id="CVE-OPEN-01")
    hub.save_finding(f)
    # Row is `open` with found_at > 0. Backfill must NOT set last_fixed_ts.

    hub.init_db()

    con = sqlite3.connect(recon_env / "beaconry.db")
    row = con.execute("SELECT last_fixed_ts FROM findings WHERE id=?", (f.id,)).fetchone()
    con.close()
    assert row[0] == 0, "open rows must stay at last_fixed_ts=0 — they were never fixed"
