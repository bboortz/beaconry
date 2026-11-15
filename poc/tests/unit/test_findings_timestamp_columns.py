"""fix/findings-http-missing-timestamp-columns — First seen / Last change /
Last seen columns render age cells for both local-DB and HTTP paths.

Covers:
    - CAP-T207: `make findings` First seen + Last change cells now also
      populated when the CLI goes through the hub's HTTP /findings
      endpoint (pre-fix: those cells rendered empty on every row because
      the endpoint omitted `found_at` + `last_status_change_ts` from its
      serialized column set, and the HTTP path never called the `_fmt`
      derivation). Extends CAP-T207 with a new Last seen column backed
      by `last_seen_ts`.
"""

from __future__ import annotations

import time

from poc.hub import hub
from poc.scripts.findings import COLUMNS, _decorate_rows, format_table


def _base_row(**extra: object) -> dict:
    row: dict[str, object] = {
        "kev": 0,
        "internet_facing": 0,
        "cve_id": "CVE-DEMO-0001",
        "severity": "High",
        "cvss": 8.5,
        "host": "homelab",
        "source_ref": "nginx:1.24",
        "package": "openssl",
        "status": "open",
    }
    row.update(extra)
    return row


# ── Column plumbing: new Last seen column present ─────────────────────────


def test_columns_include_last_seen() -> None:
    headers = {name for _, name, _ in COLUMNS}
    assert "Last seen" in headers
    # Order matters for operator scanability: Last seen sits next to Last
    # change (both talk about recency, First seen is the oldest-anchor).
    column_order = [name for _, name, _ in COLUMNS]
    assert column_order.index("Last seen") > column_order.index("Last change")


# ── Decoration helper: fills _fmt cells from raw timestamps ─────────────


def test_decorate_rows_populates_first_seen_from_found_at() -> None:
    now = int(time.time())
    row = _base_row(found_at=now - 86_400)  # 1 day ago
    _decorate_rows([row])
    assert row["found_at_fmt"] == "1d"


def test_decorate_rows_populates_last_change_from_status_change_ts() -> None:
    now = int(time.time())
    row = _base_row(last_status_change_ts=now - 3_600)  # 1h ago
    _decorate_rows([row])
    assert row["last_change_fmt"] == "1h"


def test_decorate_rows_populates_last_seen_from_last_seen_ts() -> None:
    now = int(time.time())
    row = _base_row(last_seen_ts=now - 42)
    _decorate_rows([row])
    assert row["last_seen_fmt"] == "42s"


def test_decorate_rows_handles_missing_timestamps() -> None:
    """A row with no timestamp keys (pre-schema rows, or corrupt data)
    must render em-dashes, not crash."""
    row = _base_row()  # no found_at / last_status_change_ts / last_seen_ts
    _decorate_rows([row])
    assert row["found_at_fmt"] == "—"
    assert row["last_change_fmt"] == "—"
    assert row["last_seen_fmt"] == "—"


# ── End-to-end: format_table renders the ages the user sees ─────────────


# ── Root-cause regression: hub /findings endpoint serializes the cols ──


def test_absent_count_column_present() -> None:
    """BC-122: Absent column surfaces absent_count — the operator question
    'how many runs haven't seen this finding?' answered at a glance."""
    headers = {name for _, name, _ in COLUMNS}
    assert "Absent" in headers


def test_hub_findings_endpoint_includes_absent_count() -> None:
    """BC-122: hub endpoint serializes absent_count so the CLI can
    render the Absent column over HTTP."""
    assert "absent_count" in hub._FINDINGS_ENDPOINT_COLS


def test_format_table_renders_absent_count() -> None:
    row = _base_row(absent_count=2)
    _decorate_rows([row])
    table = format_table([row])
    data_row = table.splitlines()[-1]
    assert " 2 " in data_row or data_row.endswith("2") or data_row.strip().endswith("2")


def test_hub_findings_endpoint_includes_timestamp_columns() -> None:
    """Pre-fix root cause: hub's `_FINDINGS_ENDPOINT_COLS` tuple omitted
    `found_at`, `last_status_change_ts`, `last_seen_ts` entirely — so the
    CLI received rows without those keys and rendered three empty
    columns on every row. Lock the contract: the endpoint MUST serialize
    every timestamp column the CLI formats."""
    cols = set(hub._FINDINGS_ENDPOINT_COLS)
    assert "found_at" in cols
    assert "last_status_change_ts" in cols
    assert "last_seen_ts" in cols
    # BC-105 close_reason decorates auto_fixed suffix; must also be in the
    # endpoint response.
    assert "close_reason" in cols
    # BC-117 cross_repo_hint decorates awaiting_upstream suffix.
    assert "cross_repo_hint" in cols


def test_format_table_renders_three_age_cells() -> None:
    now = int(time.time())
    row = _base_row(
        found_at=now - 86_400 * 3,  # 3d
        last_status_change_ts=now - 3_600 * 5,  # 5h
        last_seen_ts=now - 60 * 7,  # 7m
    )
    _decorate_rows([row])
    table = format_table([row])
    # Age cells appear in the single rendered row.
    assert "3d" in table
    assert "5h" in table
    assert "7m" in table
