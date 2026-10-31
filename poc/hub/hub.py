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
    import urgency as _urgency
    import waterfall as _waterfall
    from beaconry_health import build_router, check_binary, check_db, check_dir_populated
except ImportError:
    # Host test path: imported as poc.hub.hub, package at poc.beaconry_health
    from poc.beaconry_health import build_router, check_binary, check_db, check_dir_populated
    from poc.hub import audit as _audit
    from poc.hub import kev as _kev
    from poc.hub import urgency as _urgency
    from poc.hub import waterfall as _waterfall

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
            kev          INTEGER DEFAULT 0
        )
    """)
    # Idempotent migrations for DBs created before each column existed.
    with contextlib.suppress(sqlite3.OperationalError):
        con.execute("ALTER TABLE findings ADD COLUMN tier INTEGER DEFAULT 3")
    with contextlib.suppress(sqlite3.OperationalError):
        con.execute("ALTER TABLE findings ADD COLUMN kev INTEGER DEFAULT 0")
    con.commit()
    con.close()


def save_finding(f: Finding) -> bool:
    """Insert finding. Returns True if new (not a duplicate)."""
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(
            """INSERT OR IGNORE INTO findings
               (id, host, cve_id, package, version, fixed_version, severity,
                cvss, source_type, source_ref, description, found_at, tier, kev)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                int(time.time()),
                f.tier,
                1 if f.kev else 0,
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
        )
    return new_row


def load_finding(finding_id: str) -> Finding | None:
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT id,host,cve_id,package,version,fixed_version,severity,"
        "cvss,source_type,source_ref,description,tier,kev FROM findings WHERE id=?",
        (finding_id,),
    ).fetchone()
    con.close()
    if row is None:
        return None
    # Last column (kev) is INTEGER in SQLite; coerce to bool for the dataclass.
    *head, kev_int = row
    return Finding(*head, kev=bool(kev_int))


def set_status(finding_id: str, status: str) -> None:
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE findings SET status=? WHERE id=?", (status, finding_id))
    con.commit()
    con.close()
    # BC-029: every operator-visible state transition lands in the audit log.
    _audit.append("status_change", finding_id=finding_id, status=status)


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


async def process_sbom_entry(entry: dict, host: str) -> list[Finding]:
    source_meta = entry.get("source_meta", {})
    sbom = entry.get("sbom", {})

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
        )
        # BC-020: wire the urgency ladder. internet_facing deferred to a later
        # story — treat as False for V1. KEV presence trumps CVSS per the Tier 0 rule.
        f.tier = _urgency.classify_tier(f, kev=is_kev, internet_facing=False)
        findings.append(f)

    return findings


# ── Verification ──────────────────────────────────────────────────────────────


async def verify_fix(finding: Finding) -> bool:
    """Re-scan after fix. Returns True if the CVE is no longer present."""
    if MOCK:
        return True

    if finding.source_type != "docker":
        return True  # can't auto-verify non-docker in POC

    base = finding.source_ref.split(":")[0]
    new_image = f"{base}:latest"

    proc = await asyncio.create_subprocess_exec(
        "syft",
        new_image,
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
    lines.extend(
        [
            f"{emoji} <b>{esc(finding.severity)} — {esc(finding.cve_id)}</b>",
            f"CVSS: <code>{finding.cvss:.1f}</code> · Host: <code>{esc(finding.host)}</code>",
            f"Target: <code>{esc(finding.source_ref)}</code> ({esc(finding.source_type)})",
            f"Package: <code>{esc(finding.package)} {esc(finding.version)}</code> "
            f"→ fixed in <code>{esc(finding.fixed_version)}</code>",
        ]
    )
    if finding.description:
        desc = esc(finding.description[:160])
        lines.append(f"\n<i>{desc}</i>")
    lines.append(f"\n<code>{esc(fix_cmd)}</code>")
    return "\n".join(lines)


def _render_mock_telegram(text: str) -> str:
    """Render a Telegram message as a boxed stdout notification."""
    width = max((len(line) for line in text.splitlines()), default=0) + 2
    width = max(width, 60)
    top = "    ╭" + "─" * width + "╮"
    bot = "    ╰" + "─" * width + "╯"
    body = "\n".join(f"    │ {line.ljust(width - 1)}│" for line in text.splitlines())
    buttons = "      Inline keyboard: [🔧 Fix now] [⏰ Snooze 4h] [✅ Accept risk]"
    return "\n".join([top, body, bot, buttons])


async def notify(finding: Finding) -> None:
    text = format_notification(finding)

    if MOCK:
        print("    [mock] telegram send: notification dispatched")
        print("    ==> Notification")
        print(_render_mock_telegram(text))
        return

    if tg_app is None:
        log.warning("Telegram app not ready — skipping notification")
        return

    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔧 Fix now", callback_data=f"fix:{finding.id}"),
                InlineKeyboardButton("⏰ Snooze 4h", callback_data=f"snooze:{finding.id}:4"),
            ],
            [InlineKeyboardButton("✅ Accept risk", callback_data=f"ack:{finding.id}")],
        ]
    )
    await tg_app.bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=text,
        parse_mode="HTML",
        reply_markup=keyboard,
    )


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

    yield

    # BC-020: cancel KEV refresh loop on shutdown.
    _kev_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await _kev_task

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


async def send_digest(suppressed: list[Finding]) -> None:
    """Send a single HTML digest message summarising suppressed findings."""
    import html

    if not suppressed or tg_app is None:
        return
    lines = [f"📋 <b>{len(suppressed)} additional finding(s) suppressed this run</b>"]
    # BC-022: if any suppressed finding is KEV, surface a loud banner above the
    # bullet list — otherwise Tier 0 findings hide invisibly in prose when
    # #KEV > BEACONRY_MAX_FINDINGS_PER_RUN.
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
    try:
        await tg_app.bot.send_message(
            chat_id=int(TELEGRAM_CHAT_ID), text="\n".join(lines), parse_mode="HTML"
        )
    except Exception as e:  # noqa: BLE001
        log.warning("digest message failed: %s", e)


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
    log.info("Scan from %s: %d SBOM(s)", host, len(sboms))

    candidates: list[Finding] = []
    for entry in sboms:
        try:
            candidates.extend(await process_sbom_entry(entry, host))
        except Exception as e:  # noqa: BLE001
            log.error("Error processing SBOM entry: %s", e)

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
        if not save_finding(f):
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

    if suppressed:
        await send_digest(suppressed)

    return {"status": "ok", "findings": new_findings}


if __name__ == "__main__":
    uvicorn.run("hub:api", host="0.0.0.0", port=8000, log_level="info")
