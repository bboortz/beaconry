"""BC-019 — grep-verify every shipping-evidence pointer.

Parses every `docs/plan/BC-*-plan.md § Shipping evidence` table and asserts
that every ✓ row with a test-shaped pointer resolves to an actual pytest
node id (or an on-disk file for file-only pointers).

Test-shape regex: a pointer eligible for strict resolution contains `.py`
with optional `::test_name[param]`. Prose, make targets, URLs are skipped.

Deferred rows (⚠️ ❌) and plans without a `## Shipping evidence` section
are grandfathered — same policy as `verify_traceability.py`.

Env overrides (for tests + custom invocations):
  BEACONRY_PLANS_DIR  — directory to scan for BC-*-plan.md (default docs/plan)
  BEACONRY_TESTS_ROOT — tests root for pytest collection (default poc/tests)

Exits 0 on clean, 1 on any dead pointer.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

# Reuse the plan-table parser from verify_traceability — same policy as the
# shipping-evidence completeness lint; keeps the two gates aligned.
from poc.scripts.verify_traceability import _parse_shipping_evidence_rows

# Pointer eligible for strict resolution. Shape:
#     path/file.py[::test_name][[parametrize-id]]
# Node-id chars (after `::`): \w + `.` + `-` (NO comma, NO colon — those
# terminate the pointer). Optional `[...parametrize-id...]` can contain
# anything except `]` (parametrize ids legitimately have `:`, `,`, ` `, etc.).
# Backticks are stripped before matching.
_POINTER_RE = re.compile(r"([\w./-]+\.py(?:::[\w.-]+(?:\[[^\]]*\])?)?)")

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _strip_backticks(cell: str) -> str:
    return cell.replace("`", "")


def extract_pointers_from_cell(cell: str) -> list[str]:
    """Return the test-shaped pointers in a plan-table pointer cell."""
    return _POINTER_RE.findall(_strip_backticks(cell))


def normalize_node_id(collected: str) -> str:
    """Strip directory prefix from a pytest node id.

    pytest emits `poc/tests/unit/test_x.py::test_fn`;
    plans typically write `test_x.py::test_fn`. Compare by basename.
    """
    if "::" in collected:
        head, tail = collected.split("::", 1)
        return f"{Path(head).name}::{tail}"
    return Path(collected).name


def collect_pytest_nodes(tests_root: Path) -> set[str]:
    """Run pytest --collect-only -q and return the set of normalized node ids.

    Opts into every marker (`-m ""`) so e2e/compose/real_tools nodes are
    collected too — plans legitimately point at those.
    """
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            str(tests_root),
            "--collect-only",
            "-q",
            "--no-cov",
            "-m",
            "",
            "-p",
            "no:cacheprovider",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(_REPO_ROOT),
    )
    nodes: set[str] = set()
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("=", "no tests", "collected")):
            continue
        if ".py" not in stripped:
            continue
        nodes.add(normalize_node_id(stripped))
    return nodes


def row_is_strict(status_cell: str) -> bool:
    """Only ✓ rows are strictly validated. ⚠️ and ❌ are explicit deferrals."""
    return "✓" in status_cell


def _resolve_pointer(
    pointer: str,
    collected_nodes: set[str],
    tests_root: Path,
) -> str | None:
    """Return an error string if the pointer doesn't resolve, else None."""
    if "::" in pointer:
        # Node-id pointer — must be in the collected set.
        file_part, _ = pointer.split("::", 1)
        file_name = Path(file_part).name
        # Check file-name component matches any collected file.
        any_file_match = any(node.startswith(f"{file_name}::") for node in collected_nodes)
        if not any_file_match:
            return f"file {file_name!r}: not collected by pytest (missing on disk?)"
        if pointer not in collected_nodes:
            # Reduce to basename-of-head + tail just in case pointer carries a path.
            normalized = normalize_node_id(pointer)
            if normalized not in collected_nodes:
                return f"pytest node id {pointer!r}: not collected"
    else:
        # File-only pointer. Strict check applies ONLY to files whose basename
        # starts with `test_` — those are claims that a test file exists.
        # Non-test file refs (`hub.py`, `waterfall.py`, `bait_snooze.py`) are
        # ambiguous: they may be prose diff anchors or ad-hoc scripts, which
        # this lint intentionally doesn't police. Semantic correctness there
        # is a human-review concern.
        file_name = Path(pointer).name
        if not file_name.startswith("test_"):
            return None
        if (_REPO_ROOT / pointer).exists():
            return None
        if any(_REPO_ROOT.rglob(file_name)):
            return None
        return f"test file {pointer!r}: not found on disk"
    return None


def check_plan(plan_path: Path, collected_nodes: set[str], tests_root: Path) -> list[str]:
    """Return a list of error strings for this plan; empty = pass."""
    plan_text = plan_path.read_text()
    rows = _parse_shipping_evidence_rows(plan_text)
    if rows is None or not rows:
        return []  # grandfathered or empty table
    errors: list[str] = []
    for i, row in enumerate(rows, start=1):
        if len(row) < 2:
            continue
        status_cell = row[-1]
        if not row_is_strict(status_cell):
            continue
        # Pointer cell convention: second-to-last if status is last; some rows
        # may have fewer columns. We scan all non-first/non-last cells for
        # test-shaped pointers so edits that reorder columns don't silently
        # break the lint.
        candidate_cells = row[1:-1] if len(row) >= 3 else row[:-1]
        pointers: list[str] = []
        for cell in candidate_cells:
            pointers.extend(extract_pointers_from_cell(cell))
        for pointer in pointers:
            err = _resolve_pointer(pointer, collected_nodes, tests_root)
            if err is not None:
                errors.append(f"  {plan_path}:\n    row {i} ✓ pointer {pointer!r}\n      → {err}")
    return errors


def main() -> int:  # pragma: no cover - entry-point wrapper exercised via make check, not pytest
    plans_dir = Path(os.environ.get("BEACONRY_PLANS_DIR", str(_REPO_ROOT / "docs" / "plan")))
    tests_root = Path(os.environ.get("BEACONRY_TESTS_ROOT", str(_REPO_ROOT / "poc" / "tests")))
    if not plans_dir.is_dir():
        print(f"verify-evidence-pointers: plans dir {plans_dir} not found", file=sys.stderr)
        return 0  # soft-pass; traceability lint handles plan-doc presence

    collected_nodes = collect_pytest_nodes(tests_root)
    plans = sorted(plans_dir.glob("BC-*-plan.md"))

    all_errors: list[str] = []
    for plan_path in plans:
        all_errors.extend(check_plan(plan_path, collected_nodes, tests_root))

    if all_errors:
        print(
            f"verify-evidence-pointers: {len(all_errors)} dead pointer(s):",
            file=sys.stderr,
        )
        for err in all_errors:
            print(err, file=sys.stderr)
        print(
            "Fix: either implement the missing test or revise the plan row.",
            file=sys.stderr,
        )
        return 1
    print(f"✓ {len(plans)} plan(s) checked — all shipping-evidence pointers resolve")
    return 0


if __name__ == "__main__":
    sys.exit(main())
