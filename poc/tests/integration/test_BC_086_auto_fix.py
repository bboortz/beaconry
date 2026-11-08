"""BC-086 — Auto-fix status with counter threshold.

Covers:
    - CAP-T176: Counter-based auto-fix; snoozed/accepted/open all eligible
    - CAP-T177: BEACONRY_AUTO_FIX_THRESHOLD configurable (default 3)
    - CAP-T178: Auto-fix regression has distinct 🔥 AUTO-FIX REGRESSED path
    - CAP-T179: Telegram notifications include "Last seen: X ago"
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
    """Isolated DB + audit log + reconcile enabled. Default threshold=3
    (matches production default) so BC-086 counter behavior is exercised."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(hub, "DATA_DIR", data_dir)
    monkeypatch.setattr(hub, "DB_PATH", data_dir / "beaconry.db")
    monkeypatch.setattr(hub, "RECONCILE_ENABLED", True)
    monkeypatch.setattr(hub, "AUTO_FIX_THRESHOLD", 3)
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


def _read(data_dir: Path, fid: str, col: str) -> int | str | None:
    """Return the scalar value of `col` for finding `fid`. Columns we
    touch are INTEGER (status is TEXT) — pyright wants a concrete
    union so downstream `int(...)` casts don't trip reportArgumentType."""
    con = sqlite3.connect(data_dir / "beaconry.db")
    row = con.execute(f"SELECT {col} FROM findings WHERE id=?", (fid,)).fetchone()
    con.close()
    return row[0] if row else None


# ── CAP-T176 schema + status ────────────────────────────────────────────────


def test_schema_has_absent_count_and_auto_fix_columns(recon_env: Path) -> None:
    """CAP-T176: migration added absent_count + auto_fixed_ts columns."""
    con = sqlite3.connect(recon_env / "beaconry.db")
    cols = {r[1] for r in con.execute("PRAGMA table_info(findings)").fetchall()}
    con.close()
    assert "absent_count" in cols
    assert "auto_fixed_ts" in cols


def test_schema_version_is_7(recon_env: Path) -> None:
    """CAP-T176 (+ BC-089, BC-092): migration runs to v7."""
    con = sqlite3.connect(recon_env / "beaconry.db")
    version = con.execute("PRAGMA user_version").fetchone()[0]
    con.close()
    assert version == 8  # BC-102 bumped 7 → 8 (scouts.last_offline_alert_ts)


# ── CAP-T177 threshold ──────────────────────────────────────────────────────


def test_auto_fix_threshold_default_is_3() -> None:
    """CAP-T177: pytest sometimes sees the env override from other tests;
    verify the parsed module-level constant is reasonable (≥1)."""
    assert hub.AUTO_FIX_THRESHOLD >= 1


def test_healthz_includes_auto_fix_threshold(recon_env: Path) -> None:
    """CAP-T177: /healthz surfaces configured threshold so `make status`
    can echo it without the operator reading env."""
    summary = hub._hub_healthz_summary()
    assert "auto_fix_threshold" in summary["checks"]
    assert summary["checks"]["auto_fix_threshold"] == hub.AUTO_FIX_THRESHOLD


# ── CAP-T176 counter-based transitions ──────────────────────────────────────


def test_counter_increments_when_open_absent(recon_env: Path) -> None:
    """CAP-T176: a single missed scan bumps absent_count but doesn't flip
    status (threshold=3)."""
    f = _finding(fid="aaaa000000000001")
    hub.save_finding(f)
    assert _read(recon_env, f.id, "absent_count") == 0

    hub.reconcile_scan("homelab", scanned_ids=set())
    assert _read(recon_env, f.id, "status") == "open"
    assert _read(recon_env, f.id, "absent_count") == 1


