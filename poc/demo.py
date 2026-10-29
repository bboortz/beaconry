"""End-to-end POC demo (BC-001 mock + BC-002 real).

Usage:
    make demo                            # real pipeline (requires Telegram creds)
    BEACONRY_MOCK=1 make demo            # mocked (no network/Docker/Telegram)
    make demo ARGS=--self-test           # Telegram canary, no scan
    make demo ARGS=--non-interactive     # mock, auto-confirm (CI smoke mode)
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Suppress HTTP client logs — python-telegram-bot's httpx logs include the
# bot token in request URLs, which would leak secrets to stdout/log files.
for _noisy in ("httpx", "httpcore", "telegram", "telegram.ext"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# BC-002: auto-load poc/.env before any mode decision.
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent / ".env")
except ImportError:  # dotenv is a dev dep; absent in minimal installs
    pass


class MissingConfigError(RuntimeError):
    """Raised when neither BEACONRY_MOCK nor Telegram creds are set."""


def choose_mode(env: dict[str, str]) -> str:
    """Return 'mock' or 'real' based on env. Raise MissingConfigError if neither works."""
    if env.get("BEACONRY_MOCK") == "1":
        return "mock"
    if env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID"):
        return "real"
    raise MissingConfigError(
        "Missing TELEGRAM_BOT_TOKEN (and/or TELEGRAM_CHAT_ID).\n"
        "Fill poc/.env — see poc/.env.example for the template.\n"
        "To preview the pipeline without Telegram, run: BEACONRY_MOCK=1 make demo"
    )


def check_prereqs(image: str, image_present: object = None) -> list[str]:
    """Return a list of missing-prereq descriptions; empty list means OK."""
    import subprocess

    def _image_present_default(ref: str) -> bool:
        try:
            return (
                subprocess.run(
                    ["docker", "image", "inspect", ref],
                    capture_output=True,
                    check=False,
                    timeout=10,
                ).returncode
                == 0
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    presence = image_present if callable(image_present) else _image_present_default
    missing: list[str] = []
    for tool in ("syft", "grype"):
        if shutil.which(tool) is None:
            missing.append(f"{tool} not found on $PATH")
    if not presence(image):
        missing.append(f"{image} not pulled locally (run: docker pull {image})")
    return missing


@dataclass
class Args:
    self_test: bool = False
    non_interactive: bool = False


def parse_args(argv: list[str]) -> Args:
    p = argparse.ArgumentParser(prog="poc.demo", add_help=True)
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--non-interactive", action="store_true")
    ns = p.parse_args(argv)
    return Args(self_test=ns.self_test, non_interactive=ns.non_interactive)


# ── Mode setup — must happen BEFORE importing hub ───────────────────────────

_IS_MOCK = os.environ.get("BEACONRY_MOCK") == "1"
if _IS_MOCK:
    os.environ.setdefault("TELEGRAM_BOT_TOKEN", "mock-token")
    os.environ.setdefault("TELEGRAM_CHAT_ID", "0")

os.environ.setdefault("MIN_CVSS", "0")

_REPO_ROOT = Path(__file__).resolve().parents[1]
if _IS_MOCK:
    _data_dir = tempfile.mkdtemp(prefix="beaconry-demo-")
else:
    _cache = _REPO_ROOT / ".beaconry-cache"
    _cache.mkdir(exist_ok=True)
    _data_dir = str(_cache)
    os.environ.setdefault("GRYPE_DB_CACHE_DIR", str(_cache / "grype-db"))
os.environ["DATA_DIR"] = _data_dir
_tmp = _data_dir  # legacy name used by _print_header

# Make poc/hub/ importable so hub.py's `from executor.base import ...` resolves.
_HUB_DIR = _REPO_ROOT / "poc" / "hub"
if str(_HUB_DIR) not in sys.path:
    sys.path.insert(0, str(_HUB_DIR))


import hub  # noqa: E402 — poc/hub/hub.py loaded as top-level 'hub'
import urgency  # noqa: E402 — poc/hub/urgency.py

from poc.mock_data import FIXTURE_HOST, fixture_scan_payload  # noqa: E402


async def _run_syft(image: str) -> dict:
    """Shell out to real syft; return the parsed SBOM JSON."""
    import json

    proc = await asyncio.create_subprocess_exec(
        "syft",
        image,
        "-o",
        "syft-json",
        "--quiet",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"syft failed: {stderr.decode()[:200]}")
    return json.loads(stdout)


async def run_real_pipeline_once(image: str = "nginx:1.24") -> list[hub.Finding]:
    """Run the real pipeline once against a locally-pulled image.

    BC-002: scope limited to one image, no auto-fix. Telegram caller sets up
    the Application and assigns it to hub.tg_app before invoking.
    """
    hub.DATA_DIR = Path(os.environ["DATA_DIR"])
    hub.DB_PATH = hub.DATA_DIR / "beaconry.db"

    print(f"==> Scout: running syft against {image}…")
    sbom = await _run_syft(image)
    artifact_count = len(sbom.get("artifacts", []))
    print(f"    syft produced SBOM with {artifact_count} artifact(s)")

    print("==> Hub: running grype against the SBOM…")
    matches = await hub.run_grype(sbom)
    print(f"    grype matched {len(matches)} vulnerability record(s)")

    host = os.environ.get("HOST_NAME", "homelab-dev")
    min_cvss = float(os.environ.get("MIN_CVSS", "7.0"))

    new_findings: list[hub.Finding] = []
    for match in matches:
        vuln = match.get("vulnerability", {})
        artifact = match.get("artifact", {})
        cvss = hub.extract_cvss(vuln)
        if cvss < min_cvss:
            continue

        fix_versions = vuln.get("fix", {}).get("versions") or ["n/a"]
        finding = hub.Finding(
            id=hub.make_id(host, vuln["id"], image, artifact.get("name", "")),
            host=host,
            cve_id=vuln["id"],
            package=artifact.get("name", ""),
            version=artifact.get("version", ""),
            fixed_version=fix_versions[0],
            severity=vuln.get("severity", "Unknown"),
            cvss=cvss,
            source_type="docker",
            source_ref=image,
            description=(vuln.get("description") or "")[:200],
        )
        # No KEV feed and no exposure scoring in BC-002 — assume LAN-only, non-KEV.
        finding.tier = urgency.classify_tier(finding, kev=False, internet_facing=False)

        if hub.save_finding(finding):
            new_findings.append(finding)
            print(
                f"\n→ New finding {finding.cve_id} ({finding.severity} "
                f"CVSS {finding.cvss:.1f}) tier={finding.tier}  pkg={finding.package} "
                f"{finding.version} → {finding.fixed_version}"
            )
            await hub.notify(finding)

    return new_findings


RULE = "-" * 70
HEAVY = "=" * 70


def _print_header() -> None:
    print(HEAVY)
    print("Beaconry POC — End-to-end mocked demo")
    print(HEAVY)
    print(f"Mock env: BEACONRY_MOCK=1  DATA_DIR={_tmp}")
    print("(no network, no Docker daemon, no Telegram)")
    print()


async def _process_finding(
    finding: hub.Finding,
    *,
    kev: bool,
    internet_facing: bool,
    auto_confirm: bool,
) -> bool:
    """Print narration for one finding; auto- or operator-confirm; execute; verify."""
    print(RULE)
    print(f"==> Finding: {finding.cve_id} — {finding.package} {finding.version}")
    print(f"    severity: {finding.severity}  CVSS: {finding.cvss:.1f}")
    print(f"    exposure: fixture-flagged {'internet-facing' if internet_facing else 'LAN-only'}")
    print(f"    kev: {kev}")
    print(f"    tier: {urgency.TIER_LABELS[finding.tier]}")

    safe = urgency.is_safe_class(finding, kev=kev)
    print(f"    safe class: {safe} ({'KEV + container' if safe else 'operator confirm required'})")
    print()

    if not safe:
        if auto_confirm:
            print("    [auto-confirm] simulating [Fix now] tap...")
        else:
            try:
                input("    >>> Press Enter to simulate [Fix now] tap (Ctrl-C to skip): ")
            except (EOFError, KeyboardInterrupt):
                print("\n    (skipped by operator)")
                return False
        print()

    executor = next((e for e in hub.EXECUTORS if e.can_handle(finding)), None)
    assert executor is not None, "no executor matched"
    result = await executor.execute(finding)
    if not result.success:
        print(f"    ❌ fix failed: {result.message}")
        return False

    await hub.notify(finding)
    print()

    clean = await hub.verify_fix(finding)
    status = "✓ fixed and verified" if clean else "⚠ fix applied, CVE still present"
    print(f"    verify_fix: {status}")
    print()
    return clean


async def run_demo(*, auto_confirm: bool = False) -> int:
    """Run the full mocked pipeline. Returns 0 on success."""
    _print_header()

    hub.init_db()

    print(f"==> Scout: scanning fixture inventory (host: {FIXTURE_HOST})")
    payload = fixture_scan_payload()
    sboms = payload["sboms"]
    print(f"    [mock] discovered {len(sboms)} docker image(s)")
    print()

    print(f"==> Hub: SBOM received (host: {FIXTURE_HOST}, {len(sboms)} SBOM(s))")

    findings: list[tuple[hub.Finding, bool, bool]] = []
    for entry in sboms:
        matches = await hub.run_grype(entry.get("sbom", {}))
        print(f"    [mock] grype matched {len(matches)} finding(s)")
        for match in matches:
            vuln = match["vulnerability"]
            artifact = match["artifact"]
            meta = match.get("beaconry", {})
            kev = bool(meta.get("kev"))
            internet_facing = meta.get("exposure") == "internet-facing"

            finding = hub.Finding(
                id=hub.make_id(
                    FIXTURE_HOST, vuln["id"], entry["source_meta"]["image"], artifact["name"]
                ),
                host=FIXTURE_HOST,
                cve_id=vuln["id"],
                package=artifact["name"],
                version=artifact["version"],
                fixed_version=(vuln.get("fix", {}).get("versions") or ["n/a"])[0],
                severity=vuln.get("severity", "Unknown"),
                cvss=hub.extract_cvss(vuln),
                source_type=entry["source_meta"]["type"],
                source_ref=entry["source_meta"]["image"],
                description=vuln.get("description", "")[:200],
            )
            finding.tier = urgency.classify_tier(finding, kev=kev, internet_facing=internet_facing)
            findings.append((finding, kev, internet_facing))
    print()

    verified = 0
    for finding, kev, internet_facing in findings:
        ok = await _process_finding(
            finding, kev=kev, internet_facing=internet_facing, auto_confirm=auto_confirm
        )
        if ok:
            verified += 1

    print(HEAVY)
    print(f"Demo complete — {verified}/{len(findings)} findings fixed and verified")
    print(HEAVY)
    return 0 if verified == len(findings) else 1


async def run_real_demo(*, self_test: bool = False) -> int:
    """BC-002 orchestrator: real syft/grype/Telegram pipeline on nginx:1.24.

    Starts a Telegram Application, assigns it to hub.tg_app, runs the pipeline,
    then holds a wait loop until Ctrl-C so operator can tap buttons.
    """
    from telegram.ext import Application, CallbackQueryHandler

    image = "nginx:1.24"
    missing = check_prereqs(image=image)
    if missing:
        print("ERROR: prerequisites missing:", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        return 2

    print(HEAVY)
    print("Beaconry POC — Real pipeline (BC-002)")
    print(HEAVY)
    print(f"Image:          {image}")
    print(f"Dev DB:         {os.environ['DATA_DIR']}/beaconry.db")
    print(f"Grype DB cache: {os.environ.get('GRYPE_DB_CACHE_DIR', '(default)')}")
    print()

    hub.init_db()

    app = Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()
    app.add_handler(CallbackQueryHandler(hub.on_button_click))
    await app.initialize()
    await app.start()
    assert app.updater is not None
    await app.updater.start_polling()
    hub.tg_app = app
    print("✓ Telegram bot online (polling)")
    print()

    try:
        if self_test:
            await app.bot.send_message(
                chat_id=int(os.environ["TELEGRAM_CHAT_ID"]),
                text="🛰 beaconry is alive — BC-002 self-test",
            )
            print("✓ Self-test canary sent. Exit 0.")
            return 0

        findings = await run_real_pipeline_once(image=image)
        print(f"\n{len(findings)} new finding(s) notified.")

        if not findings:
            print("Nothing new to action — all findings already in DB. Exit 0.")
            return 0

        print()
        print("Waiting for operator button taps (Ctrl-C to exit)…")
        try:
            await asyncio.Event().wait()
        except (KeyboardInterrupt, asyncio.CancelledError):
            print("\nShutting down.")
        return 0
    finally:
        if app.updater is not None:
            with contextlib.suppress(RuntimeError):
                await app.updater.stop()
        await app.stop()
        await app.shutdown()


def main() -> int:
    args = parse_args(sys.argv[1:])
    try:
        mode = choose_mode(dict(os.environ))
    except MissingConfigError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    if mode == "mock":
        return asyncio.run(run_demo(auto_confirm=args.non_interactive))
    return asyncio.run(run_real_demo(self_test=args.self_test))


if __name__ == "__main__":
    sys.exit(main())
