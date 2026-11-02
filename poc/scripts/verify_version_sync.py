"""BC-062 version-sync lint.

Rules enforced:
  1. ``poc/VERSION`` must byte-equal ``/VERSION`` (repo root).
     The compose build context is ``./poc``, so Docker COPYs the poc
     mirror; the root file remains the single source of truth for
     human-facing bumps.
  2. The three component files (hub/scout/cli) must not carry a stale
     hard-coded version literal. If a ``VERSION`` constant or
     ``*_VERSION = "x.y.z"`` assignment is present, its literal has to
     equal the ``VERSION`` file.

Rule 2 is intentionally narrow — the loader (``SOFTWARE_VERSION``) is
preferred everywhere; this catches only the quick-win regressions
where a dev reintroduces a literal string by habit.

Called by ``make verify-version-sync`` and ``make check``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

VERSION_LITERAL_RE = re.compile(
    r"""(?x)
    ^\s*
    (?P<name>[A-Z_]+_VERSION|VERSION)
    \s*=\s*
    ["'](?P<value>\d+\.\d+\.\d+[^"']*)["']
    """,
    re.MULTILINE,
)


# Modules that may carry a literal only when it is the API-version
# integer. These must never be flagged as drift; filtered out below.
INT_LITERAL_RE = re.compile(r"^\s*[A-Z_]+_API_VERSION\s*=\s*\d+", re.MULTILINE)


def _read_version_file(path: Path) -> str:
    try:
        return path.read_text().strip()
    except OSError as e:
        raise RuntimeError(f"missing or unreadable: {path} ({e})") from e


def _scan_py_for_version_literal(path: Path) -> list[tuple[str, str]]:
    """Return list of (name, literal_value) pairs found in the file."""
    try:
        text = path.read_text()
    except OSError:
        return []
    return [(m.group("name"), m.group("value")) for m in VERSION_LITERAL_RE.finditer(text)]


def check_version_sync(repo_root: Path) -> list[str]:
    """Return errors list; empty = clean."""
    errors: list[str] = []
    root_version = _read_version_file(repo_root / "VERSION")
    poc_version = _read_version_file(repo_root / "poc" / "VERSION")
    if root_version != poc_version:
        errors.append(
            f"VERSION drift: /VERSION={root_version!r} but /poc/VERSION={poc_version!r} "
            "— run `cp VERSION poc/VERSION` (or edit both)."
        )
    # Scan the three component files.
    for rel in ("poc/hub/hub.py", "poc/scout/scout.py", "poc/scripts/cli_http.py"):
        path = repo_root / rel
        for name, value in _scan_py_for_version_literal(path):
            if value != root_version:
                errors.append(
                    f'{rel}: `{name} = "{value}"` drifts from VERSION ({root_version!r}). '
                    f"Replace the literal with the SOFTWARE_VERSION loader."
                )
    return errors


def main() -> int:  # pragma: no cover
    repo_root = Path(__file__).resolve().parents[2]
    errors = check_version_sync(repo_root)
    if errors:
        print("verify-version-sync: found issues:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 1
    print(f"✓ VERSION = {_read_version_file(repo_root / 'VERSION')!r}, in sync across components")
    _ = INT_LITERAL_RE  # kept for future use; silences ruff unused-var
    return 0


if __name__ == "__main__":
    sys.exit(main())
