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


# ── BC-041: reverse check — every integration test referenced by a plan row ──

# Match `BC-NNN-<slug>` from `test_BC_NNN_<slug>.py` filenames.
# Returns the dash-form slug (`BC-041-reverse-check`) suitable for plan-file
# lookup, since plan files follow `BC-NNN-<slug>-plan.md`.
_BC_FILENAME_RE = re.compile(r"^test_BC_(\d+)_([a-zA-Z0-9_]+)\.py$")

# Match `def test_<name>(...)` at top of line.
_TEST_FN_RE = re.compile(r"^def\s+(test_[a-zA-Z0-9_]+)\s*\(", re.MULTILINE)

# Functions intentionally present in a BC-tagged test file but not expected
# to have a plan row (helpers, cross-BC smoke tests, fixtures-only).
# Each entry needs a one-line reason — visible, auditable, extensible.
#
# Policy for pre-BC-041 entries ("grandfathered"): these tests shipped
# before the reverse-check gate existed. They're real, correct, and
# protect behavior — just not referenced by the plan's shipping-evidence
# table. We allowlist them rather than backfill 24 plan rows for stories
# that have long moved on. New BCs after BC-041 are expected to be clean.
REVERSE_CHECK_ALLOWLIST: dict[str, str] = {
    # BC-008 pre-ship audit protocol tests — structural self-tests of the
    # audit template, orthogonal to the plan's acceptance scenarios.
    "test_bc_003_has_shipping_evidence_table": "pre-BC-041 — BC-008 structural self-test",
    "test_claude_md_names_step_6a": "pre-BC-041 — BC-008 structural self-test",
    "test_plan_template_has_shipping_evidence_section": "pre-BC-041 — BC-008 structural self-test",
    "test_readme_mentions_pre_ship_audit": "pre-BC-041 — BC-008 structural self-test",
    "test_shipping_evidence_rows_mentioned_in_cap_t026": "pre-BC-041 — BC-008 structural self-test",
    "test_workflow_memory_names_step_6a": "pre-BC-041 — BC-008 structural self-test",
    # BC-010 compose-health-chain assertions on docker-compose.yml structure.
    "test_hub_healthcheck_targets_readyz": "pre-BC-041 — BC-010 compose config assertion",
    "test_scout_depends_on_hub_service_healthy": "pre-BC-041 — BC-010 compose config assertion",
    "test_scout_exposes_http_port": "pre-BC-041 — BC-010 compose config assertion",
    "test_scout_healthcheck_targets_readyz": "pre-BC-041 — BC-010 compose config assertion",
    "test_scout_mounts_beaconry_data_volume": "pre-BC-041 — BC-010 compose config assertion",
    # BC-012 shared beaconry_health — Dockerfile lint + pyright trap tests.
    "test_hub_dockerfile_passes_lint": "pre-BC-041 — BC-012 lint self-test",
    "test_lint_flags_fixture_dockerfile_missing_copy": "pre-BC-041 — BC-012 lint self-test",
    "test_lint_tolerates_service_that_doesnt_use_beaconry_health": "pre-BC-041 — BC-012 lint self-test",
    "test_scout_dockerfile_passes_lint": "pre-BC-041 — BC-012 lint self-test",
    "test_fixture_aliases_match_production_aliases": "pre-BC-041 — BC-012 pyright trap check",
    "test_pyright_accepts_correct_usage": "pre-BC-041 — BC-012 pyright trap check",
    # BC-019 self-test of the evidence-pointer parser.
    "test_bare_test_file_without_node_id_still_checked": "pre-BC-041 — BC-019 parser self-test",
    # BC-020 KEV ingest — notification-format tests orthogonal to the ingest Gherkin.
    "test_tier_0_notification_has_kev_header": "pre-BC-041 — BC-020 notification format self-test",
    "test_non_tier_0_notification_omits_kev_header": "pre-BC-041 — BC-020 notification format self-test",
    "test_findings_cli_surfaces_kev_and_sorts_tier_first": "pre-BC-041 — BC-020 findings CLI self-test",
    # BC-021 Dockerfile sources — directory-copy equivalence check.
    "test_dir_copy_matches_all": "pre-BC-041 — BC-021 lint helper",
    # BC-037 on-demand scan — HTTP handler non-blocking + lock-release checks.
    "test_trigger_after_release_returns_202": "pre-BC-041 — BC-037 round-trip helper",
    "test_trigger_does_not_block_handler_on_long_scan": "pre-BC-041 — BC-037 handler-latency guard",
}


