"""
Beaconry Hub 0.1.0

Receives SBOMs from scouts, matches against CVE feeds via grype,
sends Telegram notifications with inline approve/snooze/ack buttons,
dispatches to executor adapters on approval, verifies fix via re-scan.
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import sqlite3
import tempfile
import time
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from executor.base import ExecutorPlugin, Finding
from executor.docker_adapter import DockerAdapter
from executor.manual_adapter import ManualAdapter
from fastapi import FastAPI
from fastapi import Response as _FastAPIResponse
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

try:
    # Container path: beaconry_health COPY'd next to hub.py on /app
    import audit as _audit
    import kev as _kev
    import notifiers as _notifiers_pkg
    import urgency as _urgency
    import waterfall as _waterfall
    from beaconry_health import build_router, check_binary, check_db, check_dir_populated
    from notifiers import TelegramAdapter
    from notifiers import notify_digest as _dispatch_digest
    from notifiers import notify_finding as _dispatch_finding
    from notifiers import notify_heartbeat as _dispatch_heartbeat
    from notifiers import notify_regression as _dispatch_regression
    from notifiers import register as _register_adapter
except ImportError:
    # Host test path: imported as poc.hub.hub, package at poc.beaconry_health
    from poc.beaconry_health import build_router, check_binary, check_db, check_dir_populated
    from poc.hub import audit as _audit
    from poc.hub import kev as _kev
    from poc.hub import notifiers as _notifiers_pkg
    from poc.hub import urgency as _urgency
    from poc.hub import waterfall as _waterfall
    from poc.hub.notifiers import TelegramAdapter
    from poc.hub.notifiers import notify_digest as _dispatch_digest
    from poc.hub.notifiers import notify_finding as _dispatch_finding
    from poc.hub.notifiers import notify_heartbeat as _dispatch_heartbeat
    from poc.hub.notifiers import notify_regression as _dispatch_regression
    from poc.hub.notifiers import register as _register_adapter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [hub] %(levelname)s %(message)s",
)
log = logging.getLogger("hub")

# Suppress HTTP client logs — python-telegram-bot's httpx logs include the
# bot token in request URLs, which would leak secrets to stdout / docker logs.
# This mirrors poc/demo.py's suppression so the container runtime is covered too.
for _noisy in ("httpx", "httpcore", "telegram", "telegram.ext"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

MOCK = os.environ.get("BEACONRY_MOCK") == "1"
# BC-018: split MOCK into two independent dials so e2e stacks can run REAL
# python-telegram-bot against telegram-mock while still skipping the heavy
# grype DB download. MOCK=1 remains a back-compat alias (sets both).
SKIP_GRYPE_DB = os.environ.get("BEACONRY_SKIP_GRYPE_DB") == "1" or MOCK
# When set, hub's python-telegram-bot client talks here instead of
# api.telegram.org. Enables e2e via `telegram-mock` service + dev-without-
# real-bot workflows. Empty = default Telegram API.
TELEGRAM_BASE_URL = os.environ.get("BEACONRY_TELEGRAM_BASE_URL", "")
# Env is read with defaults so importing hub never crashes; functions that
# need real values (notify, lifespan) raise with a named error when invoked.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
MIN_CVSS = float(os.environ.get("MIN_CVSS", "7.0"))
# BC-009: real fix execution is on by default; operator can opt out with
# BEACONRY_FIX_ENABLED=0 (e.g. for demos or safety-conscious exploration).
FIX_ENABLED = os.environ.get("BEACONRY_FIX_ENABLED", "1") != "0"
# BC-031: scan reconciliation. Default ON because the whole point is that
# "fix is not forget". Operator can opt out with BEACONRY_RECONCILE_ENABLED=0
# for self-hosted scenarios where the long-term diff feels like surveillance,
# or when the DB holds historical state the operator doesn't want touched.
RECONCILE_ENABLED = os.environ.get("BEACONRY_RECONCILE_ENABLED", "1") != "0"
# BC-030: internet_facing detection. Default ON because the whole point of
# BC-030 is making Tier 1 reachable. Set to 0 to force the pre-BC-030 behavior
# of treating every finding as not internet-facing (Tier 1 unreachable).
INTERNET_FACING_DETECTION_ENABLED = (
    os.environ.get("BEACONRY_INTERNET_FACING_DETECTION_ENABLED", "1") != "0"
)
# BC-036: digest cadence throttle. Default 24h — digests are summarisations
# of state that doesn't change scan-to-scan, so repeating the same body every
# SCAN_INTERVAL (default 5min = 288/day) is noise. Content hash fallback
# bypasses the window if the suppressed set actually changes. 0 = disable
# the throttle entirely (pre-BC-036 behavior: fire every scan with suppressed).


def _int_env(key: str, default: int) -> int:
    """Read `key` as int; treat unset AND empty-string as default.

    Compose's `${VAR:-}` fallback resolves to "" (not unset) when the operator
    hasn't exported the var, which breaks a bare `int(os.environ.get(key, "N"))`.
    This helper swallows that gap.
    """
    raw = os.environ.get(key, "").strip()
    return int(raw) if raw else default


# BC-035: periodic reminder for findings sitting at status=open for too long.
# Default ON — the whole point is that silent-forever findings are a bug.
HEARTBEAT_ENABLED = os.environ.get("BEACONRY_HEARTBEAT_ENABLED", "1") != "0"
# How long a finding sits before heartbeat re-pokes. Default 7 days.
HEARTBEAT_INTERVAL_S = _int_env("BEACONRY_HEARTBEAT_INTERVAL_S", 7 * 86400)
# How often the sweep runs internally. Default 1h — cheap, catches new-stale.
HEARTBEAT_SWEEP_INTERVAL_S = _int_env("BEACONRY_HEARTBEAT_SWEEP_INTERVAL_S", 3600)
# BC-035: a finding whose last_seen_ts is older than this window is considered
# gone from inventory — no heartbeat (BC-031 reconcile will auto-close it).
HEARTBEAT_LIVENESS_WINDOW_S = _int_env("BEACONRY_HEARTBEAT_LIVENESS_WINDOW_S", 7 * 86400)
DIGEST_INTERVAL_S = _int_env("BEACONRY_DIGEST_INTERVAL_S", 86400)
# BC-038: scenario harness toggle. When set, `/test/*` endpoints are registered
# on the FastAPI app. These endpoints accept pre-matched candidates + an
# injected `now` timestamp so scenarios can exercise the full ingest flow
# without real grype, real scout, or real clock drift. When OFF, the routes
# are not registered at all — requests to them return 404. Must remain OFF
# in production operator stacks; env-gate only (hub does not police misuse).
TEST_MODE = os.environ.get("BEACONRY_TEST_MODE") == "1"

DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "beaconry.db"

EXECUTORS: list[ExecutorPlugin] = [DockerAdapter(), ManualAdapter()]

tg_app: Application | None = None

# BC-010: health-check state. `_startup_complete` flips True once lifespan has
# finished grype DB update (even if update warned). `_last_scan_ts` is updated
# by receive_scan so /healthz can report liveness-in-practice.
_startup_complete: bool = False
_last_scan_ts: int = 0
HUB_VERSION = "0.1.0"

# BC-011: policy-driven probe resolver. Parsed once at startup (fail-fast on
# malformed input) and read by on_button_click's `verify` action.
HEALTH_POLICIES: dict[str, "_waterfall.Probe"] = {}


def _resolve_probe_for(image_ref: str) -> "_waterfall.Probe | None":
    """Thin wrapper so tests can monkeypatch at the hub level."""
    return _waterfall.resolve_probe(image_ref, HEALTH_POLICIES)


# ── Database ──────────────────────────────────────────────────────────────────


def init_db() -> None:
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS findings (
            id           TEXT PRIMARY KEY,
            host         TEXT,
            cve_id       TEXT,
            package      TEXT,
            version      TEXT,
            fixed_version TEXT,
            severity     TEXT,
            cvss         REAL,
            source_type  TEXT,
            source_ref   TEXT,
            description  TEXT,
            found_at     INTEGER,
            status       TEXT DEFAULT 'open',
            tier         INTEGER DEFAULT 3,
            kev          INTEGER DEFAULT 0,
            last_fixed_ts INTEGER DEFAULT 0,
            last_seen_ts INTEGER DEFAULT 0,
            last_notified_ts INTEGER DEFAULT 0,
            internet_facing INTEGER DEFAULT 0
        )
    """)
    # Idempotent migrations for DBs created before each column existed.
    with contextlib.suppress(sqlite3.OperationalError):
        con.execute("ALTER TABLE findings ADD COLUMN tier INTEGER DEFAULT 3")
    with contextlib.suppress(sqlite3.OperationalError):
        con.execute("ALTER TABLE findings ADD COLUMN kev INTEGER DEFAULT 0")
    # BC-031: last_fixed_ts tracks when a finding first transitioned to fixed/
    # fixed_verified, so reconcile_scan can show "regressed since <date>" context
    # in the loud alarm. 0 = never fixed yet.
    with contextlib.suppress(sqlite3.OperationalError):
        con.execute("ALTER TABLE findings ADD COLUMN last_fixed_ts INTEGER DEFAULT 0")
    # BC-031 close-gap (breaking-change-in-planning discipline): legacy
    # `fixed`/`fixed_verified` rows written pre-BC-031 default last_fixed_ts=0,
    # which would render as "regressed since unknown" in the 🔥 notification
    # body if those CVEs ever reappear. Backfill from `found_at` — approximation
    # (the fix landed at or after the finding was found), but strictly better
    # than "unknown" for operator diffing against change logs.
    con.execute(
        "UPDATE findings SET last_fixed_ts = found_at "
        "WHERE status IN ('fixed','fixed_verified') AND last_fixed_ts = 0 AND found_at > 0"
    )
    # BC-034: last_seen_ts tracks the most recent scout cycle that still
    # reported this (host, cve, source_ref, package) tuple. Pre-BC-034 we
    # only persisted first-sighting via `found_at`. Legacy rows: backfill
    # from found_at (approximation — at least that scan definitely saw it).
    with contextlib.suppress(sqlite3.OperationalError):
        con.execute("ALTER TABLE findings ADD COLUMN last_seen_ts INTEGER DEFAULT 0")
    con.execute(
        "UPDATE findings SET last_seen_ts = found_at WHERE last_seen_ts = 0 AND found_at > 0"
    )
    # BC-035: last_notified_ts tracks the most recent reminder sent for this
    # finding (first-fire OR heartbeat). The heartbeat sweep uses it to avoid
    # re-poking too often. Legacy rows backfill from `found_at` (approximation —
    # the first notification happened near first-sighting).
    with contextlib.suppress(sqlite3.OperationalError):
        con.execute("ALTER TABLE findings ADD COLUMN last_notified_ts INTEGER DEFAULT 0")
    con.execute(
        "UPDATE findings SET last_notified_ts = found_at WHERE last_notified_ts = 0 AND found_at > 0"
    )
    # BC-030: internet_facing is 0 (False) for legacy rows — the pre-BC-030
    # hard-coded value. No backfill: the honest semantic is "unknown exposure,
    # defaulted conservatively to not-internet-facing", which is what the
    # legacy classifier assumed anyway.
    with contextlib.suppress(sqlite3.OperationalError):
        con.execute("ALTER TABLE findings ADD COLUMN internet_facing INTEGER DEFAULT 0")
    # BC-036: digest cadence state — per-host last-sent + content hash. Separate
    # table (not a column on `findings`) because the state is per-host, not
    # per-finding.
    con.execute("""
        CREATE TABLE IF NOT EXISTS digest_state (
            host         TEXT PRIMARY KEY,
            last_sent_ts INTEGER NOT NULL,
            last_hash    TEXT NOT NULL
        )
    """)
    con.commit()
    con.close()


