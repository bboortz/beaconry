"""Unit tests for the KEV ingestion module.

Covers:
    - CAP-T058: CISA KEV feed ingestion — fetch, parse, cache, lookup, freshness
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from poc.hub import kev as _kev


@pytest.fixture(autouse=True)
def _isolated_kev_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Reset the module-level state + redirect cache to tmp_path before every test."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    _kev._reset_state_for_tests()
    return data_dir


# ── parse_feed ───────────────────────────────────────────────────────────────


def test_parse_cisa_feed_shape() -> None:
    raw = json.dumps(
        {
            "title": "x",
            "catalogVersion": "y",
            "count": 2,
            "vulnerabilities": [
                {"cveID": "CVE-2024-1111", "vendorProject": "a", "product": "b"},
                {"cveID": "CVE-2024-2222", "vendorProject": "a", "product": "b"},
            ],
        }
    ).encode()
    ids = _kev.parse_feed(raw)
    assert ids == frozenset({"CVE-2024-1111", "CVE-2024-2222"})


def test_parse_feed_ignores_non_cve_ids_and_malformed_rows() -> None:
    raw = json.dumps(
        {
            "vulnerabilities": [
                {"cveID": "CVE-2024-1111"},
                {"cveID": "not-a-cve"},  # dropped
                {"cveID": 42},  # dropped — not str
                "scalar-row",  # dropped — not dict
                {"vendorProject": "no cveID key"},  # dropped
            ]
        }
    ).encode()
    assert _kev.parse_feed(raw) == frozenset({"CVE-2024-1111"})


def test_parse_feed_raises_on_wrong_top_level_shape() -> None:
    with pytest.raises(ValueError, match="vulnerabilities"):
        _kev.parse_feed(json.dumps({"vulnerabilities": "not a list"}).encode())


# ── Cache I/O ────────────────────────────────────────────────────────────────


async def test_cache_round_trip(_isolated_kev_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """fetch → write cache → fresh read reconstructs the same set."""
    feed_bytes = json.dumps({"vulnerabilities": [{"cveID": "CVE-2024-9999"}]}).encode()

    def _ok(url: str) -> bytes:  # noqa: ARG001
        return feed_bytes

    monkeypatch.setattr(_kev, "_http_fetch", _ok)

    await _kev.fetch_or_fallback()
    _kev._reset_state_for_tests()  # drop in-memory; prove disk cache rehydrates

    _kev.warm_from_cache_if_any()
    assert _kev.is_known_exploited("CVE-2024-9999") is True


# ── Lookup ───────────────────────────────────────────────────────────────────


def test_lookup_is_fast_and_case_insensitive(
    _isolated_kev_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lookup must be case-insensitive — some sources emit lowercase CVE ids."""
    cache = _isolated_kev_state / "kev_cache.json"
    cache.write_text(json.dumps({"fetched_at": int(time.time()), "cve_ids": ["CVE-2024-1111"]}))
    _kev.warm_from_cache_if_any()

    assert _kev.is_known_exploited("CVE-2024-1111") is True
    assert _kev.is_known_exploited("cve-2024-1111") is True
    assert _kev.is_known_exploited("Cve-2024-1111") is True
    assert _kev.is_known_exploited("") is False
    assert _kev.is_known_exploited("CVE-2024-0000") is False


# ── Freshness ────────────────────────────────────────────────────────────────


def test_freshness_states(_isolated_kev_state: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 1. Unfetched: no cache, no in-memory → "unfetched"
    assert _kev.freshness()["kev"] == "unfetched"

    # 2. Fresh: write a cache with recent timestamp, warm from cache → "ok"
    cache = _isolated_kev_state / "kev_cache.json"
    cache.write_text(json.dumps({"fetched_at": int(time.time()) - 60, "cve_ids": ["CVE-X"]}))
    _kev.warm_from_cache_if_any()
    summary = _kev.freshness()
    assert summary["kev"] == "ok"
    assert summary["kev_count"] == 1
    age = summary["kev_last_fetch_s"]
    assert isinstance(age, int) and age < 300

    # 3. Stale: reset + rewrite cache with old timestamp
    _kev._reset_state_for_tests()
    cache.write_text(
        json.dumps({"fetched_at": int(time.time()) - (26 * 3600), "cve_ids": ["CVE-X"]})
    )
    _kev.warm_from_cache_if_any()
    assert _kev.freshness()["kev"] == "stale"


# ── Robustness ───────────────────────────────────────────────────────────────


def test_unreadable_cache_is_treated_as_unfetched(_isolated_kev_state: Path) -> None:
    cache = _isolated_kev_state / "kev_cache.json"
    cache.write_text("not valid json {{{")
    _kev.warm_from_cache_if_any()
    assert _kev.freshness()["kev"] == "unfetched"


async def test_fetch_failure_without_cache_is_noop_safe(
    _isolated_kev_state: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """fetch_or_fallback must not raise even when both HTTP and cache fail."""
    import urllib.error

    def _fail(url: str) -> bytes:  # noqa: ARG001
        raise urllib.error.URLError("down")

    monkeypatch.setattr(_kev, "_http_fetch", _fail)
    await _kev.fetch_or_fallback()  # must not raise
    assert _kev.freshness()["kev"] == "unfetched"
