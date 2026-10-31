"""Per-file coverage floor — fail if any single module drops below MIN_PCT.

Companion to `coverage.py`'s global `fail_under`. Where global coverage is a
reservoir metric (one high-coverage file masks many low ones), per-file
enforcement blocks silent regressions in individual modules.

Reads `coverage.json` (produced by `pytest --cov-report=json`). Allowlist is
read from a module-level dict — any file whose basename is in the allowlist is
checked at the allowlisted floor instead of the default. Use allowlist entries
sparingly and only for IO-wrapper modules; prefer `# pragma: no cover` on
individual subprocess/filesystem lines over module-level exemption.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DEFAULT_MIN_PCT = 65.0

# Allowlist: {relative_path: (floor_pct, reason)}. Explicit, auditable, small.
# Any file not in this dict is held to DEFAULT_MIN_PCT.
ALLOWLIST: dict[str, tuple[float, str]] = {
    "poc/demo.py": (
        60.0,
        "interactive main loop + mock-pipeline orchestrator; smoke-tested via "
        "`demo.sh --smoke` in `make check`.",
    ),
    "poc/scripts/verify_compose_env_propagation.py": (
        45.0,
        "codebase-walking main() is live-tested via `make check`; parsers are "
        "unit-tested. Pragma on the walker would make the file harder to read.",
    ),
    "poc/scripts/verify_dockerfile_health_copy.py": (
        60.0,
        "scanner main() is live-tested via `make check`; policy fns unit-tested.",
    ),
    "poc/scripts/verify_memory_drift.py": (
        70.0,
        "orchestrator main() is live-tested via `make check`.",
    ),
    "poc/scenarios/__main__.py": (
        0.0,
        "CLI entry glue (argparse + pass/fail reporter); exercised end-to-end "
        "by `make test-scenarios` and the in-process integration test.",
    ),
}


def check_coverage(cov_json: Path, min_pct: float = DEFAULT_MIN_PCT) -> list[str]:
    """Return a list of error strings — empty means clean."""
    try:
        data = json.loads(cov_json.read_text())
    except (OSError, ValueError) as exc:
        return [f"could not read coverage json at {cov_json}: {exc}"]

    files = data.get("files", {})
    errors: list[str] = []
    for rel_path, info in sorted(files.items()):
        pct = float(info.get("summary", {}).get("percent_covered", 0.0))
        allowlisted = ALLOWLIST.get(rel_path)
        floor = allowlisted[0] if allowlisted else min_pct
        if pct < floor:
            if allowlisted:
                reason = allowlisted[1]
                errors.append(
                    f"{rel_path}: {pct:.1f}% < allowlisted floor {floor:.1f}% "
                    f"({reason}) — raise its allowlist entry or add tests"
                )
            else:
                errors.append(
                    f"{rel_path}: {pct:.1f}% < {floor:.1f}% floor — "
                    f"add tests or allowlist with reason in verify_per_file_coverage.py"
                )
    return errors


def main() -> int:  # pragma: no cover - entry-point wrapper exercised via make check, not pytest
    repo_root = Path(__file__).resolve().parents[2]
    cov_json = repo_root / "coverage.json"
    if not cov_json.exists():
        print(
            f"verify-per-file-coverage: coverage.json not found at {cov_json} — "
            "run `pytest --cov-report=json` first (make test does this).",
            file=sys.stderr,
        )
        return 1

    errors = check_coverage(cov_json)
    if errors:
        print(
            f"verify-per-file-coverage: {len(errors)} file(s) below floor:",
            file=sys.stderr,
        )
        for e in errors:
            print(f"  {e}", file=sys.stderr)
        return 1
    doc = json.loads(cov_json.read_text())
    file_count = len(doc.get("files", {}))
    print(
        f"✓ {file_count} file(s) ≥ 65% coverage (plus {len(ALLOWLIST)} allowlisted at lower floors)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