def save_finding(f: Finding, *, now_ts: int | None = None) -> bool:
    """Insert finding. Returns True if new (not a duplicate).

    `now_ts` injected for BC-038 scenarios; defaults to real clock."""
    ts = now_ts if now_ts is not None else int(time.time())
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(
            """INSERT OR IGNORE INTO findings
               (id, host, cve_id, package, version, fixed_version, severity,
                cvss, source_type, source_ref, description, found_at, tier, kev,
                internet_facing, last_seen_ts, last_notified_ts)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                f.id,
                f.host,
                f.cve_id,
                f.package,
                f.version,
                f.fixed_version,
                f.severity,
                f.cvss,
                f.source_type,
                f.source_ref,
                f.description,
                ts,
                f.tier,
                1 if f.kev else 0,
                1 if f.internet_facing else 0,
                # BC-035 close-gap (surfaced by BC-038 s08): seed last_seen_ts
                # and last_notified_ts to `now` on first write. Pre-BC-038 the
                # defaults were 0, which worked IFF init_db's backfill ran
                # between save_finding and the first heartbeat sweep — true in
                # prod (hub restart → migration) but false in scenarios + any
                # scan on a long-running hub. Seeding here makes the semantic
                # robust across both.
                ts,
                ts,
            ),
        )
        new_row = con.execute("SELECT changes()").fetchone()[0] > 0
        con.commit()
    finally:
        con.close()
    # BC-029: new finding → audit entry. Idempotent inserts don't double-log.
    if new_row:
        _audit.append(
            "finding_ingested",
            finding_id=f.id,
            cve_id=f.cve_id,
            host=f.host,
            source_ref=f.source_ref,
            tier=f.tier,
            kev=f.kev,
            cvss=f.cvss,
            internet_facing=f.internet_facing,  # BC-030
        )
    return new_row


def load_finding(finding_id: str) -> Finding | None:
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT id,host,cve_id,package,version,fixed_version,severity,"
        "cvss,source_type,source_ref,description,tier,kev,internet_facing "
        "FROM findings WHERE id=?",
        (finding_id,),
    ).fetchone()
    con.close()
    if row is None:
        return None
    # Last two columns (kev, internet_facing) are INTEGER in SQLite; coerce to bool.
    *head, kev_int, if_int = row
    return Finding(*head, kev=bool(kev_int), internet_facing=bool(if_int))


def set_status(finding_id: str, status: str) -> None:
    con = sqlite3.connect(DB_PATH)
    # BC-031: record when the fix landed so reconcile_scan can display
    # "regressed since <date>" if the finding ever reappears.
    if status in ("fixed", "fixed_verified"):
        con.execute(
            "UPDATE findings SET status=?, last_fixed_ts=? WHERE id=?",
            (status, int(time.time()), finding_id),
        )
    else:
        con.execute("UPDATE findings SET status=? WHERE id=?", (status, finding_id))
    con.commit()
    con.close()
    # BC-029: every operator-visible state transition lands in the audit log.
    _audit.append("status_change", finding_id=finding_id, status=status)


def bump_last_seen(host: str, scanned_ids: set[str], *, now_ts: int | None = None) -> None:
    """BC-034: update `last_seen_ts` for every DB finding whose id scout
    still reported in the current scan.

    Independent of RECONCILE_ENABLED — this is pure inventory bookkeeping,
    not an operator-visible state change. The reminder/heartbeat features
    that will consume this data (future BCs) each have their own opt-out.

    No-op for empty scanned_ids. Scoped to host so a scan from host-a
    cannot touch host-b bookkeeping (parity with reconcile_scan).
    `now_ts` injected for BC-038 scenarios; defaults to real clock."""
    if not scanned_ids:
        return
    ts = now_ts if now_ts is not None else int(time.time())
    con = sqlite3.connect(DB_PATH)
    try:
        placeholders = ",".join("?" * len(scanned_ids))
        con.execute(
            f"UPDATE findings SET last_seen_ts = ? WHERE host = ? AND id IN ({placeholders})",
            (ts, host, *scanned_ids),
        )
        con.commit()
    except sqlite3.OperationalError:
        # Cold boot / init_db hasn't run — nothing to update.
        pass
    finally:
        con.close()


def reconcile_scan(host: str, scanned_ids: set[str]) -> tuple[list[Finding], list[Finding]]:
    """BC-031: diff current scan against DB. Return (regressed, auto_closed).

    State transitions applied:
      - open      + absent  → fixed_verified  (auto-close, silent happy path)
      - regressed + absent  → fixed_verified  (re-fix held)
      - fixed     + present → regressed       (loud alarm, caller sends notification)
      - fixed_verified + present → regressed  (same)
      - snoozed / accepted  → unchanged (operator-decided, respect it)

    `scanned_ids` is the set of finding IDs produced by this scout cycle.
    If RECONCILE_ENABLED is False, returns ([], []) without touching the DB.
    """
    if not RECONCILE_ENABLED:
        return [], []
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(
            "SELECT id, host, cve_id, package, version, fixed_version, severity, cvss, "
            "source_type, source_ref, description, tier, kev, status, last_fixed_ts "
            "FROM findings WHERE host=? AND status IN "
            "('open', 'fixed', 'fixed_verified', 'regressed')",
            (host,),
        ).fetchall()
    except sqlite3.OperationalError:
        # Cold boot / init_db hasn't run yet — nothing to reconcile.
        con.close()
        return [], []

    regressed: list[Finding] = []
    auto_closed: list[Finding] = []
    now = int(time.time())

    for row in rows:
        fid = row[0]
        status = row[13]
        last_fixed_ts = row[14] or 0
        in_scan = fid in scanned_ids
        finding = Finding(
            id=row[0],
            host=row[1],
            cve_id=row[2],
            package=row[3],
            version=row[4],
            fixed_version=row[5],
            severity=row[6],
            cvss=row[7],
            source_type=row[8],
            source_ref=row[9],
            description=row[10] or "",
            tier=row[11],
            kev=bool(row[12]),
        )

        if status in ("open", "regressed") and not in_scan:
            con.execute(
                "UPDATE findings SET status='fixed_verified', last_fixed_ts=? WHERE id=?",
                (now, fid),
            )
            auto_closed.append(finding)
            _audit.append(
                "auto_closed",
                finding_id=fid,
                prev_status=status,
                cve_id=finding.cve_id,
            )
        elif status in ("fixed", "fixed_verified") and in_scan:
            con.execute("UPDATE findings SET status='regressed' WHERE id=?", (fid,))
            regressed.append(finding)
            _audit.append(
                "regressed",
                finding_id=fid,
                prev_status=status,
                cve_id=finding.cve_id,
                last_fixed_ts=last_fixed_ts,
                regressed_ts=now,
            )

    con.commit()
    con.close()
    return regressed, auto_closed


def get_status(finding_id: str) -> str | None:
    """Return the current status for a finding id, or None if the row is missing."""
    con = sqlite3.connect(DB_PATH)
    row = con.execute("SELECT status FROM findings WHERE id=?", (finding_id,)).fetchone()
    con.close()
    return row[0] if row else None


# ── CVE matching ──────────────────────────────────────────────────────────────


async def run_grype(sbom: dict) -> list[dict]:
    if MOCK:
        from poc.mock_data import fixture_grype_matches

        return fixture_grype_matches()

    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as f:
        json.dump(sbom, f)
        path = f.name
    try:
        proc = await asyncio.create_subprocess_exec(
            "grype",
            f"sbom:{path}",
            "-o",
            "json",
            "--quiet",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            log.warning("grype error: %s", stderr.decode()[:200])
            return []
        return json.loads(stdout).get("matches", [])
    except Exception as e:
        log.error("grype failed: %s", e)
        return []
    finally:
        os.unlink(path)


def extract_cvss(vuln: dict) -> float:
    best = 0.0
    for entry in vuln.get("cvss", []):
        score = entry.get("metrics", {}).get("baseScore", 0)
        best = max(best, float(score))
    return best


def make_id(host: str, cve_id: str, source_ref: str, package: str) -> str:
    key = f"{host}:{cve_id}:{source_ref}:{package}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


async def process_sbom_entry(
    entry: dict,
    host: str,
    port_exposure: dict[str, bool] | None = None,
) -> list[Finding]:
    source_meta = entry.get("source_meta", {})
    sbom = entry.get("sbom", {})
    port_exposure = port_exposure or {}

    matches = await run_grype(sbom)
    findings = []

    for match in matches:
        vuln = match.get("vulnerability", {})
        artifact = match.get("artifact", {})
        cvss = extract_cvss(vuln)
        cve_id = vuln["id"]
        # BC-028: check KEV against primary id + grype's `relatedVulnerabilities`
        # aliases. Non-CVE primary ids (GO-*, GHSA-*) often have a CVE alias
        # that is in the KEV catalog; checking only the primary misses them.
        related_ids = [r.get("id", "") for r in match.get("relatedVulnerabilities", [])]
        is_kev = _kev.is_known_exploited_any([cve_id, *related_ids])

        # BC-023: KEV bypasses MIN_CVSS. The product thesis is "KEV is
        # empirical exploitation signal; CVSS is theoretical severity." A
        # CVSS-7.5 KEV CVE is more urgent than a CVSS-9.8 theoretical one.
        # Filtering by CVSS first would make Tier 0 unreachable below
        # MIN_CVSS, which is the exact bug BC-020 was supposed to prevent.
        if not is_kev and cvss < MIN_CVSS:
            continue
        if is_kev and cvss < MIN_CVSS:
            log.info(
                "BC-023: KEV %s bypassed MIN_CVSS filter (cvss=%.1f < %.1f)",
                cve_id,
                cvss,
                MIN_CVSS,
            )

        source_type = source_meta.get("type", "unknown")
        source_ref = source_meta.get("image") or artifact.get("name", "")
        fix_versions = vuln.get("fix", {}).get("versions") or []
        # BC-030: honour the operator toggle. When disabled, we throw away scout's
        # exposure data and classify as pre-BC-030 — Tier 1 unreachable, all findings
        # fall through to the CVSS tier below.
        is_internet_facing = INTERNET_FACING_DETECTION_ENABLED and bool(
            port_exposure.get(source_ref, False)
        )
        # Build a provisional finding so classify_tier gets the real CVSS/severity.
        f = Finding(
            id=make_id(host, cve_id, source_ref, artifact.get("name", "")),
            host=host,
            cve_id=cve_id,
            package=artifact.get("name", ""),
            version=artifact.get("version", ""),
            fixed_version=fix_versions[0] if fix_versions else "not available",
            severity=vuln.get("severity", "Unknown"),
            cvss=cvss,
            source_type=source_type,
            source_ref=source_ref,
            description=(vuln.get("description") or "")[:200],
            kev=is_kev,
            internet_facing=is_internet_facing,
        )
        # BC-020 + BC-030: wire the urgency ladder with real internet_facing.
        # KEV presence trumps everything per the Tier 0 rule.
        f.tier = _urgency.classify_tier(f, kev=is_kev, internet_facing=is_internet_facing)
        findings.append(f)

    return findings


# ── Verification ──────────────────────────────────────────────────────────────


async def verify_fix(finding: Finding) -> bool:
    """Re-scan after fix. Returns True if the CVE is no longer present.

    BC-033: scans the SAME source_ref the operator ran — not a hardcoded
    `:latest`. Pre-BC-033 this hardcoded `:latest` was correct because the
    pre-BC-014 fix flow pulled `:latest` as "the fix". BC-014 switched to
    same-track pull (`nginx:1.24` stays `nginx:1.24`), but verify_fix wasn't
    updated. Result: operator who tapped Fix on `nginx:1.24` got verification
    against `nginx:latest` — misleading signal, often outright wrong.

    Post-BC-033: `syft <source_ref>` + grype match the actual tag the operator
    is running. For frozen tags where upstream has no fix, this will reliably
    return False (CVE still present) — honest signal that pairs with BC-032's
    `❌ unlikely` viability indicator.
    """
    if MOCK:
        return True

    if finding.source_type != "docker":
        return True  # can't auto-verify non-docker in POC

    proc = await asyncio.create_subprocess_exec(
        "syft",
        finding.source_ref,
        "-o",
        "syft-json",
        "--quiet",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        log.warning("syft re-scan failed: %s", stderr.decode()[:200])
        return False

    try:
        sbom = json.loads(stdout)
    except json.JSONDecodeError:
        return False

    matches = await run_grype(sbom)
    for match in matches:
        if (
            match.get("vulnerability", {}).get("id") == finding.cve_id
            and match.get("artifact", {}).get("name") == finding.package
        ):
            return False  # still present

    return True


# ── Telegram ──────────────────────────────────────────────────────────────────


def format_notification(finding: Finding) -> str:
    """Build the message body in Telegram HTML (parse_mode='HTML').

    HTML is used instead of legacy Markdown because Markdown v1 has no escape
    syntax — any raw `*`, `_`, backtick, or `[` in a CVE description would
    break parsing. HTML needs only `<`, `>`, `&` escaped via html.escape.
    """
    import html

    emoji = {"Critical": "🔴", "High": "🟠", "Medium": "🟡"}.get(finding.severity, "⚪")
    executor = next((e for e in EXECUTORS if e.can_handle(finding)), ManualAdapter())
    fix_cmd = executor.describe(finding)

    def esc(s: str) -> str:
        return html.escape(s, quote=False)

    lines: list[str] = []
    # BC-020: Tier 0 (KEV) gets a visually distinct header above the severity row.
    if finding.tier == 0:
        lines.append("🚨 <b>KEV — EXPLOITED IN THE WILD</b>")
    # BC-030: Tier 1 (CVSS≥9 + internet-facing) gets its own header. Falls
    # below KEV so operator still sees the strongest signal first on Tier 0 rows.
    if finding.tier == 1:
        lines.append("🌐 <b>INTERNET-FACING — Tier 1</b>")
    lines.extend(
        [
            f"{emoji} <b>{esc(finding.severity)} — {esc(finding.cve_id)}</b>",
            f"CVSS: <code>{finding.cvss:.1f}</code> · Host: <code>{esc(finding.host)}</code>",
            f"Target: <code>{esc(finding.source_ref)}</code> ({esc(finding.source_type)})",
            _package_fix_line(finding, esc),
            # BC-032: tell the operator whether tapping Fix will likely help.
            _viability_line(finding),
        ]
    )
    if finding.description:
        desc = esc(finding.description[:160])
        lines.append(f"\n<i>{desc}</i>")
    lines.append(f"\n<code>{esc(fix_cmd)}</code>")
    return "\n".join(lines)


def _has_upstream_fix(finding: Finding) -> bool:
    """BC-032 close-gap: 'not available' is the sentinel process_sbom_entry
    writes when grype's `fix.versions` is empty. Any heuristic that reasons
    about 'the fix version' must guard this case first — otherwise the
    reason text inlines 'not available' as if it were a real version."""
    return bool(finding.fixed_version) and finding.fixed_version != "not available"


def assess_fix_viability(finding: Finding) -> tuple[str, str]:
    """BC-032: give the operator a quick signal whether `🔧 Fix` is likely to work.

    Returns (label, reason) where label is one of 'likely', 'unlikely', 'unknown'.

    Heuristic (not a guarantee — grype's `fix.versions` isn't always accurate):

      - No upstream fix available (grype returns `not available`) → **unlikely**:
        regardless of tag mutability, Fix can't pull a patch that doesn't exist.
      - docker + `:latest|main|stable|edge|nightly` → **likely**: mutable tag,
        pulling fetches the current upstream which should include the fix.
      - docker + frozen semver tag (`:1.24`, `:16-alpine`, `:1.24.0`) → **unlikely**:
        BC-014 same-track pull returns the same bits; a frozen tag won't drift
        to a fixed version. Operator needs to edit the compose to a newer tag.
      - alpm + fixed_version != current version → **likely**: `sudo pacman -Syu`
        will pull the fixed version from the Arch repos.
      - alpm + no fixed_version info → **unlikely** (vendor hasn't patched).
      - Anything else (python, etc.) → **unknown**.
    """
    # Guard first: no upstream fix exists → Fix cannot help regardless of tag.
    if not _has_upstream_fix(finding):
        return (
            "unlikely",
            "no upstream fix available yet — the vendor hasn't released a patched version",
        )
    if finding.source_type == "docker":
        tag = finding.source_ref.rsplit(":", 1)[-1] if ":" in finding.source_ref else "latest"
        mutable_tags = {"latest", "main", "stable", "edge", "nightly"}
        if tag.lower() in mutable_tags:
            return (
                "likely",
                f"mutable tag '{tag}' — next pull fetches current upstream",
            )
        if re.match(r"^\d+(\.\d+)*(-[\w.]+)?$", tag):
            return (
                "unlikely",
                f"frozen tag '{tag}' — pulling returns the same bits; "
                f"edit compose to use a newer tag that ships {finding.fixed_version}",
            )
        return (
            "unknown",
            f"tag '{tag}' — mutability can't be inferred automatically",
        )
    if finding.source_type == "alpm":
        return (
            "likely",
            f"upstream repo has {finding.fixed_version}; `sudo pacman -Syu` pulls it",
        )
    return "unknown", f"viability heuristic doesn't cover source_type '{finding.source_type}'"


def _package_fix_line(finding: Finding, esc) -> str:
    """BC-032 close-gap: render the Package line, replacing grype's
    `not available` sentinel with human-readable phrasing so the body
    doesn't say 'fixed in not available'."""
    pkg_version = f"{esc(finding.package)} {esc(finding.version)}"
    if _has_upstream_fix(finding):
        return (
            f"Package: <code>{pkg_version}</code> → fixed in "
            f"<code>{esc(finding.fixed_version)}</code>"
        )
    return f"Package: <code>{pkg_version}</code> — <i>no upstream fix available yet</i>"


def _viability_line(finding: Finding) -> str:
    """Render the viability assessment as one HTML line for the notification body."""
    import html

    label, reason = assess_fix_viability(finding)
    marker = {"likely": "✅", "unlikely": "❌", "unknown": "❓"}[label]
    return f"Fix viability: {marker} <b>{label}</b> — <i>{html.escape(reason, quote=False)}</i>"


def _render_mock_telegram(text: str) -> str:
    """Render a Telegram message as a boxed stdout notification."""
    width = max((len(line) for line in text.splitlines()), default=0) + 2
    width = max(width, 60)
    top = "    ╭" + "─" * width + "╮"
    bot = "    ╰" + "─" * width + "╯"
    body = "\n".join(f"    │ {line.ljust(width - 1)}│" for line in text.splitlines())
    buttons = "      Inline keyboard: [🔧 Fix now] [⏰ Snooze 4h] [✅ Accept risk]"
    return "\n".join([top, body, bot, buttons])


def format_regression(finding: Finding, last_fixed_ts: int) -> str:
    """BC-031: distinct 🔥 message for a previously-fixed CVE that returned.

    Loud by design — operator thought this was handled months ago; now it's
    back. Shows the prior-fix timestamp so operator can diff against their
    change log."""
    import datetime
    import html

    def esc(s: str) -> str:
        return html.escape(s, quote=False)

    fixed_date = (
        datetime.datetime.fromtimestamp(last_fixed_ts).strftime("%Y-%m-%d")
        if last_fixed_ts
        else "unknown"
    )
    lines = [
        "🔥 <b>FIX REGRESSED</b>",
        f"<b>{esc(finding.cve_id)}</b> is back after being fixed on <code>{fixed_date}</code>.",
        f"Target: <code>{esc(finding.source_ref)}</code> ({esc(finding.source_type)})",
        # BC-032: show the fixed version AND the viability assessment so the
        # operator can tell whether 🔧 Re-fix will actually help or will loop.
        _package_fix_line(finding, esc),
        _viability_line(finding),
        "",
        "<i>Something reinstalled or rolled back the vulnerable version. "
        "Investigate before re-fixing — the root cause will come back again otherwise.</i>",
    ]
    return "\n".join(lines)


def format_heartbeat(finding: Finding, open_since_ts: int) -> str:
    """BC-035: periodic reminder for an open finding operator never actioned.

    Body shape mirrors format_notification but leads with a ⏰ header giving
    the time-open context so operator understands this is a nudge, not a
    first-fire."""
    import datetime
    import html

    def esc(s: str) -> str:
        return html.escape(s, quote=False)

    days_open = max(1, (int(time.time()) - open_since_ts) // 86400)
    since = (
        datetime.datetime.fromtimestamp(open_since_ts).strftime("%Y-%m-%d")
        if open_since_ts
        else "unknown"
    )
    emoji = {"Critical": "🔴", "High": "🟠", "Medium": "🟡"}.get(finding.severity, "⚪")

    lines = [
        f"⏰ <b>OPEN SINCE {esc(since)} ({days_open} day{'s' if days_open != 1 else ''})</b>",
    ]
    if finding.tier == 0:
        lines.append("🚨 <b>KEV — EXPLOITED IN THE WILD</b>")
    lines.extend(
        [
            f"{emoji} <b>{esc(finding.severity)} — {esc(finding.cve_id)}</b>",
            f"CVSS: <code>{finding.cvss:.1f}</code> · Host: <code>{esc(finding.host)}</code>",
            f"Target: <code>{esc(finding.source_ref)}</code> ({esc(finding.source_type)})",
            _package_fix_line(finding, esc),
            _viability_line(finding),
            "",
            "<i>You haven't actioned this finding yet. Snooze to defer or "
            "accept the risk if you've decided to live with it.</i>",
        ]
    )
    return "\n".join(lines)


# ── BC-035: keyboard builders exposed to the TelegramAdapter ─────────────────


def _kb_finding(finding: Finding) -> "InlineKeyboardMarkup":
    """First-fire + heartbeat buttons — Fix / Snooze / Accept risk."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔧 Fix now", callback_data=f"fix:{finding.id}"),
                InlineKeyboardButton("⏰ Snooze 4h", callback_data=f"snooze:{finding.id}:4"),
            ],
            [InlineKeyboardButton("✅ Accept risk", callback_data=f"ack:{finding.id}")],
        ]
    )


def _kb_regression(finding: Finding) -> "InlineKeyboardMarkup":
    """BC-031 regression — no Snooze (regression deserves attention, not deferral)."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔧 Re-fix", callback_data=f"fix:{finding.id}"),
                InlineKeyboardButton("✅ Accept risk", callback_data=f"ack:{finding.id}"),
            ]
        ]
    )


