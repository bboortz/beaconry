"""BC-044: live-smoke harness for operator-facing CLIs.

Non-destructive scenarios that invoke `make findings`, `make status`,
`make audit`, `make scan` against the LIVE compose stack and assert
on the structural contracts operators depend on.

Catches the class of bug where a code path only runs when the stack is
`make live-rebuild`'d AND the operator exercises the real CLI
(BC-030 Dockerfile-COPY omission, BC-035 import-scope bug, BC-042
FOLLOW buffering). Unit tests + the BC-038 scenario harness cover
hub ingestion flow — this covers the operator CLI surface.

Opt-out: `BEACONRY_SKIP_CLI_SMOKE=1` → runner prints "skipped" + exits 0.
Needed for CI without privileged Docker + for the BC-038 scenario stack
which boots its own hub container.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from abc import ABC, abstractmethod
from pathlib import Path

HUB_CONTAINER = os.environ.get("BEACONRY_HUB_CONTAINER", "beaconry-hub")
SCOUT_CONTAINER = "beaconry-scout"
REPO_ROOT = Path(__file__).resolve().parents[2]


# ── Prerequisite check ─────────────────────────────────────────────────────


def is_hub_running() -> bool:
    """Return True iff `beaconry-hub` is up (same test as `make scan`)."""
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


# ── Shell helpers ──────────────────────────────────────────────────────────


def run_make(
    target: str, *, timeout: int = 60, **env_overrides: str
) -> subprocess.CompletedProcess:
    """Invoke `make <target> KEY=VAL …` from the repo root. Returns the
    CompletedProcess so scenarios can assert on stdout/stderr/returncode."""
    env = {**os.environ, **env_overrides}
    args = [k + "=" + str(v) for k, v in env_overrides.items() if k == k.upper()]
    return subprocess.run(
        ["make", target, *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


# ── Scenario base + helpers ────────────────────────────────────────────────


class CliScenario(ABC):
    order: int
    name: str
    description: str

    @abstractmethod
    def run(self) -> None:
        raise NotImplementedError


def assert_contains(haystack: str, needle: str, *, context: str = "") -> None:
    if needle not in haystack:
        snippet = haystack[:400].rstrip()
        raise AssertionError(
            f"missing {needle!r}"
            + (f" {context}" if context else "")
            + f"\n     --- output ---\n     {snippet.replace(chr(10), chr(10) + '     ')}"
        )


# ── Parsers (unit-testable) ────────────────────────────────────────────────


def count_findings_body_rows(stdout: str) -> int:
    """Count data rows (not header / separator) in a `make findings` dump."""
    lines = stdout.splitlines()
    return sum(1 for ln in lines if ln and ln[:3] in ("✓  ", "—  "))


def count_audit_body_rows(stdout: str) -> int:
    """Count data rows (not header / separator) in a `make audit` dump.
    Audit rows start with HH:MM:SS, which matches `\\d\\d:\\d\\d:\\d\\d`."""
    pattern = re.compile(r"^\d\d:\d\d:\d\d\b")
    return sum(1 for ln in stdout.splitlines() if pattern.match(ln))


def audit_has_header(stdout: str) -> bool:
    """BC-042: FOLLOW must emit the TIME column header, not just raw JSON."""
    return any("TIME" in ln and "EVENT" in ln for ln in stdout.splitlines()[:3])


# ── Scenarios ─────────────────────────────────────────────────────────────


class S01FindingsColumns(CliScenario):
    order = 1
    name = "findings_columns"
    description = (
        "`make findings` emits KEV/NET/CVE/Severity/CVSS/Host/Target/Package/Status columns"
    )

    def run(self) -> None:
        r = run_make("findings")
        if r.returncode != 0:
            raise AssertionError(f"`make findings` exited {r.returncode}: {r.stderr[:200]}")
        for col in ("KEV", "NET", "CVE", "Severity", "CVSS", "Host", "Target", "Package", "Status"):
            assert_contains(r.stdout, col, context=f"(column header missing: {col})")


class S02StatusShowsBothServices(CliScenario):
    order = 2
    name = "status_shows_both_services"
    description = "`make status` renders both beaconry-hub and beaconry-scout rows"

    def run(self) -> None:
        r = run_make("status")
        if r.returncode != 0:
            raise AssertionError(f"`make status` exited {r.returncode}: {r.stderr[:200]}")
        assert_contains(r.stdout, HUB_CONTAINER)
        assert_contains(r.stdout, SCOUT_CONTAINER)


class S03AuditLastWithFilter(CliScenario):
    order = 3
    name = "audit_last_filter"
    description = "`make audit LAST=5` returns 5 data rows + the TIME header"

    def run(self) -> None:
        r = run_make("audit", LAST="5")
        if r.returncode != 0:
            raise AssertionError(f"`make audit LAST=5` exited {r.returncode}: {r.stderr[:200]}")
        assert_contains(r.stdout, "TIME")
        rows = count_audit_body_rows(r.stdout)
        if rows != 5:
            raise AssertionError(f"expected 5 body rows, got {rows}")


class S04AuditCountMode(CliScenario):
    order = 4
    name = "audit_count_mode"
    description = "`make audit COUNT=1` produces an event-type summary"

    def run(self) -> None:
        r = run_make("audit", COUNT="1")
        if r.returncode != 0:
            raise AssertionError(f"`make audit COUNT=1` exited {r.returncode}: {r.stderr[:200]}")
        for col in ("EVENT", "COUNT", "FIRST SEEN", "LAST SEEN"):
            assert_contains(r.stdout, col, context="(summary column missing)")


class S05AuditBadSinceExitsCleanly(CliScenario):
    """BC-042 close-gap regression guard."""

    order = 5
    name = "audit_bad_since_exits_cleanly"
    description = (
        "`make audit SINCE=yesterday` exits 2 with 'time parse error' — NOT a Python traceback"
    )

    def run(self) -> None:
        r = run_make("audit", SINCE="yesterday")
        if r.returncode == 0:
            raise AssertionError(
                f"`make audit SINCE=yesterday` returned 0 — expected non-zero. stdout: {r.stdout[:200]}"
            )
        combined = r.stdout + r.stderr
        if "Traceback" in combined:
            raise AssertionError(
                f"got a Python traceback instead of a clean error:\n{combined[:400]}"
            )
        assert_contains(combined, "time parse error", context="(expected clean error message)")


class S06AuditFollowFormatsOutput(CliScenario):
    """BC-042 close-gap regression guard — buffered/raw-JSON regression."""

    order = 6
    name = "audit_follow_formats_output"
    description = "`make audit FOLLOW=1` emits the TIME/EVENT header through pipes (not raw JSON)"

    def run(self) -> None:
        # Short timeout — we just need to see the header + a line or two.
        try:
            r = run_make("audit", FOLLOW="1", timeout=4)
        except subprocess.TimeoutExpired as exc:
            # Expected: tail -f doesn't terminate on its own. Inspect what
            # was buffered before the kill.
            stdout = (
                (exc.stdout or b"").decode("utf-8", errors="replace")
                if isinstance(exc.stdout, bytes)
                else (exc.stdout or "")
            )
            if not audit_has_header(stdout):
                raise AssertionError(
                    f"`make audit FOLLOW=1` did not emit the TIME/EVENT header within 4s.\n"
                    f"First 400 chars:\n{stdout[:400]}"
                ) from None
            # Reject raw JSON lines (BC-042 regression).
            if any(ln.startswith('{"ts":') for ln in stdout.splitlines()):
                raise AssertionError(
                    "`make audit FOLLOW=1` emitted raw JSON — formatter not applied"
                ) from None
            return
        # Make-returned-cleanly path (e.g. no new events + process ended).
        if not audit_has_header(r.stdout):
            raise AssertionError(f"no TIME/EVENT header in FOLLOW output:\n{r.stdout[:400]}")


class S07AuditEmptyFilterMessage(CliScenario):
    order = 7
    name = "audit_empty_filter_message"
    description = "`make audit CVE=noexist` prints 'No events' (not a crash, not blank)"

    def run(self) -> None:
        r = run_make("audit", CVE="CVE-NOT-REAL-999")
        if r.returncode != 0:
            raise AssertionError(f"exit {r.returncode}: {r.stderr[:200]}")
        assert_contains(r.stdout, "No events")


class S08ScanIdempotent(CliScenario):
    order = 8
    name = "scan_idempotent_response"
    description = "`make scan` returns 202 (scheduled) or 409 (already running) — both are OK"

    def run(self) -> None:
        r = run_make("scan")
        # make scan uses exit 2 for 409 (already running). Both treated as OK.
        if r.returncode not in (0, 2):
            raise AssertionError(
                f"`make scan` exited {r.returncode} (expected 0 or 2):\n{r.stdout + r.stderr}"
            )
        combined = r.stdout + r.stderr
        if "scheduled" not in combined and "already running" not in combined:
            raise AssertionError(
                f"neither 'scheduled' nor 'already running' in output:\n{combined[:300]}"
            )


class S09FindingsFilterByStatus(CliScenario):
    order = 9
    name = "findings_filter_by_status"
    description = "`make findings STATUS=open` works + columns intact"

    def run(self) -> None:
        r = run_make("findings", STATUS="open")
        if r.returncode != 0:
            raise AssertionError(f"exit {r.returncode}: {r.stderr[:200]}")
        # Either "no findings" OR real rows + columns present.
        if "No findings" not in r.stdout:
            for col in ("KEV", "CVE", "Severity"):
                assert_contains(r.stdout, col, context="(status filter dropped column)")


class S10NotifiersImportInsideHub(CliScenario):
    """BC-030 regression guard — notifiers/ must be COPY'd into the hub image."""

    order = 10
    name = "hub_notifiers_importable"
    description = "Hub container can `import notifiers` (BC-030 Dockerfile COPY regression)"

    def run(self) -> None:
        r = subprocess.run(
            ["docker", "exec", HUB_CONTAINER, "python3", "-c", "import notifiers; print('ok')"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if r.returncode != 0:
            raise AssertionError(
                f"hub container cannot `import notifiers`:\n  {r.stderr[:200]}\n"
                "  Did the Dockerfile COPY `hub/notifiers/` get removed?"
            )


SCENARIOS: list[CliScenario] = [
    S01FindingsColumns(),
    S02StatusShowsBothServices(),
    S03AuditLastWithFilter(),
    S04AuditCountMode(),
    S05AuditBadSinceExitsCleanly(),
    S06AuditFollowFormatsOutput(),
    S07AuditEmptyFilterMessage(),
    S08ScanIdempotent(),
    S09FindingsFilterByStatus(),
    S10NotifiersImportInsideHub(),
]


# ── Runner ─────────────────────────────────────────────────────────────────


def _run_one(scenario: CliScenario) -> bool:
    try:
        scenario.run()
        print(f"✓ {scenario.name:40s}  {scenario.description}")
        return True
    except AssertionError as exc:
        print(f"✗ {scenario.name:40s}  {scenario.description}")
        for line in str(exc).splitlines():
            print(f"  └─ {line}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"✗ {scenario.name:40s}  ERROR: {exc.__class__.__name__}: {exc}")
        return False


def main() -> int:
    if os.environ.get("BEACONRY_SKIP_CLI_SMOKE"):
        print("↷ cli-smoke: skipped per BEACONRY_SKIP_CLI_SMOKE")
        return 0

    if not is_hub_running():
        print(
            f"✗ {HUB_CONTAINER} not running — run `make live-rebuild` first "
            f"(or set BEACONRY_SKIP_CLI_SMOKE=1 to opt out of this gate)",
            file=sys.stderr,
        )
        return 2

    print(f"==> running {len(SCENARIOS)} CLI-smoke scenario(s) against live stack")
    passed = sum(1 for s in sorted(SCENARIOS, key=lambda s: s.order) if _run_one(s))
    failed = len(SCENARIOS) - passed
    print()
    print(f"{passed}/{len(SCENARIOS)} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
