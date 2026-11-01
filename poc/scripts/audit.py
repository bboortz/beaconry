"""BC-042: operator CLI for the audit log.

Reads `${DATA_DIR}/audit.log` (JSONL, append-only, written by BC-029's
`poc/hub/audit.py`) and renders a filterable aligned timeline.

Compose-aware: when the live `beaconry-hub` container is up, the CLI
docker-exec's into it so the operator sees the real-volume log, not a
stale local-dev file. Same routing shape as `findings.py` / `status.py`.

Filters (all AND together):
    LAST=N        — show last N events (default 50; ignored by COUNT/FOLLOW)
    SINCE=1h30m   — relative time window (s/m/h/d) OR ISO `YYYY-MM-DDTHH:MM`
    UNTIL=…       — upper bound (same shapes as SINCE)
    EVENT=a,b     — comma-separated event-name OR filter
    CVE=CVE-X     — match the event's cve_id directly OR resolve finding_id via DB
    HOST=X        — match host
    FINDING=X     — match finding_id
    COUNT=1       — summary mode: one line per event type with count + span
    FOLLOW=1      — tail -f mode (ignores LAST/COUNT)

Env overrides:
    DATA_DIR                — audit.log location (default /data)
    BEACONRY_HUB_CONTAINER  — container name for compose-aware routing (default beaconry-hub)
    BEACONRY_FINDINGS_LOCAL — any non-empty value forces local DATA_DIR path even if hub is up
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

# Known event types the hub emits. Documented so `make audit --help` can list
# them with a one-line meaning. New events should land here when a new BC adds
# `_audit.append("<name>", ...)`.
EVENT_HELP: dict[str, str] = {
    "finding_ingested": "a new finding (unique by id) landed in the DB",
    "status_change": "status transition (snoozed/accepted/fixed/fixed_verified/regressed)",
    "auto_closed": "BC-031 reconcile closed a finding no longer in inventory",
    "regressed": "🔥 BC-031 — a previously-fixed CVE reappeared",
    "heartbeat_reminder_sent": "BC-035 periodic reminder for a stale open finding",
    "digest_sent": "📋 BC-022/036 — digest message dispatched",
    "digest_throttled": "BC-036 — digest skipped (interval not elapsed + content unchanged)",
    "scan_received": "BC-042 — scout POSTed a /scan request (even if 0 findings)",
}

# Column widths — stable so output stays grep-friendly across runs.
COL_TIME = 8
COL_AGE = 11
COL_EVENT = 24  # longest: `heartbeat_reminder_sent` = 23
COL_CVE = 20
COL_HOST = 12
COL_EXTRA = 50  # right-truncated with …

HUB_CONTAINER = os.environ.get("BEACONRY_HUB_CONTAINER", "beaconry-hub")

# ── Time parsing ────────────────────────────────────────────────────────────

_DURATION_RE = re.compile(r"(\d+)\s*([smhd])", re.IGNORECASE)
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$")
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(spec: str) -> int:
    """Parse `1h30m` / `45s` / `2d` → total seconds. Raises ValueError on garbage."""
    spec = spec.strip().lower()
    if not spec:
        raise ValueError("empty duration")
    matches = _DURATION_RE.findall(spec)
    if not matches:
        raise ValueError(f"not a duration: {spec!r} (expected e.g. 1h30m, 45s, 2d)")
    # Reject non-digit/non-unit leftovers.
    reconstructed = "".join(num + unit for num, unit in matches)
    if reconstructed.replace(" ", "") != spec.replace(" ", ""):
        raise ValueError(f"not a duration: {spec!r}")
    return sum(int(n) * _UNIT_SECONDS[u] for n, u in matches)


def parse_time_spec(spec: str, *, now: int | None = None) -> int:
    """Parse SINCE / UNTIL value into a unix timestamp.

    Accepts a duration (`1h`) relative to `now` OR an ISO datetime
    (`2026-11-01T14:00` or `2026-11-01T14:00:00`).
    """
    now = now if now is not None else int(time.time())
    spec = spec.strip()
    if _ISO_RE.match(spec):
        from datetime import datetime

        fmt = "%Y-%m-%dT%H:%M:%S" if len(spec) == 19 else "%Y-%m-%dT%H:%M"
        return int(datetime.strptime(spec, fmt).timestamp())
    # Duration → subtract from now.
    return now - parse_duration(spec)


def format_age(seconds: int) -> str:
    """`123 → '2m ago'`, `3600 → '1h ago'`, `0 → 'just now'`."""
    if seconds < 2:
        return "just now"
    if seconds < 60:
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    if seconds < 86400:
        return f"{seconds // 3600}h ago"
    return f"{seconds // 86400}d ago"


# ── Audit log reading ──────────────────────────────────────────────────────


def read_audit_log(path: Path) -> list[dict]:
    """Return events (newest-first)."""
    if not path.exists():
        return []
    events: list[dict] = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    events.reverse()
    return events


# ── DB cross-reference (finding_id → cve_id, host) ──────────────────────────


def load_finding_lookup(db_path: Path) -> dict[str, tuple[str, str, str | None]]:
    """Return `{finding_id: (cve_id, host, cve_alias)}`. Empty dict if DB missing.

    BC-043: cve_alias included so audit events on non-CVE primaries can be
    enriched with their CVE paper trail — operator can filter `make audit
    CVE=CVE-2024-X` and match findings whose primary is `GO-2026-Y` as well.
    """
    if not db_path.exists():
        return {}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            # Degrade gracefully for pre-BC-043 DBs — the column may be missing.
            cols = {row[1] for row in con.execute("PRAGMA table_info(findings)")}
            has_alias = "cve_alias" in cols
            select_cols = "id, cve_id, host, cve_alias" if has_alias else "id, cve_id, host"
            rows = con.execute(f"SELECT {select_cols} FROM findings").fetchall()
        finally:
            con.close()
    except sqlite3.OperationalError:
        return {}
    out: dict[str, tuple[str, str, str | None]] = {}
    for row in rows:
        if has_alias:
            out[row[0]] = (row[1], row[2], row[3])
        else:
            out[row[0]] = (row[1], row[2], None)
    return out


def enrich_event(event: dict, lookup: dict[str, tuple[str, str, str | None]]) -> dict:
    """Mutate `event` in-place: add `cve_id`, `host`, `cve_alias` if resolvable
    from `finding_id`. BC-043: cve_alias enrichment lets the CVE filter match
    non-CVE primaries via their alias."""
    finding_id = event.get("finding_id")
    if finding_id and finding_id in lookup:
        cve, host, alias = lookup[finding_id]
        event.setdefault("cve_id", cve)
        event.setdefault("host", host)
        if alias is not None:
            event.setdefault("cve_alias", alias)
    return event


# ── Filters ─────────────────────────────────────────────────────────────────


def apply_filters(
    events: list[dict],
    *,
    since_ts: int | None = None,
    until_ts: int | None = None,
    event_types: set[str] | None = None,
    cve_id: str | None = None,
    host: str | None = None,
    finding_id: str | None = None,
) -> list[dict]:
    out: list[dict] = []
    for e in events:
        ts = e.get("ts", 0)
        if since_ts is not None and ts < since_ts:
            continue
        if until_ts is not None and ts > until_ts:
            continue
        if event_types and e.get("event") not in event_types:
            continue
        if cve_id and e.get("cve_id") != cve_id and e.get("cve_alias") != cve_id:
            # BC-043: match either direct primary id OR the CVE alias so
            # `make audit CVE=CVE-2024-X` works on `GO-2026-Y` findings
            # whose alias is CVE-2024-X.
            continue
        if host and e.get("host") != host:
            continue
        if finding_id and e.get("finding_id") != finding_id:
            continue
        out.append(e)
    return out


# ── Rendering ───────────────────────────────────────────────────────────────


_RESERVED_FIELDS = {"ts", "event", "cve_id", "host"}


def format_extra(event: dict) -> str:
    """Render non-reserved fields as `k=v` pairs."""
    parts = []
    for k, v in event.items():
        if k in _RESERVED_FIELDS:
            continue
        if isinstance(v, bool):
            parts.append(f"{k}={int(v)}")
        else:
            parts.append(f"{k}={v}")
    extra = " ".join(parts)
    if len(extra) > COL_EXTRA:
        return extra[: COL_EXTRA - 1] + "…"
    return extra


def _trunc(s: str, w: int) -> str:
    if len(s) <= w:
        return s
    if w <= 1:
        return s[:w]
    return s[: w - 1] + "…"


def format_row(event: dict, *, now: int) -> str:
    ts = event.get("ts", 0)
    time_str = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "        "
    age_str = format_age(max(0, now - ts)) if ts else ""
    event_name = event.get("event", "?")
    cve = event.get("cve_id") or "—"
    host = event.get("host") or "—"
    extra = format_extra(event)
    return "  ".join(
        [
            _trunc(time_str, COL_TIME).ljust(COL_TIME),
            _trunc(age_str, COL_AGE).ljust(COL_AGE),
            _trunc(event_name, COL_EVENT).ljust(COL_EVENT),
            _trunc(cve, COL_CVE).ljust(COL_CVE),
            _trunc(host, COL_HOST).ljust(COL_HOST),
            extra,
        ]
    )


def format_header() -> str:
    headers = [
        ("TIME", COL_TIME),
        ("AGE", COL_AGE),
        ("EVENT", COL_EVENT),
        ("CVE", COL_CVE),
        ("HOST", COL_HOST),
        ("EXTRA", COL_EXTRA),
    ]
    return "  ".join(h.ljust(w) for h, w in headers)


def format_separator() -> str:
    widths = [COL_TIME, COL_AGE, COL_EVENT, COL_CVE, COL_HOST, COL_EXTRA]
    return "  ".join("-" * w for w in widths)


def format_timeline(events: list[dict], *, now: int) -> str:
    lines = [format_header(), format_separator()]
    for e in events:
        lines.append(format_row(e, now=now))
    return "\n".join(lines)


def format_count_summary(events: list[dict], *, now: int) -> str:
    """Group events by type; emit `event | count | first-seen | last-seen`."""
    buckets: dict[str, list[int]] = {}
    for e in events:
        name = e.get("event", "?")
        buckets.setdefault(name, []).append(e.get("ts", 0))
    cols = [("EVENT", COL_EVENT), ("COUNT", 5), ("FIRST SEEN", 15), ("LAST SEEN", 15)]
    lines = ["  ".join(h.ljust(w) for h, w in cols), "  ".join("-" * w for _, w in cols)]
    for name in sorted(buckets):
        tss = buckets[name]
        first_age = format_age(max(0, now - min(tss)))
        last_age = format_age(max(0, now - max(tss)))
        lines.append(
            "  ".join(
                [
                    _trunc(name, COL_EVENT).ljust(COL_EVENT),
                    str(len(tss)).ljust(5),
                    first_age.ljust(15),
                    last_age.ljust(15),
                ]
            )
        )
    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────────────


def _env_arg(name: str) -> str | None:
    """Pull a `KEY=val` argument from env (set by the Makefile)."""
    v = os.environ.get(name, "")
    return v if v else None


def _event_type_help() -> str:
    lines = ["Known event types (from BC-029 onwards):"]
    for name in sorted(EVENT_HELP):
        lines.append(f"  {name:26s} {EVENT_HELP[name]}")
    return "\n".join(lines)


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="poc.scripts.audit",
        description="Operator CLI view of the beaconry audit log.",
        epilog=_event_type_help(),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--last", type=int, default=50, help="show last N events (default 50)")
    p.add_argument("--since", default=_env_arg("SINCE"), help="time window start: 1h / 2d / ISO")
    p.add_argument("--until", default=_env_arg("UNTIL"), help="time window end")
    p.add_argument(
        "--event", default=_env_arg("EVENT"), help="comma-separated event type OR filter"
    )
    p.add_argument("--cve", default=_env_arg("CVE"), help="filter by CVE id")
    p.add_argument("--host", default=_env_arg("HOST"), help="filter by host")
    p.add_argument("--finding", default=_env_arg("FINDING"), help="filter by finding_id")
    p.add_argument(
        "--count",
        action="store_true",
        default=bool(_env_arg("COUNT")),
        help="summary mode: one line per event type",
    )
    p.add_argument(
        "--follow",
        action="store_true",
        default=bool(_env_arg("FOLLOW")),
        help="live tail (ignores --last / --count)",
    )
    args = p.parse_args(argv)
    # If LAST=N env was set, apply it (argparse defaults have priority otherwise).
    if _env_arg("LAST"):
        with contextlib.suppress(ValueError):
            args.last = int(_env_arg("LAST") or "50")
    return args


def _running_compose_hub() -> bool:
    try:
        r = subprocess.run(
            ["docker", "inspect", "--format={{.State.Running}}", HUB_CONTAINER],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0 and r.stdout.strip() == "true"


def _run_in_container(argv_rest: list[str]) -> int:
    """Delegate to the host-side script running inside the hub container.

    Mirrors findings.py's approach: ship an inline script (so we don't depend
    on this file being COPY'd into the image) that reads /data/audit.log,
    cross-refs /data/beaconry.db, applies the same filters.
    """
    # Pass filters via env so the inline script picks them up through the same
    # BeaconryEnv API as the host-side path. argv_rest is empty — Makefile
    # passes LAST/SINCE/EVENT/CVE/HOST/FINDING/COUNT/FOLLOW via env.
    passthrough = {
        k: os.environ.get(k, "")
        for k in ("LAST", "SINCE", "UNTIL", "EVENT", "CVE", "HOST", "FINDING", "COUNT", "FOLLOW")
    }
    cmd = [
        "docker",
        "exec",
        "-e",
        "DATA_DIR=/data",
        *sum((["-e", f"{k}={v}"] for k, v in passthrough.items() if v), []),
        HUB_CONTAINER,
        "python3",
        "-c",
        _INLINE_SCRIPT,
    ]
    return subprocess.run(cmd, check=False).returncode


def _follow_mode(audit_path: Path) -> int:
    """Tail -f semantic — format each new JSONL line as a pretty row.

    If the compose hub is up, docker-exec a `tail -f` and pipe each line
    through our formatter in a subprocess loop. Otherwise read the local
    file directly. Filters other than `--follow` are intentionally ignored
    in follow mode (the stream has no cross-event state to filter on
    retrospectively; apply filters offline instead)."""
    if _running_compose_hub() and not os.environ.get("BEACONRY_FINDINGS_LOCAL"):
        # Stream raw JSONL from the container, format on this side.
        proc = subprocess.Popen(
            ["docker", "exec", HUB_CONTAINER, "tail", "-f", "/data/audit.log"],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert proc.stdout is not None
            # Line-buffer stdout so pipes/make-wrapped invocations get rows
            # immediately rather than batched. Mirrors `tail -f` ergonomics.
            print(format_header(), flush=True)
            print(format_separator(), flush=True)
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                print(format_row(event, now=int(time.time())), flush=True)
        except KeyboardInterrupt:
            proc.terminate()
            return 0
        finally:
            proc.wait()
        return 0
    if not audit_path.exists():
        print(f"audit.log not found at {audit_path}", file=sys.stderr)
        return 1
    try:
        with audit_path.open() as f:
            f.seek(0, 2)  # EOF
            print(format_header(), flush=True)
            print(format_separator(), flush=True)
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.5)
                    continue
                try:
                    event = json.loads(line.strip())
                    print(format_row(event, now=int(time.time())), flush=True)
                except json.JSONDecodeError:
                    continue
    except KeyboardInterrupt:
        return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    # Compose-aware routing — docker-exec into the live hub if present.
    if (
        _running_compose_hub()
        and not os.environ.get("BEACONRY_FINDINGS_LOCAL")
        and not args.follow  # follow has its own compose path
    ):
        return _run_in_container([])

    data_dir = Path(os.environ.get("DATA_DIR", ".beaconry-cache"))
    audit_path = data_dir / "audit.log"
    db_path = data_dir / "beaconry.db"

    if args.follow:
        return _follow_mode(audit_path)

    now = int(time.time())
    events = read_audit_log(audit_path)
    lookup = load_finding_lookup(db_path)
    for e in events:
        enrich_event(e, lookup)

    # Parse filter env/flags.
    since_ts: int | None = None
    until_ts: int | None = None
    try:
        if args.since:
            since_ts = parse_time_spec(args.since, now=now)
        if args.until:
            until_ts = parse_time_spec(args.until, now=now)
    except ValueError as exc:
        print(f"✗ time parse error: {exc}", file=sys.stderr)
        return 2

    event_types: set[str] | None = None
    if args.event:
        event_types = {e.strip() for e in args.event.split(",") if e.strip()}

    filtered = apply_filters(
        events,
        since_ts=since_ts,
        until_ts=until_ts,
        event_types=event_types,
        cve_id=args.cve,
        host=args.host,
        finding_id=args.finding,
    )

    if args.count:
        if not filtered:
            print("No events match the filters.")
            return 0
        print(format_count_summary(filtered, now=now))
        return 0

    if not filtered:
        print(f"No events in {audit_path} match the filters.")
        return 0

    shown = filtered[: args.last] if args.last else filtered
    print(format_timeline(shown, now=now))
    if len(filtered) > len(shown):
        print(
            f"\n({len(filtered) - len(shown)} more hidden by LAST={args.last}; "
            "raise LAST=N to see more.)"
        )
    return 0


# ── In-container inline script ─────────────────────────────────────────────
# Executed via `docker exec … python3 -c _INLINE_SCRIPT` so operators get the
# live-volume audit.log without needing this file COPY'd into the image.
# Mirrors the host-side load/filter/render logic.

_INLINE_SCRIPT = r"""
import json, os, re, sqlite3, sys, time
from datetime import datetime