# BC-035 thin wrappers — hub call sites keep their original names but now
# dispatch through the notifier registry. One adapter failing (e.g. Telegram
# offline) must not stop others; the registry handles isolation.


def _mock_print(label: str, text: str) -> None:
    """BC-035 fallback: in MOCK mode with no adapter registered (demo path),
    preserve the pre-BC-035 '[mock] telegram send' output so BC-001 e2e tests
    still see the labelled stub."""
    print(f"    [mock] telegram send: {label} dispatched")
    print(f"    ==> {label.title()}")
    print(_render_mock_telegram(text))


async def notify_regression(finding: Finding, last_fixed_ts: int) -> None:
    """BC-031: 🔥 FIX REGRESSED. Dispatched to all enabled notification adapters."""
    if MOCK and not _notifiers_pkg.enabled_adapters():
        _mock_print("Regression", format_regression(finding, last_fixed_ts))
        return
    await _dispatch_regression(finding, last_fixed_ts)


async def notify(finding: Finding) -> None:
    """BC-003: first-fire notification. Dispatched to all enabled adapters."""
    if MOCK and not _notifiers_pkg.enabled_adapters():
        _mock_print("Notification", format_notification(finding))
        return
    await _dispatch_finding(finding)


async def notify_heartbeat(finding: Finding, open_since_ts: int) -> None:
    """BC-035: periodic reminder. Dispatched to all enabled adapters."""
    if MOCK and not _notifiers_pkg.enabled_adapters():
        _mock_print("Heartbeat", format_heartbeat(finding, open_since_ts))
        return
    await _dispatch_heartbeat(finding, open_since_ts)


