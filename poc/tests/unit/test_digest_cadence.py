"""BC-036 — digest cadence throttle with content-hash fallback.

Covers:
    - CAP-T080: Digest cadence throttle — `should_send_digest` returns True on
      first-fire, on content change, or when the interval has elapsed; False
      inside the interval with unchanged content. Opt-out via INTERVAL_S=0.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import hub
import pytest

from poc.hub.executor.base import Finding


@pytest.fixture
def digest_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(hub, "DATA_DIR", data_dir)
    monkeypatch.setattr(hub, "DB_PATH", data_dir / "beaconry.db")
    monkeypatch.setattr(hub, "DIGEST_INTERVAL_S", 86400)
    hub.init_db()
    yield data_dir


def _finding(fid: str, cve_id: str = "CVE-X") -> Finding:
    return Finding(
        id=fid,
        host="h1",
        cve_id=cve_id,
        package="pkg",
        version="1",
        fixed_version="2",
        severity="High",
        cvss=8.0,
        source_type="docker",
        source_ref="nginx:1.24",
        description="d",
    )


# ── _digest_hash properties ─────────────────────────────────────────────────


def test_digest_hash_properties() -> None:
    """Stable, order-independent, sensitive to set changes."""
    a = [_finding("a"), _finding("b"), _finding("c")]
    b = [_finding("c"), _finding("a"), _finding("b")]  # same set, different order
    c = [_finding("a"), _finding("b")]  # subset
    d = [_finding("a"), _finding("b"), _finding("d")]  # one swap

    ha = hub._digest_hash(a)
    hb = hub._digest_hash(b)
    hc = hub._digest_hash(c)
    hd = hub._digest_hash(d)

    assert ha == hb, "order-independent"
    assert ha != hc, "sensitive to missing id"
    assert ha != hd, "sensitive to swapped id"
    # Stability — second call reproduces the same hash.
    assert ha == hub._digest_hash(a)


def test_digest_hash_empty_set() -> None:
    """Edge case: empty suppressed list has a stable hash (not an error)."""
    h = hub._digest_hash([])
    assert isinstance(h, str) and len(h) > 0


# ── digest_state table ──────────────────────────────────────────────────────


def test_digest_state_table_migration(digest_env: Path) -> None:
    con = sqlite3.connect(hub.DB_PATH)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert "digest_state" in tables


def test_digest_state_migration_idempotent(digest_env: Path) -> None:
    hub.init_db()
    hub.init_db()
    con = sqlite3.connect(hub.DB_PATH)
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    con.close()
    assert "digest_state" in tables


# ── should_send_digest decisions ────────────────────────────────────────────


def test_first_digest_in_window_fires(digest_env: Path) -> None:
    """No prior digest for this host → always send."""
    s = [_finding("a"), _finding("b")]
    should, _hash, reason = hub.should_send_digest("h1", s, now=1000)
    assert should is True
    assert reason == "first_fire"


def test_repeat_within_interval_same_set_skipped(digest_env: Path) -> None:
    """Interval not elapsed + hash unchanged → no digest."""
    s = [_finding("a"), _finding("b")]
    # Simulate a prior send.
    hub.record_digest_sent("h1", s, now=1000)
    should, _hash, reason = hub.should_send_digest("h1", s, now=1000 + 3600)
    assert should is False
    assert reason == "throttled"


def test_set_change_bypasses_interval_when_enabled(
    digest_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BC-036 behaviour: with DIGEST_ON_CONTENT_CHANGE=True (default),
    a hash change fires the digest even inside the interval."""
    monkeypatch.setattr(hub, "DIGEST_ON_CONTENT_CHANGE", True)
    s1 = [_finding("a"), _finding("b")]
    hub.record_digest_sent("h1", s1, now=1000)
    s2 = [_finding("a"), _finding("c")]  # swap b for c
    should, _hash, reason = hub.should_send_digest("h1", s2, now=1000 + 3600)
    assert should is True
    assert reason == "content_changed"


