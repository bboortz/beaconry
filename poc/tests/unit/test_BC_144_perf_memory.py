"""BC-144 — perf-gate memory-growth assertion.

Covers:
    - CAP-T240: `MemoryCheck` computes growth + monotonic-growth
      detection over RSS samples; absolute bound tunable via
      `BEACONRY_PERF_MEMORY_GROWTH_MB`; `Verdict.memory_breach` surfaces
      the fail state; `_render` adds a Memory-check block; the gate
      exits 1 on memory breach even if latency passed.
"""

from __future__ import annotations

import datetime as dt

from poc.scripts import perf_gate as pg

# ── Growth math ────────────────────────────────────────────────────────


def test_clean_hub_passes_memory_check() -> None:
    """Flat samples → zero growth → clean verdict."""
    m = pg.MemoryCheck(t0_rss_mb=180, t1_rss_mb=180, samples_mb=[180, 181, 180, 182, 180])
    assert m.growth_mb == 0
    assert m.monotonic_growth is False
    assert m.breached is False


def test_leak_fixture_fails_gate() -> None:
    """Absolute growth exceeds the bound → breach."""
    m = pg.MemoryCheck(t0_rss_mb=180, t1_rss_mb=500, samples_mb=[180, 250, 400, 500])
    assert m.growth_mb == 320
    assert m.breached is True


def test_monotonic_growth_three_samples_fails() -> None:
    """Slow leak below absolute bound but monotonic → still catches."""
    # Growth 30 MB total < 100 MB bound, but 5 strictly-increasing samples.
    m = pg.MemoryCheck(
        t0_rss_mb=100,
        t1_rss_mb=130,
        samples_mb=[100, 105, 112, 118, 125, 130],
    )
    assert m.growth_mb == 30
    assert m.monotonic_growth is True
    assert m.breached is True


def test_noisy_samples_without_leak_do_not_trigger_monotonic() -> None:
    """Spiky RSS with no trend → monotonic check stays False."""
    m = pg.MemoryCheck(t0_rss_mb=180, t1_rss_mb=178, samples_mb=[180, 195, 180, 190, 178])
    assert m.monotonic_growth is False
    assert m.breached is False


def test_missing_rss_samples_dont_fail_gate() -> None:
    """Non-Linux dev / older hub (rss_mb absent) → can't breach."""
    m = pg.MemoryCheck(t0_rss_mb=None, t1_rss_mb=None, samples_mb=[])
    assert m.breached is False


# ── Env knob tunes bound ──────────────────────────────────────────────


def test_env_knob_overrides_default(monkeypatch) -> None:
    """BEACONRY_PERF_MEMORY_GROWTH_MB raises the bound."""
    import importlib

    monkeypatch.setenv("BEACONRY_PERF_MEMORY_GROWTH_MB", "500")
    importlib.reload(pg)
    try:
        assert pg.BUDGET_MEMORY_GROWTH_MB == 500.0
        m = pg.MemoryCheck(t0_rss_mb=100, t1_rss_mb=300, samples_mb=[100, 300])
        assert m.growth_mb == 200
        # 200 < 500 → under bound. And only 2 samples so no monotonic alarm
        # (needs ≥ MONOTONIC_GROWTH_SAMPLES samples).
        assert not m.breached
    finally:
        monkeypatch.delenv("BEACONRY_PERF_MEMORY_GROWTH_MB", raising=False)
        importlib.reload(pg)


# ── Verdict integration ───────────────────────────────────────────────


def test_verdict_memory_breach_fails_gate() -> None:
    """Even if latency is clean, memory breach fails the gate."""
    good_m = pg.Measurement(key="/healthz", cold_ms=5, warm=[2] * 9)
    verdict = pg.Verdict(
        measurements=[good_m],
        exceptions={},
        today=dt.date(2026, 11, 17),
        memory=pg.MemoryCheck(t0_rss_mb=180, t1_rss_mb=500, samples_mb=[180, 500]),
    )
    assert verdict.breaches == []  # no latency breach
    assert verdict.memory_breach is True


def test_verdict_no_memory_check_passes() -> None:
    """No MemoryCheck provided → memory_breach is False."""
    good_m = pg.Measurement(key="/healthz", cold_ms=5, warm=[2] * 9)
    verdict = pg.Verdict(
        measurements=[good_m],
        exceptions={},
        today=dt.date(2026, 11, 17),
    )
    assert verdict.memory_breach is False


# ── Render shows the block ────────────────────────────────────────────


def test_render_includes_memory_check_row() -> None:
    v = pg.Verdict(
        measurements=[pg.Measurement(key="/x", cold_ms=1, warm=[2] * 9)],
        exceptions={},
        today=dt.date(2026, 11, 17),
        memory=pg.MemoryCheck(t0_rss_mb=180, t1_rss_mb=190, samples_mb=[180, 185, 190]),
    )
    out = pg._render(v)
    assert "=== Memory check ===" in out
    assert "T0:" in out
    assert "T1:" in out
    assert "Growth:" in out