CALLBACK_RE = re.compile(r"^(fix|snooze|ack|verify):[a-f0-9]{16}(?::\d+)?$")


def _esc(s: str) -> str:
    import html

    return html.escape(s, quote=False)


async def _handle_fix(query, finding: Finding, finding_id: str, parts: list[str]) -> None:  # noqa: ARG001
    """Fix action: dispatch to executor, verify, edit the message with the terminal state."""
    if not FIX_ENABLED:
        await query.edit_message_text(
            f"⚙️ Fix execution disabled (BEACONRY_FIX_ENABLED=0) — "
            f"<code>{_esc(finding.cve_id)}</code> left as <code>open</code>",
            parse_mode="HTML",
        )
        return

    await query.edit_message_text(
        f"⏳ Fixing <code>{_esc(finding.cve_id)}</code> on <code>{_esc(finding.host)}</code>…",
        parse_mode="HTML",
    )
    executor = next((e for e in EXECUTORS if e.can_handle(finding)), ManualAdapter())
    result = await executor.execute(finding)

    if not result.success:
        await query.edit_message_text(
            f"❌ <b>Fix failed</b>: <code>{_esc(result.message)}</code>\n"
            f"Image unchanged. Rollback tag still exists if you need to inspect.",
            parse_mode="HTML",
        )
        return

    # Non-docker findings: ManualAdapter printed the command; operator runs it,
    # not beaconry. Status stays open (manual action required).
    if finding.source_type != "docker":
        await query.edit_message_text(
            f"⚙️ <b>Manual action required</b>\n"
            f"CVE: <code>{_esc(finding.cve_id)}</code>\n"
            f"Run: <code>{_esc(result.command)}</code>\n"
            f"<i>Status stays open until you confirm externally.</i>",
            parse_mode="HTML",
        )
        return

    await query.edit_message_text(
        f"🔍 Fix applied — verifying…\n<code>{_esc(result.command)}</code>",
        parse_mode="HTML",
    )
    clean = await verify_fix(finding)

    if not clean:
        await query.edit_message_text(
            f"⚠️ <b>Pulled, but CVE still present upstream</b>\n"
            f"CVE: <code>{_esc(finding.cve_id)}</code> in "
            f"<code>{_esc(finding.package)}</code>\n"
            f"Command: <code>{_esc(result.command)}</code>\n"
            f"<i>Upstream hasn't patched yet — status stays open.</i>",
            parse_mode="HTML",
        )
        return

    set_status(finding_id, "fixed")
    # BC-011: attach "🔍 Verify service health" button when a probe policy exists.
    fixed_text = (
        f"✅ <b>Fixed and verified</b>\n"
        f"CVE: <code>{_esc(finding.cve_id)}</code> on "
        f"<code>{_esc(finding.host)}</code>\n"
        f"Command: <code>{_esc(result.command)}</code>\n"
        f"Re-scan: clean ✓"
    )
    probe = _resolve_probe_for(finding.source_ref)
    if probe is None:
        await query.edit_message_text(fixed_text, parse_mode="HTML")
        return
    verify_keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔍 Verify service health",
                    callback_data=f"verify:{finding_id}",
                )
            ]
        ]
    )
    fixed_text += "\n\n<i>Restart your container, then tap below to probe the service.</i>"
    await query.edit_message_text(fixed_text, parse_mode="HTML", reply_markup=verify_keyboard)


