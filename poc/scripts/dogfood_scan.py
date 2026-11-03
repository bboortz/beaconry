"""BC-082 — dogfood grype gate.

Runs grype against beaconry's own built images (`beaconry-hub:latest`,
`beaconry-scout:latest`) and fails if any High/Critical CVE has a
published fix. Catches the BC-081 class of issue proactively: the
next time a pinned third-party binary (docker CLI, syft, grype itself)
embeds a vulnerable Go stdlib or a dep bump introduces a fixable
High+, the gate fires at pre-push time instead of on an operator's
Telegram.

Design choices:

- Reuses the grype binary already inside the hub image — no host
  grype install required. `docker run --entrypoint /usr/bin/grype
  beaconry-hub:latest beaconry-hub:latest -o json`.
- Only flags findings with `vulnerability.fix.state == "fixed"` and
  non-empty `fix.versions`. "won't fix" / "unknown" / "not-affected"
  pass. Rationale: if there's nothing we can do, the gate shouldn't
  block.
- Severity floor: High. Medium+ fixable can land follow-up BCs.
- Opt-out: `BEACONRY_SKIP_DOGFOOD=1` — dev-only emergency escape.
  Documented in VARIABLES.md.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any

TARGET_IMAGES: tuple[str, ...] = (
    "beaconry-hub:latest",
    "beaconry-scout:latest",
)

# Severity floor. Anything strictly above or equal fails the gate when fixable.
_SEVERITY_FLOOR: frozenset[str] = frozenset({"High", "Critical"})

# Host's docker-socket gid for mounted-socket scans. Fallback for hosts
# where neither `getent group docker` nor $DOCKER_GID resolves.
_DEFAULT_DOCKER_GID = "999"


def _resolve_docker_gid() -> str:
    """Pick the gid of the host's /var/run/docker.sock, in priority order:
    1. $DOCKER_GID env (operator override)
    2. `getent group docker` (works on all glibc distros)
    3. `stat -c %g /var/run/docker.sock`
    4. _DEFAULT_DOCKER_GID (Debian/Ubuntu fallback)

    Make doesn't source poc/.env, so dogfood-scan can't inherit the compose
    value without extra plumbing — look it up ourselves at scan time.
    """
    env = os.environ.get("DOCKER_GID")
    if env:
        return env
    try:
        proc = subprocess.run(
            ["getent", "group", "docker"], capture_output=True, text=True, timeout=5, check=False
        )
        if proc.returncode == 0 and ":" in proc.stdout:
            # getent format: `docker:x:962:benni`
            parts = proc.stdout.strip().split(":")
            if len(parts) >= 3 and parts[2].isdigit():
                return parts[2]
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    try:
        proc = subprocess.run(
            ["stat", "-c", "%g", "/var/run/docker.sock"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip().isdigit():
            return proc.stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        pass
    return _DEFAULT_DOCKER_GID


def _is_fixable(vuln: dict[str, Any]) -> bool:
    """Returns True iff grype reports a published fix we could pull in.

    JSON shape (grype v0.80+):
      "fix": {"state": "fixed", "versions": ["1.2.3"]}
      or:
      "fix": {"state": "not-fixed", "versions": []}
      or:
      "fix": {"state": "wont-fix", "versions": []}
    """
    fix = vuln.get("fix", {})
    state = fix.get("state", "unknown")
    versions = fix.get("versions") or []
    return state == "fixed" and len(versions) > 0


def parse_findings(grype_json: dict[str, Any]) -> list[dict[str, str]]:
    """Return the actionable (High+/fixable) findings from a grype JSON report.

    Each returned dict has: cve_id, severity, package, version, fixed_in, type.
    Pure function — unit-testable without Docker.
    """
    actionable: list[dict[str, str]] = []
    for match in grype_json.get("matches", []):
        vuln = match.get("vulnerability", {})
        severity = vuln.get("severity", "Unknown")
        if severity not in _SEVERITY_FLOOR:
            continue
        if not _is_fixable(vuln):
            continue
        artifact = match.get("artifact", {})
        fixed_in = ", ".join(vuln.get("fix", {}).get("versions") or [])
        actionable.append(
            {
                "cve_id": vuln.get("id", "UNKNOWN"),
                "severity": severity,
                "package": artifact.get("name", "unknown"),
                "version": artifact.get("version", "unknown"),
                "fixed_in": fixed_in,
                "type": artifact.get("type", "unknown"),
            }
        )
    return actionable


def _scan_image(image: str, docker_gid: str) -> dict[str, Any]:
    """Shell out to hub-bundled grype via the Docker daemon socket. Returns
    the parsed grype JSON. Raises on process failure (so main() surfaces it)."""
    cmd = [
        "docker",
        "run",
        "--rm",
        "--group-add",
        docker_gid,
        "-v",
        "/var/run/docker.sock:/var/run/docker.sock:ro",
        "--entrypoint",
        "/usr/bin/grype",
        "beaconry-hub:latest",
        image,
        "-o",
        "json",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"grype scan of `{image}` failed: {proc.stderr[:400]}")
    return json.loads(proc.stdout)


def main(argv: list[str] | None = None) -> int:
    if os.environ.get("BEACONRY_SKIP_DOGFOOD"):
        print("↷ dogfood-scan: skipped per BEACONRY_SKIP_DOGFOOD")
        return 0

    docker_gid = _resolve_docker_gid()
    all_findings: list[tuple[str, dict[str, str]]] = []

    for image in TARGET_IMAGES:
        print(f"==> dogfood-scan: scanning {image}")
        try:
            report = _scan_image(image, docker_gid)
        except (RuntimeError, FileNotFoundError, json.JSONDecodeError) as e:
            print(f"✗ dogfood-scan: {image} — scan error: {e}", file=sys.stderr)
            return 2
        findings = parse_findings(report)
        for f in findings:
            all_findings.append((image, f))

    if not all_findings:
        print(
            f"✓ dogfood-scan: {len(TARGET_IMAGES)} image(s) clean "
            f"(no High/Critical with published fix)"
        )
        return 0

    print("", file=sys.stderr)
    print(
        f"✗ dogfood-scan: {len(all_findings)} actionable finding(s) in beaconry's own images:",
        file=sys.stderr,
    )
    for image, f in all_findings:
        print(
            f"  {image}: {f['severity']:8s} {f['cve_id']:22s} "
            f"{f['package']} {f['version']} → fixed in {f['fixed_in']}",
            file=sys.stderr,
        )
    print(
        "",
        "Pin bumps live in poc/hub/Dockerfile (DOCKER_CLI_VERSION, builder base),",
        "poc/scout/Dockerfile, and hub/scout requirements.txt. Fix at the source",
        "or (dev-only emergency) `BEACONRY_SKIP_DOGFOOD=1 make check-full`.",
        sep="\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
