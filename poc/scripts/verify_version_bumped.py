"""BC-062 pre-push gate — refuse to push unless VERSION moved.

Policy: every push from a feature/fix branch must carry a VERSION bump
relative to its merge-base with ``origin/dev`` (fallback: ``dev``,
``origin/main``, ``main``). This is the enforcement half of the
auto-bump mechanism — ``make bump-{patch,minor,major,auto}`` writes it,
this lint fails loudly if a dev forgot to run it.

Opt-outs:
  - ``BEACONRY_SKIP_VERSION_BUMP=1`` → treat as pass (emergency only;
    pre-commit framework surfaces skipped hooks).
  - On ``dev`` and ``main`` themselves → pass (merge commits don't need
    a bump; the bump landed in the feature branch).
  - When the push diff is zero commits (fast-forward, nothing new) → pass.

Also sanity-checks that the new version is strictly > the old by SemVer
comparison, so a dev who hand-edits VERSION downward gets caught.

Exit 0 clean, non-zero with a human-readable message otherwise.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _git(args: list[str], cwd: Path) -> str:
    out = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    return out.stdout.strip() if out.returncode == 0 else ""


def _current_branch(repo_root: Path) -> str:
    return _git(["rev-parse", "--abbrev-ref", "HEAD"], repo_root)


def _resolve_base_ref(repo_root: Path) -> str | None:
    for ref in ("origin/dev", "dev", "origin/main", "main"):
        if _git(["rev-parse", "--verify", f"{ref}^{{commit}}"], repo_root):
            return ref
    return None


def _read_version_at(repo_root: Path, treeish: str) -> str | None:
    """Return the version string at <treeish>:VERSION, None if missing."""
    content = _git(["show", f"{treeish}:VERSION"], repo_root)
    return content.strip() if content else None


def _semver_tuple(v: str) -> tuple[int, int, int] | None:
    m = SEMVER_RE.match(v)
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def check(repo_root: Path) -> list[str]:
    if os.environ.get("BEACONRY_SKIP_VERSION_BUMP") == "1":
        return []
    branch = _current_branch(repo_root)
    # dev + main are merge targets — the bump already happened on the
    # feature branch. Don't double-require.
    if branch in {"dev", "main"}:
        return []

    base = _resolve_base_ref(repo_root)
    if not base:
        # No dev/main anywhere → fresh repo, pre-remote. Nothing to compare.
        return []

    merge_base = _git(["merge-base", base, "HEAD"], repo_root)
    if not merge_base:
        return []

    # No commits ahead of base at all → nothing being pushed.
    ahead = _git(["rev-list", "--count", f"{merge_base}..HEAD"], repo_root)
    if ahead == "0":
        return []

    head_version = (repo_root / "VERSION").read_text().strip()
    base_version = _read_version_at(repo_root, merge_base)

    if base_version is None:
        # First time VERSION exists in history → treat as a bump.
        return []

    if head_version == base_version:
        return [
            f"VERSION unchanged ({head_version}) relative to {base}@{merge_base[:8]}. "
            "Run `make bump-patch` (or minor/major, or `make bump-auto` to infer "
            "from commit messages) and amend/commit before pushing. "
            "Emergency bypass: BEACONRY_SKIP_VERSION_BUMP=1 git push."
        ]

    head_t = _semver_tuple(head_version)
    base_t = _semver_tuple(base_version)
    if head_t is None:
        return [f"VERSION {head_version!r} is not valid SemVer (MAJOR.MINOR.PATCH)."]
    if base_t is None:
        # Legacy base — accept any valid new version.
        return []
    if head_t <= base_t:
        return [
            f"VERSION went backwards or stayed equal: {base_version} → {head_version}. "
            "A push must strictly increase the version."
        ]
    return []


def main() -> int:  # pragma: no cover
    errors = check(_repo_root())
    if errors:
        print("verify-version-bumped: push blocked:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 1
    print("✓ VERSION bumped since last push base")
    return 0


if __name__ == "__main__":
    sys.exit(main())