async def _handle_snooze(query, finding: Finding, finding_id: str, parts: list[str]) -> None:
    hours = int(parts[2]) if len(parts) > 2 else 4
    set_status(finding_id, "snoozed")
    await query.edit_message_text(
        f"⏰ Snoozed {hours}h — <code>{_esc(finding.cve_id)}</code>",
        parse_mode="HTML",
    )


async def _handle_ack(query, finding: Finding, finding_id: str, parts: list[str]) -> None:  # noqa: ARG001
    set_status(finding_id, "accepted")
    await query.edit_message_text(
        f"✅ Risk accepted — <code>{_esc(finding.cve_id)}</code>",
        parse_mode="HTML",
    )


async def _handle_verify(query, finding: Finding, finding_id: str, parts: list[str]) -> None:  # noqa: ARG001
    """BC-011: operator taps after restarting their service — run the configured probe."""
    if get_status(finding_id) == "fixed_verified":
        await query.edit_message_text(
            f"ℹ️ Already fixed_verified — <code>{_esc(finding.cve_id)}</code>",
            parse_mode="HTML",
        )
        return
    probe = _resolve_probe_for(finding.source_ref)
    if probe is None:
        await query.edit_message_text(
            f"⏭️ No probe configured for <code>{_esc(finding.source_ref)}</code>\n"
            f"<i>Set BEACONRY_HEALTH_PROBES to enable post-restart verification.</i>",
            parse_mode="HTML",
        )
        return
    await query.edit_message_text(
        f"⏳ Probing <code>{_esc(finding.source_ref)}</code> ({probe.probe_type}:{_esc(probe.probe_arg)})…",
        parse_mode="HTML",
    )
    # BC-011 fix: use the module-level _waterfall binding (dual-mode import
    # at the top of this file) rather than a fresh `from poc.hub.waterfall ...`
    # which fails inside the container (no `poc` package at /app).
    result = await _waterfall.run_probe(probe)
    if result.ok:
        set_status(finding_id, "fixed_verified")
        await query.edit_message_text(
            f"✅ <b>Service healthy — probe passed</b>\n"
            f"CVE: <code>{_esc(finding.cve_id)}</code>\n"
            f"Probe: <code>{_esc(probe.probe_type)}:{_esc(probe.probe_arg)}</code> → "
            f"{_esc(result.reason)}",
            parse_mode="HTML",
        )
        return
    set_status(finding_id, "verification_failed")
    base_name = finding.source_ref.split(":")[0]
    await query.edit_message_text(
        f"⚠️ <b>Probe failed:</b> <code>{_esc(result.reason)}</code>\n"
        f"CVE: <code>{_esc(finding.cve_id)}</code>\n"
        f"Rollback with: <code>docker tag {base_name}:rollback {_esc(finding.source_ref)}</code>\n"
        f"<i>Then restart the service and tap again.</i>",
        parse_mode="HTML",
    )


