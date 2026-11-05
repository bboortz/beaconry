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
from fastapi import Depends, FastAPI, HTTPException
from fastapi import Request as _FastAPIRequest
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
    from notifiers import notify_auto_fix_regression as _dispatch_auto_fix_regression
    from notifiers import notify_digest as _dispatch_digest
    from notifiers import notify_finding as _dispatch_finding
    from notifiers import notify_heartbeat as _dispatch_heartbeat
    from notifiers import notify_regression as _dispatch_regression
    from notifiers import notify_snooze_expired as _dispatch_snooze_expired
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
    from poc.hub.notifiers import (
        notify_auto_fix_regression as _dispatch_auto_fix_regression,
    )
    from poc.hub.notifiers import notify_digest as _dispatch_digest
    from poc.hub.notifiers import notify_finding as _dispatch_finding
    from poc.hub.notifiers import notify_heartbeat as _dispatch_heartbeat
    from poc.hub.notifiers import notify_regression as _dispatch_regression
    from poc.hub.notifiers import notify_snooze_expired as _dispatch_snooze_expired
    from poc.hub.notifiers import register as _register_adapter

# BC-087: operator-controlled root log level. DEBUG re-enables BC-086's
# per-finding reconcile trace. Unknown values silently fall back to INFO
# (warning emitted below once the logger exists).
_LOG_LEVEL_RAW = os.environ.get("BEACONRY_LOG_LEVEL", "INFO").upper()
_LOG_LEVEL = getattr(logging, _LOG_LEVEL_RAW, None)
if not isinstance(_LOG_LEVEL, int):
    _LOG_LEVEL = logging.INFO
logging.basicConfig(
    level=_LOG_LEVEL,
    format="%(asctime)s [hub] %(levelname)s %(message)s",
)
log = logging.getLogger("hub")
if os.environ.get("BEACONRY_LOG_LEVEL") and _LOG_LEVEL_RAW not in {
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "CRITICAL",
}:
    log.warning("BEACONRY_LOG_LEVEL=%r invalid; falling back to INFO", _LOG_LEVEL_RAW)

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
# BC-086: number of consecutive absent-from-scan cycles before a finding
# flips to the `auto_fixed` status. 3 is conservative — one glitchy scan
# won't trigger auto-close. Set to 1 to reproduce pre-BC-086 behaviour
# (immediate close on first absence).
AUTO_FIX_THRESHOLD = max(1, int(os.environ.get("BEACONRY_AUTO_FIX_THRESHOLD", "3")))


def _parse_snooze_hours(raw: str) -> list[int]:
    """BC-089: parse `BEACONRY_SNOOZE_DEFAULT_HOURS=1,4,24,168` → [1, 4, 24, 168].
    Drops non-integer tokens silently; result clamped to ≥1 and deduped
    while preserving operator-chosen order. Empty → [1, 4, 24, 168] default."""
    if not raw.strip():
        return [1, 4, 24, 168]
    out: list[int] = []
    seen: set[int] = set()
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            n = max(1, int(tok))
        except ValueError:
            continue
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out or [1, 4, 24, 168]


# BC-089: snooze button no longer lies. Three knobs:
#   - SNOOZE_DEFAULT_HOURS — comma-separated hours rendered as [⏰ Nh]
#     buttons on every snooze-eligible notification keyboard.
#   - SNOOZE_SWEEP_INTERVAL_S — cadence of the auto-expiry task.
#     Precision-vs-load tradeoff; worst-case drift is this value.
#   - SNOOZE_NOTIFY_ON_EXPIRE — whether to Telegram-ping on expiry.
#     Set to 0 for silent re-open (audit still fires).
SNOOZE_DEFAULT_HOURS = _parse_snooze_hours(os.environ.get("BEACONRY_SNOOZE_DEFAULT_HOURS", ""))
SNOOZE_SWEEP_INTERVAL_S = max(5, int(os.environ.get("BEACONRY_SNOOZE_SWEEP_INTERVAL_S") or "60"))
SNOOZE_NOTIFY_ON_EXPIRE = os.environ.get("BEACONRY_SNOOZE_NOTIFY_ON_EXPIRE", "1") != "0"
# BC-090: operator can opt out of the distinct auto-fix regression Telegram
# ping while keeping the audit trail. Rare escape hatch for operators who
# accept the BC-086 silent-close policy and don't want a loud alert on the
# bounce-back. Default on (loud). Separate from per-adapter
# BEACONRY_NOTIFIER_<NAME>_ENABLED and the per-snooze-kind BC-089 toggle.
AUTO_FIX_REGRESSION_NOTIFY_ENABLED = (
    os.environ.get("BEACONRY_AUTO_FIX_REGRESSION_NOTIFY_ENABLED", "1") != "0"
)
# BC-092: canary ring gate. Default ON (gate enforced) — production fixes
# require a staging-verified precedent for the same CVE. Operator opt-out:
# BEACONRY_CANARY_RING_ENABLED=0 disables the gate globally; operators with
# no staging host must either opt out or explicitly flag at least one host
# as staging.
CANARY_RING_ENABLED = os.environ.get("BEACONRY_CANARY_RING_ENABLED", "1") != "0"
# Emergency escape: force-allow a single production fix (audited distinctly).
# Operator sets to 1 before tapping, resets to 0 after. Not a long-standing
# posture — audit log records every force-allowed fix with its CVE + host.
CANARY_PRODUCTION_FORCE_ALLOW = os.environ.get("BEACONRY_CANARY_PRODUCTION_FORCE_ALLOW", "0") == "1"
# BC-092: valid host roles for canary ordering.
VALID_HOST_ROLES = frozenset({"workstation", "staging", "production"})


def _normalize_role(raw: str | None) -> str:
    """BC-092: clamp a scout-provided role to the valid set. Invalid /
    missing → 'workstation'. Keeps production-level gating conservative —
    a misconfigured scout defaults to the least-privileged ring."""
    if not raw:
        return "workstation"
    cleaned = raw.strip().lower()
    if cleaned not in VALID_HOST_ROLES:
        return "workstation"
    return cleaned


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
# BC-065: operator opt-out for the content-change bypass. BC-036 fires the
# digest whenever the suppressed-set hash changes, which short-circuits the
# interval throttle in noisy homelabs (new CVE rotates the capped tail →
# digest every scan). Set to 0 to require the interval to actually elapse.
# Default 1 preserves pre-BC-065 behaviour for existing deployments.
DIGEST_ON_CONTENT_CHANGE = os.environ.get("BEACONRY_DIGEST_ON_CONTENT_CHANGE", "1") != "0"
# BC-038: scenario harness toggle. When set, `/test/*` endpoints are registered
# on the FastAPI app. These endpoints accept pre-matched candidates + an
# injected `now` timestamp so scenarios can exercise the full ingest flow
# without real grype, real scout, or real clock drift. When OFF, the routes
# are not registered at all — requests to them return 404. Must remain OFF
# in production operator stacks; env-gate only (hub does not police misuse).
TEST_MODE = os.environ.get("BEACONRY_TEST_MODE") == "1"

DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "beaconry.db"

# BC-066: executor is a single policy choice, not per-finding dispatch.
# Default `manual` keeps the POC safe on arbitrary infrastructure — operator
# does the work, hub logs intent and re-reminds. Opt in to `docker` for
# auto-pull on homogeneous stacks.
EXECUTOR_NAME = os.environ.get("BEACONRY_EXECUTOR", "manual").lower()
_executor_registry: dict[str, ExecutorPlugin] = {
    "manual": ManualAdapter(),
    "docker": DockerAdapter(),
}
EXECUTOR: ExecutorPlugin = _executor_registry.get(EXECUTOR_NAME, ManualAdapter())
# BC-066: reminder cadence when operator claimed manual-fix. 0 disables.
MANUAL_REMINDER_INTERVAL_S = _int_env("BEACONRY_MANUAL_REMINDER_INTERVAL_S", 3600)

# Legacy export — any external callers still iterating EXECUTORS get the
# single active instance. New code should read EXECUTOR directly.
EXECUTORS: list[ExecutorPlugin] = [EXECUTOR]

tg_app: Application | None = None

# BC-010: health-check state. `_startup_complete` flips True once lifespan has
# finished grype DB update (even if update warned). `_last_scan_ts` is updated
# by receive_scan so /healthz can report liveness-in-practice.
_startup_complete: bool = False
_last_scan_ts: int = 0
# BC-067: per-host rolling SBOM-count baseline. In-memory only (resets on
# hub restart; good enough — a partial scan right after restart is
# survivable, the harmful case is "hub up for days, scout glitches
# briefly"). Max-of-seen so a one-off big scan doesn't permanently
# raise the threshold.
_scout_sbom_baseline: dict[str, int] = {}
# A scan whose sbom_count is < this fraction of the baseline is treated
# as a partial inventory and skipped for reconcile. 0.5 = tolerate up to
# half the normal count missing before suspecting a scout glitch.
_PARTIAL_INVENTORY_THRESHOLD: float = 0.5


def _is_partial_inventory(host: str, sbom_count: int | None) -> bool:
    """BC-067: True iff the scan looks like a partial inventory and
    reconcile should be skipped. Returns False when:
      - caller didn't pass a count (scenario/test path — treat as full)
      - no baseline yet for this host (first scan — treat as full)
      - count is at least PARTIAL_INVENTORY_THRESHOLD × baseline
    """
    if sbom_count is None:
        return False
    baseline = _scout_sbom_baseline.get(host)
    if baseline is None or baseline <= 0:
        return False
    return sbom_count < baseline * _PARTIAL_INVENTORY_THRESHOLD


def _update_scout_sbom_baseline(host: str, sbom_count: int | None) -> None:
    """BC-067: bump the rolling max for this host. Only moves up — a scout
    that temporarily shrinks (one container removed) doesn't lower the
    partial-inventory threshold for the next scan."""
    if sbom_count is None or sbom_count <= 0:
        return
    current = _scout_sbom_baseline.get(host, 0)
    if sbom_count > current:
        _scout_sbom_baseline[host] = sbom_count


# BC-062: software/API/schema versions. Software is read from the single
# `VERSION` file at repo root (or /app/VERSION in the container). API +
# schema are integers bumped on breaking changes.
try:
    from beaconry_health import SOFTWARE_VERSION
except ImportError:
    from poc.beaconry_health import SOFTWARE_VERSION

HUB_VERSION = SOFTWARE_VERSION  # back-compat alias
API_VERSION = 1
EXPECTED_SCHEMA_VERSION = 7
STRICT_API = os.environ.get("BEACONRY_STRICT_API") == "1"


class SchemaTooNew(RuntimeError):
    """BC-062: raised by init_db when PRAGMA user_version exceeds
    EXPECTED_SCHEMA_VERSION. The hub refuses to boot — running old code
    against newer data is a silent-corruption hazard.
    """


# BC-011: policy-driven probe resolver. Parsed once at startup (fail-fast on
# malformed input) and read by on_button_click's `verify` action.
HEALTH_POLICIES: dict[str, "_waterfall.Probe"] = {}


def _resolve_probe_for(image_ref: str) -> "_waterfall.Probe | None":
    """Thin wrapper so tests can monkeypatch at the hub level."""
    return _waterfall.resolve_probe(image_ref, HEALTH_POLICIES)


# ── Database ──────────────────────────────────────────────────────────────────


FLEET_ACTIVE_WINDOW_S = int(os.environ.get("BEACONRY_FLEET_ACTIVE_WINDOW_S", "600"))
FLEET_DEAD_AFTER_S = int(os.environ.get("BEACONRY_FLEET_DEAD_AFTER_S", "86400"))