AUDIT = "/data/audit.log"
DB = "/data/beaconry.db"

LAST = int(os.environ.get("LAST") or "50")
SINCE = os.environ.get("SINCE") or ""
UNTIL = os.environ.get("UNTIL") or ""
EVENT = os.environ.get("EVENT") or ""
CVE = os.environ.get("CVE") or ""
HOST = os.environ.get("HOST") or ""
FINDING = os.environ.get("FINDING") or ""
COUNT = bool(os.environ.get("COUNT"))
FOLLOW = bool(os.environ.get("FOLLOW"))

UNIT = {"s":1,"m":60,"h":3600,"d":86400}
DURATION = re.compile(r"(\d+)\s*([smhd])", re.I)
ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$")

def parse_time(spec, now):
    spec = spec.strip()
    if ISO.match(spec):
        fmt = "%Y-%m-%dT%H:%M:%S" if len(spec)==19 else "%Y-%m-%dT%H:%M"
        return int(datetime.strptime(spec, fmt).timestamp())
    m = DURATION.findall(spec.lower())
    if not m:
        raise ValueError("bad duration: "+spec)
    return now - sum(int(n)*UNIT[u.lower()] for n,u in m)

def age(sec):
    if sec<2: return "just now"
    if sec<60: return "%ds ago"%sec
    if sec<3600: return "%dm ago"%(sec//60)
    if sec<86400: return "%dh ago"%(sec//3600)
    return "%dd ago"%(sec//86400)

def trunc(s,w):
    s=str(s or "")
    return s if len(s)<=w else s[:w-1]+"…"

if not os.path.exists(AUDIT):
    print("audit.log not found at "+AUDIT); sys.exit(0)

now = int(time.time())
events = []
with open(AUDIT) as f:
    for line in f:
        line=line.strip()
        if not line: continue
        try: events.append(json.loads(line))
        except: continue
events.reverse()

lookup = {}
if os.path.exists(DB):
    try:
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        cols = {r[1] for r in con.execute("PRAGMA table_info(findings)")}
        has_alias = "cve_alias" in cols
        sel = "id, cve_id, host, cve_alias" if has_alias else "id, cve_id, host"
        for r in con.execute(f"SELECT {sel} FROM findings"):
            lookup[r[0]] = (r[1], r[2], r[3] if has_alias else None)
        con.close()
    except Exception: pass

for e in events:
    fid = e.get("finding_id")
    if fid and fid in lookup:
        c, h, a = lookup[fid]
        e.setdefault("cve_id", c)
        e.setdefault("host", h)
        if a is not None:
            e.setdefault("cve_alias", a)

try:
    since_ts = parse_time(SINCE, now) if SINCE else None
    until_ts = parse_time(UNTIL, now) if UNTIL else None
except ValueError as _exc:
    sys.stderr.write("✗ time parse error: " + str(_exc) + "\n")
    sys.exit(2)
event_types = {x.strip() for x in EVENT.split(",")} if EVENT else None

filtered = []
for e in events:
    ts = e.get("ts",0)
    if since_ts is not None and ts<since_ts: continue
    if until_ts is not None and ts>until_ts: continue
    if event_types and e.get("event") not in event_types: continue
    if CVE and e.get("cve_id") != CVE and e.get("cve_alias") != CVE: continue
    if HOST and e.get("host") != HOST: continue
    if FINDING and e.get("finding_id") != FINDING: continue
    filtered.append(e)

if COUNT:
    if not filtered:
        print("No events match the filters."); sys.exit(0)
    buckets = {}
    for e in filtered:
        buckets.setdefault(e.get("event","?"), []).append(e.get("ts",0))
    hdr = ["EVENT".ljust(24),"COUNT".ljust(5),"FIRST SEEN".ljust(15),"LAST SEEN".ljust(15)]
    print("  ".join(hdr))
    print("  ".join("-"*24, "-"*5, "-"*15, "-"*15) if False else "  ".join(["-"*24,"-"*5,"-"*15,"-"*15]))
    for name in sorted(buckets):
        tss = buckets[name]
        print("  ".join([trunc(name,24).ljust(24), str(len(tss)).ljust(5),
                         age(now-min(tss)).ljust(15), age(now-max(tss)).ljust(15)]))
    sys.exit(0)

if not filtered:
    print("No events in "+AUDIT+" match the filters."); sys.exit(0)

RESERVED = {"ts","event","cve_id","host","finding_id"}
def fmt_extra(e):
    parts = []
    for k,v in e.items():
        if k in RESERVED: continue
        if isinstance(v,bool): parts.append(f"{k}={int(v)}")
        else: parts.append(f"{k}={v}")
    s=" ".join(parts)
    return s[:49]+"…" if len(s)>50 else s

print("  ".join(["TIME".ljust(8),"AGE".ljust(11),"EVENT".ljust(24),"CVE".ljust(20),"HOST".ljust(12),"EXTRA".ljust(50)]))
print("  ".join(["-"*8,"-"*11,"-"*24,"-"*20,"-"*12,"-"*50]))
shown = filtered[:LAST]
for e in shown:
    ts = e.get("ts",0)
    print("  ".join([
        trunc(time.strftime("%H:%M:%S", time.localtime(ts)) if ts else "",8).ljust(8),
        trunc(age(now-ts) if ts else "",11).ljust(11),
        trunc(e.get("event","?"),24).ljust(24),
        trunc(e.get("cve_id") or "—",20).ljust(20),
        trunc(e.get("host") or "—",12).ljust(12),
        fmt_extra(e),
    ]))
if len(filtered) > len(shown):
    print(f"\n({len(filtered)-len(shown)} more hidden by LAST={LAST}; raise LAST=N to see more.)")
"""


if __name__ == "__main__":
    raise SystemExit(main())
