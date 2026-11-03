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

# BC-078 Python-base contract. Builder Python must match runtime Python
# or wheels won't load (same major.minor). Accept any 3.11+-slim with
# {bookworm,trixie} suffix. BC-082 bumped to 3.13-slim-trixie to pick up
# newer libc/libssl/libexpat and close dogfood-grype findings on the
# debian12 base.
BUILDER_BASE_RE = re.compile(
    r"^FROM\s+python:3\.(?P<minor>1[1-9])-slim-(?:bookworm|trixie)\s+AS\s+builder\b",
    re.MULTILINE,
)

# BC-078 runtime contract. BC-082 bumped debian12 → debian13 to pick up
# newer libc/libssl/libexpat. Both tags are accepted during the migration
# window; operators on debian12 see a dogfood-scan finding until they
# rebuild. Any other distroless flavor (debug, bullseye) is a conscious
# choice that should edit this lint too.
RUNTIME_IMAGE_RE = re.compile(
    r"^FROM\s+gcr\.io/distroless/python3-debian1[23]:nonroot\s+AS\s+runtime\b",
    re.MULTILINE,
)
# Preferred (newest accepted) tag — error messages name this one.
RUNTIME_IMAGE_PREFERRED = "gcr.io/distroless/python3-debian13:nonroot"

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
            f"{path}: builder stage must FROM python:3.1X-slim-bookworm "
            f"(or -trixie) AS builder. BC-078 pinned the builder to python 3.11+ "
            f"so wheels ABI-match the distroless runtime; a drift here produces "
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