def _fleet_status(last_posted_ts: int, now: int) -> str:
    """BC-061: liveness state from the time since the scout's last POST."""
    age = now - last_posted_ts
    if age <= FLEET_ACTIVE_WINDOW_S:
        return "active"
    if age <= FLEET_DEAD_AFTER_S:
        return "stale"
    return "dead"


def record_scout_post(  # noqa: PLR0913 — BC-092 added role; grew over time with lifecycle
    host: str,
    *,
    now: int,
    sbom_count: int,
    software_version: str | None = None,
    api_version: int | None = None,
    health: str | None = None,
    bump_scan_ts: bool = True,
    role: str | None = None,
) -> None:
    """BC-061/BC-063: upsert a scouts-registry row on every scout POST.

    First-time POST registers the scout. Repeat POSTs bump
    `last_posted_ts` + `last_sbom_count`. `first_seen_ts` is only set
    on INSERT, never updated. BC-063: `software_version` /
    `api_version` / `last_health` are updated on every POST so the
    FLEET view reflects the current scout, not whatever it was when
    it first registered. `bump_scan_ts=False` (heartbeat POSTs) keeps
    `last_scan_ts` untouched so FLEET's LAST SCAN column reflects
    actual scan activity — not the last keepalive.

    BC-092: `role` (workstation|staging|production) is read from the
    scout's heartbeat / scan payload. Missing / invalid values clamp
    to `workstation`. The INSERT path writes the role explicitly;
    the ON CONFLICT UPDATE path writes it too so a scout that changes
    its role via env + restart propagates on the next POST.

    Tolerant of pre-BC-061 DBs (no scouts table yet) so test fixtures
    that skip init_db don't break.
    """
    scan_ts = now if bump_scan_ts else None
    effective_role = _normalize_role(role)
    con = sqlite3.connect(DB_PATH)
    try:
        with contextlib.suppress(sqlite3.OperationalError):
            if bump_scan_ts:
                con.execute(
                    "INSERT INTO scouts (host, first_seen_ts, last_posted_ts, "
                    "  last_sbom_count, software_version, api_version, last_health, "
                    "  last_scan_ts, role) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(host) DO UPDATE SET "
                    "  last_posted_ts = excluded.last_posted_ts, "
                    "  last_sbom_count = excluded.last_sbom_count, "
                    "  software_version = excluded.software_version, "
                    "  api_version = excluded.api_version, "
                    "  last_health = excluded.last_health, "
                    "  last_scan_ts = excluded.last_scan_ts, "
                    "  role = excluded.role",
                    (
                        host,
                        now,
                        now,
                        sbom_count,
                        software_version,
                        api_version,
                        health,
                        scan_ts,
                        effective_role,
                    ),
                )
            else:
                # Heartbeat path: leave last_scan_ts + last_sbom_count untouched.
                con.execute(
                    "INSERT INTO scouts (host, first_seen_ts, last_posted_ts, "
                    "  last_sbom_count, software_version, api_version, last_health, role) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(host) DO UPDATE SET "
                    "  last_posted_ts = excluded.last_posted_ts, "
                    "  software_version = excluded.software_version, "
                    "  api_version = excluded.api_version, "
                    "  last_health = excluded.last_health, "
                    "  role = excluded.role",
                    (host, now, now, 0, software_version, api_version, health, effective_role),
                )
            con.commit()
    finally:
        con.close()


def init_db() -> None:
    con = sqlite3.connect(DB_PATH)
    # BC-062: schema-version bookkeeping. SQLite's built-in PRAGMA
    # user_version is zero for fresh DBs and pre-BC-062 DBs; we migrate
    # both forward. Refuse to boot if the DB is at a higher version
    # than this code supports — running old code against newer data is
    # a silent-corruption hazard.
    current_schema = con.execute("PRAGMA user_version").fetchone()[0]
    if current_schema > EXPECTED_SCHEMA_VERSION:
        con.close()
        raise SchemaTooNew(
            f"beaconry DB is at schema version {current_schema} but this hub "
            f"supports up to {EXPECTED_SCHEMA_VERSION}. Upgrade the hub binary "
            f"or restore an older DB."
        )
    # BC-061: scouts registry — one row per host that has ever POSTed to
    # /scan. Enables /fleet to show zero-finding scouts and apply
    # liveness status (active / stale / dead).
    # BC-063: three nullable columns for per-scout metadata — scout
    # version, API contract version, and last-reported health. Fresh
    # DBs create them inline; v2 DBs get them via ALTER TABLE below.
    con.execute("""
        CREATE TABLE IF NOT EXISTS scouts (
            host             TEXT PRIMARY KEY,
            first_seen_ts    INTEGER NOT NULL,
            last_posted_ts   INTEGER NOT NULL,
            last_sbom_count  INTEGER NOT NULL DEFAULT 0,
            software_version TEXT,
            api_version      INTEGER,
            last_health      TEXT,
            last_scan_ts     INTEGER,
            role             TEXT NOT NULL DEFAULT 'workstation'
        )
    """)
    # BC-063 migration v2 → v3: PRAGMA table_info drives idempotency so a
    # fresh DB (columns already present via CREATE TABLE above) and a
    # repeat migration are both no-ops. `last_scan_ts` is distinct from
    # `last_posted_ts`: heartbeat POSTs bump only the latter.
    # BC-092 v6 → v7: `role` column for canary-ring gate; legacy rows get
    # `workstation` default so pre-BC-092 scouts stay unaffected.
    existing_cols = {row[1] for row in con.execute("PRAGMA table_info(scouts)").fetchall()}
    for col, decl in (
        ("software_version", "TEXT"),
        ("api_version", "INTEGER"),
        ("last_health", "TEXT"),
        ("last_scan_ts", "INTEGER"),
        ("role", "TEXT NOT NULL DEFAULT 'workstation'"),
    ):
        if col not in existing_cols:
            con.execute(f"ALTER TABLE scouts ADD COLUMN {col} {decl}")
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
            internet_facing INTEGER DEFAULT 0,
            cve_alias TEXT,
            last_claim_ts INTEGER DEFAULT 0,
            absent_count INTEGER DEFAULT 0,
            auto_fixed_ts INTEGER DEFAULT 0,
            snooze_until_ts INTEGER DEFAULT 0
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
    # BC-043: CVE alias for non-CVE primary ids (NULL by default; no backfill
    # because the related-id data isn't preserved in-schema for legacy rows).
    with contextlib.suppress(sqlite3.OperationalError):
        con.execute("ALTER TABLE findings ADD COLUMN cve_alias TEXT")
    # BC-066: last_claim_ts set when the operator taps [Fix now] under the
    # manual executor — records the intent, drives the 1h reminder sweep.
    # NULL / 0 = no outstanding claim; manual_claim_sweep ignores the row.
    with contextlib.suppress(sqlite3.OperationalError):
        con.execute("ALTER TABLE findings ADD COLUMN last_claim_ts INTEGER DEFAULT 0")
    # BC-086: counter + timestamp for the new `auto_fixed` status. absent_count
    # increments on each scan where this finding is absent; when it reaches
    # BEACONRY_AUTO_FIX_THRESHOLD the status flips to `auto_fixed` with
    # auto_fixed_ts recording when. BC-031 previously closed `open|regressed`
    # on first absence; BC-086 extends that to `snoozed|accepted` AND adds
    # the counter-based delay so transient scan glitches don't silently
    # resolve long-lived findings.
    with contextlib.suppress(sqlite3.OperationalError):
        con.execute("ALTER TABLE findings ADD COLUMN absent_count INTEGER DEFAULT 0")
    with contextlib.suppress(sqlite3.OperationalError):
        con.execute("ALTER TABLE findings ADD COLUMN auto_fixed_ts INTEGER DEFAULT 0")
    # BC-089: unix ts when snoozed finding should auto-re-open. 0 = no expiry
    # (either not snoozed, or pre-BC-089 legacy snooze). sweep SQL filters
    # `WHERE snooze_until_ts > 0 AND snooze_until_ts <= now` so legacy rows
    # stay snoozed indefinitely (preserves pre-BC-089 operator expectation).
    with contextlib.suppress(sqlite3.OperationalError):
        con.execute("ALTER TABLE findings ADD COLUMN snooze_until_ts INTEGER DEFAULT 0")
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
    # BC-061: backfill scouts from findings so pre-BC-061 operators don't
    # start with an empty registry. Idempotent — ON CONFLICT skips rows
    # that already exist (e.g. a scout that posted after init_db ran).
    with contextlib.suppress(sqlite3.OperationalError):
        con.execute("""
            INSERT INTO scouts (host, first_seen_ts, last_posted_ts, last_sbom_count)
            SELECT host, COALESCE(MIN(found_at), 0), COALESCE(MAX(last_seen_ts), 0), 0
            FROM findings
            WHERE host IS NOT NULL AND host != ''
            GROUP BY host
            ON CONFLICT(host) DO NOTHING
        """)
    # BC-062: stamp the DB with the schema version we just migrated to.
    con.execute(f"PRAGMA user_version = {EXPECTED_SCHEMA_VERSION}")
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
                internet_facing, last_seen_ts, last_notified_ts, cve_alias)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                f.cve_alias,  # BC-043
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
            cve_alias=f.cve_alias,  # BC-043
        )
    return new_row


def load_finding(finding_id: str) -> Finding | None:
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT id,host,cve_id,package,version,fixed_version,severity,"
        "cvss,source_type,source_ref,description,tier,kev,internet_facing,"
        "cve_alias "
        "FROM findings WHERE id=?",
        (finding_id,),
    ).fetchone()
    con.close()
    if row is None:
        return None
    # Trailing INTEGER columns coerce to bool; cve_alias passes through (may be None).
    *head, kev_int, if_int, cve_alias = row
    return Finding(
        *head,
        kev=bool(kev_int),
        internet_facing=bool(if_int),
        cve_alias=cve_alias,
    )


class FindingNotFound(Exception):
    """BC-058: raised by set_status when finding_id has no row in the DB.

    Pre-BC-058 the UPDATE was a silent no-op and set_status still wrote
    a status_change audit event — a correctness landmine for every
    caller that didn't pre-check with load_finding. Raising surfaces
    the drift immediately.
    """


def set_status(finding_id: str, status: str, *, source: str = "telegram") -> None:
    """BC-058: mutate a finding's status, emit a rich audit event.

    Payload shape matches the BC-055 PATCH /findings/{id} handler so
    `make audit` enrichment is uniform regardless of mutation path:
    `{finding_id, host, cve_id, old_status, new_status, source}`.

    Raises `FindingNotFound` when the id has no row.
    """
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute(
            "SELECT host, cve_id, status FROM findings WHERE id = ?",
            (finding_id,),
        ).fetchone()
        if row is None:
            raise FindingNotFound(finding_id)
        host, cve_id, old_status = row
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
    finally:
        con.close()
    # BC-029/BC-058: operator-visible state transition. Shape mirrors BC-055 PATCH.
    _audit.append(
        "status_change",
        finding_id=finding_id,
        host=host,
        cve_id=cve_id,
        old_status=old_status,
        new_status=status,
        source=source,
    )