# Registry of callback-action handlers. Each handler takes (query, finding,
# finding_id, parts) and edits the message with its terminal state.
_ACTION_HANDLERS = {
    "fix": _handle_fix,
    "snooze": _handle_snooze,
    "ack": _handle_ack,
    "verify": _handle_verify,
}


async def on_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:  # noqa: ARG001
    """BC-003: Telegram callback dispatcher. Validates, loads the finding,
    checks idempotence, routes to the per-action handler."""
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()

    # BC-003: reject malformed callback_data before any DB access.
    if not CALLBACK_RE.match(query.data):
        log.warning("Rejected malformed callback_data: %r", query.data[:100])
        await query.edit_message_text("⚠️ Invalid request — malformed callback data.")
        return

    parts = query.data.split(":")
    action, finding_id = parts[0], parts[1]

    finding = load_finding(finding_id)
    if not finding:
        await query.edit_message_text("⚠️ Finding not found or already resolved.")
        return

    # BC-003: idempotence — if a Snooze/Accept tap lands on a finding already in
    # that state, show the current state and no-op the DB write.
    status_targets = {"snooze": "snoozed", "ack": "accepted"}
    if action in status_targets and get_status(finding_id) == status_targets[action]:
        await query.edit_message_text(
            f"ℹ️ Already {status_targets[action]} — <code>{_esc(finding.cve_id)}</code>",
            parse_mode="HTML",
        )
        return

    handler = _ACTION_HANDLERS.get(action)
    if handler is not None:
        await handler(query, finding, finding_id, parts)


