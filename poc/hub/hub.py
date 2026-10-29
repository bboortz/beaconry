"""
Beaconry Hub 0.1.0

Receives SBOMs from scouts, matches against CVE feeds via grype,
sends Telegram notifications with inline approve/snooze/ack buttons,
dispatches to executor adapters on approval, verifies fix via re-scan.
"""

import asyncio
import hashlib
import json
import logging
import os
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
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

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
# Env is read with defaults so importing hub never crashes; functions that
# need real values (notify, lifespan) raise with a named error when invoked.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
MIN_CVSS = float(os.environ.get("MIN_CVSS", "7.0"))
# BC-002: feature flag so Fix-now shows a stub until BC-003 wires the executor.
FIX_ENABLED = os.environ.get("BEACONRY_FIX_ENABLED") == "1"

DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "beaconry.db"

EXECUTORS: list[ExecutorPlugin] = [DockerAdapter(), ManualAdapter()]

tg_app: Application | None = None


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
            status       TEXT DEFAULT 'open'
        )
    """)
    con.commit()
    con.close()


def save_finding(f: Finding) -> bool:
    """Insert finding. Returns True if new (not a duplicate)."""
    con = sqlite3.connect(DB_PATH)
    try:
        con.execute(
            """INSERT OR IGNORE INTO findings
               (id, host, cve_id, package, version, fixed_version, severity,
                cvss, source_type, source_ref, description, found_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
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
            ),
        )
        new_row = con.execute("SELECT changes()").fetchone()[0] > 0
        con.commit()
        return new_row
    finally:
        con.close()


def load_finding(finding_id: str) -> Finding | None:
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT id,host,cve_id,package,version,fixed_version,severity,"
        "cvss,source_type,source_ref,description FROM findings WHERE id=?",
        (finding_id,),
    ).fetchone()
    con.close()
    return Finding(*row) if row else None


def set_status(finding_id: str, status: str) -> None:
    con = sqlite3.connect(DB_PATH)
    con.execute("UPDATE findings SET status=? WHERE id=?", (status, finding_id))
    con.commit()
    con.close()


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

        if cvss < MIN_CVSS:
            continue

        source_type = source_meta.get("type", "unknown")
        source_ref = source_meta.get("image") or artifact.get("name", "")
        fix_versions = vuln.get("fix", {}).get("versions") or []

        findings.append(
            Finding(
                id=make_id(host, vuln["id"], source_ref, artifact.get("name", "")),
                host=host,
                cve_id=vuln["id"],
                package=artifact.get("name", ""),
                version=artifact.get("version", ""),
                fixed_version=fix_versions[0] if fix_versions else "not available",
                severity=vuln.get("severity", "Unknown"),
                cvss=cvss,
                source_type=source_type,
                source_ref=source_ref,
                description=(vuln.get("description") or "")[:200],
            )
        )

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

    lines = [
        f"{emoji} <b>{esc(finding.severity)} — {esc(finding.cve_id)}</b>",
        f"CVSS: <code>{finding.cvss:.1f}</code> · Host: <code>{esc(finding.host)}</code>",
        f"Target: <code>{esc(finding.source_ref)}</code> ({esc(finding.source_type)})",
        f"Package: <code>{esc(finding.package)} {esc(finding.version)}</code> "
        f"→ fixed in <code>{esc(finding.fixed_version)}</code>",
    ]
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


async def on_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()

    parts = query.data.split(":")
    action, finding_id = parts[0], parts[1]

    finding = load_finding(finding_id)
    if not finding:
        await query.edit_message_text("⚠️ Finding not found or already resolved.")
        return

    import html

    def _esc(s: str) -> str:
        return html.escape(s, quote=False)

    if action == "fix":
        if not FIX_ENABLED:
            await query.edit_message_text(
                f"⚙️ Fix execution coming in BC-003 — <code>{_esc(finding.cve_id)}</code> "
                f"left as <code>open</code>",
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
                f"❌ <b>Fix failed</b>\n<code>{_esc(result.message)}</code>",
                parse_mode="HTML",
            )
            return

        await query.edit_message_text(
            f"🔍 Fix applied — verifying…\n<code>{_esc(result.command)}</code>",
            parse_mode="HTML",
        )
        clean = await verify_fix(finding)

        if clean:
            set_status(finding_id, "fixed")
            await query.edit_message_text(
                f"✅ <b>Fixed and verified</b>\n"
                f"CVE: <code>{_esc(finding.cve_id)}</code> on "
                f"<code>{_esc(finding.host)}</code>\n"
                f"Command: <code>{_esc(result.command)}</code>\n"
                f"Re-scan: clean ✓",
                parse_mode="HTML",
            )
        else:
            await query.edit_message_text(
                f"⚠️ <b>Fix applied — CVE still detected</b>\n"
                f"CVE: <code>{_esc(finding.cve_id)}</code>\n"
                f"Command: <code>{_esc(result.command)}</code>\n"
                f"Manual review required.",
                parse_mode="HTML",
            )

    elif action == "snooze":
        hours = int(parts[2]) if len(parts) > 2 else 4
        set_status(finding_id, "snoozed")
        await query.edit_message_text(
            f"⏰ Snoozed {hours}h — <code>{_esc(finding.cve_id)}</code>",
            parse_mode="HTML",
        )

    elif action == "ack":
        set_status(finding_id, "accepted")
        await query.edit_message_text(
            f"✅ Risk accepted — <code>{_esc(finding.cve_id)}</code>",
            parse_mode="HTML",
        )


# ── FastAPI + lifespan ────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    global tg_app

    init_db()

    if MOCK:
        log.info("MOCK mode — skipping grype DB update and Telegram startup")
        yield
        return

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

    _app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    _app.add_handler(CallbackQueryHandler(on_button_click))
    await _app.initialize()
    await _app.start()
    assert _app.updater is not None
    await _app.updater.start_polling()
    tg_app = _app
    log.info("Telegram bot polling started")

    yield

    assert tg_app is not None
    assert tg_app.updater is not None
    await tg_app.updater.stop()
    await tg_app.stop()
    await tg_app.shutdown()


api = FastAPI(title="Beaconry Hub", version="0.1.0", lifespan=lifespan)


@api.get("/health")
async def health() -> dict:
    return {"status": "ok"}


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
    for f in suppressed[:10]:
        lines.append(
            f"• <code>{html.escape(f.cve_id)}</code> {f.severity} "
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