def _load_last_seen(finding_id: str) -> int | None:
    """BC-087: pull `last_seen_ts` for one finding so notify*() can pass it
    through to format_*. Returns None when the row is missing OR the column
    is 0 (unseen since BC-034) — `format_last_seen` treats both as
    'no info, skip the Last-seen line'."""
    try:
        con = sqlite3.connect(DB_PATH)
    except sqlite3.OperationalError:
        return None
    try:
        row = con.execute("SELECT last_seen_ts FROM findings WHERE id=?", (finding_id,)).fetchone()
    except sqlite3.OperationalError:
        # init_db hasn't run yet — nothing to read.
        return None
    finally:
        con.close()
    if row is None or not row[0]:
        return None
    return int(row[0])


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
        # BC-086: also reset absent_count so a finding that reappears in a
        # single scan clears its auto-fix progress; the counter only ever
        # grows across uninterrupted absences.
        con.execute(
            f"UPDATE findings SET last_seen_ts = ?, absent_count = 0 "
            f"WHERE host = ? AND id IN ({placeholders})",
            (ts, host, *scanned_ids),
        )
        affected = con.total_changes
        con.commit()
        # BC-086 diagnostic (BC-087: DEBUG, opt-in via BEACONRY_LOG_LEVEL=DEBUG).
        # Helps correlate with reconcile's absent list when debugging the counter.
        log.debug(
            "BC-086 bump_last_seen host=%s scanned_ids=%d rows_updated=%d",
            host,
            len(scanned_ids),
            affected,
        )
    except sqlite3.OperationalError:
        # Cold boot / init_db hasn't run — nothing to update.
        pass
    finally:
        con.close()


def reconcile_scan(
    host: str, scanned_ids: set[str]
) -> tuple[list[Finding], list[Finding], list[Finding]]:
    """BC-031 + BC-086: diff current scan against DB.

    Returns (regressed_from_fix, auto_fixed, regressed_from_auto_fix).

    State transitions (BC-086 extends BC-031's scope + adds counter):

      - open | regressed | snoozed | accepted  + absent:
          bump absent_count. If absent_count ≥ AUTO_FIX_THRESHOLD →
          flip status='auto_fixed', record auto_fixed_ts, emit
          `finding_auto_fixed` audit event. Silent (no Telegram).
      - fixed | fixed_verified              + present:
          flip status='regressed', loud "🔥 FIX REGRESSED" alert,
          emit `regressed` audit event (BC-031 original behaviour).
      - auto_fixed                          + present:
          flip status='regressed', **distinct** "🔥 AUTO-FIX REGRESSED"
          alert, emit `regressed_from_auto_fix` audit event.
      - fixed | regressed                   + absent:
          unchanged (operator decisions stay operator-owned).

    The three return lists route to distinct notifier paths:
        regressed_from_fix        → existing BC-031 FIX REGRESSED message
        auto_fixed                → no notification (silent close)
        regressed_from_auto_fix   → new BC-086 AUTO-FIX REGRESSED message

    If RECONCILE_ENABLED is False, returns three empty lists.
    """
    if not RECONCILE_ENABLED:
        return [], [], []
    # BC-086 diagnostic (BC-087: DEBUG, opt-in via BEACONRY_LOG_LEVEL=DEBUG).
    # Entry line so the per-finding lines below have context.
    log.debug(
        "BC-086 reconcile ENTER host=%s scanned_ids_count=%d threshold=%d",
        host,
        len(scanned_ids),
        AUTO_FIX_THRESHOLD,
    )
    con = sqlite3.connect(DB_PATH)
    try:
        try:
            # BC-086: widen scope to snoozed + accepted; add
            # absent_count + auto_fixed_ts columns to the row payload.
            rows = con.execute(
                "SELECT id, host, cve_id, package, version, fixed_version, severity, cvss, "
                "source_type, source_ref, description, tier, kev, status, last_fixed_ts, "
                "absent_count, auto_fixed_ts "
                "FROM findings WHERE host=? AND status IN "
                "('open', 'fixed', 'fixed_verified', 'regressed', 'snoozed', 'accepted', 'auto_fixed')",
                (host,),
            ).fetchall()
        except sqlite3.OperationalError:
            # Cold boot / init_db hasn't run yet — nothing to reconcile.
            return [], [], []

        regressed_from_fix: list[Finding] = []
        auto_fixed_closes: list[Finding] = []
        regressed_from_auto_fix: list[Finding] = []
        now = int(time.time())

        for row in rows:
            fid = row[0]
            status = row[13]
            last_fixed_ts = row[14] or 0
            absent_count = row[15] or 0
            auto_fixed_ts = row[16] or 0
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

            # BC-086 diagnostic (BC-087: DEBUG, opt-in via BEACONRY_LOG_LEVEL=DEBUG).
            # Per-finding decision log — essential for debugging but too noisy
            # at INFO (one line per finding per scan per host).
            log.debug(
                "BC-086 reconcile fid=%s status=%s absent_count=%d in_scan=%s",
                fid,
                status,
                absent_count,
                in_scan,
            )

            # BC-086: counter-based auto-fix for open/regressed/snoozed/accepted.
            if status in ("open", "regressed", "snoozed", "accepted") and not in_scan:
                new_count = absent_count + 1
                if new_count >= AUTO_FIX_THRESHOLD:
                    con.execute(
                        "UPDATE findings SET status='auto_fixed', "
                        "auto_fixed_ts=?, absent_count=? WHERE id=?",
                        (now, new_count, fid),
                    )
                    auto_fixed_closes.append(finding)
                    _audit.append(
                        "finding_auto_fixed",
                        finding_id=fid,
                        prev_status=status,
                        cve_id=finding.cve_id,
                        absent_scans=new_count,
                        threshold=AUTO_FIX_THRESHOLD,
                    )
                else:
                    con.execute(
                        "UPDATE findings SET absent_count=? WHERE id=?",
                        (new_count, fid),
                    )
            # BC-031 regression (operator-initiated fix): distinct message.
            elif status in ("fixed", "fixed_verified") and in_scan:
                con.execute(
                    "UPDATE findings SET status='regressed', absent_count=0 WHERE id=?",
                    (fid,),
                )
                regressed_from_fix.append(finding)
                _audit.append(
                    "regressed",
                    finding_id=fid,
                    prev_status=status,
                    cve_id=finding.cve_id,
                    last_fixed_ts=last_fixed_ts,
                    regressed_ts=now,
                )
            # BC-086: auto-fix regression → distinct audit event + loud alert.
            elif status == "auto_fixed" and in_scan:
                con.execute(
                    "UPDATE findings SET status='regressed', absent_count=0 WHERE id=?",
                    (fid,),
                )
                regressed_from_auto_fix.append(finding)
                _audit.append(
                    "regressed_from_auto_fix",
                    finding_id=fid,
                    prev_status="auto_fixed",
                    cve_id=finding.cve_id,
                    auto_fixed_ts=auto_fixed_ts,
                    regressed_ts=now,
                )

        con.commit()
        return regressed_from_fix, auto_fixed_closes, regressed_from_auto_fix
    finally:
        con.close()


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


def _pick_cve_alias(primary_id: str, related_ids: list[str]) -> str | None:
    """BC-043: pick the first CVE-shaped id from `related_ids`.

    Returns None if primary IS a CVE (no alias needed) OR if no related id
    is CVE-shaped. First-match wins — grype's related order is stable.
    """
    if primary_id.upper().startswith("CVE-"):
        return None
    for rid in related_ids:
        if rid and rid.upper().startswith("CVE-"):
            return rid
    return None


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
        # BC-067: `vuln["id"]` previously raised KeyError on malformed grype
        # output (DB corruption, legacy advisory shape). The broad except at
        # `_ingest_candidates` swallowed it and the entire scan lost that
        # finding silently — the POC's one job (surface CVEs) was broken by
        # a single bad row. Skip the match with an audit event + log so the
        # drop is observable instead of invisible.
        cve_id = vuln.get("id")
        if not cve_id:
            log.warning(
                "BC-067: grype match missing vulnerability.id — skipping (artifact=%r)",
                artifact.get("name"),
            )
            _audit.append(
                "invalid_grype_match",
                host=host,
                artifact=artifact.get("name", ""),
                reason="missing vulnerability.id",
            )
            continue
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
        # BC-068: grype occasionally returns `fix.versions: [""]` or
        # `[None]` (data artifact from legacy advisories). Filter blanks
        # so the renderer doesn't surface "Fixed version: (blank)" and
        # confuse operators ("is there a fix or not?").
        fix_versions = [
            v.strip() for v in (vuln.get("fix", {}).get("versions") or []) if v and v.strip()
        ]
        # BC-030: honour the operator toggle. When disabled, we throw away scout's
        # exposure data and classify as pre-BC-030 — Tier 1 unreachable, all findings
        # fall through to the CVSS tier below.
        is_internet_facing = INTERNET_FACING_DETECTION_ENABLED and bool(
            port_exposure.get(source_ref, False)
        )
        # BC-043: resolve the CVE alias from `relatedVulnerabilities` for
        # non-CVE primary ids (`GO-*`, `GHSA-*`, …). Lets operators cross-
        # reference NVD / GitHub advisories AND makes the KEV paper trail
        # visible when primary is non-CVE.
        cve_alias = _pick_cve_alias(cve_id, related_ids)
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
            cve_alias=cve_alias,
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


def _severity_line(finding: Finding, emoji: str, esc) -> str:
    """BC-043: single-source severity line used by all three format functions.

    Shape:
        CVE primary                → `🔴 Critical — CVE-2026-X`
        Non-CVE primary + alias    → `🔴 Critical — GO-2026-X · CVE-2024-Y`
        Non-CVE primary + no alias → `🔴 Critical — GO-2026-X · no CVE alias`

    Keeps the pre-BC-043 `<b>Critical — <id></b>` bolding for the primary
    so existing tests that assert on the bold run keep passing; the alias
    suffix lives outside the bold.
    """
    base = f"{emoji} <b>{esc(finding.severity)} — {esc(finding.cve_id)}</b>"
    if finding.cve_id.upper().startswith("CVE-"):
        return base
    if finding.cve_alias:
        return f"{base} · <code>{esc(finding.cve_alias)}</code>"
    return f"{base} · <i>no CVE alias</i>"


def is_multi_host_inventory() -> bool:
    """BC-057: True when the findings table has ≥2 distinct host values.

    Flips the notification lead from the severity line to a dedicated
    host prefix so operators running multi-host scouts see "where is
    this?" immediately. Zero visual regression for single-host operators.
    """
    if not DB_PATH.exists():
        return False
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT COUNT(DISTINCT host) FROM findings").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return False
    return bool(row and row[0] and row[0] > 1)


def format_notification(
    finding: Finding,
    *,
    multi_host: bool | None = None,
    last_seen_ts: int | None = None,
) -> str:
    """Build the message body in Telegram HTML (parse_mode='HTML').

    HTML is used instead of legacy Markdown because Markdown v1 has no escape
    syntax — any raw `*`, `_`, backtick, or `[` in a CVE description would
    break parsing. HTML needs only `<`, `>`, `&` escaped via html.escape.
    """
    import html

    emoji = {"Critical": "🔴", "High": "🟠", "Medium": "🟡"}.get(finding.severity, "⚪")
    # BC-066: single-executor policy (BEACONRY_EXECUTOR). ManualAdapter is
    # the default; docker is opt-in.
    fix_cmd = EXECUTOR.describe(finding)

    def esc(s: str) -> str:
        return html.escape(s, quote=False)

    lines: list[str] = []
    # BC-057: lead with host when the hub has findings from ≥2 distinct
    # hosts. Single-host inventories keep today's format unchanged.
    # BC-058: caller may pass `multi_host` as a hint to skip the DB
    # lookup when rendering a batch (digest, sweep). None → DB lookup.
    is_multi = multi_host if multi_host is not None else is_multi_host_inventory()
    if is_multi:
        lines.append(f"🖥️ <code>{esc(finding.host)}</code>")
    # BC-020: Tier 0 (KEV) gets a visually distinct header above the severity row.
    if finding.tier == 0:
        lines.append("🚨 <b>KEV — EXPLOITED IN THE WILD</b>")
        # BC-043: when the primary is non-CVE, surface the alias prominently
        # under the KEV header so the CISA paper trail is unmissable.
        if finding.cve_alias and not finding.cve_id.upper().startswith("CVE-"):
            lines.append(f"<i>via {esc(finding.cve_alias)}</i>")
    # BC-030: Tier 1 (CVSS≥9 + internet-facing) gets its own header. Falls
    # below KEV so operator still sees the strongest signal first on Tier 0 rows.
    if finding.tier == 1:
        lines.append("🌐 <b>INTERNET-FACING — Tier 1</b>")
    lines.extend(
        [
            _severity_line(finding, emoji, esc),
            f"CVSS: <code>{finding.cvss:.1f}</code> · Host: <code>{esc(finding.host)}</code>",
            f"Target: <code>{esc(finding.source_ref)}</code> ({esc(finding.source_type)})",
            _package_fix_line(finding, esc),
            # BC-032: tell the operator whether tapping Fix will likely help.
            _viability_line(finding),
        ]
    )
    # BC-086: Last-seen line injected between header block and description
    # so the operator can tell if the alert is live-right-now (first-fire)
    # or long-standing (heartbeat / reminder / regression context).
    if last_seen_ts is not None:
        last_seen_line = format_last_seen(last_seen_ts)
        if last_seen_line:
            lines.append(last_seen_line)
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
    # BC-089: mock-renderer reflects the configurable snooze keyboard so the
    # printed demo + tests stay in sync with what Telegram actually shows.
    snooze_labels = " ".join(f"[⏰ {_snooze_button_label(h)}]" for h in SNOOZE_DEFAULT_HOURS)
    buttons = f"      Inline keyboard: [🔧 Fix now] {snooze_labels} [✅ Accept risk]"
    return "\n".join([top, body, bot, buttons])