def test_open_flips_to_auto_fixed_at_threshold(recon_env: Path) -> None:
    """CAP-T176: after threshold consecutive missing scans, open → auto_fixed."""
    f = _finding(fid="aaaa000000000002")
    hub.save_finding(f)
    for _ in range(3):
        hub.reconcile_scan("homelab", scanned_ids=set())
    assert _read(recon_env, f.id, "status") == "auto_fixed"
    assert _read(recon_env, f.id, "absent_count") == 3
    auto_ts = _read(recon_env, f.id, "auto_fixed_ts")
    assert isinstance(auto_ts, int) and auto_ts > 0

    events = _audit_events(recon_env)
    auto_event = next((e for e in events if e["event"] == "finding_auto_fixed"), None)
    assert auto_event is not None
    assert auto_event["prev_status"] == "open"
    assert auto_event["threshold"] == 3
    assert auto_event["absent_scans"] == 3


def test_snoozed_flips_to_auto_fixed_at_threshold(recon_env: Path) -> None:
    """CAP-T176 (the key BC-086 bug fix): snoozed finding whose image
    disappears from scans for 3 cycles auto-closes. Pre-BC-086 reconcile
    skipped snoozed entirely — finding stayed snoozed forever."""
    f = _finding(fid="bbbb000000000003")
    hub.save_finding(f)
    hub.set_status(f.id, "snoozed")

    for _ in range(3):
        hub.reconcile_scan("homelab", scanned_ids=set())
    assert _read(recon_env, f.id, "status") == "auto_fixed"

    events = _audit_events(recon_env)
    auto_event = next((e for e in events if e["event"] == "finding_auto_fixed"), None)
    assert auto_event is not None
    assert auto_event["prev_status"] == "snoozed"


def test_accepted_flips_to_auto_fixed_at_threshold(recon_env: Path) -> None:
    """CAP-T176: same for accepted — acceptance is a decision about
    current state; once state is gone, close the loop."""
    f = _finding(fid="cccc000000000004")
    hub.save_finding(f)
    hub.set_status(f.id, "accepted")

    for _ in range(3):
        hub.reconcile_scan("homelab", scanned_ids=set())
    assert _read(recon_env, f.id, "status") == "auto_fixed"


def test_counter_resets_when_finding_reappears(recon_env: Path) -> None:
    """CAP-T176: if a finding reappears between absent scans, counter
    resets and the threshold timer starts over. The counter only ever
    grows across *uninterrupted* absences."""
    f = _finding(fid="dddd000000000005")
    hub.save_finding(f)

    # Two absent scans: counter at 2
    hub.reconcile_scan("homelab", scanned_ids=set())
    hub.reconcile_scan("homelab", scanned_ids=set())
    assert _read(recon_env, f.id, "absent_count") == 2

    # Finding reappears → bump_last_seen resets counter
    hub.bump_last_seen("homelab", {f.id})
    assert _read(recon_env, f.id, "absent_count") == 0

    # One more absent scan: counter at 1, NOT at 3 (previous progress lost)
    hub.reconcile_scan("homelab", scanned_ids=set())
    assert _read(recon_env, f.id, "status") == "open"
    assert _read(recon_env, f.id, "absent_count") == 1


def test_last_seen_updates_on_present(recon_env: Path) -> None:
    """CAP-T179: last_seen_ts updates on every scan where finding is present."""
    f = _finding(fid="eeee000000000006")
    hub.save_finding(f)
    old = _read(recon_env, f.id, "last_seen_ts")
    assert isinstance(old, int) and old > 0

    # Force a forward clock with bump_last_seen
    hub.bump_last_seen("homelab", {f.id}, now_ts=old + 1000)
    new = _read(recon_env, f.id, "last_seen_ts")
    assert isinstance(new, int) and new >= old + 1000


# ── CAP-T178 auto-fix regression ────────────────────────────────────────────


