"""BC-102 — Hub-side scout-offline alerts + stale-scout findings reconcile.

Covers:
    - CAP-B050: operator-visible detection of scout outages (edge-triggered
      Telegram + audit on offline / online transitions).
    - CAP-T204: `scout_offline_sweep` background-task logic: threshold
      respect, edge-triggered state via `last_offline_alert_ts`,
      stale-scout reconcile that bumps `absent_count` and flips to
      `auto_fixed` on the `BEACONRY_AUTO_FIX_THRESHOLD`-th tick, and the
      `BEACONRY_AUTO_CLOSE_STALE_SCOUT=0` opt-out.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from poc.hub import audit as _audit_mod
from poc.hub import hub

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def db_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(hub, "DATA_DIR", data_dir)
    monkeypatch.setattr(hub, "DB_PATH", data_dir / "beaconry.db")
    # Point the audit module's DATA_DIR at the test dir too.
    monkeypatch.setattr(_audit_mod, "DATA_DIR", data_dir, raising=False)
    monkeypatch.setattr(_audit_mod, "AUDIT_PATH", data_dir / "audit.log", raising=False)
    hub.init_db()
    return data_dir


@pytest.fixture(autouse=True)
def _no_notify_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace notify_scout_* with sync no-ops so the sweep doesn't
    create coroutines the test loop can't await."""

    def _noop(*_a, **_kw) -> None:
        return None

    monkeypatch.setattr(hub, "notify_scout_offline", _noop, raising=True)
    monkeypatch.setattr(hub, "notify_scout_online", _noop, raising=True)
    # asyncio.create_task(sync_callable()) would still error; replace
    # create_task to immediately call the arg.
    monkeypatch.setattr(hub.asyncio, "create_task", lambda coro: None)


def _seed_scout(
    db_path: Path, host: str, last_posted_ts: int, last_offline_alert_ts: int = 0
) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "INSERT INTO scouts (host, first_seen_ts, last_posted_ts, last_offline_alert_ts) "
            "VALUES (?, ?, ?, ?)",
            (host, last_posted_ts - 1, last_posted_ts, last_offline_alert_ts),
        )
        con.commit()
    finally:
        con.close()


def _seed_finding(
    db_path: Path, fid: str, host: str, status: str = "open", absent_count: int = 0
) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.execute(
            "INSERT INTO findings (id, host, cve_id, package, version, fixed_version, "
            "severity, cvss, source_type, source_ref, description, found_at, status, "
            "last_seen_ts, absent_count) "
            "VALUES (?, ?, ?, 'pkg', 'v', '', 'Critical', 9.9, 'docker', 'img', 'd', "
            "?, ?, ?, ?)",
            (fid, host, f"CVE-2026-{fid[:4]}", 1, status, 1, absent_count),
        )
        con.commit()
    finally:
        con.close()


def _read_scout(db_path: Path, host: str) -> tuple[int, int]:
    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "SELECT last_posted_ts, last_offline_alert_ts FROM scouts WHERE host=?",
            (host,),
        ).fetchone()
    finally:
        con.close()
    return (int(row[0]), int(row[1])) if row else (0, 0)


def _read_finding_status(db_path: Path, fid: str) -> tuple[str, int]:
    con = sqlite3.connect(db_path)
    try:
        row = con.execute("SELECT status, absent_count FROM findings WHERE id=?", (fid,)).fetchone()
    finally:
        con.close()
    return (row[0], int(row[1])) if row else ("", 0)


def _audit_events(db_env: Path) -> list[dict]:
    log_path = db_env / "audit.log"
    if not log_path.exists():
        return []
    import json as _json

    return [_json.loads(line) for line in log_path.read_text().splitlines() if line.strip()]


# ── G0: schema migration ────────────────────────────────────────────────────


def test_schema_adds_column_idempotently(db_env: Path) -> None:
    """ALTER TABLE adds last_offline_alert_ts on fresh + repeat init_db."""
    con = sqlite3.connect(db_env / "beaconry.db")
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info(scouts)").fetchall()}
    finally:
        con.close()
    assert "last_offline_alert_ts" in cols, "BC-102 column missing after init_db"
    # Second init is a no-op
    hub.init_db()  # must not raise


# ── G6: fresh scout never triggers ──────────────────────────────────────────