def format_last_seen(last_seen_ts: int, *, now_ts: int | None = None) -> str:
    """BC-086: human-readable "Last seen: Xh ago" line injected into every
    finding notification. Returns the empty string if last_seen_ts is 0 or
    very recent (<30 s); that indicates the finding is live right now
    and the line would add noise."""
    if not last_seen_ts:
        return ""
    now = now_ts if now_ts is not None else int(time.time())
    delta = max(0, now - last_seen_ts)
    if delta < 30:
        return "Last seen: just now"
    if delta < 3600:
        return f"Last seen: {delta // 60}m ago"
    if delta < 86400:
        return f"Last seen: {delta // 3600}h ago"
    days = delta // 86400
    return f"Last seen: {days}d ago"


def format_auto_fix_regression(
    finding: Finding, auto_fixed_ts: int, *, last_seen_ts: int | None = None
) -> str:
    """BC-086: distinct 🔥 message for a CVE that was closed by the
    counter-based auto-fix (BC-086) and then came back in a later scan.

    Critically distinct from `format_regression` (operator-initiated fix
    regression) so the operator can tell which process reclosed the
    finding: a human tap + verified scan, or the system inferring
    mitigation from scan absence. Different root-cause investigation
    paths — don't collapse the message."""
    import datetime
    import html

    def esc(s: str) -> str:
        return html.escape(s, quote=False)

    auto_date = (
        datetime.datetime.fromtimestamp(auto_fixed_ts).strftime("%Y-%m-%d")
        if auto_fixed_ts
        else "unknown"
    )
    primary_display = f"<b>{esc(finding.cve_id)}</b>"
    if not finding.cve_id.upper().startswith("CVE-"):
        if finding.cve_alias:
            primary_display += f" · <code>{esc(finding.cve_alias)}</code>"
        else:
            primary_display += " · <i>no CVE alias</i>"
    lines = [
        "🔥 <b>AUTO-FIX REGRESSED</b>",
        f"{primary_display} auto-closed on <code>{auto_date}</code> is back on "
        f"<code>{esc(finding.host)}</code>.",
        f"Target: <code>{esc(finding.source_ref)}</code> ({esc(finding.source_type)})",
        _package_fix_line(finding, esc),
        _viability_line(finding),
        "",
        "<i>The counter-based auto-fix (BC-086) inferred mitigation from "
        "scan absence; now the image is back with the same CVE. The "
        "mitigation was either incomplete or something restored the "
        "vulnerable image. Investigate before treating this as a routine "
        "reappearance — auto-fix is less authoritative than an operator fix.</i>",
    ]
    if last_seen_ts is not None:
        last_seen_line = format_last_seen(last_seen_ts)
        if last_seen_line:
            lines.append(last_seen_line)
    return "\n".join(lines)


def format_regression(
    finding: Finding, last_fixed_ts: int, *, last_seen_ts: int | None = None
) -> str:
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
    # BC-043: for non-CVE primaries, include the alias (or "no CVE alias")
    # in the loud regression header so the paper trail is visible on reappear.
    primary_display = f"<b>{esc(finding.cve_id)}</b>"
    if not finding.cve_id.upper().startswith("CVE-"):
        if finding.cve_alias:
            primary_display += f" · <code>{esc(finding.cve_alias)}</code>"
        else:
            primary_display += " · <i>no CVE alias</i>"
    lines = [
        "🔥 <b>FIX REGRESSED</b>",
        f"{primary_display} is back after being fixed on <code>{fixed_date}</code>.",
        f"Target: <code>{esc(finding.source_ref)}</code> ({esc(finding.source_type)})",
        # BC-032: show the fixed version AND the viability assessment so the
        # operator can tell whether 🔧 Re-fix will actually help or will loop.
        _package_fix_line(finding, esc),
        _viability_line(finding),
        "",
        "<i>Something reinstalled or rolled back the vulnerable version. "
        "Investigate before re-fixing — the root cause will come back again otherwise.</i>",
    ]
    if last_seen_ts is not None:
        last_seen_line = format_last_seen(last_seen_ts)
        if last_seen_line:
            lines.append(last_seen_line)
    return "\n".join(lines)


def format_heartbeat(
    finding: Finding, open_since_ts: int, *, last_seen_ts: int | None = None
) -> str:
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
        if finding.cve_alias and not finding.cve_id.upper().startswith("CVE-"):
            lines.append(f"<i>via {esc(finding.cve_alias)}</i>")
    lines.extend(
        [
            _severity_line(finding, emoji, esc),  # BC-043
            f"CVSS: <code>{finding.cvss:.1f}</code> · Host: <code>{esc(finding.host)}</code>",
            f"Target: <code>{esc(finding.source_ref)}</code> ({esc(finding.source_type)})",
            _package_fix_line(finding, esc),
            _viability_line(finding),
            "",
            "<i>You haven't actioned this finding yet. Snooze to defer or "
            "accept the risk if you've decided to live with it.</i>",
        ]
    )
    # BC-086: show last-seen context so operator can tell if scout is still
    # actively tracking this or if the image has gone stale.
    if last_seen_ts is not None:
        last_seen_line = format_last_seen(last_seen_ts)
        if last_seen_line:
            lines.append(last_seen_line)
    return "\n".join(lines)


def format_snooze_expired(finding: Finding) -> str:
    """BC-089: distinct body for a finding whose snooze just expired.
    Semantics differ from regression (= 'thought fixed, isn't') — this
    says 'you deferred, timer rang, here it is again'."""
    import html

    def esc(s: str) -> str:
        return html.escape(s, quote=False)

    emoji = {"Critical": "🔴", "High": "🟠", "Medium": "🟡"}.get(finding.severity, "⚪")
    lines = [
        "⏰ <b>SNOOZE EXPIRED — back on your radar</b>",
        _severity_line(finding, emoji, esc),
        f"CVSS: <code>{finding.cvss:.1f}</code> · Host: <code>{esc(finding.host)}</code>",
        f"Target: <code>{esc(finding.source_ref)}</code> ({esc(finding.source_type)})",
        _package_fix_line(finding, esc),
        _viability_line(finding),
        "",
        f"<i>{esc(finding.description[:160])}</i>",
    ]
    return "\n".join(line for line in lines if line is not None)


# ── BC-035: keyboard builders exposed to the TelegramAdapter ─────────────────


_CB_MAX_BYTES = 64  # Telegram limits callback_data to 64 bytes.


def _cb_data(action: str, finding: Finding, *extra: str) -> str:
    """BC-059: build callback_data under Telegram's 64-byte cap.

    Shape: ``{action}:{finding.id}[:{extra}]*[:{cve_id}]``. The CVE id
    goes LAST so an on-the-wire splitter can `parts[-1]` it; existing
    positional parsers (hours at index 2 for snooze) still work.

    If appending the CVE would blow the cap, drop it defensively — the
    BC-058 error can still fall back to the 8-char id prefix.
    """
    base = ":".join([action, finding.id, *extra])
    cve = finding.cve_id or ""
    if not cve:
        return base
    with_cve = base + ":" + cve
    return with_cve if len(with_cve.encode()) <= _CB_MAX_BYTES else base


def _snooze_button_label(hours: int) -> str:
    """BC-089: '1h', '4h', '1d', '1w' — human-friendly duration suffix for
    the inline keyboard."""
    if hours >= 168 and hours % 168 == 0:
        return f"{hours // 168}w"
    if hours >= 24 and hours % 24 == 0:
        return f"{hours // 24}d"
    return f"{hours}h"


def _kb_finding(finding: Finding) -> "InlineKeyboardMarkup":
    """First-fire + heartbeat buttons — Fix / Snooze(s) / Accept risk.

    BC-089: snooze buttons are now plural, driven by
    `BEACONRY_SNOOZE_DEFAULT_HOURS` (default `1,4,24,168` = 1h/4h/1d/1w).
    Operator can set the env to a single value (e.g. `4`) to keep the
    original one-button layout.
    """
    snooze_buttons = [
        InlineKeyboardButton(
            f"⏰ {_snooze_button_label(h)}",
            callback_data=_cb_data("snooze", finding, str(h)),
        )
        for h in SNOOZE_DEFAULT_HOURS
    ]
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton("🔧 Fix now", callback_data=_cb_data("fix", finding))],
        snooze_buttons,
        [InlineKeyboardButton("✅ Accept risk", callback_data=_cb_data("ack", finding))],
    ]
    return InlineKeyboardMarkup(rows)


def _kb_regression(finding: Finding) -> "InlineKeyboardMarkup":
    """BC-031 regression — no Snooze (regression deserves attention, not deferral)."""
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔧 Re-fix", callback_data=_cb_data("fix", finding)),
                InlineKeyboardButton("✅ Accept risk", callback_data=_cb_data("ack", finding)),
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


def _emit_notify_sent(finding: Finding, kind: str) -> None:
    """BC-058: durable audit record of every notification send so stale-
    tap debugging is possible later. One event per finding-channel send."""
    _audit.append(
        "notify_sent",
        finding_id=finding.id,
        cve_id=finding.cve_id,
        host=finding.host,
        kind=kind,
    )


_NOTIFY_REASON_MAX = 200


def _format_fail_reason(exc: Exception) -> str:
    """BC-060: compact exception summary for audit — class name + short msg.

    Truncated to {_NOTIFY_REASON_MAX} chars. python-telegram-bot errors
    sometimes include the API URL with the bot token in it; prefer the
    class name as the primary signal and treat the audit log itself as
    sensitive. A dedicated token-redaction regex is a follow-up BC.
    """
    msg = str(exc)[:_NOTIFY_REASON_MAX]
    return f"{exc.__class__.__name__}: {msg}"


def _emit_notify_sent_failed(finding: Finding, kind: str, exc: Exception) -> None:
    """BC-060: symmetric audit event for failed dispatches."""
    _audit.append(
        "notify_sent_failed",
        finding_id=finding.id,
        cve_id=finding.cve_id,
        host=finding.host,
        kind=kind,
        reason=_format_fail_reason(exc),
    )


