"""BC-021 Dockerfile sources lint — catch forgotten `COPY <svc>/*.py`.

Rule: every non-test, non-`__init__.py` `.py` file in `poc/<service>/`
must be matched by at least one COPY line in that service's Dockerfile.

Three COPY syntaxes are honored:
  1. Explicit filename:  `COPY hub/kev.py ...`     → matches `kev.py` only
  2. Glob:               `COPY hub/*.py ...`       → matches every `*.py`
  3. Directory:          `COPY hub/ ...`           → matches everything

Catches BC-020-class ship-time packaging regressions where a new import
lands in a service but the Dockerfile's explicit COPY list isn't updated.
Companion to BC-012's `verify-dockerfile-health-copy` (different direction:
intra-service vs external shared-package COPY).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Files intentionally not required to be COPY'd:
#   - `__init__.py` (package marker; often not needed at container runtime)
#   - `test_*.py`   (test files; never shipped)
_SKIP_RE = re.compile(r"^(__init__\.py|test_.*\.py)$")

_COPY_LINE_RE = re.compile(r"^\s*COPY\s+(.+)$", re.IGNORECASE)
# Trailing destination is one token at the end; everything before is a source.
# We don't need to be shell-perfect — just split on whitespace and treat the
# last token as the destination.


def _dockerfile_sources(dockerfile: Path) -> list[str]:
    """Return every COPY source token across all `COPY` lines."""
    sources: list[str] = []
    try:
        for raw_line in dockerfile.read_text().splitlines():
            m = _COPY_LINE_RE.match(raw_line)
            if not m:
                continue
            tokens = m.group(1).split()
            if len(tokens) < 2:
                continue
            # All tokens except the last (destination).
            sources.extend(tokens[:-1])
    except OSError:
        return []
    return sources


def _service_py_files(service_dir: Path) -> list[str]:
    """Return the basenames of non-test, non-__init__ .py files in `service_dir`.
    Non-recursive — subdirectories are expected to be COPY'd via dir-level COPY."""
    files: list[str] = []
    for p in sorted(service_dir.glob("*.py")):
        if _SKIP_RE.match(p.name):
            continue
        files.append(p.name)
    return files


def explicit_copy_matches(source_token: str, service: str, filename: str) -> bool:
    """`COPY hub/kev.py ...` matches basename `kev.py` in service `hub`."""
    return source_token in (f"{service}/{filename}", filename)


def glob_copy_matches(source_token: str, service: str, filename: str) -> bool:
    """`COPY hub/*.py ...` matches every .py in `hub/`."""
    if not filename.endswith(".py"):
        return False
    return source_token in (f"{service}/*.py", "*.py")


def dir_copy_matches(source_token: str, service: str) -> bool:
    """`COPY hub/ ...` or `COPY hub ...` matches everything in `hub/`."""
    return source_token in (f"{service}/", service, f"./{service}", f"./{service}/")


def _is_covered(filename: str, service: str, copy_sources: list[str]) -> bool:
    for src in copy_sources:
        if (
            explicit_copy_matches(src, service, filename)
            or glob_copy_matches(src, service, filename)
            or dir_copy_matches(src, service)
        ):
            return True
    return False


def check_service(service_dir: Path, dockerfile: Path) -> list[str]:
    """Return error strings for this service; empty = pass."""
    service_name = service_dir.name
    py_files = _service_py_files(service_dir)
    copy_sources = _dockerfile_sources(dockerfile)
    missing = [f for f in py_files if not _is_covered(f, service_name, copy_sources)]
    if not missing:
        return []
    return [
        f"{dockerfile}: Python files exist in {service_dir}/ but Dockerfile does "
        f"not COPY them:\n    - " + "\n    - ".join(missing) + "\n"
        f"  Fix: add them to the `COPY {service_name}/*.py ./` line (or use a glob)."
    ]


def main() -> int:  # pragma: no cover - entry-point wrapper exercised via make check, not pytest
    poc_root = Path(__file__).resolve().parents[1]
    dockerfiles = sorted(poc_root.glob("*/Dockerfile"))
    errors: list[str] = []
    for dockerfile in dockerfiles:
        errors.extend(check_service(dockerfile.parent, dockerfile))

    if errors:
        print("verify-dockerfile-sources: found issues:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 1
    print(f"✓ {len(dockerfiles)} Dockerfile(s) checked — every service-local .py is COPY'd")
    return 0


if __name__ == "__main__":
    sys.exit(main())
