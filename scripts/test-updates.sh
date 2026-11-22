#!/usr/bin/env bash
# Smart test runner: only runs tests for changed modules (BC-149 / SH-023).
#
# Mirrors homelab/scripts/test-updates.sh + forge/scripts/smart-check.sh:
# detect changed files via git diff, map each to its top-level module,
# run the matching slice of pytest. Falls back to full suite when:
#   - tooling/config files touched (Makefile, pyproject.toml, requirements*, conftest)
#   - >4 modules affected (diminishing-returns threshold)
#   - BEACONRY_FULL=1 (operator override)
#
# Always-run GLOBAL tests cover cross-module invariants (traceability,
# docs-drift, evidence-pointers) regardless of what changed.
#
# Usage:
#   bash scripts/test-updates.sh           # run scoped pytest
#   bash scripts/test-updates.sh --dry-run # print planned invocation, exit 0
#
# Environment:
#   GITHUB_BASE_REF       set by GHA on pull_request
#   GITHUB_EVENT_NAME     set by GHA (push | pull_request)
#   BEACONRY_FULL=1       force full run
#   PYTEST_ARGS           appended to pytest command

set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

PYTEST="${PYTEST:-$(if [ -x .venv/bin/pytest ]; then echo .venv/bin/pytest; else echo pytest; fi)}"
EXTRA="${PYTEST_ARGS:-}"

say() { printf '==> %s\n' "$*"; }

# ── Determine BASE ref ──────────────────────────────────────────────────────
if [[ -n "${GITHUB_BASE_REF:-}" ]]; then
  BASE="origin/${GITHUB_BASE_REF}"
elif [[ "${GITHUB_EVENT_NAME:-}" == "push" ]]; then
  BASE="HEAD^"
else
  BASE="origin/dev"
fi
git rev-parse --verify --quiet "${BASE}" >/dev/null 2>&1 || BASE="HEAD^"

CHANGED=$(git diff --name-only "${BASE}..HEAD" 2>/dev/null || echo "")

run_pytest() {
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "[dry-run] $PYTEST $*"
    exit 0   # short-circuit: dry-run shows only the FIRST dispatch that would fire
  fi
  # shellcheck disable=SC2086
  exec $PYTEST "$@" $EXTRA
}

# ── 1. Forced-full override + empty diff + docs-only short-circuits ─────────
if [[ "${BEACONRY_FULL:-0}" == "1" ]]; then
  say "BEACONRY_FULL=1 — running full test suite"
  run_pytest
fi

if [[ -z "$CHANGED" ]]; then
  say "no diff vs $BASE — running full test suite (defensive; empty diff probably means branch is identical to base)"
  run_pytest
fi

NON_DOC=$(echo "$CHANGED" | grep -vE '\.md$|^docs/' || true)
if [[ -z "$NON_DOC" ]]; then
  say "docs-only diff — running global invariants (traceability + docs-drift)"
  run_pytest poc/tests/integration/test_BC_004_traceability.py \
             poc/tests/integration/test_BC_019_verify_evidence_pointers.py \
             poc/tests/unit/test_docs_drift_guard.py
fi

# ── 2. Full-run bailouts — tooling / config / conftest touched ──────────────
FULL_RE='^(Makefile$|pyproject\.toml$|requirements.*\.txt$|poc/tests/conftest\.py$|\.github/workflows/|scripts/test-updates\.sh$|scripts/lint-updates\.sh$)'
BAILOUT=$(echo "$CHANGED" | grep -E "$FULL_RE" | head -1 || true)
if [[ -n "$BAILOUT" ]]; then
  say "full-run bailout — tooling/config file $BAILOUT touched; running full test suite"
  run_pytest
fi

# ── 3. Compute affected modules ──────────────────────────────────────────────
# Each module → set of test-file paths that cover it.
declare -A MODULE_TESTS=(
  [hub]="poc/tests/integration/test_BC_010_compose_health_chain.py \
         poc/tests/integration/test_BC_005_compose.py \
         poc/tests/integration/test_BC_030_internet_facing_detection.py \
         poc/tests/integration/test_BC_031_scan_reconciliation.py \
         poc/tests/integration/test_BC_037_on_demand_scan.py \
         poc/tests/unit"
  [scout]="poc/tests/integration/test_BC_020_kev_ingest.py \
           poc/tests/integration/test_BC_002_real_pipeline.py \
           poc/tests/integration/test_BC_001_mocked_e2e.py \
           poc/tests/unit"
  [scripts]="poc/tests/integration/test_BC_019_verify_evidence_pointers.py \
             poc/tests/integration/test_BC_041_reverse_check.py \
             poc/tests/unit"
  [beaconry_health]="poc/tests/integration/test_BC_010_compose_health_chain.py \
                     poc/tests/unit"
  [telegram_mock]="poc/tests/integration/test_BC_007_findings_cli.py \
                   poc/tests/unit"
  [tests]="__self__"   # changed test files themselves → run those only
)

affected=()
touched_tests=()
for p in $CHANGED; do
  case "$p" in
    poc/hub/*)              affected+=("hub") ;;
    poc/scout/*)            affected+=("scout") ;;
    poc/scripts/*)          affected+=("scripts") ;;
    poc/beaconry_health/*)  affected+=("beaconry_health") ;;
    poc/telegram_mock/*)    affected+=("telegram_mock") ;;
    poc/tests/*)
      affected+=("tests")
      [[ -f "$p" ]] && touched_tests+=("$p")
      ;;
    *) : ;;  # unknown path — covered by bailouts above, otherwise fall through
  esac
done

if [ ${#affected[@]} -gt 0 ]; then
  mapfile -t affected < <(printf '%s\n' "${affected[@]}" | sort -u)
fi

if [[ ${#affected[@]} -eq 0 ]]; then
  say "non-doc diff touches no known module — defensive full run"
  run_pytest
fi

if [[ ${#affected[@]} -gt 4 ]]; then
  say "full-run bailout — ${#affected[@]} modules affected (cap 4): ${affected[*]}"
  run_pytest
fi

# ── 4. Collect test paths for the affected modules ──────────────────────────
to_run=()
for mod in "${affected[@]}"; do
  if [[ "${MODULE_TESTS[$mod]}" == "__self__" ]]; then
    for t in "${touched_tests[@]}"; do to_run+=("$t"); done
  else
    for t in ${MODULE_TESTS[$mod]}; do to_run+=("$t"); done
  fi
done

# Always-run GLOBAL tests.
GLOBAL_TESTS=(
  poc/tests/integration/test_BC_004_traceability.py
  poc/tests/integration/test_BC_019_verify_evidence_pointers.py
  poc/tests/unit/test_docs_drift_guard.py
)
for t in "${GLOBAL_TESTS[@]}"; do to_run+=("$t"); done

mapfile -t to_run < <(printf '%s\n' "${to_run[@]}" | sort -u)

say "scoped run — ${#affected[@]} module(s) affected: ${affected[*]}"
say "  test paths: ${#to_run[@]} entries"
run_pytest "${to_run[@]}"
