"""BC-042 unit tests — parsers, filters, formatters.

Covers:
    - CAP-T089: `make audit` CLI with filters + compose-aware routing
"""

from __future__ import annotations

import time

import pytest

from poc.scripts import audit as a

# ── Time parsing ────────────────────────────────────────────────────────────


def test_parse_duration_accepts_common_shapes() -> None:
    assert a.parse_duration("1h") == 3600
    assert a.parse_duration("30m") == 1800
    assert a.parse_duration("45s") == 45
    assert a.parse_duration("2d") == 2 * 86400
    assert a.parse_duration("1h30m") == 3600 + 1800
    assert a.parse_duration("1d2h3m4s") == 86400 + 7200 + 180 + 4


def test_parse_duration_case_insensitive() -> None:
    assert a.parse_duration("1H") == 3600
    assert a.parse_duration("30M") == 1800


def test_parse_duration_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        a.parse_duration("")
    with pytest.raises(ValueError):
        a.parse_duration("yesterday")
    with pytest.raises(ValueError):
        a.parse_duration("1x")


def test_parse_time_spec_relative_and_iso() -> None:
    # Relative — subtracted from `now`.
    assert a.parse_time_spec("1h", now=1_700_000_000) == 1_700_000_000 - 3600
    # ISO without seconds.
    ts = a.parse_time_spec("2026-11-01T14:00", now=1_700_000_000)
    # Just assert it parsed to SOMETHING sensible, not a specific tz value.
    assert isinstance(ts, int) and ts > 0
    # ISO with seconds.
    ts2 = a.parse_time_spec("2026-11-01T14:00:30", now=1_700_000_000)
    assert ts2 == ts + 30


def test_format_age_covers_all_bands() -> None:
    assert a.format_age(0) == "just now"
    assert a.format_age(1) == "just now"
    assert a.format_age(5) == "5s ago"
    assert a.format_age(59) == "59s ago"
    assert a.format_age(60) == "1m ago"
    assert a.format_age(3599) == "59m ago"
    assert a.format_age(3600) == "1h ago"
    assert a.format_age(86399) == "23h ago"
    assert a.format_age(86400) == "1d ago"


# ── Filters ────────────────────────────────────────────────────────────────


def _ev(**kwargs: object) -> dict:
    return dict({"ts": 100, "event": "x"}, **kwargs)


def test_filter_since_until_window() -> None:
    events = [_ev(ts=100), _ev(ts=200), _ev(ts=300)]
    assert a.apply_filters(events, since_ts=150) == [_ev(ts=200), _ev(ts=300)]
    assert a.apply_filters(events, until_ts=200) == [_ev(ts=100), _ev(ts=200)]
    assert a.apply_filters(events, since_ts=150, until_ts=250) == [_ev(ts=200)]


def test_filter_event_types() -> None:
    events = [_ev(event="a"), _ev(event="b"), _ev(event="c")]
    assert a.apply_filters(events, event_types={"a", "c"}) == [_ev(event="a"), _ev(event="c")]


def test_filter_by_host() -> None:
    events = [_ev(host="h1"), _ev(host="h2"), _ev(host="h1")]
    assert len(a.apply_filters(events, host="h1")) == 2


def test_filter_by_cve_id() -> None:
    events = [_ev(cve_id="CVE-X"), _ev(cve_id="CVE-Y"), _ev()]
    assert a.apply_filters(events, cve_id="CVE-X") == [_ev(cve_id="CVE-X")]


def test_parse_duration_rejects_pure_whitespace() -> None:
    """BC-042 follow-up: ensure the parser doesn't silently accept blanks
    (which would hide operator typos like `SINCE=   `)."""
    with pytest.raises(ValueError):
        a.parse_duration("   ")