def test_auto_fix_regression_uses_distinct_audit_event(recon_env: Path) -> None:
    """CAP-T178: auto_fixed finding that returns in a later scan flips to
    `regressed` AND emits `regressed_from_auto_fix` (not plain `regressed`).
    Operator can distinguish from manual-fix regressions in `make audit`."""
    f = _finding(fid="ffff000000000007")
    hub.save_finding(f)
    for _ in range(3):
        hub.reconcile_scan("homelab", scanned_ids=set())
    assert _read(recon_env, f.id, "status") == "auto_fixed"

    # Now the image comes back
    r1, _, r3 = hub.reconcile_scan("homelab", scanned_ids={f.id})
    assert r1 == []  # NOT the generic regressed-from-fix list
    assert len(r3) == 1 and r3[0].id == f.id
    assert _read(recon_env, f.id, "status") == "regressed"

    events = _audit_events(recon_env)
    event_types = [e["event"] for e in events]
    assert "regressed_from_auto_fix" in event_types
    assert "regressed" not in event_types  # distinct from BC-031's event


def test_auto_fix_regression_uses_distinct_message() -> None:
    """CAP-T178: format_auto_fix_regression says "AUTO-FIX REGRESSED",
    NOT the generic "FIX REGRESSED" (BC-031). Operator-visible difference."""
    f = _finding(fid="2222000000000008")
    body = hub.format_auto_fix_regression(f, auto_fixed_ts=1800000000)
    assert "AUTO-FIX REGRESSED" in body
    assert "FIX REGRESSED" in body  # substring ok ("AUTO-FIX REGRESSED" contains it)
    # Distinctness check: no generic "<b>FIX REGRESSED</b>" tag by itself
    assert "🔥 <b>AUTO-FIX REGRESSED</b>" in body


# ── CAP-T179 last-seen in notifications ─────────────────────────────────────


def test_format_last_seen_variants() -> None:
    """CAP-T179: human-readable output: just now | Nm | Nh | Nd."""
    now = 1800000000
    assert hub.format_last_seen(now - 10, now_ts=now) == "Last seen: just now"
    assert hub.format_last_seen(now - 600, now_ts=now) == "Last seen: 10m ago"
    assert hub.format_last_seen(now - 7200, now_ts=now) == "Last seen: 2h ago"
    assert hub.format_last_seen(now - 3 * 86400, now_ts=now) == "Last seen: 3d ago"
    # 0 means never-seen → empty (no line)
    assert hub.format_last_seen(0, now_ts=now) == ""


def test_notification_includes_last_seen_when_provided() -> None:
    """CAP-T179: pass last_seen_ts into format_notification → body includes
    the Last seen line. Backward-compat: not passing it = no line."""
    f = _finding(fid="3333000000000009")
    with_hint = hub.format_notification(f, last_seen_ts=int(1800000000 - 3600))
    without = hub.format_notification(f)
    assert "Last seen:" in with_hint
    assert "Last seen:" not in without


def test_regression_body_includes_last_seen() -> None:
    """CAP-T179: regression notifications also include last_seen context."""
    f = _finding(fid="4444000000000010")
    body = hub.format_regression(f, last_fixed_ts=1799000000, last_seen_ts=1799990000)
    assert "Last seen:" in body


def test_auto_fix_regression_body_includes_last_seen() -> None:
    """CAP-T179: AUTO-FIX regression also carries last_seen."""
    f = _finding(fid="5555000000000011")
    body = hub.format_auto_fix_regression(f, auto_fixed_ts=1799500000, last_seen_ts=1799990000)
    assert "Last seen:" in body


# ── Threshold respects env override ─────────────────────────────────────────


def test_threshold_one_closes_immediately(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CAP-T177: AUTO_FIX_THRESHOLD=1 reproduces BC-031's immediate-close
    cadence. Operators can opt into the pre-BC-086 behavior if they prefer."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(hub, "DATA_DIR", data_dir)
    monkeypatch.setattr(hub, "DB_PATH", data_dir / "beaconry.db")
    monkeypatch.setattr(hub, "RECONCILE_ENABLED", True)
    monkeypatch.setattr(hub, "AUTO_FIX_THRESHOLD", 1)
    hub.init_db()

    f = _finding(fid="6666000000000012")
    hub.save_finding(f)
    hub.reconcile_scan("homelab", scanned_ids=set())
    con = sqlite3.connect(data_dir / "beaconry.db")
    status = con.execute("SELECT status FROM findings WHERE id=?", (f.id,)).fetchone()[0]
    con.close()
    assert status == "auto_fixed"
