"""BC-082 — Dockerfile pinned-version floor lint.

Guards against three regression classes BC-077..BC-081 revealed:

1. `DOCKER_CLI_VERSION` can silently drop below the BC-081 floor
   (29.9.2 → shipped with Go stdlib 1.26.10). Previous pin 27.5.1
   shipped Go 1.22.11 → GO-2026-6604 → beaconry alerted on itself.
2. Builder base can drift off `python:3.11-slim-bookworm` and
   produce wheels that don't ABI-match the distroless runtime.
3. Runtime stage can drift off `gcr.io/distroless/python3-debian12:nonroot`
   and silently reintroduce `/bin/sh` (BC-078 contract).

This lint is cheap enough to run on every `make check` and loud
enough that a developer reverting a pin sees exactly which BC
decision they're walking back.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# BC-081 floor. First Docker CLI build with Go stdlib ≥1.26.9 (fix for
# GO-2026-6604). Bump this when a newer floor becomes necessary; the
# dogfood-grype gate (CAP-T167) will flag the next regression first.
DOCKER_CLI_MIN = (29, 9, 2)

# BC-078 → BC-095 Python-base contract. Builder Python must match runtime
# Python or wheels won't load (same major.minor).
# Accepted variants:
#   * upstream `python:3.1X-slim-{bookworm,trixie}` (BC-082 era — kept for
#     the migration window so a revert-triage path exists)
#   * `ghcr.io/bboortz/forge-builder-trixie:latest` (BC-095 — forge-signed,
#     daily-rebuilt; ships python 3.13.18 ABI-matching the scratch runtime)
# Any other variant must edit this lint too — that's the "conscious choice"
# gate.
BUILDER_BASE_RE = re.compile(
    r"^FROM\s+(?:"
    r"python:3\.(?P<minor>1[1-9])-slim-(?:bookworm|trixie)"
    r"|ghcr\.io/bboortz/forge-builder-trixie:latest"
    r")\s+AS\s+builder\b",
    re.MULTILINE,
)

# BC-078 → BC-082 → BC-083 → BC-097 runtime contract.
# BC-083 moved runtime to the self-owned forge image on the semver-pinned
# `:3.13` tag; BC-097 (2026-11-08) bumped to `:latest` for symmetry with
# the BC-095 builder and to pick up FG-031's `/hc` immediately.
# `:3.13` + distroless debian12/debian13 stay accepted for rollback and
# the migration window. Any other variant (random ghcr repo,
# distroless-debug, etc.) must edit this lint too — that's the
# "conscious choice" gate.
RUNTIME_IMAGE_RE = re.compile(
    r"^FROM\s+(?:"
    r"ghcr\.io/bboortz/python-scratch-trixie:(?:latest|3\.13)"  # BC-097/BC-083 primary
    r"|gcr\.io/distroless/python3-debian1[23]:nonroot"  # BC-078/BC-082 fallback
    r")\s+AS\s+runtime\b",
    re.MULTILINE,
)
# Preferred (newest accepted) tag — error messages name this one.
RUNTIME_IMAGE_PREFERRED = "ghcr.io/bboortz/python-scratch-trixie:latest"

# BC-081 docker CLI pin.
DOCKER_CLI_RE = re.compile(
    r"^ARG\s+DOCKER_CLI_VERSION=(?P<version>\d+\.\d+\.\d+)\b",
    re.MULTILINE,
)


def parse_version(raw: str) -> tuple[int, int, int]:
    major, minor, patch = raw.split(".", 2)
    return int(major), int(minor), int(patch)


def check_dockerfile(path: Path, *, expect_docker_cli: bool) -> list[str]:
    """Return a list of violation strings. Empty = clean.

    `expect_docker_cli=True` for hub (needs docker CLI); False for scout
    (scout doesn't need docker CLI, so the pin lint doesn't apply).
    """
    violations: list[str] = []
    text = path.read_text()

    # 1. Builder base
    if not BUILDER_BASE_RE.search(text):
        violations.append(
            f"{path}: builder stage must FROM one of the accepted bases "
            f"(BC-078/BC-082: `python:3.1X-slim-{{bookworm,trixie}}`, or "
            f"BC-095: `ghcr.io/bboortz/forge-builder-trixie:latest`) "
            f"AS builder. All accepted variants ship python 3.11+ so wheels "
            f"ABI-match the scratch runtime; a drift here produces "
            f"ImportError on container start."
        )

    # 2. Runtime stage
    if not RUNTIME_IMAGE_RE.search(text):
        violations.append(
            f"{path}: runtime stage must FROM {RUNTIME_IMAGE_PREFERRED} AS runtime "
            f"(debian12 also accepted during migration). "
            f"BC-078 contract: this family has no /bin/sh and ships uid 65532. "
            f"Any other distroless variant needs a conscious update to this lint."
        )

    # 3. Docker CLI pin (hub only)
    if expect_docker_cli:
        m = DOCKER_CLI_RE.search(text)
        if not m:
            violations.append(
                f"{path}: expected an `ARG DOCKER_CLI_VERSION=X.Y.Z` line "
                f"(BC-081 floor: ≥{'.'.join(str(x) for x in DOCKER_CLI_MIN)})"
            )
        else:
            current = parse_version(m.group("version"))
            if current < DOCKER_CLI_MIN:
                violations.append(
                    f"{path}: DOCKER_CLI_VERSION={m.group('version')} is below "
                    f"the BC-081 floor "
                    f"{'.'.join(str(x) for x in DOCKER_CLI_MIN)}. "
                    f"Older docker CLI binaries embed Go stdlib <1.26.9 → "
                    f"GO-2026-6604 fires on beaconry's own hub image. "
                    f"See docs/stories/BC-081-docker-cli-go-stdlib.md."
                )

    return violations


def check(repo_root: Path) -> list[str]:
    """Entry point for both CLI and tests."""
    hub = repo_root / "poc" / "hub" / "Dockerfile"
    scout = repo_root / "poc" / "scout" / "Dockerfile"
    violations: list[str] = []
    violations.extend(check_dockerfile(hub, expect_docker_cli=True))
    violations.extend(check_dockerfile(scout, expect_docker_cli=False))
    return violations


def main(argv: list[str] | None = None) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    violations = check(repo_root)
    if violations:
        print("verify-pinned-versions: found violations:", file=sys.stderr)
        for v in violations:
            print(f"  {v}", file=sys.stderr)
        return 1
    print(
        f"✓ verify-pinned-versions: Dockerfile pins ok "
        f"(docker CLI ≥{'.'.join(str(x) for x in DOCKER_CLI_MIN)}, runtime distroless)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
