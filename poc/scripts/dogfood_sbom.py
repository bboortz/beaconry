"""BC-096 — dogfood grype gate against each image's baked-in SBOM.

Complements `dogfood_scan.py` (BC-082). Where that script scans the
live image via the Docker socket, this one scans the **frozen** SBOM
that the Dockerfile builder stage wrote to `/sbom.json` and the
runtime stage COPYs in (forge FG-020 pattern — see
`docs/plan/BC-096-sbom-at-builder-plan.md`).

The split makes the two dogfood signals addressable independently:

- `dogfood-scan` catches live-image findings including Go-stdlib bugs
  embedded in binaries (syft, grype, docker CLI). Needs the docker
  socket mount; grype re-reads the image every run.
- `dogfood-sbom` (this script) catches findings against a **frozen
  snapshot** of the python-dep layout. Rebuild → fresh SBOM → different
  findings = image regressed. No rebuild → same SBOM → different
  findings = grype DB learned a new CVE.

Only the **hub** image ships `grype` (scout is read-only inventory, no
executors). So for scout, we `docker cp` its baked-in SBOM out to a
host tempfile and feed it to grype running inside a one-shot hub
container via a `:ro` mount. Hub's SBOM gets the same treatment for
symmetry — one scan path, one failure mode.

Shape mirrors `dogfood_scan.py`:
- Severity floor: High. Medium+ fixable can land follow-up BCs.
- Only flags `vulnerability.fix.state == "fixed"` with non-empty
  `fix.versions`.
- Opt-out: `BEACONRY_SKIP_DOGFOOD=1` (same env, same emergency path).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

TARGET_IMAGES: tuple[str, ...] = (
    "beaconry-hub:latest",
    "beaconry-scout:latest",
)

# Image that carries the grype binary. Both hub + scout bake their own
# SBOM, but only hub ships grype — scout is scanner-side read-only
# (no executors, no grype).
GRYPE_IMAGE = "beaconry-hub:latest"

# Path baked into each image by the Dockerfile builder stage. Must
# stay in lock-step with the `COPY --from=builder /sbom.json /sbom.json`
# lines in poc/{hub,scout}/Dockerfile.
SBOM_PATH_IN_IMAGE = "/sbom.json"

# Severity floor — identical to dogfood_scan.py.
_SEVERITY_FLOOR: frozenset[str] = frozenset({"High", "Critical"})


def _is_fixable(vuln: dict[str, Any]) -> bool:
    """True iff grype reports a published fix we could pull in."""
    fix = vuln.get("fix", {})
    state = fix.get("state", "unknown")
    versions = fix.get("versions") or []
    return state == "fixed" and len(versions) > 0


def parse_findings(grype_json: dict[str, Any]) -> list[dict[str, str]]:
    """Return the actionable (High+/fixable) findings from a grype JSON report.

    Pure function — unit-testable without Docker. Same shape as
    `dogfood_scan.parse_findings`.
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


def _extract_sbom(image: str, dest: Path) -> None:
    """`docker create` + `docker cp` the image's /sbom.json out to `dest`."""
    cid_proc = subprocess.run(
        ["docker", "create", f"--name=dogfood-sbom-{uuid.uuid4().hex[:8]}", image, "/noop"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if cid_proc.returncode != 0:
        raise RuntimeError(f"docker create `{image}` failed: {cid_proc.stderr[:400]}")
    cid = cid_proc.stdout.strip()
    try:
        cp = subprocess.run(
            ["docker", "cp", f"{cid}:{SBOM_PATH_IN_IMAGE}", str(dest)],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if cp.returncode != 0:
            raise RuntimeError(f"docker cp SBOM from `{image}` failed: {cp.stderr[:400]}")
    finally:
        subprocess.run(["docker", "rm", "-f", cid], capture_output=True, timeout=30, check=False)


def _scan_image(image: str) -> dict[str, Any]:
    """Scan the image's baked-in /sbom.json with grype.

    For every target: extract its SBOM to a host tempfile, then feed it
    to grype inside a one-shot `GRYPE_IMAGE` container via a read-only
    bind mount. No host grype install, no docker-socket mount into
    scout, works for both targets.
    """
    with tempfile.TemporaryDirectory(prefix="beaconry-dogfood-sbom-") as td:
        sbom_host = Path(td) / "sbom.json"
        _extract_sbom(image, sbom_host)

        cmd = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{sbom_host}:/sbom.json:ro",
            "--entrypoint",
            "/usr/local/bin/grype",
            GRYPE_IMAGE,
            "sbom:/sbom.json",
            "-o",
            "json",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"grype SBOM-scan of `{image}` failed: {proc.stderr[:400]}")
        return json.loads(proc.stdout)


def main(argv: list[str] | None = None) -> int:
    if os.environ.get("BEACONRY_SKIP_DOGFOOD"):
        print("↷ dogfood-sbom: skipped per BEACONRY_SKIP_DOGFOOD")
        return 0

    all_findings: list[tuple[str, dict[str, str]]] = []

    for image in TARGET_IMAGES:
        print(f"==> dogfood-sbom: extract+scan {SBOM_PATH_IN_IMAGE} from {image}")
        try:
            report = _scan_image(image)
        except (RuntimeError, FileNotFoundError, json.JSONDecodeError) as e:
            print(f"✗ dogfood-sbom: {image} — scan error: {e}", file=sys.stderr)
            return 2
        findings = parse_findings(report)
        for f in findings:
            all_findings.append((image, f))

    if not all_findings:
        print(
            f"✓ dogfood-sbom: {len(TARGET_IMAGES)} image(s) clean "
            f"(no High/Critical with published fix in frozen SBOM)"
        )
        return 0

    print("", file=sys.stderr)
    print(
        f"✗ dogfood-sbom: {len(all_findings)} actionable finding(s) "
        f"in beaconry's own baked-in SBOMs:",
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
        "If the same CVE appears in `make dogfood-scan` → image regressed; fix",
        "the dep (poc/{hub,scout}/requirements.txt or Dockerfile pin). If it",
        "appears only here → the frozen SBOM pre-dates a grype DB update that",
        "newly matches an existing package; rebuild (`make live-rebuild`) to",
        "refresh the SBOM. Dev-only emergency: `BEACONRY_SKIP_DOGFOOD=1 make",
        "check-full`.",
        sep="\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
