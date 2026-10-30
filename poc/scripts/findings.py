"""BC-007: read-only CLI view of findings in the dev SQLite DB.

Usage:
    python -m poc.scripts.findings                      # all findings
    python -m poc.scripts.findings --status accepted    # filter by status

Reads $DATA_DIR/beaconry.db (default `.beaconry-cache/beaconry.db`); never writes.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

# BC-011: added fixed_verified + verification_failed. Order defines sort rank —
# unresolved first, then intermediate, then terminal states grouped together.
STATUS_ORDER = (
    "open",
    "verification_failed",
    "snoozed",
    "accepted",
    "fixed",
    "fixed_verified",
)
STATUS_RANK = {s: i for i, s in enumerate(STATUS_ORDER)}

# (dict-key, header, max_width). Order defines column order.
# `tier` is queried for sort but not displayed — severity carries the same signal
# for operators and the row budget is already at 110 chars.
COLUMNS: list[tuple[str, str, int]] = [
    ("cve_id", "CVE", 20),
    ("severity", "Severity", 10),
    ("cvss", "CVSS", 5),
    ("host", "Host", 14),
    ("source_ref", "Target", 22),
    ("package", "Package", 18),
    ("status", "Status", 19),  # BC-011: fits "verification_failed"
]
SORT_ONLY_COLUMNS = ("tier",)


def _truncate(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 1:
        return value[:width]
    return value[: width - 1] + "…"


def _format_cell(raw: object, width: int) -> str:
    if isinstance(raw, float):
        text = f"{raw:.1f}"
    elif raw is None:
        text = ""
    else:
        text = str(raw)
    return _truncate(text, width).ljust(width)


def format_table(rows: list[dict]) -> str:
    """Render findings as an aligned table (header + body)."""
    header = "  ".join(name.ljust(w) for _, name, w in COLUMNS)
    separator = "  ".join("-" * w for _, _, w in COLUMNS)
    body_lines = []
    for row in rows:
        cells = [_format_cell(row.get(key), w) for key, _, w in COLUMNS]
        body_lines.append("  ".join(cells))
    return "\n".join([header, separator, *body_lines])


def load_findings(db_path: Path, status: str | None = None) -> list[dict]:
    if not db_path.exists():
        return []
    display_columns = [key for key, _, _ in COLUMNS]
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        # Degrade gracefully for DBs that predate the tier column — findings.py
        # is pure-read, so it can't migrate; it just drops the tier from the
        # sort key when the column is absent.
        schema_cols = {row[1] for row in con.execute("PRAGMA table_info(findings)")}
        extra_cols = [c for c in SORT_ONLY_COLUMNS if c in schema_cols]
        all_columns = [*display_columns, *extra_cols]
        sql = f"SELECT {', '.join(all_columns)} FROM findings"
        params: tuple[str, ...] = ()
        if status:
            sql += " WHERE status = ?"
            params = (status,)
        rows = [dict(zip(all_columns, r, strict=True)) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()
    rows.sort(
        key=lambda r: (
            STATUS_RANK.get(r.get("status", "open"), 99),
            r.get("tier", 99),
            -(r.get("cvss") or 0.0),
        )
    )
    return rows


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="poc.scripts.findings",
        description="Read-only CLI view of the beaconry findings DB.",
    )
    p.add_argument("--status", choices=STATUS_ORDER, default=None)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    data_dir = Path(os.environ.get("DATA_DIR", ".beaconry-cache"))
    db_path = data_dir / "beaconry.db"
    rows = load_findings(db_path, status=args.status)
    if not rows:
        if args.status:
            print(f"No findings with status={args.status!r} in {db_path}.")
        else:
            print(f"No findings in {db_path} yet.")
        print("Run `make demo` first to populate the dev DB.")
        return 0
    print(format_table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
