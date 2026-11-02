"""BC-062 SemVer bump tool.

Modes:
  --patch   X.Y.Z -> X.Y.(Z+1)
  --minor   X.Y.Z -> X.(Y+1).0
  --major   X.Y.Z -> (X+1).0.0
  --auto    inspect `git log <base>..HEAD` and infer the bump from
            conventional-commit prefixes:
              - any BREAKING CHANGE (footer) or `!` after type → major
              - any `feat` → minor
              - any `fix` / `perf` / anything else that touched code → patch
              - no commits with a type → patch (safe default)
            The base is the merge-base with origin/dev (fallback: dev,
            then main).

Writes both ``VERSION`` and ``poc/VERSION`` atomically. Does NOT create
a git tag — the pre-push verify step is the enforcement point; tagging
is deferred to a future BC on release automation.

Exit codes: 0 = bumped (prints old → new on stdout), 2 = usage error,
3 = version file read error.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read(path: Path) -> str:
    return path.read_text().strip()


def parse(version: str) -> tuple[int, int, int]:
    m = SEMVER_RE.match(version)
    if not m:
        raise ValueError(f"not a SemVer version: {version!r}")
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def bump(current: str, kind: str) -> str:
    major, minor, patch = parse(current)
    if kind == "major":
        return f"{major + 1}.0.0"
    if kind == "minor":
        return f"{major}.{minor + 1}.0"
    if kind == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"unknown bump kind: {kind!r}")


def _git(args: list[str], cwd: Path) -> str:
    """Run git, strip, return stdout. Empty string on non-zero exit."""
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if out.returncode != 0:
        return ""
    return out.stdout.strip()


def _resolve_base(repo_root: Path) -> str | None:
    """Prefer origin/dev → dev → origin/main → main. First one that resolves wins."""
    for ref in ("origin/dev", "dev", "origin/main", "main"):
        if _git(["rev-parse", "--verify", f"{ref}^{{commit}}"], repo_root):
            return ref
    return None


def infer_bump_from_commits(repo_root: Path) -> str:
    """Return 'major', 'minor', or 'patch' from conventional-commit scan."""
    base = _resolve_base(repo_root)
    if not base:
        return "patch"
    log = _git(["log", f"{base}..HEAD", "--pretty=%s%n%b%n--"], repo_root)
    if not log:
        return "patch"
    saw_feat = False
    for commit in log.split("--\n"):
        commit = commit.strip()
        if not commit:
            continue
        subject = commit.splitlines()[0] if commit.splitlines() else ""
        # BREAKING CHANGE: anywhere in body OR `!` right before the colon.
        if "BREAKING CHANGE" in commit or re.match(r"^[a-z]+(\([^)]+\))?!:", subject):
            return "major"
        if re.match(r"^feat(\([^)]+\))?:", subject):
            saw_feat = True
    return "minor" if saw_feat else "patch"


def write(repo_root: Path, new_version: str) -> None:
    for p in (repo_root / "VERSION", repo_root / "poc" / "VERSION"):
        p.write_text(f"{new_version}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bump beaconry VERSION (SemVer).")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--major", action="store_true")
    group.add_argument("--minor", action="store_true")
    group.add_argument("--patch", action="store_true")
    group.add_argument(
        "--auto",
        action="store_true",
        help="Infer kind from conventional-commit messages since origin/dev.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the bump without writing files.",
    )
    args = parser.parse_args(argv)

    repo_root = _repo_root()
    try:
        current = _read(repo_root / "VERSION")
    except OSError as e:
        print(f"bump-version: cannot read VERSION: {e}", file=sys.stderr)
        return 3

    kind = (
        "major"
        if args.major
        else "minor"
        if args.minor
        else "patch"
        if args.patch
        else infer_bump_from_commits(repo_root)
    )
    try:
        new = bump(current, kind)
    except ValueError as e:
        print(f"bump-version: {e}", file=sys.stderr)
        return 3

    if args.dry_run:
        print(f"would bump {kind}: {current} -> {new}")
        return 0

    write(repo_root, new)
    print(f"bumped {kind}: {current} -> {new}")
    # BC-069: regenerate CHANGELOG.md so the operator never has to remember
    # a second step. Skip in dry-run and when explicitly bypassed.
    if os.environ.get("BEACONRY_SKIP_CHANGELOG") == "1":
        print("CHANGELOG regeneration skipped (BEACONRY_SKIP_CHANGELOG=1).")
    else:
        try:
            # Local import so bump_version remains usable even if changelog.py
            # has a syntax error (defensive — tooling must not block itself).
            from poc.scripts.changelog import write as write_changelog

            write_changelog(repo_root=repo_root)
            print(f"✓ CHANGELOG.md updated for v{new}")
        except Exception as e:  # noqa: BLE001
            print(f"⚠ CHANGELOG regeneration failed: {e}", file=sys.stderr)
            print("  Run `make changelog` manually before pushing.", file=sys.stderr)
    print("remember to `git add VERSION poc/VERSION CHANGELOG.md` + commit before pushing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