async def notify_regression(finding: Finding, last_fixed_ts: int) -> None:
    """BC-031: 🔥 FIX REGRESSED. Dispatched to all enabled notification adapters."""
    last_seen_ts = _load_last_seen(finding.id)
    if MOCK and not _notifiers_pkg.enabled_adapters():
        _mock_print(
            "Regression",
            format_regression(finding, last_fixed_ts, last_seen_ts=last_seen_ts),
        )
        _emit_notify_sent(finding, "regression")
        return
    try:
        await _dispatch_regression(finding, last_fixed_ts, last_seen_ts)
    except Exception as exc:
        _emit_notify_sent_failed(finding, "regression", exc)
        raise
    _emit_notify_sent(finding, "regression")


async def notify_auto_fix_regression(finding: Finding, auto_fixed_ts: int) -> None:
    """BC-086 (CAP-T178): 🔥 AUTO-FIX REGRESSED — distinct notifier for
    findings closed by the counter-based auto-fix that have now reappeared.

    BC-090: now dispatches through `send_auto_fix_regression` (not
    `send_regression`) so the real Telegram body renders
    `format_auto_fix_regression`. Pre-BC-090 the MOCK path showed the
    distinct body but the live Telegram path fell through to
    `format_regression` — dead code on the hot path.

    Opt-out: `BEACONRY_AUTO_FIX_REGRESSION_NOTIFY_ENABLED=0` suppresses
    the Telegram ping (status flip + `regressed_from_auto_fix` audit
    still fire). Separate from per-adapter and per-snooze-kind toggles.
    """
    last_seen_ts = _load_last_seen(finding.id)
    if not AUTO_FIX_REGRESSION_NOTIFY_ENABLED:
        # Operator opted out; audit still records the suppression so
        # the regression isn't silently dropped from the record.
        _audit.append(
            "notify_suppressed",
            finding_id=finding.id,
            cve_id=finding.cve_id,
            host=finding.host,
            kind="auto_fix_regression",
            reason="BEACONRY_AUTO_FIX_REGRESSION_NOTIFY_ENABLED=0",
        )
        return
    if MOCK and not _notifiers_pkg.enabled_adapters():
        _mock_print(
            "AutoFixRegression",
            format_auto_fix_regression(finding, auto_fixed_ts, last_seen_ts=last_seen_ts),
        )
        _emit_notify_sent(finding, "auto_fix_regression")
        return
    try:
        await _dispatch_auto_fix_regression(finding, auto_fixed_ts, last_seen_ts)
    except Exception as exc:
        _emit_notify_sent_failed(finding, "auto_fix_regression", exc)
        raise
    _emit_notify_sent(finding, "auto_fix_regression")


async def notify(finding: Finding) -> None:
    """BC-003: first-fire notification. Dispatched to all enabled adapters."""
    last_seen_ts = _load_last_seen(finding.id)
    if MOCK and not _notifiers_pkg.enabled_adapters():
        _mock_print(
            "Notification",
            format_notification(finding, last_seen_ts=last_seen_ts),
        )
        _emit_notify_sent(finding, "finding")
        return
    try:
        await _dispatch_finding(finding, last_seen_ts)
    except Exception as exc:
        _emit_notify_sent_failed(finding, "finding", exc)
        raise
    _emit_notify_sent(finding, "finding")


async def notify_snooze_expired(finding: Finding) -> None:
    """BC-089: distinct '⏰ Snooze expired' notification. Operator-visible path
    is `BEACONRY_SNOOZE_NOTIFY_ON_EXPIRE=1` (default). If the env is `0`,
    `snooze_expiry_sweep` audits the transition but doesn't call this."""
    if MOCK and not _notifiers_pkg.enabled_adapters():
        _mock_print("SnoozeExpired", format_snooze_expired(finding))
        _emit_notify_sent(finding, "snooze_expired")
        return
    try:
        await _dispatch_snooze_expired(finding)
    except Exception as exc:
        _emit_notify_sent_failed(finding, "snooze_expired", exc)
        raise
    _emit_notify_sent(finding, "snooze_expired")


async def notify_heartbeat(finding: Finding, open_since_ts: int) -> None:
    """BC-035: periodic reminder. Dispatched to all enabled adapters."""
    last_seen_ts = _load_last_seen(finding.id)
    if MOCK and not _notifiers_pkg.enabled_adapters():
        _mock_print(
            "Heartbeat",
            format_heartbeat(finding, open_since_ts, last_seen_ts=last_seen_ts),
        )
        _emit_notify_sent(finding, "heartbeat")
        return
    try:
        await _dispatch_heartbeat(finding, open_since_ts, last_seen_ts)
    except Exception as exc:
        _emit_notify_sent_failed(finding, "heartbeat", exc)
        raise
    _emit_notify_sent(finding, "heartbeat")


# BC-059: optional trailing CVE segment (back-compat with pre-BC-059 messages
# still in operators' chat history). Segment matches CVE-shaped primary ids
# — CVE-YYYY-NNNN, GO-YYYY-NNNNNN, AVG-NNNN, etc. — not arbitrary garbage.
CALLBACK_RE = re.compile(
    r"^(fix|snooze|ack|verify):[a-f0-9]{16}(?::\d+)?(?::[A-Z][A-Z0-9-]{2,30})?$"
)


def _esc(s: str) -> str:
    import html

    return html.escape(s, quote=False)


def _get_host_role(host: str) -> str:
    """BC-092: read `scouts.role` for a host; fall back to 'workstation' for
    unknown hosts (scout never heartbeated — shouldn't block, operator will
    see the issue elsewhere)."""
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute("SELECT role FROM scouts WHERE host = ?", (host,)).fetchone()
    except sqlite3.OperationalError:
        return "workstation"
    finally:
        con.close()
    return _normalize_role(row[0] if row else None)


def _staging_hosts_with_cve_verified(cve_id: str) -> list[str]:
    """BC-092: find staging hosts whose same CVE is in `fixed_verified`.
    Non-empty result means the canary gate can allow the production fix."""
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute(
            "SELECT DISTINCT f.host FROM findings f "
            "JOIN scouts s ON f.host = s.host "
            "WHERE f.cve_id = ? AND f.status = 'fixed_verified' "
            "AND s.role = 'staging'",
            (cve_id,),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()
    return [r[0] for r in rows]


def _staging_hosts_in_fleet() -> list[str]:
    """BC-092: list every scout tagged `staging`. Used in the gate-block
    reply so the operator sees which scouts they could have fixed first."""
    con = sqlite3.connect(DB_PATH)
    try:
        rows = con.execute("SELECT host FROM scouts WHERE role = 'staging'").fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        con.close()
    return [r[0] for r in rows]


async def _handle_fix(query, finding: Finding, finding_id: str, parts: list[str]) -> None:  # noqa: ARG001
    """Fix action: dispatch to the configured executor and edit the message.

    BC-066: `EXECUTOR` is a single policy choice (manual|docker) picked at
    hub startup. Manual mode logs operator intent + reminds in 1h; docker
    mode re-pulls the image + verifies via next scan reconcile.

    BC-092: canary-ring gate runs before dispatch. Production-role hosts
    need a staging-verified precedent for the same CVE unless the operator
    explicitly opts out of the gate or force-allows this fix.
    """
    if not FIX_ENABLED:
        await query.edit_message_text(
            f"⚙️ Fix execution disabled (BEACONRY_FIX_ENABLED=0) — "
            f"<code>{_esc(finding.cve_id)}</code> left as <code>open</code>",
            parse_mode="HTML",
        )
        return

    # BC-092: canary-ring gate.
    role = _get_host_role(finding.host)
    if CANARY_RING_ENABLED and role == "production":
        if CANARY_PRODUCTION_FORCE_ALLOW:
            _audit.append(
                "canary_gate_force_allowed",
                finding_id=finding.id,
                cve_id=finding.cve_id,
                host=finding.host,
                reason="BEACONRY_CANARY_PRODUCTION_FORCE_ALLOW=1",
            )
        else:
            staging_verified = _staging_hosts_with_cve_verified(finding.cve_id)
            if not staging_verified:
                staging_in_fleet = _staging_hosts_in_fleet()
                hint = (
                    f"staging hosts: <code>{_esc(', '.join(staging_in_fleet))}</code>"
                    if staging_in_fleet
                    else (
                        "no staging hosts configured — set "
                        "<code>SCOUT_HOST_ROLE=staging</code> on at least one scout, "
                        "or disable the gate with "
                        "<code>BEACONRY_CANARY_RING_ENABLED=0</code>"
                    )
                )
                _audit.append(
                    "canary_gate_blocked",
                    finding_id=finding.id,
                    cve_id=finding.cve_id,
                    host=finding.host,
                    staging_hosts=staging_in_fleet,
                )
                await query.edit_message_text(
                    f"🛑 <b>Canary gate blocked this fix</b>\n"
                    f"<code>{_esc(finding.cve_id)}</code> on <code>{_esc(finding.host)}</code> "
                    f"is production-role; no staging host has verified the same CVE yet.\n"
                    f"Verify on {hint} first, then re-tap.",
                    parse_mode="HTML",
                )
                return
            _audit.append(
                "canary_gate_passed",
                finding_id=finding.id,
                cve_id=finding.cve_id,
                host=finding.host,
                staging_hosts=staging_verified,
            )

    # BC-066: manual executor short-circuits — audit intent, log claim_ts,
    # show operator the command, schedule reminder. No execution.
    if EXECUTOR_NAME == "manual":
        now = int(time.time())
        _record_manual_claim(finding.id, now)
        command = EXECUTOR.describe(finding)
        _audit.append(
            "manual_fix_claimed",
            finding_id=finding.id,
            cve_id=finding.cve_id,
            host=finding.host,
            command=command,
            claim_ts=now,
        )
        reminder_note = (
            f"We'll re-check in {MANUAL_REMINDER_INTERVAL_S // 60}m."
            if MANUAL_REMINDER_INTERVAL_S > 0
            else "Reminders disabled."
        )
        await query.edit_message_text(
            f"📝 <b>Logged</b> · <code>{_esc(finding.cve_id)}</code> on "
            f"<code>{_esc(finding.host)}</code>\n"
            f"Run: <code>{_esc(command)}</code>\n"
            f"<i>{reminder_note}</i>",
            parse_mode="HTML",
        )
        return

    await query.edit_message_text(
        f"⏳ Fixing <code>{_esc(finding.cve_id)}</code> on <code>{_esc(finding.host)}</code>…",
        parse_mode="HTML",
    )
    result = await EXECUTOR.execute(finding)

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
                    callback_data=_cb_data("verify", finding),
                )
            ]
        ]
    )
    fixed_text += "\n\n<i>Restart your container, then tap below to probe the service.</i>"
    await query.edit_message_text(fixed_text, parse_mode="HTML", reply_markup=verify_keyboard)


async def _handle_snooze(query, finding: Finding, finding_id: str, parts: list[str]) -> None:
    """BC-089: parse hours, store `now + hours*3600` into `snooze_until_ts` so
    the periodic sweep can auto-re-open at expiry. Pre-BC-089 behaviour was
    'snooze stores nothing, stays indefinite' — now the label tells the truth.
    """
    hours = int(parts[2]) if len(parts) > 2 else 4
    now_ts = int(time.time())
    expiry_ts = now_ts + hours * 3600
    set_status(finding_id, "snoozed")
    _set_snooze_until(finding_id, expiry_ts)
    # Render the expiry time in operator-local format alongside the hour count
    # so the operator can sanity-check it against their wall clock.
    import datetime as _dt

    expiry_str = _dt.datetime.fromtimestamp(expiry_ts).strftime("%Y-%m-%d %H:%M")
    await query.edit_message_text(
        f"⏰ Snoozed {hours}h — <code>{_esc(finding.cve_id)}</code>\n"
        f"Returns at <b>{expiry_str}</b>",
        parse_mode="HTML",
    )


