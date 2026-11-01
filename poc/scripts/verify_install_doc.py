"""BC-056 — docs/INSTALL.md drift lint (short-name reshape of BC-054).

Three checks on `docs/INSTALL.md`:

1. **Env vars** — every uppercase identifier referenced in INSTALL.md
   must appear as a row in `docs/VARIABLES.md`. Catches stale env-var
   names that got renamed.

2. **Make targets** — every `make <target>` reference must appear in
   the Makefile's top-level `.PHONY:` declaration.

3. **File paths** — every `poc/…` or `docs/…` path mentioned must exist
   on disk, UNLESS it's an install destination (`/etc/…`, `~/…`) or an
   Ansible-example scaffold (`roles/…`) operators create themselves.

Exit codes:
    0  clean
    1  drift
    2  usage error (file missing)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

INSTALL_RELPATH = Path("docs") / "INSTALL.md"
VARIABLES_RELPATH = Path("docs") / "VARIABLES.md"
MAKEFILE_RELPATH = Path("Makefile")

# Backticked uppercase identifiers in INSTALL.md. We only enforce on
# names that look like env vars (snake-case uppercase with underscores,
# starting with a letter, 2+ chars). Short-name conventions mean this
# captures HUB_URL, HUB_TOKEN, HUB_SCOUT_TOKEN, HUB_CLI_TOKEN,
# HUB_PUBLISH_ADDR, HOST_NAME, SCAN_INTERVAL, SCOUT_INCLUDE, …
_ENV_VAR_RE = re.compile(r"`([A-Z][A-Z0-9_]{2,})`")

# `make <target>` where `<target>` is a kebab/snake-case identifier.
_MAKE_TARGET_RE = re.compile(r"\bmake\s+([a-z][a-z0-9_-]*)\b")

# File-path mentions. Only `poc/…` and `docs/…` are repo files.
# Everything else (/etc/…, ~/…, roles/…) is a target-host destination
# or Ansible scaffolding operators create themselves.
_REPO_PATH_RE = re.compile(r"`((?:poc|docs)/[A-Za-z0-9_./-]+)`")

# `make <target>` tokens we don't ship as a Makefile phony — stop words.
_MAKE_TARGET_IGNORE: frozenset[str] = frozenset(
    {
        "install",  # part of the `pip install` / `apt install` phrases
    }
)

# Env-var names we reference but don't own (BEACONRY_SKIP_GRYPE_DB is a
# dev-mode dial that may be in VARIABLES.md but also appears in prose).
# Keep tight; add only when a false positive is clearly justified.
_ENV_VAR_IGNORE: frozenset[str] = frozenset(
    {
        "BEACONRY_SKIP_GRYPE_DB",  # dev-mode escape hatch; mentioned in troubleshooting
    }
)


def _read(path: Path) -> str:
    try:
        return path.read_text()
    except (OSError, UnicodeDecodeError) as exc:
        raise FileNotFoundError(f"cannot read {path}: {exc}") from exc


def parse_phony_targets(makefile_text: str) -> set[str]:
    """Return the set of names in the Makefile's `.PHONY:` line."""
    out: set[str] = set()
    for line in makefile_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith(".PHONY:"):
            continue
        out.update(stripped.removeprefix(".PHONY:").split())
    return out


def parse_variables_md_names(variables_text: str) -> set[str]:
    """Return every `` `NAME` `` row name in VARIABLES.md's table body."""
    body_re = re.compile(r"^\|\s*`([A-Z0-9_]+)`\s*\|", re.MULTILINE)
    return set(body_re.findall(variables_text))


def check_env_vars(install_text: str, variables_text: str) -> list[str]:
    """Rule 1 — every env-var-shaped identifier in INSTALL.md is in VARIABLES.md."""
    documented = parse_variables_md_names(variables_text)
    violations: list[str] = []
    for match in sorted(set(_ENV_VAR_RE.findall(install_text))):
        if match in _ENV_VAR_IGNORE or match in documented:
            continue
        # Skip identifiers that look like constants but aren't env vars
        # (e.g., `NOT`, `KEV`, `INT_CVSS`). Heuristic: must contain `_`
        # OR be at least 6 chars. One-word all-caps shorter than 6 is
        # almost certainly a non-env identifier (a column header, state
        # name, etc.).
        if "_" not in match and len(match) < 6:
            continue
        violations.append(
            f"`{match}` referenced in docs/INSTALL.md but not documented in "
            f"docs/VARIABLES.md. Add a row OR rename the reference."
        )
    return violations


def check_make_targets(install_text: str, makefile_text: str) -> list[str]:
    """Rule 2 — every `make <target>` is in .PHONY."""
    phony = parse_phony_targets(makefile_text)
    violations: list[str] = []
    for target in sorted(set(_MAKE_TARGET_RE.findall(install_text))):
        if target in _MAKE_TARGET_IGNORE or target in phony:
            continue
        violations.append(
            f"`make {target}` referenced in docs/INSTALL.md but not in "
            f"Makefile's `.PHONY:` list. Add the target OR rename the reference."
        )
    return violations


def check_file_paths(install_text: str, repo_root: Path) -> list[str]:
    """Rule 3 — every repo-shaped path exists on disk."""
    violations: list[str] = []
    for raw in sorted(set(_REPO_PATH_RE.findall(install_text))):
        candidate = raw.rstrip(".,;:")
        if (repo_root / candidate).exists():
            continue
        violations.append(
            f"`{candidate}` referenced in docs/INSTALL.md but does not exist "
            f"in the repo. Create the file OR rename the reference."
        )
    return violations


def check(repo_root: Path) -> list[str]:
    install = _read(repo_root / INSTALL_RELPATH)
    variables = _read(repo_root / VARIABLES_RELPATH)
    makefile = _read(repo_root / MAKEFILE_RELPATH)
    out: list[str] = []
    out.extend(check_env_vars(install, variables))
    out.extend(check_make_targets(install, makefile))
    out.extend(check_file_paths(install, repo_root))
    return out


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    try:
        violations = check(repo_root)
    except FileNotFoundError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    if violations:
        print(f"verify-install-doc: {len(violations)} violation(s):")
        for v in violations:
            print(f"    ✗ {v}")
        return 1
    print("✓ verify-install-doc: docs/INSTALL.md in sync with repo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