def test_fresh_scout_no_alert(db_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """last_posted_ts=0 (never POSTed) must not produce an alert."""
    con = sqlite3.connect(db_env / "beaconry.db")
    try:
        con.execute(
            "INSERT INTO scouts (host, first_seen_ts, last_posted_ts, last_offline_alert_ts) "
            "VALUES ('host-new', 1, 0, 0)"
        )
        con.commit()
    finally:
        con.close()
    counts = hub.scout_offline_sweep(
        now_ts=99_999, threshold_s=600, auto_close=True, threshold_ticks=3
    )
    assert counts == {"offline_alerts": 0, "online_alerts": 0, "findings_auto_closed": 0}


# ── G5a: below threshold no action ──────────────────────────────────────────


def test_below_threshold_no_action(db_env: Path) -> None:
    """Scout POSTed recently → no alert."""
    now = 10_000
    _seed_scout(db_env / "beaconry.db", "host-a", last_posted_ts=now - 60)
    counts = hub.scout_offline_sweep(now_ts=now, threshold_s=600, threshold_ticks=3)
    assert counts["offline_alerts"] == 0
    assert _read_scout(db_env / "beaconry.db", "host-a")[1] == 0


# ── G1: offline transition alerts once ──────────────────────────────────────


def test_offline_transition_alerts_once(db_env: Path) -> None:
    """Scout crosses threshold → exactly one offline alert + stamp last_offline_alert_ts."""
    now = 10_000
    _seed_scout(db_env / "beaconry.db", "host-a", last_posted_ts=now - 700)
    counts = hub.scout_offline_sweep(now_ts=now, threshold_s=600, threshold_ticks=3)
    assert counts["offline_alerts"] == 1
    assert counts["online_alerts"] == 0
    _, stamp = _read_scout(db_env / "beaconry.db", "host-a")
    assert stamp == now, f"last_offline_alert_ts should equal now, got {stamp}"

    events = _audit_events(db_env)
    assert any(e["event"] == "scout_offline" and e.get("host") == "host-a" for e in events), (
        f"expected scout_offline audit for host-a; got {[e['event'] for e in events]}"
    )


# ── G1b: no repeat alert while stable-offline ───────────────────────────────


def test_offline_stable_state_no_repeat(db_env: Path) -> None:
    """Already-alerted scout that stays offline → no additional alert next sweep."""
    now = 10_000
    _seed_scout(
        db_env / "beaconry.db",
        "host-a",
        last_posted_ts=now - 700,
        last_offline_alert_ts=now - 60,
    )
    counts = hub.scout_offline_sweep(
        now_ts=now, threshold_s=600, auto_close=False, threshold_ticks=3
    )
    assert counts["offline_alerts"] == 0
    assert counts["online_alerts"] == 0


# ── G2: recovery alert ──────────────────────────────────────────────────────


def test_recovery_emits_online_action(db_env: Path) -> None:
    """Previously-offline scout POSTs again → scout_online audit + clear stamp."""
    now = 10_000
    _seed_scout(
        db_env / "beaconry.db",
        "host-a",
        last_posted_ts=now - 10,  # just POSTed → under threshold
        last_offline_alert_ts=now - 500,
    )
    counts = hub.scout_offline_sweep(now_ts=now, threshold_s=600, threshold_ticks=3)
    assert counts["online_alerts"] == 1
    _, stamp = _read_scout(db_env / "beaconry.db", "host-a")
    assert stamp == 0, "last_offline_alert_ts must reset to 0 on recovery"

    events = _audit_events(db_env)
    assert any(e["event"] == "scout_online" and e.get("host") == "host-a" for e in events)


# ── G4: auto-close after threshold ticks ────────────────────────────────────


def test_auto_close_after_threshold_ticks(db_env: Path) -> None:
    """3 consecutive sweep ticks while scout is offline → findings auto_fixed."""
    now = 10_000
    _seed_scout(
        db_env / "beaconry.db",
        "host-a",
        last_posted_ts=now - 700,
        last_offline_alert_ts=now - 60,  # already alerted → reconcile path
    )
    _seed_finding(db_env / "beaconry.db", "f" * 16, "host-a", status="open")

    # Tick 1 — absent_count becomes 1
    hub.scout_offline_sweep(now_ts=now, threshold_s=600, auto_close=True, threshold_ticks=3)
    status, cnt = _read_finding_status(db_env / "beaconry.db", "f" * 16)
    assert status == "open" and cnt == 1

    # Tick 2 — absent_count becomes 2
    hub.scout_offline_sweep(now_ts=now + 1, threshold_s=600, auto_close=True, threshold_ticks=3)
    status, cnt = _read_finding_status(db_env / "beaconry.db", "f" * 16)
    assert status == "open" and cnt == 2

    # Tick 3 — flip to auto_fixed
    hub.scout_offline_sweep(now_ts=now + 2, threshold_s=600, auto_close=True, threshold_ticks=3)
    status, cnt = _read_finding_status(db_env / "beaconry.db", "f" * 16)
    assert status == "auto_fixed" and cnt == 3, f"expected auto_fixed@3, got {status}@{cnt}"

    events = _audit_events(db_env)
    assert any(e["event"] == "finding_auto_fixed_stale_scout" for e in events), (
        "expected finding_auto_fixed_stale_scout audit event"
    )


# ── G5b: opt-out disables only auto-close ──────────────────────────────────


def test_opt_out_disables_only_auto_close(db_env: Path) -> None:
    """auto_close=False → alert still fires, findings don't auto-close."""
    now = 10_000
    # Fresh offline transition
    _seed_scout(db_env / "beaconry.db", "host-a", last_posted_ts=now - 700)
    _seed_finding(db_env / "beaconry.db", "g" * 16, "host-a", status="open")

    # First tick: offline alert fires, no reconcile on first tick anyway
    hub.scout_offline_sweep(now_ts=now, threshold_s=600, auto_close=False, threshold_ticks=3)
    # Second tick: reconcile would run (was_alerted + is_offline_now) but opt-out
    hub.scout_offline_sweep(now_ts=now + 1, threshold_s=600, auto_close=False, threshold_ticks=3)
    hub.scout_offline_sweep(now_ts=now + 2, threshold_s=600, auto_close=False, threshold_ticks=3)
    status, cnt = _read_finding_status(db_env / "beaconry.db", "g" * 16)
    assert status == "open", f"opt-out must preserve open status, got {status}"
    assert cnt == 0, f"opt-out must not bump absent_count, got {cnt}"

    events = _audit_events(db_env)
    assert any(e["event"] == "scout_offline" for e in events), (
        "offline alert must still fire under opt-out"
    )


# ── G3: audit event names ──────────────────────────────────────────────────


def test_audit_event_names(db_env: Path) -> None:
    """All three BC-102 audit events use the documented names."""
    now = 10_000
    _seed_scout(db_env / "beaconry.db", "host-a", last_posted_ts=now - 700)
    _seed_finding(db_env / "beaconry.db", "h" * 16, "host-a", status="open")

    # Offline
    hub.scout_offline_sweep(now_ts=now, threshold_s=600, auto_close=True, threshold_ticks=1)
    # Reconcile — absent_count becomes 1 == threshold → auto_fixed on this same tick's next sweep
    hub.scout_offline_sweep(now_ts=now + 1, threshold_s=600, auto_close=True, threshold_ticks=1)
    # Recovery
    _seed_scout_last_posted(db_env / "beaconry.db", "host-a", new_ts=now + 10)
    hub.scout_offline_sweep(now_ts=now + 10, threshold_s=600, auto_close=True, threshold_ticks=1)

    names = {e["event"] for e in _audit_events(db_env)}
    assert {"scout_offline", "scout_online", "finding_auto_fixed_stale_scout"}.issubset(names), (
        f"missing BC-102 event names; got {names}"
    )


def _seed_scout_last_posted(db_path: Path, host: str, new_ts: int) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.execute("UPDATE scouts SET last_posted_ts=? WHERE host=?", (new_ts, host))
        con.commit()
    finally:
        con.close()


# ── Env-driven defaults ─────────────────────────────────────────────────────


def test_env_defaults_reasonable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default threshold ≥ 60s, sweep interval ≥ 5s, auto-close default on."""
    import importlib

    for var in (
        "BEACONRY_SCOUT_OFFLINE_ALERT_THRESHOLD_S",
        "BEACONRY_SCOUT_OFFLINE_SWEEP_INTERVAL_S",
        "BEACONRY_AUTO_CLOSE_STALE_SCOUT",
    ):
        monkeypatch.delenv(var, raising=False)
    importlib.reload(hub)
    assert hub.SCOUT_OFFLINE_ALERT_THRESHOLD_S == 600
    assert hub.SCOUT_OFFLINE_SWEEP_INTERVAL_S == 60
    assert hub.AUTO_CLOSE_STALE_SCOUT is True