def _set_snooze_until(finding_id: str, snooze_until_ts: int) -> None:
    """BC-089: UPDATE the single column. Separate from set_status so the
    audit event emitted by set_status already fired for `snoozed` — this
    is pure metadata."""
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(
            "UPDATE findings SET snooze_until_ts = ? WHERE id = ?",
            (snooze_until_ts, finding_id),
        )
        con.commit()
    finally:
        con.close()


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
        # BC-058 + BC-059: diagnosable error. Include the CVE from
        # callback_data when present (post-BC-059 messages); fall back
        # to the id prefix for pre-BC-059 messages still in the chat.
        short_id = finding_id[:8]
        verb = action.capitalize()
        cve_in_cb = parts[-1] if len(parts) >= 3 and parts[-1][:1].isalpha() else None
        if cve_in_cb:
            subject = f"<code>{_esc(cve_in_cb)}</code> (id <code>{short_id}…</code>)"
        else:
            subject = f"finding <code>{short_id}…</code>"
        await query.edit_message_text(
            f"⚠️ {verb} failed — {subject} is not in the current DB.\n"
            f"<i>Likely a stale notification from before a scan reset. "
            f"Safe to ignore.</i>",
            parse_mode="HTML",
        )
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

    # BC-055: warn operators who exposed the hub to the LAN without a token.
    _warn_if_exposed_without_auth()

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
                "snooze_expired": format_snooze_expired,
                "auto_fix_regression": format_auto_fix_regression,
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

    # BC-089: periodic sweep of snoozed findings whose expiry has passed.
    _snooze_task = asyncio.create_task(_snooze_expiry_loop_forever())

    yield

    # BC-020: cancel KEV refresh loop on shutdown.
    _kev_task.cancel()
    _heartbeat_task.cancel()
    _snooze_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await _kev_task
    with contextlib.suppress(asyncio.CancelledError):
        await _snooze_task
    with contextlib.suppress(asyncio.CancelledError):
        await _heartbeat_task

    assert tg_app is not None
    assert tg_app.updater is not None
    await tg_app.updater.stop()
    await tg_app.stop()
    await tg_app.shutdown()


api = FastAPI(title="Beaconry Hub", version=SOFTWARE_VERSION, lifespan=lifespan)


# BC-062: API version middleware.
# - Adds `X-Beaconry-API` to every response so clients can tell what
#   contract the hub serves.
# - Inspects `X-Beaconry-API-Expect` on requests. Missing → no-op
#   (unversioned client / curl). Present + different from API_VERSION:
#   log WARNING. If BEACONRY_STRICT_API=1 → short-circuit 412.
@api.middleware("http")
async def _api_version_middleware(request: _FastAPIRequest, call_next):  # type: ignore[no-untyped-def]
    expected = request.headers.get("X-Beaconry-API-Expect", "")
    if expected and expected != str(API_VERSION):
        log.warning(
            "BC-062: client api-version mismatch — client expects %r, hub serves %r",
            expected,
            API_VERSION,
        )
        if STRICT_API:
            from fastapi.responses import JSONResponse

            resp = JSONResponse(
                status_code=412,
                content={
                    "detail": "api version mismatch",
                    "client_expects": expected,
                    "hub_serves": API_VERSION,
                },
            )
            resp.headers["X-Beaconry-API"] = str(API_VERSION)
            return resp
    response = await call_next(request)
    response.headers["X-Beaconry-API"] = str(API_VERSION)
    return response


# ── BC-055: Bearer auth + HTTP management surface ─────────────────────────


def _check_bearer(request: _FastAPIRequest, expected: str) -> None:
    """Shared bearer check. Empty `expected` = auth disabled."""
    import hmac as _hmac

    if not expected:
        return
    header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not header.startswith(prefix):
        raise HTTPException(status_code=401, detail="unauthorized")
    provided = header[len(prefix) :]
    if not _hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="unauthorized")


async def require_scout_token(request: _FastAPIRequest) -> None:
    """BC-055: gates `/scan` (scout → hub ingest). Reads `HUB_SCOUT_TOKEN`.
    Separate from the CLI token so a scout-side leak doesn't compromise
    the operator surface and vice versa.
    """
    _check_bearer(request, os.environ.get("HUB_SCOUT_TOKEN", ""))


async def require_cli_token(request: _FastAPIRequest) -> None:
    """BC-055: gates the operator surface (/findings, /audit, /fleet,
    /scan/trigger, PATCH /findings). Reads `HUB_CLI_TOKEN`.
    """
    _check_bearer(request, os.environ.get("HUB_CLI_TOKEN", ""))


_LOOPBACK_ADDRS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1", ""})


def _warn_if_exposed_without_auth() -> None:
    """BC-055 safety net: operator opened the hub to the LAN but forgot
    one of the tokens. Non-fatal — reverse proxy may terminate auth upstream.
    """
    publish = os.environ.get("HUB_PUBLISH_ADDR", "127.0.0.1")
    if publish in _LOOPBACK_ADDRS:
        return
    missing = [
        name for name in ("HUB_SCOUT_TOKEN", "HUB_CLI_TOKEN") if not os.environ.get(name, "")
    ]
    if missing:
        log.warning(
            "BC-055: hub bound to %r but %s empty — reachable without auth. "
            "Set the token(s) or terminate auth at a reverse proxy.",
            publish,
            " + ".join(missing),
        )


_FINDINGS_ENDPOINT_COLS = (
    "kev",
    "internet_facing",
    "cve_id",
    "severity",
    "cvss",
    "host",
    "source_ref",
    "package",
    "status",
    "tier",
)


