"""BC-142 — memory metrics on /healthz + beaconry status render.

Covers:
    - CAP-T238: `_memory_metrics()` reads `/proc/self/status` for RSS
      + peak; `/sys/fs/cgroup/memory.max` (cgroup v2; v1 fallback) for
      the container memory limit; returns dict subset of
      {rss_mb, rss_peak_mb, mem_limit_mb}. `/healthz checks` block
      is extended additively. `beaconry status` DETAILS renders the
      utilization. Fail-open: any read error → empty dict.
"""

from __future__ import annotations

from unittest.mock import mock_open, patch

from poc.hub import hub
from poc.scripts import status as status_mod

# ── /proc/self/status parse ────────────────────────────────────────────


def test_healthz_includes_rss_mb() -> None:
    """VmRSS is parsed from /proc/self/status (real read in CI/dev)."""
    m = hub._memory_metrics()
    # On Linux we expect VmRSS to be present. On macOS /proc is absent;
    # the dict is empty. Either shape is valid; the fail-open contract.
    if "rss_mb" in m:
        assert isinstance(m["rss_mb"], int)
        assert m["rss_mb"] > 0


def test_healthz_includes_rss_peak_mb() -> None:
    m = hub._memory_metrics()
    if "rss_peak_mb" in m:
        assert isinstance(m["rss_peak_mb"], int)
        assert m["rss_peak_mb"] >= m.get("rss_mb", 0)


# ── cgroup limit parse ────────────────────────────────────────────────


def test_cgroup_v2_max_bytes_parses() -> None:
    """Synthetic cgroup v2 `memory.max` with a concrete byte value."""
    status_text = "VmRSS:  184320 kB\nVmHWM:  204800 kB\n"
    cgroup_text = "1073741824\n"  # 1 GB exactly

    def _fake_open(path, *args, **kwargs):
        if path == "/proc/self/status":
            return mock_open(read_data=status_text)(*args, **kwargs)
        if path == "/sys/fs/cgroup/memory.max":
            return mock_open(read_data=cgroup_text)(*args, **kwargs)
        raise FileNotFoundError(path)

    with patch("builtins.open", side_effect=_fake_open):
        m = hub._memory_metrics()
    assert m["rss_mb"] == 180
    assert m["rss_peak_mb"] == 200
    assert m["mem_limit_mb"] == 1024


def test_cgroup_max_sentinel_returns_null() -> None:
    """cgroup v2 `memory.max` == 'max' (uncapped) → mem_limit_mb is None."""
    status_text = "VmRSS: 100000 kB\n"
    cgroup_text = "max\n"

    def _fake_open(path, *args, **kwargs):
        if path == "/proc/self/status":
            return mock_open(read_data=status_text)(*args, **kwargs)
        if path == "/sys/fs/cgroup/memory.max":
            return mock_open(read_data=cgroup_text)(*args, **kwargs)
        raise FileNotFoundError(path)

    with patch("builtins.open", side_effect=_fake_open):
        m = hub._memory_metrics()
    assert m.get("mem_limit_mb") is None


def test_cgroup_absent_returns_null_mem_limit() -> None:
    """No cgroup files at all (bare-metal / tests) → mem_limit_mb absent."""
    status_text = "VmRSS:  50000 kB\n"

    def _fake_open(path, *args, **kwargs):
        if path == "/proc/self/status":
            return mock_open(read_data=status_text)(*args, **kwargs)
        raise FileNotFoundError(path)

    with patch("builtins.open", side_effect=_fake_open):
        m = hub._memory_metrics()
    # rss present, mem_limit_mb absent
    assert "rss_mb" in m
    assert "mem_limit_mb" not in m


def test_proc_absent_returns_empty() -> None:
    """Non-Linux (macOS dev): /proc is absent → empty dict, no error."""

    def _fake_open(path, *args, **kwargs):
        raise FileNotFoundError(path)

    with patch("builtins.open", side_effect=_fake_open):
        m = hub._memory_metrics()
    assert m == {}


# ── Render in beaconry status ─────────────────────────────────────────


def test_status_cli_renders_memory_details() -> None:
    body = {
        "status": "ok",
        "checks": {
            "grype_db": "ok",
            "telegram": "polling",
            "pipeline_integrity_mismatches": 0,
            "rss_mb": 180,
            "mem_limit_mb": 1024,
        },
    }
    details = status_mod._hub_details(body)
    assert "rss 180MB / 1024MB cap" in details


def test_status_cli_renders_uncapped_memory() -> None:
    body = {
        "status": "ok",
        "checks": {
            "grype_db": "ok",
            "telegram": "polling",
            "pipeline_integrity_mismatches": 0,
            "rss_mb": 180,
            "mem_limit_mb": None,
        },
    }
    details = status_mod._hub_details(body)
    assert "rss 180MB / no cap" in details


def test_status_cli_omits_memory_when_absent() -> None:
    """Non-Linux hub (/proc absent) → no memory line in DETAILS."""
    body = {
        "status": "ok",
        "checks": {
            "grype_db": "ok",
            "telegram": "polling",
            "pipeline_integrity_mismatches": 0,
        },
    }
    details = status_mod._hub_details(body)
    assert "rss" not in details


# ── Perf-friendliness ─────────────────────────────────────────────────


def test_metrics_read_is_cheap() -> None:
    """One _memory_metrics() call is <5 ms even when /proc is real."""
    import time

    t0 = time.perf_counter()
    for _ in range(100):
        hub._memory_metrics()
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.5, f"100 reads took {elapsed * 1000:.0f}ms (budget 500ms = 5ms/call)"