def _bc_slug_from_test_file(path: Path) -> str | None:
    """Return `BC-NNN-slug` for a `test_BC_NNN_slug.py`, else None."""
    m = _BC_FILENAME_RE.match(path.name)
    if not m:
        return None
    num, slug = m.group(1), m.group(2)
    return f"BC-{num}-{slug.replace('_', '-')}"


def _find_plan_for_bc(plans_dir: Path, bc_slug: str) -> Path | None:
    """Return the plan file matching `BC-NNN-*-plan.md` for a given BC slug.

    Slug matching is prefix-based on the BC number since the test file's
    slug and the plan's slug can diverge by a word (e.g. `test_BC_038_scenario_harness.py`
    ↔ `BC-038-scenario-harness-plan.md`). We match by `BC-NNN-` prefix.
    """
    prefix = bc_slug.split("-")[0] + "-" + bc_slug.split("-")[1] + "-"
    for plan in plans_dir.glob("BC-*-plan.md"):
        if plan.name.startswith(prefix):
            return plan
    return None


def _test_functions_in_file(test_file: Path) -> list[str]:
    """Return the list of `test_*` function names defined in `test_file`.

    Skips private helpers (names starting with `_`). Finds top-level
    `def test_...(` declarations via regex — doesn't execute the file.
    """
    try:
        text = test_file.read_text()
    except (OSError, UnicodeDecodeError):
        return []
    return _TEST_FN_RE.findall(text)


def _emit_reverse_template(test_file: Path, test_name: str) -> str:
    """Copy-pasteable shipping-evidence row for an orphan test."""
    return f"| <scenario / goal> | integration | `{test_file.name}::{test_name}` | ✓ |"


def reverse_check(
    *,
    plans_dir: Path | None = None,
    tests_dir: Path | None = None,
) -> list[str]:
    """Return a list of orphan-test violations. Empty = clean.

    For every `poc/tests/integration/test_BC_NNN_*.py`:
      - Resolve the matching `BC-NNN-*-plan.md`.
      - If no matching plan → soft-pass (BC-019 handles plan presence).
      - If plan has no `## Shipping evidence` section → soft-pass.
      - Otherwise: for every `test_*` function in the file, assert the
        plan body mentions that function name somewhere in the shipping-
        evidence table.
    """
    plans_dir = plans_dir or (_REPO_ROOT / "docs" / "plan")
    tests_dir = tests_dir or (_REPO_ROOT / "poc" / "tests" / "integration")
    if not plans_dir.is_dir() or not tests_dir.is_dir():
        return []

    violations: list[str] = []
    for test_file in sorted(tests_dir.glob("test_BC_*.py")):
        bc_slug = _bc_slug_from_test_file(test_file)
        if bc_slug is None:
            continue
        plan = _find_plan_for_bc(plans_dir, bc_slug)
        if plan is None:
            continue  # soft-pass: BC-019 enforces plan presence
        plan_text = plan.read_text()
        rows = _parse_shipping_evidence_rows(plan_text)
        if rows is None:
            continue  # soft-pass: no `## Shipping evidence` section at all
        # Empty-list (section exists, no rows) is NOT soft-pass — otherwise
        # a plan can escape the gate by having a header + separator only.
        # Flatten rows into one blob — reverse check is "is the test name
        # mentioned anywhere in the table?", not strict pointer parsing.
        table_blob = "\n".join("|".join(row) for row in rows) if rows else ""
        for test_name in _test_functions_in_file(test_file):
            if test_name in REVERSE_CHECK_ALLOWLIST:
                continue
            if test_name not in table_blob:
                violations.append(
                    f"{test_file.relative_to(_REPO_ROOT)}::{test_name}: "
                    f"shipped as integration test but no shipping-evidence row "
                    f"in `{plan.relative_to(_REPO_ROOT)}` references it. "
                    f"Add a row OR allowlist with reason in "
                    f"`poc/scripts/verify_evidence_pointers.py::REVERSE_CHECK_ALLOWLIST`.\n"
                    f"        Template:\n"
                    f"        {_emit_reverse_template(test_file, test_name)}"
                )
    return violations


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

    # BC-041: reverse pass — every integration test referenced by a plan row.
    reverse_errors = reverse_check(plans_dir=plans_dir, tests_dir=tests_root / "integration")
    for e in reverse_errors:
        all_errors.append(f"  {e}")

    if all_errors:
        print(
            f"verify-evidence-pointers: {len(all_errors)} violation(s):",
            file=sys.stderr,
        )
        for err in all_errors:
            print(err, file=sys.stderr)
        print(
            "Fix: either implement the missing test, revise the plan row, "
            "or (reverse pass only) allowlist the helper.",
            file=sys.stderr,
        )
        return 1
    print(
        f"✓ {len(plans)} plan(s) checked — "
        f"all pointers resolve + every integration test is referenced"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