@api.get("/findings", dependencies=[Depends(require_cli_token)])
async def http_findings(
    status: str | None = None,
    host: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """BC-055: read-only JSON view of findings matching `make findings`."""
    if not DB_PATH.exists():
        return []
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        schema_cols = {row[1] for row in con.execute("PRAGMA table_info(findings)")}
        selected = [c for c in _FINDINGS_ENDPOINT_COLS if c in schema_cols]
        if not selected:
            # `findings` table absent or has none of the expected columns —
            # schema not initialized yet. Treat as empty, not SQL error.
            return []
        sql = f"SELECT {', '.join(selected)} FROM findings"
        conds: list[str] = []
        params: list[str | int] = []
        if status:
            conds.append("status = ?")
            params.append(status)
        if host:
            conds.append("host = ?")
            params.append(host)
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += " LIMIT ?"
        params.append(max(1, min(limit, 10000)))
        rows: list[dict] = []
        for r in con.execute(sql, tuple(params)).fetchall():
            row = dict(zip(selected, r, strict=True))
            for mc in _FINDINGS_ENDPOINT_COLS:
                row.setdefault(mc, None)
            rows.append(row)
    finally:
        con.close()
    return rows


def _read_audit_events() -> list[dict]:
    audit_path = DATA_DIR / "audit.log"
    if not audit_path.exists():
        return []
    events: list[dict] = []
    for line in audit_path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    events.reverse()
    return events


def _load_finding_lookup() -> dict[str, tuple[str, str, str | None]]:
    lookup: dict[str, tuple[str, str, str | None]] = {}
    if not DB_PATH.exists():
        return lookup
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            cols = {row[1] for row in con.execute("PRAGMA table_info(findings)")}
            has_alias = "cve_alias" in cols
            select_cols = "id, cve_id, host, cve_alias" if has_alias else "id, cve_id, host"
            for r in con.execute(f"SELECT {select_cols} FROM findings").fetchall():
                lookup[r[0]] = (r[1], r[2], r[3] if has_alias else None)
        finally:
            con.close()
    except sqlite3.Error:
        pass
    return lookup


def _audit_event_matches(
    e: dict,
    *,
    since: int | None,
    until: int | None,
    event_types: set[str] | None,
    cve: str | None,
    host: str | None,
    finding: str | None,
) -> bool:
    ts = e.get("ts", 0)
    if since is not None and ts < since:
        return False
    if until is not None and ts > until:
        return False
    if event_types and e.get("event") not in event_types:
        return False
    if cve and e.get("cve_id") != cve and e.get("cve_alias") != cve:
        return False
    if host and e.get("host") != host:
        return False
    return not (finding and e.get("finding_id") != finding)


@api.get("/audit", dependencies=[Depends(require_cli_token)])
async def http_audit(
    last: int = 50,
    since: int | None = None,
    until: int | None = None,
    event: str | None = None,
    cve: str | None = None,
    host: str | None = None,
    finding: str | None = None,
) -> list[dict]:
    """BC-055: enriched audit tail (newest first)."""
    events = _read_audit_events()
    lookup = _load_finding_lookup()
    event_types = {e.strip() for e in event.split(",")} if event else None
    cap = max(1, min(last, 10000))
    out: list[dict] = []
    for e in events:
        fid = e.get("finding_id")
        if fid and fid in lookup:
            cve_id, h, alias = lookup[fid]
            e.setdefault("cve_id", cve_id)
            e.setdefault("host", h)
            if alias is not None:
                e.setdefault("cve_alias", alias)
        if not _audit_event_matches(
            e,
            since=since,
            until=until,
            event_types=event_types,
            cve=cve,
            host=host,
            finding=finding,
        ):
            continue
        out.append(e)
        if len(out) >= cap:
            break
    return out


@api.get("/fleet", dependencies=[Depends(require_cli_token)])
async def http_fleet() -> list[dict]:
    """BC-055/BC-061: per-host fleet summary.

    BC-061: driven by the `scouts` registry (LEFT JOIN findings) so zero-
    finding scouts are visible. Each row carries a liveness `status`
    computed from `now - last_posted_ts` against the configurable
    thresholds.
    """
    if not DB_PATH.exists():
        return []
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            schema_tables = {
                row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            # Pre-BC-061 DBs may not have the scouts table yet (upgrade in flight);
            # fall back to the findings-derived shape without liveness.
            if "scouts" not in schema_tables:
                rows = con.execute(
                    "SELECT host, MAX(last_seen_ts), "
                    "SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END), "
                    "COUNT(*) FROM findings GROUP BY host"
                ).fetchall()
                legacy = True
            else:
                # BC-063: four extra per-scout columns surface in /fleet.
                # Columns 7-10 are nullable (pre-BC-063 scouts); callers
                # treat them as optional / render "-" / "?" when missing.
                # BC-092: also surface s.role so `make status` can render
                # the canary ring position. Column index 11; None for pre-
                # BC-092 scouts (SELECT returns the DB default `workstation`
                # via ALTER TABLE in init_db).
                rows = con.execute("""
                    SELECT s.host,
                           s.first_seen_ts,
                           s.last_posted_ts,
                           s.last_sbom_count,
                           COALESCE(MAX(f.last_seen_ts), 0),
                           COALESCE(SUM(CASE WHEN f.status = 'open' THEN 1 ELSE 0 END), 0),
                           COUNT(f.id),
                           s.software_version,
                           s.api_version,
                           s.last_health,
                           s.last_scan_ts,
                           s.role
                    FROM scouts s
                    LEFT JOIN findings f ON f.host = s.host
                    GROUP BY s.host, s.first_seen_ts, s.last_posted_ts,
                             s.last_sbom_count, s.software_version,
                             s.api_version, s.last_health, s.last_scan_ts,
                             s.role
                """).fetchall()
                legacy = False
        finally:
            con.close()
    except sqlite3.Error:
        return []
    now = int(time.time())
    if legacy:
        out = [
            {
                "host": r[0],
                "last_seen_ts": int(r[1] or 0),
                "open_findings": int(r[2] or 0),
                "total_findings": int(r[3] or 0),
            }
            for r in rows
            if r[0]
        ]
        out.sort(key=lambda e: e["last_seen_ts"], reverse=True)
        return out
    # BC-064: heartbeat-stale threshold. 2× scout interval (default 60s)
    # with a 300s floor so operators who disabled heartbeats
    # (SCOUT_HEARTBEAT_INTERVAL_S=0) fall back to BC-061 scan-cadence
    # semantics instead of flapping every scout to stale instantly.
    _hb_interval = int(os.environ.get("SCOUT_HEARTBEAT_INTERVAL_S", "60"))
    _hb_stale_threshold = max(2 * _hb_interval, 300)
    out = [
        {
            "host": r[0],
            "first_seen_ts": int(r[1] or 0),
            "last_posted_ts": int(r[2] or 0),
            "last_sbom_count": int(r[3] or 0),
            # BC-055 legacy: findings-derived watermark. Kept in the response
            # for back-compat; `status.py` reads `last_heartbeat_ts` instead
            # since BC-064 (findings watermark was misleading in FLEET UX).
            "last_seen_ts": int(r[4] or 0),
            "open_findings": int(r[5] or 0),
            "total_findings": int(r[6] or 0),
            "status": _fleet_status(int(r[2] or 0), now),
            # BC-063: per-scout metadata. None for pre-BC-063 scouts.
            "software_version": r[7],
            "api_version": r[8],
            "last_health": r[9],
            "last_scan_ts": int(r[10]) if r[10] is not None else None,
            # BC-092: canary ring role. Falls back to 'workstation' for pre-
            # BC-092 scouts (the schema ALTER adds the column with that default).
            "role": r[11] if r[11] else "workstation",
            # BC-064: honest "last contact" + stale flag so the UI can
            # drop a cached last_health label that we can't trust anymore.
            "last_heartbeat_ts": int(r[2] or 0),
            "heartbeat_stale": bool(int(r[2] or 0) > 0 and now - int(r[2]) > _hb_stale_threshold),
        }
        for r in rows
        if r[0]
    ]
    out.sort(key=lambda e: e["last_posted_ts"], reverse=True)
    return out


# Operator-allowed PATCH targets. Reflects the states a human can
# transition a finding into — system states (`regressed`, `verification_failed`)
# are not operator-initiated.
_OPERATOR_MUTABLE_STATUSES: frozenset[str] = frozenset({"snoozed", "accepted", "fixed", "open"})


@api.patch("/findings/{finding_id}", dependencies=[Depends(require_cli_token)])
async def http_patch_finding(finding_id: str, payload: dict) -> dict:
    """BC-055: mutate finding status from a remote CLI.

    Body: `{"status": "snoozed" | "accepted" | "fixed" | "open"}`.
    Returns the updated row. 404 on unknown id, 400 on invalid status.
    """
    new_status = payload.get("status", "")
    if new_status not in _OPERATOR_MUTABLE_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"invalid status {new_status!r}; must be one of "
            f"{sorted(_OPERATOR_MUTABLE_STATUSES)}",
        )
    con = sqlite3.connect(DB_PATH)
    try:
        row = con.execute(
            "SELECT id, host, cve_id, status FROM findings WHERE id = ?",
            (finding_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail=f"no finding with id={finding_id!r}")
        old_status = row[3]
        con.execute(
            "UPDATE findings SET status = ? WHERE id = ?",
            (new_status, finding_id),
        )
        con.commit()
    finally:
        con.close()
    _audit.append(
        "status_change",
        finding_id=finding_id,
        host=row[1],
        cve_id=row[2],
        old_status=old_status,
        new_status=new_status,
        source="http",
    )
    return {
        "id": finding_id,
        "host": row[1],
        "cve_id": row[2],
        "old_status": old_status,
        "status": new_status,
    }


@api.post("/scan/trigger", dependencies=[Depends(require_cli_token)])
async def http_scan_trigger() -> dict:
    """BC-055: proxy to the scout's local scan trigger.

    Scout stays bound inside the compose network — no host-side port
    exposure. The hub reaches it on `scout:8001` (Docker DNS).
    """
    import urllib.error
    import urllib.request

    scout_url = os.environ.get("SCOUT_TRIGGER_URL", "http://scout:8001/scan/trigger")
    req = urllib.request.Request(scout_url, method="POST", data=b"")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 — internal URL
            status_code = resp.status
            body = resp.read().decode() or "{}"
    except urllib.error.HTTPError as exc:
        return {"status": "error", "code": exc.code, "detail": exc.read().decode()[:200]}
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
        raise HTTPException(
            status_code=503, detail=f"scout unreachable: {exc.__class__.__name__}"
        ) from exc
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        parsed = {"raw": body[:200]}
    parsed.setdefault("code", status_code)
    return parsed


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
        # BC-066: configured executor (manual|docker). Lets operators
        # confirm `BEACONRY_EXECUTOR` is wired without reading env.
        "executor": EXECUTOR_NAME,
        # BC-086: configured auto-fix threshold (consecutive absent scans
        # before a finding flips to auto_fixed). Surfaced so `make status`
        # shows it without the operator having to read env.
        "auto_fix_threshold": AUTO_FIX_THRESHOLD,
    }
    # KEV is non-fatal — stale/unfetched surface in /healthz but don't flip "degraded".
    # BC-066: executor is a policy label, not a health signal — exclude it too.
    # BC-086: auto_fix_threshold is a config echo, not a health signal — exclude.
    bool_checks = [
        v
        for k, v in checks.items()
        if k
        not in (
            "last_scan_ts",
            "kev",
            "kev_last_fetch_s",
            "kev_count",
            "executor",
            "auto_fix_threshold",
        )
    ]
    degraded = any(v not in ("ok", "polling", "mock") for v in bool_checks)
    return {
        "status": "degraded" if degraded else "ok",
        # BC-062: three independent version axes. Software is the shipped
        # release (VERSION file). API is the HTTP contract integer.
        # Schema is the SQLite PRAGMA user_version this binary expects.
        "version": {
            "software": SOFTWARE_VERSION,
            "api": API_VERSION,
            "schema": EXPECTED_SCHEMA_VERSION,
        },
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
    Pure render; sending is the adapter's job.

    BC-086 post-ship: each bullet carries the source_ref (image/target) so
    the operator can tell which service the CVE belongs to without
    cross-referencing. The host header matches format_notification's style
    for visual consistency.
    """
    import html

    host = suppressed[0].host if suppressed else ""
    lines: list[str] = []
    if host:
        lines.append(f"🖥️ <code>{html.escape(host)}</code>")
    lines.append(f"📋 <b>{len(suppressed)} additional finding(s) suppressed this run</b>")
    kev_count = sum(1 for f in suppressed if f.kev)
    if kev_count:
        lines.append(
            f"⚠️ <b>{kev_count} suppressed finding(s) are KEV</b> — "
            f"raise <code>BEACONRY_MAX_FINDINGS_PER_RUN</code> to see them individually."
        )
    for f in suppressed[:10]:
        prefix = "🚨 " if f.kev else ""
        # Truncate source_ref if very long to keep the bullet readable.
        src = f.source_ref if len(f.source_ref) <= 40 else f.source_ref[:37] + "…"
        lines.append(
            f"• {prefix}<code>{html.escape(f.cve_id)}</code> {f.severity} "
            f"CVSS {f.cvss:.1f} — <code>{html.escape(f.package)}</code> "
            f"on <code>{html.escape(src)}</code>"
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
      - content hash differs from last send AND DIGEST_ON_CONTENT_CHANGE
        → send (content_changed). BC-065 adds the gate so noisy homelabs
        can keep the 24h throttle meaningful.
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
    if DIGEST_ON_CONTENT_CHANGE and last_hash != content_hash:
        return True, content_hash, "content_changed"
    if now - last_sent_ts >= DIGEST_INTERVAL_S:
        return True, content_hash, "interval_elapsed"
    return False, content_hash, "throttled"


def record_digest_sent(host: str, suppressed: list[Finding], *, now: int) -> None:
    """BC-036: persist last-sent state + emit `digest_sent` audit event.
    BC-058: also emit `notify_digest_sent` with the full list of
    suppressed finding_ids so operators can trace which items the
    digest covered."""
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
    _audit.append(
        "notify_digest_sent",
        host=host,
        finding_ids=[f.id for f in suppressed],
        count=len(suppressed),
    )


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
    """BC-035: thin wrapper — all enabled notification adapters receive the digest.
    BC-060: audit on dispatch failure so operators can see which digests
    never landed. Exception is re-raised to preserve caller's existing
    error-handling (log.warning in process_sbom_entry)."""
    if not suppressed:
        return
    try:
        await _dispatch_digest(suppressed)
    except Exception as exc:
        _audit.append(
            "notify_digest_sent_failed",
            finding_ids=[f.id for f in suppressed],
            count=len(suppressed),
            reason=_format_fail_reason(exc),
        )
        raise


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


def _record_manual_claim(finding_id: str, now_ts: int) -> None:
    """BC-066: write `last_claim_ts = now_ts` for a finding. Idempotent;
    tolerant of pre-BC-066 DBs where the column may not exist yet."""
    con = sqlite3.connect(DB_PATH)
    try:
        with contextlib.suppress(sqlite3.OperationalError):
            con.execute(
                "UPDATE findings SET last_claim_ts = ? WHERE id = ?",
                (now_ts, finding_id),
            )
            con.commit()
    finally:
        con.close()


async def manual_claim_sweep(*, now_ts: int | None = None) -> int:
    """BC-066: for every open finding with an overdue manual-fix claim,
    send a reminder notification and bump `last_claim_ts` so the next
    reminder lands one interval later.

    Returns the number of reminders sent. No-op when
    `MANUAL_REMINDER_INTERVAL_S == 0`.

    Qualifying criteria:
      - status='open' (reconcile's auto-close for `fixed_verified` is the
        natural off-switch — a confirmed fix stops the sweep)
      - last_claim_ts > 0 (operator actually tapped [Fix now])
      - now - last_claim_ts > MANUAL_REMINDER_INTERVAL_S

    Called from `_heartbeat_loop_forever` on the same tick as the BC-035
    sweep — one background loop, two policies.
    """
    if MANUAL_REMINDER_INTERVAL_S <= 0:
        return 0
    con = sqlite3.connect(DB_PATH)
    try:
        now = now_ts if now_ts is not None else int(time.time())
        cutoff = now - MANUAL_REMINDER_INTERVAL_S
        rows = con.execute(
            "SELECT id, host, cve_id, package, version, fixed_version, severity, "
            "cvss, source_type, source_ref, description, tier, kev, last_claim_ts "
            "FROM findings "
            "WHERE status='open' AND last_claim_ts > 0 AND last_claim_ts < ?",
            (cutoff,),
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
        claim_age_s = max(1, now - int(row[13] or 0))
        try:
            # Reuse the heartbeat notifier — a reminder "this is still open
            # N minutes after you claimed you'd fix it" is semantically the
            # same shape. The notifier adapter formats; the audit event name
            # distinguishes the two cadences for forensics.
            await notify_heartbeat(finding, now - claim_age_s)
        except Exception as e:  # noqa: BLE001
            log.warning("manual-claim reminder failed for %s: %s", finding.cve_id, e)
            continue
        con.execute(
            "UPDATE findings SET last_claim_ts = ?, last_notified_ts = ? WHERE id = ?",
            (now, now, finding.id),
        )
        _audit.append(
            "manual_fix_reminder_sent",
            finding_id=finding.id,
            cve_id=finding.cve_id,
            claim_age_s=claim_age_s,
        )
        sent += 1
    con.commit()
    con.close()
    if sent:
        log.info("BC-066: manual-claim sweep sent %d reminder(s)", sent)
    return sent


async def snooze_expiry_sweep(*, now_ts: int | None = None) -> int:
    """BC-089: find snoozed findings whose expiry has passed, flip to `open`,
    audit, optionally fire a distinct expiry notification.

    SQL filter `snooze_until_ts > 0` preserves pre-BC-089 legacy snoozes
    (which have `snooze_until_ts = 0` = indefinite). Returns the number of
    rows flipped — operator can observe via DEBUG logs or `make audit
    EVENT=snooze_expired`."""
    now = now_ts if now_ts is not None else int(time.time())
    con = sqlite3.connect(DB_PATH)
    try:
        try:
            rows = con.execute(
                "SELECT id, host, cve_id, package, version, fixed_version, severity, "
                "cvss, source_type, source_ref, description, tier, kev, "
                "snooze_until_ts FROM findings "
                "WHERE status = 'snoozed' AND snooze_until_ts > 0 "
                "AND snooze_until_ts <= ?",
                (now,),
            ).fetchall()
        except sqlite3.OperationalError:
            # init_db hasn't run or column missing on pre-BC-089 DB.
            return 0
        if not rows:
            return 0
        expired: list[tuple[Finding, int]] = []
        for row in rows:
            f = Finding(
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
            expired.append((f, row[13]))
            con.execute(
                "UPDATE findings SET status = 'open', snooze_until_ts = 0 WHERE id = ?",
                (f.id,),
            )
        con.commit()
    finally:
        con.close()

    for f, prior_snooze_until in expired:
        _audit.append(
            "snooze_expired",
            finding_id=f.id,
            cve_id=f.cve_id,
            host=f.host,
            snooze_until_ts=prior_snooze_until,
        )
        if SNOOZE_NOTIFY_ON_EXPIRE:
            try:
                await notify_snooze_expired(f)
            except Exception as e:  # noqa: BLE001
                log.warning("BC-089 notify_snooze_expired failed for %s: %s", f.cve_id, e)
    log.debug("BC-089 snooze_expiry_sweep flipped=%d now_ts=%d", len(expired), now)
    return len(expired)


async def _snooze_expiry_loop_forever() -> None:  # pragma: no cover - infinite loop
    """BC-089: background task started by lifespan; polls every
    SNOOZE_SWEEP_INTERVAL_S (default 60s)."""
    while True:
        try:
            await snooze_expiry_sweep()
        except Exception as e:  # noqa: BLE001
            log.warning("BC-089: snooze expiry sweep failed: %s", e)
        await asyncio.sleep(SNOOZE_SWEEP_INTERVAL_S)


async def _heartbeat_loop_forever() -> (
    None
):  # pragma: no cover - infinite async loop; tested via sweep directly
    while True:
        # BC-067: run manual-claim sweep BEFORE the general heartbeat sweep.
        # manual_claim_sweep bumps both `last_claim_ts` AND `last_notified_ts`
        # on fire; heartbeat_sweep's WHERE clause filters on `last_notified_ts
        # < cutoff`. Running manual first means a finding overdue on both
        # timers emits ONE reminder (manual) and the heartbeat sweep sees the
        # fresh `last_notified_ts` and skips it — instead of double-firing
        # ~10ms apart on the same tick.
        try:
            await manual_claim_sweep()
        except Exception as e:  # noqa: BLE001
            log.warning("BC-066: manual-claim sweep failed: %s", e)
        try:
            await heartbeat_sweep()
        except Exception as e:  # noqa: BLE001
            log.warning("BC-035: heartbeat sweep failed: %s", e)
        await asyncio.sleep(HEARTBEAT_SWEEP_INTERVAL_S)


def _mark_scan_now() -> None:
    """BC-010: bump the last-scan timestamp surfaced via /healthz."""
    global _last_scan_ts
    _last_scan_ts = int(time.time())


@api.post("/scan", dependencies=[Depends(require_scout_token)])
async def receive_scan(payload: dict, request: _FastAPIRequest) -> dict:
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
    # BC-061: register the scout BEFORE any candidate processing so even
    # an empty-SBOM scan (or a scan that crashes grype downstream) still
    # shows the scout as active in /fleet.
    # BC-063: capture the scout's self-reported metadata (missing on
    # pre-BC-063 scouts → persisted as NULL).
    record_scout_post(
        host,
        now=int(time.time()),
        sbom_count=len(sboms),
        software_version=payload.get("software_version"),
        api_version=payload.get("api_version"),
        health=payload.get("health"),
        role=payload.get("role"),  # BC-092
    )
    # BC-042: audit every scan reception, even if 0 candidates. Closes the
    # "did scout even post?" blind spot — a silent scout and a scout-with-0
    # candidates were indistinguishable in the audit log pre-BC-042.
    # BC-075: metrics-fuel audit event — written ONLY when scout opts in via
    # `X-Beaconry-Metrics-Opt-In: 1`. Privacy-first default. CVE processing,
    # notifications, DB persistence, fleet view all keep working regardless.
    if request.headers.get("X-Beaconry-Metrics-Opt-In") == "1":
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

    return await _ingest_candidates(
        host, candidates, now_ts=int(time.time()), sbom_count=len(sboms)
    )


@api.post("/heartbeat", dependencies=[Depends(require_scout_token)])
async def receive_heartbeat(payload: dict, request: _FastAPIRequest) -> dict:
    """BC-063: lightweight scout keepalive — host + version + health.

    No SBOMs. No candidate processing. Hub updates the scouts row's
    `last_posted_ts` + version + health, but leaves `last_scan_ts` +
    `last_sbom_count` untouched so FLEET's LAST SCAN column reflects
    real scan activity (not every 60s keepalive). Scout bearer token
    required — same auth surface as /scan.

    BC-075: `heartbeat_received` is a metrics-fuel audit event — written
    ONLY when scout opts in via `X-Beaconry-Metrics-Opt-In: 1`. Fleet
    health (scouts registry) still updates for every heartbeat regardless.
    """
    host = payload.get("host", "unknown")
    now = int(time.time())
    record_scout_post(
        host,
        now=now,
        sbom_count=0,  # ignored on heartbeat path
        software_version=payload.get("software_version"),
        api_version=payload.get("api_version"),
        health=payload.get("health"),
        bump_scan_ts=False,
        role=payload.get("role"),  # BC-092
    )
    if request.headers.get("X-Beaconry-Metrics-Opt-In") == "1":
        _audit.append(
            "heartbeat_received",
            host=host,
            health=payload.get("health") or "unknown",
        )
    return {"ok": True, "host": host, "ts": now}


async def _dispatch_regression_notifications(
    host: str,
    regressed: list[Finding],
    regressed_from_auto_fix: list[Finding],
    auto_fixed: list[Finding],
) -> None:
    """BC-031 + BC-086: fire the two regression notifier paths + log the
    silent auto-fix closes. Extracted from `_ingest_candidates` to keep
    that function under the complexity gate; also makes the two regression
    classes trivially testable in isolation.

    `regressed` → BC-031 "🔥 FIX REGRESSED" (operator-initiated fix came back).
    `regressed_from_auto_fix` → BC-086 "🔥 AUTO-FIX REGRESSED" (system-inferred
    close came back — distinct message + audit event so operator can diff
    the two investigation paths).
    `auto_fixed` → silent close; just a log line here.
    """
    if regressed:
        log.warning("BC-031: %d regression(s) detected on %s", len(regressed), host)
    for f in regressed:
        con = sqlite3.connect(DB_PATH)
        row = con.execute("SELECT last_fixed_ts FROM findings WHERE id=?", (f.id,)).fetchone()
        con.close()
        last_fixed_ts = (row[0] if row else 0) or 0
        try:
            await notify_regression(f, last_fixed_ts)
        except Exception as e:  # noqa: BLE001
            log.warning("notify_regression failed for %s: %s", f.cve_id, e)
    if regressed_from_auto_fix:
        log.warning("BC-086: %d AUTO-FIX regression(s) on %s", len(regressed_from_auto_fix), host)
    for f in regressed_from_auto_fix:
        con = sqlite3.connect(DB_PATH)
        row = con.execute("SELECT auto_fixed_ts FROM findings WHERE id=?", (f.id,)).fetchone()
        con.close()
        auto_fixed_ts = (row[0] if row else 0) or 0
        try:
            await notify_auto_fix_regression(f, auto_fixed_ts)
        except Exception as e:  # noqa: BLE001
            log.warning("notify_auto_fix_regression failed for %s: %s", f.cve_id, e)
    if auto_fixed:
        log.info("BC-086: %d auto-fix close(s) on %s (silent)", len(auto_fixed), host)


async def _ingest_candidates(
    host: str,
    candidates: list[Finding],
    *,
    now_ts: int,
    sbom_count: int | None = None,
) -> dict:
    """BC-038: extracted ingest flow — dedup+cap, save+notify, digest, reconcile.

    Shared between `receive_scan` (real path, invokes `process_sbom_entry` first)
    and `/test/scan` (test path, candidates are pre-matched). `now_ts` is
    injected so scenarios can simulate clock advancement without real sleeps.

    BC-067: `sbom_count` is the number of SBOMs the scout POSTed on this
    scan — used by the partial-inventory guard to decide whether reconcile
    is safe. Scenario / test path doesn't pass it (always None); real
    `receive_scan` does.
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
    # BC-067: partial-inventory guard. If the scout's SBOM count dropped
    # dramatically vs. the rolling baseline (socket hiccup, syft crashed on
    # one image), reconcile would mass-close everything missing from the
    # shrunk set — then re-flag them all as regressions on the next scan.
    # Skip reconcile when the signal looks like noise, but still save new
    # findings (candidates path above is unaffected).
    if _is_partial_inventory(host, sbom_count):
        _audit.append(
            "reconcile_skipped_partial_inventory",
            host=host,
            sbom_count=sbom_count or 0,
            baseline=_scout_sbom_baseline.get(host, 0),
        )
        log.warning(
            "BC-067: reconcile skipped for %s — sbom_count=%s vs baseline=%s (<50%%)",
            host,
            sbom_count,
            _scout_sbom_baseline.get(host, 0),
        )
        regressed, auto_fixed, regressed_from_auto_fix = [], [], []
    else:
        _update_scout_sbom_baseline(host, sbom_count)
        regressed, auto_fixed, regressed_from_auto_fix = reconcile_scan(host, scanned_ids)
    await _dispatch_regression_notifications(host, regressed, regressed_from_auto_fix, auto_fixed)

    return {
        "status": "ok",
        "findings": new_findings,
        "regressed": len(regressed),
        "auto_fixed": len(auto_fixed),
        "regressed_from_auto_fix": len(regressed_from_auto_fix),
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

    # BC-067: gate /test/* behind the CLI bearer so an operator who
    # forgets to unset BEACONRY_TEST_MODE in production doesn't expose
    # DB wipe, finding injection, and status forgery to anyone on the
    # LAN. The scenario harness in `poc/scenarios/*` already sends the
    # CLI bearer, so no test-stack behaviour change.
    @api.post("/test/wipe", dependencies=[Depends(require_cli_token)])
    async def test_wipe() -> dict:
        """BC-038: reset DB + audit log so each scenario starts clean."""
        if DB_PATH.exists():
            DB_PATH.unlink()
        audit_path = DATA_DIR / "audit.log"
        if audit_path.exists():
            audit_path.unlink()
        init_db()
        return {"status": "wiped"}

    @api.post("/test/scan", dependencies=[Depends(require_cli_token)])
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

    @api.post("/test/sweep", dependencies=[Depends(require_cli_token)])
    async def test_sweep(payload: dict) -> dict:
        """BC-038: invoke heartbeat_sweep with an injected `now` timestamp."""
        at = payload.get("at") or int(time.time())
        sent = await heartbeat_sweep(now_ts=at)
        return {"sent": sent, "at": at}

    @api.post("/test/set_status", dependencies=[Depends(require_cli_token)])
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
