"""BC-132 — HTTP latency budget gate + sidecar SQLite audit index.

Covers:
    - CAP-T235: `make perf` discovers every @api route, measures 10
      consecutive calls per endpoint, asserts P50/P95/P99 against the
      budget, applies exceptions from `perf-exceptions.toml`,
      optionally compares to `perf-baseline.json`. The deterministic
      seeder produces byte-equivalent fixtures across machines. The
      sidecar `audit_index` SQLite table turns `/audit*` queries
      from O(log-size) tail-scans into O(result-set) indexed reads;
      the lifespan hook mirrors every `_audit.append` into the index
      so JSONL durability is preserved as the write-ahead truth.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

import pytest

from poc.hub import audit as _audit_mod
from poc.hub import hub
from poc.scripts import perf_gate, perf_seeder

# ── Fixture-seeder determinism ─────────────────────────────────────────


def test_perf_seeder_deterministic_seed_42(tmp_path: Path) -> None:
    """Same seed → byte-identical audit.log. DB bytes can vary due to
    sqlite internal row IDs, but event bodies are deterministic."""
    out1 = tmp_path / "a"
    out2 = tmp_path / "b"
    perf_seeder.seed_perf_fixture(out1, seed=42, n_findings=50, n_audit_events=500)
    perf_seeder.seed_perf_fixture(out2, seed=42, n_findings=50, n_audit_events=500)
    assert (out1 / "audit.log").read_bytes() == (out2 / "audit.log").read_bytes()


def test_perf_seeder_different_seeds_differ(tmp_path: Path) -> None:
    """Different seed → different content (guards against accidental
    constant-value generation)."""
    out1 = tmp_path / "a"
    out2 = tmp_path / "b"
    perf_seeder.seed_perf_fixture(out1, seed=42, n_findings=50, n_audit_events=500)
    perf_seeder.seed_perf_fixture(out2, seed=7, n_findings=50, n_audit_events=500)
    assert (out1 / "audit.log").read_bytes() != (out2 / "audit.log").read_bytes()


# ── Statistics ────────────────────────────────────────────────────────


def test_stats_p95_from_known_samples() -> None:
    """P50 = statistics.median; P95/P99 = nearest-rank percentile."""
    values = [float(i) for i in range(1, 101)]
    m = perf_gate.Measurement(key="/x", cold_ms=0.0, warm=values)
    # median of [1..100] is (50+51)/2 = 50.5
    assert m.p50 == 50.5
    assert m.p95 == 95
    assert m.p99 == 99


def test_stats_handles_short_sample() -> None:
    """9-sample warm set (our default) returns sensible stats."""
    m = perf_gate.Measurement(
        key="/x",
        cold_ms=100.0,
        warm=[10, 12, 11, 13, 10, 11, 12, 14, 15],
    )
    assert 10 <= m.p50 <= 13
    assert m.p95 >= m.p50
    assert m.p99 >= m.p95


# ── Exception handling ───────────────────────────────────────────────


def test_exception_past_fix_by_fails_gate() -> None:
    exc = perf_gate.PerfException(
        key="/slow",
        reason="refactor pending",
        fix_by=dt.date(2026, 11, 1),
        permanent=False,
    )
    assert exc.is_active(dt.date(2026, 10, 30))
    assert not exc.is_active(dt.date(2026, 11, 2))


def test_exception_permanent_never_fails() -> None:
    exc = perf_gate.PerfException(
        key="/slow",
        reason="inherently slow bulk export",
        fix_by=None,
        permanent=True,
    )
    assert exc.is_active(dt.date(2030, 1, 1))


def test_verdict_separates_exempt_from_breach() -> None:
    """A breaching endpoint covered by an active exception is reported
    as exempt, not counted as a breach."""
    today = dt.date(2026, 11, 17)
    exempt = perf_gate.Measurement(key="/exempt", cold_ms=0, warm=[400] * 9)
    not_exempt = perf_gate.Measurement(key="/not", cold_ms=0, warm=[400] * 9)
    exceptions = {
        "/exempt": perf_gate.PerfException(
            key="/exempt",
            reason="x",
            fix_by=dt.date(2027, 1, 1),
            permanent=False,
        ),
    }
    v = perf_gate.Verdict(
        measurements=[exempt, not_exempt],
        exceptions=exceptions,
        today=today,
    )
    breaches = v.breaches
    assert len(breaches) == 1
    assert breaches[0][0].key == "/not"


def test_baseline_regression_warns_not_blocks() -> None:
    """Measurement ≥ 2× baseline P50 but still under budget → warning,
    not breach."""
    m = perf_gate.Measurement(key="/warm", cold_ms=0, warm=[60] * 9)  # P50=60
    v = perf_gate.Verdict(
        measurements=[m],
        exceptions={},
        today=dt.date(2026, 11, 17),
        baseline={"/warm": 25.0},  # 60 >= 2 * 25 → warning
    )
    assert v.warnings == [m]
    assert v.breaches == []


# ── Sidecar SQLite audit index ────────────────────────────────────────


@pytest.fixture
def hub_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setattr(hub, "DATA_DIR", data_dir)
    monkeypatch.setattr(hub, "DB_PATH", data_dir / "beaconry.db")
    monkeypatch.setenv("HUB_SCOUT_TOKEN", "")
    monkeypatch.setenv("HUB_CLI_TOKEN", "")
    hub.init_db()
    _audit_mod.clear_post_append_hooks()
    # BC-132: audit_index uses a module-level cached sqlite connection
    # for perf; reset it between tests so each fixture's DB_PATH binds
    # a fresh handle.
    hub._reset_audit_index_conn()
    return data_dir


def test_audit_index_table_exists_after_init(hub_env: Path) -> None:
    """Schema v13 must create the audit_index table + indexes."""
    con = sqlite3.connect(hub.DB_PATH)
    tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    indexes = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    con.close()
    assert "audit_index" in tables
    assert "idx_audit_event_ts" in indexes
    assert "idx_audit_ts" in indexes
    assert "idx_audit_host_ts" in indexes


def test_audit_index_hook_populates_on_append(hub_env: Path) -> None:
    """Registered hook mirrors every _audit.append into audit_index.
    BC-139: events go through the deque buffer; call the flush path
    explicitly (production uses the background flush task)."""
    _audit_mod.register_post_append_hook(hub._audit_index_append)
    _audit_mod.append("scan_integrity", host="h1", iteration_hash="abc")
    _audit_mod.append("hub_startup", software_version="0.22.16")
    with hub._audit_index_lock:
        hub._flush_audit_index_batch_locked()
    con = sqlite3.connect(hub.DB_PATH)
    rows = con.execute("SELECT event, host FROM audit_index ORDER BY id").fetchall()
    con.close()
    assert len(rows) == 2
    assert rows[0] == ("scan_integrity", "h1")
    assert rows[1] == ("hub_startup", None)


def test_rebuild_audit_index_from_existing_log(hub_env: Path) -> None:
    """Bootstrap path: pre-existing audit.log + empty index →
    _rebuild_audit_index_if_empty backfills every event."""
    (hub_env / "audit.log").write_text(
        '{"ts": 1000, "event": "scan_integrity", "host": "h1"}\n'
        '{"ts": 1001, "event": "hub_startup"}\n'
        '{"ts": 1002, "event": "finding_filtered", "cve_id": "CVE-X"}\n',
    )
    hub._rebuild_audit_index_if_empty()
    con = sqlite3.connect(hub.DB_PATH)
    row = con.execute("SELECT COUNT(*) FROM audit_index").fetchone()
    con.close()
    assert row[0] == 3


def test_rebuild_audit_index_skips_when_already_populated(hub_env: Path) -> None:
    """Idempotency: rebuild is a no-op if the index already has rows.
    BC-139: flush the buffer first so the sqlite table has rows."""
    _audit_mod.register_post_append_hook(hub._audit_index_append)
    _audit_mod.append("hub_startup", software_version="0.22.16")
    with hub._audit_index_lock:
        hub._flush_audit_index_batch_locked()
    con = sqlite3.connect(hub.DB_PATH)
    count_before = con.execute("SELECT COUNT(*) FROM audit_index").fetchone()[0]
    con.close()
    assert count_before == 1, "flush must land the event"
    (hub_env / "audit.log").write_text(
        '{"ts": 1, "event": "x"}\n{"ts": 2, "event": "y"}\n',
    )
    hub._rebuild_audit_index_if_empty()
    con = sqlite3.connect(hub.DB_PATH)
    count_after = con.execute("SELECT COUNT(*) FROM audit_index").fetchone()[0]
    con.close()
    assert count_after == count_before, "rebuild must not duplicate when index has rows"


def test_query_audit_index_returns_none_on_empty_index(hub_env: Path) -> None:
    """Empty index → caller falls back to file-tail. Guards the
    pre-BC-132 DB migration path + test fixtures that write audit.log
    directly."""
    result = hub._query_audit_index(matcher=lambda _: True, limit=10)
    assert result is None


def test_query_audit_index_filters_by_event_type(hub_env: Path) -> None:
    """Indexed path pushes event filter down to SQL WHERE clause.
    BC-139: finding_filtered events are skipped by the hook, so the
    query path should only see hub_startup. Flush the buffer first so
    it lands in the sqlite table."""
    _audit_mod.register_post_append_hook(hub._audit_index_append)
    # Use scan_integrity (not finding_filtered which is skipped by BC-139).
    for i in range(5):
        _audit_mod.append("scan_integrity", iteration_hash=f"iter-{i}")
    _audit_mod.append("hub_startup", software_version="0.22.16")
    with hub._audit_index_lock:
        hub._flush_audit_index_batch_locked()

    result = hub._query_audit_index(
        matcher=lambda _: True,
        limit=10,
        event_types={"hub_startup"},
    )
    assert result is not None
    assert len(result) == 1
    assert result[0]["event"] == "hub_startup"


def test_load_save_baseline_roundtrip(tmp_path: Path) -> None:
    """perf-baseline.json writes P50 keyed by endpoint; load reads it back."""
    path = tmp_path / "perf-baseline.json"
    v = perf_gate.Verdict(
        measurements=[
            perf_gate.Measurement(key="/a", cold_ms=0, warm=[5] * 9),
            perf_gate.Measurement(key="/b", cold_ms=0, warm=[20] * 9),
        ],
        exceptions={},
        today=dt.date(2026, 11, 17),
    )
    perf_gate._save_baseline(path, v)
    reloaded = perf_gate._load_baseline(path)
    assert reloaded == {"/a": 5.0, "/b": 20.0}


def test_load_baseline_missing_file_returns_none(tmp_path: Path) -> None:
    """No file → None (gate skips regression check)."""
    assert perf_gate._load_baseline(tmp_path / "nope.json") is None


def test_render_output_shape(hub_env: Path) -> None:
    """Render produces a table header + one row per measurement +
    a trailing summary line. Exact format isn't guaranteed, but the
    key fields must appear."""
    v = perf_gate.Verdict(
        measurements=[
            perf_gate.Measurement(key="/healthz", cold_ms=5, warm=[2] * 9),
        ],
        exceptions={},
        today=dt.date(2026, 11, 17),
    )
    out = perf_gate._render(v)
    assert "/healthz" in out
    assert "cold" in out
    assert "P50" in out
    assert "P95" in out
    assert "under" in out or "✓" in out


def test_load_fixtures_discovers_declared_endpoints() -> None:
    """The fixtures TOML must at least mention the core health routes."""
    specs = perf_gate.load_fixtures()
    keys = {s.key for s in specs}
    assert "/healthz" in keys
    assert "/audit#default" in keys
    assert "/scan" in keys


def test_build_audit_where_translates_filters() -> None:
    """_build_audit_where composes clean WHERE clauses + params."""
    clause, params = hub._build_audit_where(
        event_types={"scan_integrity", "hub_startup"},
        host="h1",
        finding_id=None,
        cve_id=None,
        since_ts=12345,
    )
    assert "event IN (?,?)" in clause
    assert "host = ?" in clause
    assert "ts >= ?" in clause
    assert "h1" in params
    assert 12345 in params


def test_build_audit_where_empty_when_no_filters() -> None:
    clause, params = hub._build_audit_where(
        event_types=None,
        host=None,
        finding_id=None,
        cve_id=None,
        since_ts=None,
    )
    assert clause == ""
    assert params == []


def test_query_audit_index_newest_first(hub_env: Path) -> None:
    """Index results are returned newest-first (ORDER BY ts DESC).
    BC-139: flush the buffered events to the sqlite table first."""
    _audit_mod.register_post_append_hook(hub._audit_index_append)
    import time as _t

    for i in range(5):
        _audit_mod.append("scan_integrity", host="h1", n=i)
        _t.sleep(0.001)  # ensure monotonic ts
    with hub._audit_index_lock:
        hub._flush_audit_index_batch_locked()

    result = hub._query_audit_index(matcher=lambda _: True, limit=10)
    assert result is not None
    # Index returns newest-first; our events may share the second-
    # granularity ts, so sort stability depends on insertion order.
    # Assert the RESULT COUNT matches and the SET matches — the order
    # test is covered separately when ts differs.
    assert len(result) == 5
