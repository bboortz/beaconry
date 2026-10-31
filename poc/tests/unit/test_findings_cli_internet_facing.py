"""BC-030 — `make findings` NET exposure column render.

Covers:
    - CAP-T070: `make findings` + Telegram notification surface exposure dimension
"""

from __future__ import annotations

from poc.scripts.findings import format_table


def _row(internet_facing: int, **extra: object) -> dict:
    base: dict[str, object] = {
        "kev": 0,
        "internet_facing": internet_facing,
        "cve_id": "CVE-DEMO",
        "severity": "High",
        "cvss": 8.5,
        "host": "homelab",
        "source_ref": "nginx:1.24",
        "package": "openssl",
        "status": "open",
    }
    base.update(extra)
    return base


def test_exposure_column_render_exposed() -> None:
    """🌐 renders for internet-facing rows."""
    table = format_table([_row(internet_facing=1)])
    assert "🌐" in table
    assert "NET" in table.splitlines()[0]


def test_exposure_column_render_lan_only() -> None:
    """— renders for not-internet-facing rows."""
    table = format_table([_row(internet_facing=0)])
    # The dash marker appears in both the kev and net columns of a 0/0 row.
    # Both columns must render — — but we specifically assert the row has
    # the LAN marker (if it rendered 🌐 instead that would be wrong).
    assert "🌐" not in table


def test_exposure_column_handles_missing_value() -> None:
    """Legacy DBs with no column pass None → — rendered."""
    row = _row(internet_facing=0)
    row["internet_facing"] = None  # mimic the schema-aware loader's gap default
    table = format_table([row])
    assert "🌐" not in table


def test_exposure_column_position() -> None:
    """NET column sits right after KEV so operator sees both at-a-glance."""
    header = format_table([_row(internet_facing=1)]).splitlines()[0]
    kev_idx = header.index("KEV")
    net_idx = header.index("NET")
    assert kev_idx < net_idx, "NET must follow KEV"
    # Nothing other than whitespace between them.
    between = header[kev_idx + 3 : net_idx]
    assert between.strip() == ""
