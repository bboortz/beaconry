"""BC-069 — enforce CHANGELOG.md has an entry for the current VERSION.

Pre-push gate (and `make check` gate): refuses to proceed if the
current `VERSION` doesn't appear as a `## [v<version>]` header in
`CHANGELOG.md`. Pairs with `BC-062`'s version-bump pre-push gate
to keep release metadata honest.

Bypass: `BEACONRY_SKIP_CHANGELOG=1` — same emergency pattern as
`BEACONRY_SKIP_VERSION_BUMP`.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_HEADER_RE = re.compile(r"^## \[v(?P<version>\d+\.\d+\.\d+[^\]]*)\]")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def check(repo_root: Path | None = None) -> list[str]:
    if os.environ.get("BEACONRY_SKIP_CHANGELOG") == "1":
        return []
    root = repo_root or _repo_root()
    version_path = root / "VERSION"
    changelog_path = root / "CHANGELOG.md"
    try:
        version = version_path.read_text().strip()
    except OSError as e:
        return [f"cannot read VERSION: {e}"]
    if not changelog_path.exists():
        return [
            "CHANGELOG.md is missing. Run `make changelog` to generate it, "
            "then commit alongside VERSION."
        ]
    headers = [
        m.group("version")
        for line in changelog_path.read_text().splitlines()
        if (m := _HEADER_RE.match(line))
    ]
    if version not in headers:
        return [
            f"CHANGELOG.md has no `## [v{version}]` header. Run "
            f"`make changelog` to regenerate it (bump_version does this "
            f"automatically on BC-069+). Emergency bypass: "
            f"BEACONRY_SKIP_CHANGELOG=1."
        ]
    return []


def main() -> int:  # pragma: no cover
    errors = check()
    if errors:
        print("verify-changelog: push blocked:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 1
    print("✓ CHANGELOG.md has an entry for the current VERSION")
    return 0


if __name__ == "__main__":
    sys.exit(main())