def test_set_change_respects_interval_when_disabled(
    digest_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BC-065: DIGEST_ON_CONTENT_CHANGE=False → hash change does NOT
    bypass the interval. Reason is 'throttled' (same as unchanged inside
    the interval) so audit log stays coherent."""
    monkeypatch.setattr(hub, "DIGEST_ON_CONTENT_CHANGE", False)
    s1 = [_finding("a"), _finding("b")]
    hub.record_digest_sent("h1", s1, now=1000)
    s2 = [_finding("a"), _finding("c")]  # content changed
    should, _hash, reason = hub.should_send_digest("h1", s2, now=1000 + 3600)
    assert should is False
    assert reason == "throttled"


def test_interval_elapsed_fires_when_content_change_disabled(
    digest_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BC-065: the interval-elapsed path still fires when the content-
    change bypass is turned off — the 24h floor is still enforced."""
    monkeypatch.setattr(hub, "DIGEST_ON_CONTENT_CHANGE", False)
    s1 = [_finding("a"), _finding("b")]
    hub.record_digest_sent("h1", s1, now=1000)
    s2 = [_finding("a"), _finding("c")]  # content changed too
    # 25h later → interval has elapsed, must fire regardless of flag.
    should, _hash, reason = hub.should_send_digest("h1", s2, now=1000 + 25 * 3600)
    assert should is True
    assert reason == "interval_elapsed"


def test_first_fire_ignores_content_change_flag(
    digest_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BC-065: a host that has never received a digest still fires on
    the first eligible scan even with the flag off (there is no prior
    hash to compare)."""
    monkeypatch.setattr(hub, "DIGEST_ON_CONTENT_CHANGE", False)
    s = [_finding("a")]
    should, _hash, reason = hub.should_send_digest("h1", s, now=1000)
    assert should is True
    assert reason == "first_fire"


def test_interval_elapsed_fires_even_unchanged(digest_env: Path) -> None:
    """Interval elapsed, same set → periodic nudge sends."""
    s = [_finding("a"), _finding("b")]
    hub.record_digest_sent("h1", s, now=1000)
    # 25h later
    should, _hash, reason = hub.should_send_digest("h1", s, now=1000 + 25 * 3600)
    assert should is True
    assert reason == "interval_elapsed"


def test_interval_zero_never_throttles(digest_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """BEACONRY_DIGEST_INTERVAL_S=0 → send every call (pre-BC-036 behavior)."""
    monkeypatch.setattr(hub, "DIGEST_INTERVAL_S", 0)
    s = [_finding("a")]
    hub.record_digest_sent("h1", s, now=1000)
    # 1 second later, same set — would be throttled with default interval
    should, _hash, reason = hub.should_send_digest("h1", s, now=1001)
    assert should is True
    assert reason == "throttle_disabled"


def test_empty_set_never_fires(digest_env: Path) -> None:
    """BC-022 regression guard — empty suppressed must never trigger send."""
    should, _hash, reason = hub.should_send_digest("h1", [], now=1000)
    assert should is False
    assert reason == "empty"


def test_per_host_state_isolation(digest_env: Path) -> None:
    """h1's recent send must not throttle h2."""
    s = [_finding("a")]
    hub.record_digest_sent("h1", s, now=1000)
    should_h1, _, _ = hub.should_send_digest("h1", s, now=1000 + 60)
    should_h2, _, _ = hub.should_send_digest("h2", s, now=1000 + 60)
    assert should_h1 is False
    assert should_h2 is True


# ── Audit events ────────────────────────────────────────────────────────────


def test_audit_events_cover_both_paths(digest_env: Path) -> None:
    """record_digest_sent emits `digest_sent`; record_digest_throttled emits
    `digest_throttled`. Both carry host + hash."""
    s = [_finding("a")]
    hub.record_digest_sent("h1", s, now=1000)
    hub.record_digest_throttled("h1", s, now=1500, reason="throttled")

    audit = hub.DATA_DIR / "audit.log"
    events = [json.loads(line) for line in audit.read_text().splitlines() if line.strip()]
    sent = [e for e in events if e["event"] == "digest_sent"]
    thr = [e for e in events if e["event"] == "digest_throttled"]
    assert len(sent) == 1
    assert len(thr) == 1
    assert sent[0]["host"] == "h1"
    assert "hash" in sent[0]
    assert thr[0]["host"] == "h1"
    assert thr[0]["reason"] == "throttled"


# ── record_digest_sent round-trip ───────────────────────────────────────────


def test_record_digest_sent_persists_and_round_trips(digest_env: Path) -> None:
    """After record, should_send_digest reads it back."""
    s = [_finding("a"), _finding("b")]
    hub.record_digest_sent("h1", s, now=1000)
    # Within interval, unchanged — should be throttled.
    assert hub.should_send_digest("h1", s, now=2000)[0] is False
    # Row visible in DB.
    con = sqlite3.connect(hub.DB_PATH)
    row = con.execute(
        "SELECT host, last_sent_ts, last_hash FROM digest_state WHERE host=?", ("h1",)
    ).fetchone()
    con.close()
    assert row is not None
    assert row[0] == "h1"
    assert row[1] == 1000
    assert row[2] == hub._digest_hash(s)


def test_record_digest_sent_upserts(digest_env: Path) -> None:
    """Second record for same host updates existing row (not INSERT ignored)."""
    s1 = [_finding("a")]
    s2 = [_finding("b")]
    hub.record_digest_sent("h1", s1, now=1000)
    hub.record_digest_sent("h1", s2, now=2000)
    con = sqlite3.connect(hub.DB_PATH)
    rows = con.execute(
        "SELECT last_sent_ts, last_hash FROM digest_state WHERE host=?", ("h1",)
    ).fetchall()
    con.close()
    assert len(rows) == 1
    assert rows[0][0] == 2000
    assert rows[0][1] == hub._digest_hash(s2)
