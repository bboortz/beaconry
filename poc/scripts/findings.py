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
    "regressed",  # BC-031: highest urgency — a previously-fixed CVE is back
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
# for operators and the row budget is already at 110 chars. BC-020 added `kev`
# as a visible 3-char column (✓/—) because the Tier 0 differentiation is the
# whole product value; operators scanning the table must see it at a glance.
COLUMNS: list[tuple[str, str, int]] = [
    ("kev", "KEV", 3),
    # BC-030: 🌐 = internet-facing per the port-publication detector; "—" = LAN-only
    # or unknown. Column is 3 chars wide to match the ✓/— KEV precedent.
    ("internet_facing", "NET", 3),
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


def _format_cell(
    raw: object, width: int, *, is_kev: bool = False, is_exposure: bool = False
) -> str:
    if is_kev:
        # kev column: SQLite stores 0/1 ints; render as ✓ / — for scanability.
        text = "✓" if raw else "—"
    elif is_exposure:
        # BC-030: internet_facing column — 🌐 for exposed, — for LAN-only/unknown.
        text = "🌐" if raw else "—"
    elif isinstance(raw, float):
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
        cells = [
            _format_cell(
                row.get(key),
                w,
                is_kev=(key == "kev"),
                is_exposure=(key == "internet_facing"),
            )
            for key, _, w in COLUMNS
        ]
        body_lines.append("  ".join(cells))
    return "\n".join([header, separator, *body_lines])


def load_findings(db_path: Path, status: str | None = None) -> list[dict]:
    if not db_path.exists():
        return []
    display_columns = [key for key, _, _ in COLUMNS]
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        # Degrade gracefully for pre-BC-011/BC-020 DBs — findings.py is pure-read,
        # so it drops absent columns from the SELECT and defaults them to None.
        schema_cols = {row[1] for row in con.execute("PRAGMA table_info(findings)")}
        present_display = [c for c in display_columns if c in schema_cols]
        missing_display = [c for c in display_columns if c not in schema_cols]
        extra_cols = [c for c in SORT_ONLY_COLUMNS if c in schema_cols]
        selected = [*present_display, *extra_cols]
        sql = f"SELECT {', '.join(selected)} FROM findings"
        params: tuple[str, ...] = ()
        if status:
            sql += " WHERE status = ?"
            params = (status,)
        rows = []
        for r in con.execute(sql, params).fetchall():
            row = dict(zip(selected, r, strict=True))
            for mc in missing_display:
                row[mc] = None
            rows.append(row)
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


HUB_CONTAINER = os.environ.get("BEACONRY_HUB_CONTAINER", "beaconry-hub")


def _running_compose_hub() -> bool:
    """Return True if a container named `beaconry-hub` is up — used to route the query
    through `docker exec` so operators see the live Compose-volume DB, not the stale
    local-dev `.beaconry-cache/` file. Mirrors BC-013's `make status` compose awareness.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["docker", "inspect", "--format={{.State.Running}}", HUB_CONTAINER],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == "true"


def _run_in_container(args: list[str]) -> int:
    """Exec findings.py inside the running hub container; stream output to our stdout."""
    import subprocess

    cmd = ["docker", "exec", HUB_CONTAINER, "python3", "-m", "scripts.findings", *args]
    # In the container, poc/ is the WORKDIR under /app, and scripts.findings is `scripts`
    # relative to that. But hub.py is COPY'd flat to /app, not under `poc/`. Simpler:
    # just invoke findings.py as a path.
    cmd = [
        "docker",
        "exec",
        "-e",
        "DATA_DIR=/data",
        HUB_CONTAINER,
        "python3",
        "-c",
        _INLINE_FINDINGS_SCRIPT.replace("##STATUS_FILTER##", repr(args[-1] if args else None)),
    ]
    return subprocess.run(cmd, check=False).returncode


# Minimal inline script executed inside the container so we don't depend on
# poc/scripts/findings.py being COPY'd into the image. Mirrors the top-level
# load/sort/format logic above.
_INLINE_FINDINGS_SCRIPT = """
import os, sqlite3, sys
STATUS_ORDER = ('open', 'verification_failed', 'snoozed', 'accepted', 'fixed', 'fixed_verified')
STATUS_RANK = {s: i for i, s in enumerate(STATUS_ORDER)}
db = '/data/beaconry.db'
if not os.path.exists(db):
    print(f'No findings in {db} yet.'); sys.exit(0)
status = ##STATUS_FILTER##
con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
cols = {r[1] for r in con.execute('PRAGMA table_info(findings)')}
tier_sort = ', tier' if 'tier' in cols else ''
kev_col = ', kev' if 'kev' in cols else ''
if_col = ', internet_facing' if 'internet_facing' in cols else ''
sql = f'SELECT cve_id,severity,cvss,host,source_ref,package,status{tier_sort}{kev_col}{if_col} FROM findings'
if status:
    sql += ' WHERE status=?'
    rows = con.execute(sql, (status,)).fetchall()
else:
    rows = con.execute(sql).fetchall()
if not rows:
    msg = f'No findings' + (f\" with status='{status}'\" if status else '') + f' in {db}.'
    print(msg); sys.exit(0)
# positional indexes: [cve,sev,cvss,host,target,pkg,status,(tier?),(kev?),(if?)]
tier_idx = 7 if tier_sort else None
kev_idx = (7 + bool(tier_sort)) if kev_col else None
if_idx = (7 + bool(tier_sort) + bool(kev_col)) if if_col else None
rows = sorted(rows, key=lambda r: (STATUS_RANK.get(r[6], 99), r[tier_idx] if tier_idx and len(r) > tier_idx else 99, -(r[2] or 0.0)))
widths = [3, 3, 20, 10, 5, 14, 22, 18, 19]
headers = ['KEV', 'NET', 'CVE', 'Severity', 'CVSS', 'Host', 'Target', 'Package', 'Status']
def fmt(v, w, kev=False, exposed=False):
    if kev:        s = '✓' if v else '—'
    elif exposed:  s = '🌐' if v else '—'
    else:          s = f'{v:.1f}' if isinstance(v, float) else ('' if v is None else str(v))
    return (s[:w-1] + '…' if len(s) > w else s).ljust(w)
print('  '.join(h.ljust(w) for h,w in zip(headers, widths)))
print('  '.join('-'*w for w in widths))
for r in rows:
    kev_val = r[kev_idx] if kev_idx is not None and len(r) > kev_idx else None
    if_val  = r[if_idx]  if if_idx  is not None and len(r) > if_idx  else None
    display = [kev_val, if_val, r[0], r[1], r[2], r[3], r[4], r[5], r[6]]
    print('  '.join(fmt(display[i], widths[i], kev=(i==0), exposed=(i==1)) for i in range(9)))
"""


def _findings_via_http(cfg, status: str | None) -> int:  # type: ignore[no-untyped-def]
    """BC-055 HTTP path for remote hubs. Returns the CLI exit code."""
    import urllib.parse

    from poc.scripts.cli_http import friendly_http_error, http_get

    url = cfg.hub_url.rstrip("/") + "/findings"
    if status:
        url += "?" + urllib.parse.urlencode({"status": status})
    try:
        rows = http_get(url, token=cfg.hub_token)
    except Exception as exc:  # noqa: BLE001 — single friendly line.
        print("✗ " + friendly_http_error(url, exc), file=sys.stderr)
        return 2
    if not isinstance(rows, list) or not rows:
        print(f"No findings on remote hub {cfg.hub_url}.")
        return 0
    print(format_table(rows))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    # BC-055: triage source — remote hub → HTTP; local compose → docker-exec;
    # else local DB file.
    from poc.scripts.cli_config import load_cli_config
    from poc.scripts.cli_http import choose_source

    cfg = load_cli_config()
    source = choose_source(
        cfg,
        compose_hub_running=_running_compose_hub(),
        force_local=bool(os.environ.get("BEACONRY_FINDINGS_LOCAL")),
    )

    if source == "http":
        return _findings_via_http(cfg, args.status)

    if source == "local-compose":
        return _run_in_container([args.status] if args.status else [])

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
