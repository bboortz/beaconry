"""BC-012 Dockerfile lint — catch forgotten `COPY beaconry_health/`.

Rule: if any .py file in a service directory imports `beaconry_health` (or an
attribute from it), that service's Dockerfile must contain a line that COPIES
`beaconry_health/` into the image. Forgetting the COPY is a container-start
runtime failure — this lint moves the catch to merge-time instead.

Called by `make check` via `make verify-traceability` (sibling gate). Exits
0 clean, non-zero with named errors.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

IMPORT_RE = re.compile(r"^\s*(?:from\s+beaconry_health|import\s+beaconry_health)")
COPY_RE = re.compile(r"^\s*COPY\s+.*beaconry_health", re.IGNORECASE)


def _service_imports_beaconry_health(service_dir: Path) -> bool:
    for py in service_dir.rglob("*.py"):
        try:
            for line in py.read_text().splitlines():
                if IMPORT_RE.match(line):
                    return True
        except OSError:
            continue
    return False


def _dockerfile_copies_beaconry_health(dockerfile: Path) -> bool:
    try:
        for line in dockerfile.read_text().splitlines():
            if COPY_RE.match(line):
                return True
    except OSError:
        return False
    return False


def check_dockerfile(dockerfile: Path, service_dir: Path) -> list[str]:
    """Return a list of error strings — empty means clean."""
    uses = _service_imports_beaconry_health(service_dir)
    copies = _dockerfile_copies_beaconry_health(dockerfile)
    if uses and not copies:
        return [
            f"{dockerfile}: service imports beaconry_health but Dockerfile forgot "
            f"`COPY beaconry_health/ ./beaconry_health/` — add it next to the other COPY lines."
        ]
    return []


def main() -> int:  # pragma: no cover - entry-point wrapper exercised via make check, not pytest
    poc_root = Path(__file__).resolve().parents[1]
    errors: list[str] = []
    for dockerfile in poc_root.glob("*/Dockerfile"):
        service_dir = dockerfile.parent
        errors.extend(check_dockerfile(dockerfile, service_dir))

    if errors:
        print("verify-dockerfile-health-copy: found issues:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 1
    print(
        f"✓ {len(list(poc_root.glob('*/Dockerfile')))} Dockerfile(s) checked for beaconry_health COPY"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