def test_filter_by_cve_id_matches_cve_alias() -> None:
    """BC-043: CVE filter also matches `cve_alias` so `make audit CVE=X`
    works on non-CVE primary findings whose alias is X."""
    events = [
        _ev(cve_id="GO-2026-6604", cve_alias="CVE-2024-12345"),
        _ev(cve_id="GO-2026-7777"),
        _ev(cve_id="CVE-2024-12345"),
    ]
    result = a.apply_filters(events, cve_id="CVE-2024-12345")
    assert len(result) == 2


def test_filter_by_finding_id() -> None:
    events = [_ev(finding_id="abc"), _ev(finding_id="def"), _ev()]
    assert a.apply_filters(events, finding_id="abc") == [_ev(finding_id="abc")]


def test_filters_and_together() -> None:
    events = [
        _ev(ts=100, event="a", host="h1"),
        _ev(ts=200, event="a", host="h2"),
        _ev(ts=300, event="b", host="h1"),
    ]
    result = a.apply_filters(events, event_types={"a"}, host="h1", since_ts=50)
    assert result == [_ev(ts=100, event="a", host="h1")]


# ── Enrichment ─────────────────────────────────────────────────────────────


def test_enrich_event_resolves_finding_id() -> None:
    e = _ev(event="status_change", finding_id="abc")
    lookup: dict[str, tuple[str, str, str | None]] = {"abc": ("CVE-FOO", "h1", None)}
    a.enrich_event(e, lookup)
    assert e["cve_id"] == "CVE-FOO"
    assert e["host"] == "h1"


def test_enrich_event_leaves_other_rows_alone() -> None:
    e = _ev(event="status_change", finding_id="unknown")
    a.enrich_event(e, {})
    assert "cve_id" not in e


def test_enrich_event_does_not_overwrite() -> None:
    """Events that carry their own cve_id (e.g. heartbeat_reminder_sent) must
    not be overwritten by the DB lookup."""
    e = _ev(event="heartbeat_reminder_sent", finding_id="abc", cve_id="CVE-ORIGINAL")
    lookup: dict[str, tuple[str, str, str | None]] = {"abc": ("CVE-DB-DIFFERENT", "h1", None)}
    a.enrich_event(e, lookup)
    assert e["cve_id"] == "CVE-ORIGINAL"


def test_enrich_event_populates_cve_alias() -> None:
    """BC-043: when the DB row has `cve_alias`, enrichment copies it onto
    the event so the CVE filter can match via alias."""
    e = _ev(event="status_change", finding_id="abc")
    lookup: dict[str, tuple[str, str, str | None]] = {"abc": ("GO-2026-X", "h1", "CVE-2024-REAL")}
    a.enrich_event(e, lookup)
    assert e["cve_id"] == "GO-2026-X"
    assert e["cve_alias"] == "CVE-2024-REAL"


# ── Formatting ──────────────────────────────────────────────────────────────


def test_format_row_width() -> None:
    e = _ev(
        ts=int(time.time()),
        event="finding_ingested",
        cve_id="CVE-X",
        host="homelab",
        tier=1,
        cvss=9.5,
    )
    row = a.format_row(e, now=int(time.time()))
    # Columns are tab-free, two-space separated — stable grep.
    assert "finding_ingested" in row
    assert "CVE-X" in row
    assert "homelab" in row
    assert "tier=1" in row


def test_format_extra_truncates_long_values() -> None:
    e = _ev(**{f"k{i}": f"v{i}" for i in range(50)})
    extra = a.format_extra(e)
    assert len(extra) <= a.COL_EXTRA
    assert extra.endswith("…") or len(extra) == a.COL_EXTRA


def test_format_count_summary_groups_and_sorts() -> None:
    events = [
        _ev(ts=100, event="a"),
        _ev(ts=200, event="b"),
        _ev(ts=300, event="a"),
    ]
    out = a.format_count_summary(events, now=400)
    assert "EVENT" in out  # header present
    lines = out.splitlines()
    # Sorted alphabetically → `a` before `b`.
    assert lines[2].startswith("a")
    assert lines[3].startswith("b")
    assert "2" in lines[2]  # count for `a`
    assert "1" in lines[3]  # count for `b`