# ── FastAPI + lifespan ────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(
    app: FastAPI,
):  # pragma: no cover - async Telegram startup/shutdown; exercised live via make demo + make test-e2e
    global tg_app, _startup_complete

    init_db()

    # BC-020: warm KEV in-memory from disk cache so the first scan already has
    # urgency scoring (even if the HTTP fetch is still pending). Also kicks off
    # the hourly refresh loop. Non-blocking: /readyz does NOT wait on KEV.
    _kev.warm_from_cache_if_any()
    _kev_task = asyncio.create_task(_kev.refresh_loop())

    # BC-011: parse policies once at startup; malformed → named error, do not boot.
    global HEALTH_POLICIES
    HEALTH_POLICIES = _waterfall.parse_policies(os.environ.get("BEACONRY_HEALTH_PROBES", ""))
    if HEALTH_POLICIES:
        log.info(
            "BC-011 health policies loaded: %s",
            {k: f"{v.probe_type}:{v.probe_arg}" for k, v in HEALTH_POLICIES.items()},
        )

    # BC-018: dial 1 — grype DB download. Skipped in MOCK or when the
    # dedicated BEACONRY_SKIP_GRYPE_DB flag is set (e.g., e2e stacks).
    if SKIP_GRYPE_DB:
        log.info("Skipping grype vulnerability database update (SKIP_GRYPE_DB=1)")
    else:
        log.info("Updating grype vulnerability database (first run: ~200MB download)…")
        proc = await asyncio.create_subprocess_exec(
            "grype",
            "db",
            "update",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            log.warning("grype db update warning: %s", stderr.decode()[:200])
        else:
            log.info("Grype vulnerability database ready")
    _startup_complete = True

    # BC-018: dial 2 — Telegram wiring. Driven by TELEGRAM_BOT_TOKEN presence.
    # Empty token (or MOCK=1 with no token) → skip. BEACONRY_TELEGRAM_BASE_URL
    # points python-telegram-bot at a mock service for e2e / dev-without-bot.
    if not TELEGRAM_BOT_TOKEN:
        log.info("TELEGRAM_BOT_TOKEN empty — skipping Telegram startup")
        yield
        _kev_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _kev_task
        return

    builder = Application.builder().token(TELEGRAM_BOT_TOKEN)
    if TELEGRAM_BASE_URL:
        log.info("BC-018: Telegram client pointed at %s", TELEGRAM_BASE_URL)
        builder = builder.base_url(f"{TELEGRAM_BASE_URL.rstrip('/')}/bot")
    _app = builder.build()
    _app.add_handler(CallbackQueryHandler(on_button_click))
    await _app.initialize()
    await _app.start()
    assert _app.updater is not None
    await _app.updater.start_polling()
    tg_app = _app
    log.info("Telegram bot polling started")

    # BC-035: register the Telegram notification adapter. Future adapters
    # (email, Reddit, SMS, …) register themselves here too, each gated by
    # its own BEACONRY_NOTIFIER_<NAME>_ENABLED env var.
    _register_adapter(
        TelegramAdapter(
            tg_app_getter=lambda: tg_app,
            chat_id_getter=lambda: TELEGRAM_CHAT_ID,
            formatters={
                "notification": format_notification,
                "regression": format_regression,
                "heartbeat": format_heartbeat,
                "digest": format_digest_body,
            },
            keyboard_builders={
                "finding": _kb_finding,
                "regression": _kb_regression,
                "heartbeat": _kb_finding,  # same actions as a first-fire
            },
            mock_renderer=_render_mock_telegram,
        )
    )

    # BC-035: periodic reminder loop for stale open findings.
    _heartbeat_task = asyncio.create_task(_heartbeat_loop_forever())

    yield

    # BC-020: cancel KEV refresh loop on shutdown.
    _kev_task.cancel()
    _heartbeat_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await _kev_task
    with contextlib.suppress(asyncio.CancelledError):
        await _heartbeat_task

    assert tg_app is not None
    assert tg_app.updater is not None
    await tg_app.updater.stop()
    await tg_app.stop()
    await tg_app.shutdown()


api = FastAPI(title="Beaconry Hub", version="0.1.0", lifespan=lifespan)


def _grype_db_dir() -> Path:
    return Path(os.environ.get("GRYPE_DB_CACHE_DIR", "/data/grype-db"))


def _telegram_check() -> str:
    if MOCK:
        return "mock"
    return "polling" if tg_app is not None else "not_running"


def _hub_readyz_providers() -> dict[str, str]:
    """Local prereq probes — must NOT include external services (restart-loop trap)."""
    return {
        "db": check_db(DB_PATH),
        "grype_db": check_dir_populated(_grype_db_dir()),
        "grype_binary": check_binary("grype"),
    }


def _hub_healthz_summary() -> dict:
    kev_summary = _kev.freshness()
    checks = {
        **_hub_readyz_providers(),
        "telegram": _telegram_check(),
        "last_scan_ts": _last_scan_ts,
        # BC-020: KEV freshness. Status is "ok" | "stale" | "unfetched".
        **kev_summary,
    }
    # KEV is non-fatal — stale/unfetched surface in /healthz but don't flip "degraded".
    bool_checks = [
        v
        for k, v in checks.items()
        if k not in ("last_scan_ts", "kev", "kev_last_fetch_s", "kev_count")
    ]
    degraded = any(v not in ("ok", "polling", "mock") for v in bool_checks)
    return {
        "status": "degraded" if degraded else "ok",
        "version": HUB_VERSION,
        "checks": checks,
    }


api.include_router(
    build_router(
        livez_provider=lambda: "alive",
        readyz_providers=_hub_readyz_providers,
        startup_flag_getter=lambda: _startup_complete,
        healthz_summary_getter=_hub_healthz_summary,
    )
)


@api.get("/health")
async def health(response: _FastAPIResponse) -> dict:
    """Legacy alias — kept as a transitional compat endpoint for pre-BC-010
    monitors. Delegates to the same readyz logic via the shared provider.
    """
    checks = _hub_readyz_providers()
    failed = [k for k, v in checks.items() if v != "ok"]
    if failed:
        response.status_code = 503
        return {"status": "not_ready"}
    return {"status": "ready"}


def dedup_and_cap(findings: list[Finding], cap: int) -> tuple[list[Finding], list[Finding]]:
    """Deduplicate by cve_id (keep highest-CVSS variant), rank by (tier,-cvss),
    return (top_cap, suppressed_rest)."""
    best: dict[str, Finding] = {}
    for f in findings:
        existing = best.get(f.cve_id)
        if existing is None or f.cvss > existing.cvss:
            best[f.cve_id] = f
    unique = sorted(best.values(), key=lambda f: (f.tier, -f.cvss))
    return unique[:cap], unique[cap:]


def format_digest_body(suppressed: list[Finding]) -> str:
    """BC-022 digest render — one HTML blob summarising cap-suppressed findings.
    Pure render; sending is the adapter's job."""
    import html

    lines = [f"📋 <b>{len(suppressed)} additional finding(s) suppressed this run</b>"]
    kev_count = sum(1 for f in suppressed if f.kev)
    if kev_count:
        lines.append(
            f"⚠️ <b>{kev_count} suppressed finding(s) are KEV</b> — "
            f"raise <code>BEACONRY_MAX_FINDINGS_PER_RUN</code> to see them individually."
        )
    for f in suppressed[:10]:
        prefix = "🚨 " if f.kev else ""
        lines.append(
            f"• {prefix}<code>{html.escape(f.cve_id)}</code> {f.severity} "
            f"CVSS {f.cvss:.1f} — <code>{html.escape(f.package)}</code>"
        )
    if len(suppressed) > 10:
        lines.append(f"… and {len(suppressed) - 10} more")
    lines.append("\n<i>Raise MIN_CVSS or lower BEACONRY_MAX_FINDINGS_PER_RUN to tune.</i>")
    return "\n".join(lines)


def _digest_hash(suppressed: list[Finding]) -> str:
    """BC-036: stable content hash of a suppressed set — sorted by finding id so
    order doesn't matter, truncated like `make_id` for consistency."""
    joined = ":".join(sorted(f.id for f in suppressed))
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def should_send_digest(host: str, suppressed: list[Finding], *, now: int) -> tuple[bool, str, str]:
    """BC-036: decide whether to send a digest for `host` at `now`.

    Returns (should_send, content_hash, reason). Reason strings are stable
    identifiers emitted to the audit log for forensics.

    Policy:
      - empty suppressed → never send (BC-022 preserved)
      - DIGEST_INTERVAL_S == 0 → always send (pre-BC-036 opt-out)
      - no prior send for this host → send (first_fire)
      - content hash differs from last send → send (content_changed)
      - interval elapsed since last send → send (interval_elapsed)
      - otherwise → throttled
    """
    content_hash = _digest_hash(suppressed)
    if not suppressed:
        return False, content_hash, "empty"
    if DIGEST_INTERVAL_S == 0:
        return True, content_hash, "throttle_disabled"
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute(
            "SELECT last_sent_ts, last_hash FROM digest_state WHERE host=?", (host,)
        ).fetchone()
    finally:
        con.close()
    if row is None:
        return True, content_hash, "first_fire"
    last_sent_ts, last_hash = row
    if last_hash != content_hash:
        return True, content_hash, "content_changed"
    if now - last_sent_ts >= DIGEST_INTERVAL_S:
        return True, content_hash, "interval_elapsed"
    return False, content_hash, "throttled"


def record_digest_sent(host: str, suppressed: list[Finding], *, now: int) -> None:
    """BC-036: persist last-sent state + emit `digest_sent` audit event."""
    content_hash = _digest_hash(suppressed)
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(
            "INSERT INTO digest_state (host, last_sent_ts, last_hash) VALUES (?,?,?) "
            "ON CONFLICT(host) DO UPDATE SET last_sent_ts=excluded.last_sent_ts, "
            "last_hash=excluded.last_hash",
            (host, now, content_hash),
        )
        con.commit()
    finally:
        con.close()
    _audit.append("digest_sent", host=host, hash=content_hash, count=len(suppressed))


def record_digest_throttled(host: str, suppressed: list[Finding], *, now: int, reason: str) -> None:
    """BC-036: emit `digest_throttled` audit event. No DB write — nothing changed."""
    _audit.append(
        "digest_throttled",
        host=host,
        hash=_digest_hash(suppressed),
        count=len(suppressed),
        reason=reason,
    )


async def send_digest(suppressed: list[Finding]) -> None:
    """BC-035: thin wrapper — all enabled notification adapters receive the digest."""
    if not suppressed:
        return
    await _dispatch_digest(suppressed)


async def heartbeat_sweep(*, now_ts: int | None = None) -> int:
    """BC-035: scan the DB for stale open findings and emit a reminder for each.

    Returns the number of reminders sent. No-op when HEARTBEAT_ENABLED is False.
    `now_ts` injected for BC-038 scenarios; defaults to real clock.

    Qualifying criteria for a reminder:
      - status='open' (snoozed/accepted/fixed/regressed/fixed_verified skipped)
      - last_seen_ts > now - HEARTBEAT_LIVENESS_WINDOW_S (still in inventory;
        no point reminding about findings scout no longer sees — those will
        auto-close via BC-031 reconcile)
      - last_notified_ts < now - HEARTBEAT_INTERVAL_S (not recently poked)

    Writes `last_notified_ts = now` for each reminded finding so the next
    sweep doesn't re-poke until the interval elapses again. Audit event
    `heartbeat_reminder_sent` emitted per reminder (BC-029)."""
    if not HEARTBEAT_ENABLED:
        return 0
    con = sqlite3.connect(DB_PATH)
    try:
        now = now_ts if now_ts is not None else int(time.time())
        liveness_cutoff = now - HEARTBEAT_LIVENESS_WINDOW_S
        poke_cutoff = now - HEARTBEAT_INTERVAL_S
        rows = con.execute(
            "SELECT id, host, cve_id, package, version, fixed_version, severity, "
            "cvss, source_type, source_ref, description, tier, kev, found_at, "
            "last_notified_ts FROM findings "
            "WHERE status='open' AND last_seen_ts > ? "
            "AND (last_notified_ts = 0 OR last_notified_ts < ?)",
            (liveness_cutoff, poke_cutoff),
        ).fetchall()
    except sqlite3.OperationalError:
        con.close()
        return 0

    sent = 0
    for row in rows:
        finding = Finding(
            id=row[0],
            host=row[1],
            cve_id=row[2],
            package=row[3],
            version=row[4],
            fixed_version=row[5],
            severity=row[6],
            cvss=row[7],
            source_type=row[8],
            source_ref=row[9],
            description=row[10] or "",
            tier=row[11],
            kev=bool(row[12]),
        )
        open_since = row[13] or now
        try:
            await _dispatch_heartbeat(finding, open_since)
        except Exception as e:  # noqa: BLE001
            log.warning("heartbeat notify failed for %s: %s", finding.cve_id, e)
            continue
        con.execute(
            "UPDATE findings SET last_notified_ts = ? WHERE id = ?",
            (now, finding.id),
        )
        days_open = max(1, (now - open_since) // 86400)
        _audit.append(
            "heartbeat_reminder_sent",
            finding_id=finding.id,
            cve_id=finding.cve_id,
            days_open=days_open,
        )
        sent += 1
    con.commit()
    con.close()
    if sent:
        log.info("BC-035: heartbeat sweep sent %d reminder(s)", sent)
    return sent


async def _heartbeat_loop_forever() -> (
    None
):  # pragma: no cover - infinite async loop; tested via sweep directly
    while True:
        try:
            await heartbeat_sweep()
        except Exception as e:  # noqa: BLE001
            log.warning("BC-035: heartbeat sweep failed: %s", e)
        await asyncio.sleep(HEARTBEAT_SWEEP_INTERVAL_S)


def _mark_scan_now() -> None:
    """BC-010: bump the last-scan timestamp surfaced via /healthz."""
    global _last_scan_ts
    _last_scan_ts = int(time.time())


@api.post("/scan")
async def receive_scan(payload: dict) -> dict:
    """BC-005: apply CVE dedup + per-run cap across the whole inventory before notifying.

    Previously each SBOM's findings were notified independently, flooding the
    operator (10 images × 15 CVEs = 150 messages). Now the handler collects
    candidates across every SBOM, deduplicates by CVE id (keeping the highest
    CVSS variant), ranks by (tier, -cvss), notifies the top
    BEACONRY_MAX_FINDINGS_PER_RUN (default 5), and sends a single digest for
    the rest.
    """
    _mark_scan_now()
    host = payload.get("host", "unknown")
    sboms: list[dict] = payload.get("sboms", [])
    # BC-030: optional per-image exposure map from scout — absent on older scouts.
    port_exposure: dict[str, bool] = payload.get("container_exposure") or {}
    exposed_count = sum(1 for v in port_exposure.values() if v)
    log.info(
        "Scan from %s: %d SBOM(s), %d exposed image(s)",
        host,
        len(sboms),
        exposed_count,
    )
    # BC-042: audit every scan reception, even if 0 candidates. Closes the
    # "did scout even post?" blind spot — a silent scout and a scout-with-0
    # candidates were indistinguishable in the audit log pre-BC-042.
    _audit.append(
        "scan_received",
        host=host,
        sbom_count=len(sboms),
        exposed_count=exposed_count,
    )

    candidates: list[Finding] = []
    for entry in sboms:
        try:
            candidates.extend(await process_sbom_entry(entry, host, port_exposure=port_exposure))
        except Exception as e:  # noqa: BLE001
            log.error("Error processing SBOM entry: %s", e)

    return await _ingest_candidates(host, candidates, now_ts=int(time.time()))


async def _ingest_candidates(
    host: str,
    candidates: list[Finding],
    *,
    now_ts: int,
) -> dict:
    """BC-038: extracted ingest flow — dedup+cap, save+notify, digest, reconcile.

    Shared between `receive_scan` (real path, invokes `process_sbom_entry` first)
    and `/test/scan` (test path, candidates are pre-matched). `now_ts` is
    injected so scenarios can simulate clock advancement without real sleeps.
    """
    cap = int(os.environ.get("BEACONRY_MAX_FINDINGS_PER_RUN", "5"))
    to_notify, suppressed = dedup_and_cap(candidates, cap)
    log.info(
        "Scan summary: %d candidate(s), %d unique CVE(s), notifying top %d, suppressing %d",
        len(candidates),
        len(to_notify) + len(suppressed),
        len(to_notify),
        len(suppressed),
    )

    new_findings = 0
    for f in to_notify:
        if not save_finding(f, now_ts=now_ts):
            continue
        log.info(
            "New finding: %s (%s CVSS %.1f) in %s",
            f.cve_id,
            f.severity,
            f.cvss,
            f.source_ref,
        )
        try:
            await notify(f)
        except Exception as e:  # noqa: BLE001 — one bad notify must not kill the loop
            log.warning("notify failed for %s: %s", f.cve_id, e)
            continue
        new_findings += 1
        await asyncio.sleep(0.3)  # mild rate-limiting vs. Telegram flood control

    # BC-036: throttle digests. First-fires above are unaffected; this gate
    # is strictly digest-local. See `should_send_digest` for the policy.
    if suppressed:
        should_fire, _hash, reason = should_send_digest(host, suppressed, now=now_ts)
        if should_fire:
            await send_digest(suppressed)
            record_digest_sent(host, suppressed, now=now_ts)
        else:
            record_digest_throttled(host, suppressed, now=now_ts, reason=reason)

    # BC-031: reconcile this scan against DB — auto-close findings no longer
    # present, raise 🔥 FIX REGRESSED alarms for findings that returned after
    # being marked fixed. Fire-and-forget: a failed notification must not
    # block the scan response.
    scanned_ids = {c.id for c in candidates}
    # BC-034: bookkeeping — always, regardless of RECONCILE_ENABLED.
    bump_last_seen(host, scanned_ids, now_ts=now_ts)
    regressed, auto_closed = reconcile_scan(host, scanned_ids)
    if regressed:
        log.warning("BC-031: %d regression(s) detected on %s", len(regressed), host)
    for f in regressed:
        # Load the last_fixed_ts from the DB (set_status wrote it on the fix).
        con = sqlite3.connect(DB_PATH)
        row = con.execute("SELECT last_fixed_ts FROM findings WHERE id=?", (f.id,)).fetchone()
        con.close()
        last_fixed_ts = (row[0] if row else 0) or 0
        try:
            await notify_regression(f, last_fixed_ts)
        except Exception as e:  # noqa: BLE001
            log.warning("notify_regression failed for %s: %s", f.cve_id, e)
    if auto_closed:
        log.info("BC-031: %d auto-close(s) on %s", len(auto_closed), host)

    return {
        "status": "ok",
        "findings": new_findings,
        "regressed": len(regressed),
        "auto_closed": len(auto_closed),
    }


# ── BC-038: scenario-harness test-mode endpoints ─────────────────────────────
#
# Registered at import time ONLY when `BEACONRY_TEST_MODE=1`. When the flag
# is off (production default), these routes are not attached — requests to
# `/test/*` return 404 Not Found.
#
# Deliberate design: route registration is the gate, not a per-request check.
# A grep for "@api.post" shows exactly which endpoints are addressable; a
# future `/ultrareview` lint can enforce that no un-gated route starts with
# `/test/` (tracked in docs/plan/IDEAS.md).

if TEST_MODE:
    log.warning("BC-038: BEACONRY_TEST_MODE=1 — /test/* endpoints are LIVE on this hub")

    @api.post("/test/wipe")
    async def test_wipe() -> dict:
        """BC-038: reset DB + audit log so each scenario starts clean."""
        if DB_PATH.exists():
            DB_PATH.unlink()
        audit_path = DATA_DIR / "audit.log"
        if audit_path.exists():
            audit_path.unlink()
        init_db()
        return {"status": "wiped"}

    @api.post("/test/scan")
    async def test_scan(payload: dict) -> dict:
        """BC-038: ingest pre-matched candidates, bypassing grype.

        Body shape:
            {
              "host": "h1",
              "candidates": [{"cve_id": "CVE-A", "cvss": 9.5, "severity": "Critical",
                              "package": "openssl", "version": "1.0",
                              "fixed_version": "1.1", "source_type": "docker",
                              "source_ref": "nginx:1.24", "description": "x",
                              "kev": false}],
              "port_exposure": {"nginx:1.24": true},   # optional
              "at": 1700000000                         # optional (defaults to now)
            }
        """
        host = payload.get("host", "scenario-host")
        port_exposure: dict[str, bool] = payload.get("port_exposure") or {}
        at = payload.get("at") or int(time.time())

        candidates: list[Finding] = []
        for c in payload.get("candidates", []):
            f = Finding(
                id=make_id(host, c["cve_id"], c["source_ref"], c["package"]),
                host=host,
                cve_id=c["cve_id"],
                package=c["package"],
                version=c["version"],
                fixed_version=c.get("fixed_version", "not available"),
                severity=c.get("severity", "High"),
                cvss=c["cvss"],
                source_type=c.get("source_type", "docker"),
                source_ref=c["source_ref"],
                description=c.get("description", ""),
                kev=bool(c.get("kev", False)),
            )
            # Honour the toggles the same way `process_sbom_entry` does.
            is_if = INTERNET_FACING_DETECTION_ENABLED and bool(
                port_exposure.get(f.source_ref, False)
            )
            f.internet_facing = is_if
            f.tier = _urgency.classify_tier(f, kev=f.kev, internet_facing=is_if)
            candidates.append(f)

        return await _ingest_candidates(host, candidates, now_ts=at)

    @api.post("/test/sweep")
    async def test_sweep(payload: dict) -> dict:
        """BC-038: invoke heartbeat_sweep with an injected `now` timestamp."""
        at = payload.get("at") or int(time.time())
        sent = await heartbeat_sweep(now_ts=at)
        return {"sent": sent, "at": at}

    @api.post("/test/set_status")
    async def test_set_status(payload: dict) -> dict:
        """BC-038: set a finding's status without going through the Telegram
        callback path. Scenarios use this to simulate `operator snoozed` /
        `operator accepted` / `operator fixed`. Must resolve the finding by
        (host, cve_id) since scenarios pin those but not the hashed `id`."""
        host = payload["host"]
        cve_id = payload["cve_id"]
        status = payload["status"]
        con = sqlite3.connect(DB_PATH)
        try:
            row = con.execute(
                "SELECT id FROM findings WHERE host=? AND cve_id=?", (host, cve_id)
            ).fetchone()
        finally:
            con.close()
        if row is None:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=404, detail=f"no finding for host={host} cve_id={cve_id}"
            )
        set_status(row[0], status)
        return {"status": "ok", "finding_id": row[0]}


if __name__ == "__main__":
    uvicorn.run("hub:api", host="0.0.0.0", port=8000, log_level="info")
