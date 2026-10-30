# BC-019 — Grep-verify every shipping-evidence pointer

*Branch: `feat/BC-019-verify-evidence-pointers`*
*Start date: 2026-10-30*
*Story: `docs/stories/BC-019-verify-evidence-pointers.md`*

---

## Situation

`docs/plan/BC-*-plan.md § Shipping evidence` tables are the ship-gate for every story. Each row is scanned by `verify_traceability.py::check_shipping_evidence` which asserts (a) every row's status column has `✓`, (b) no template placeholders remain, (c) the Gherkin-scenario count ≤ row count (advisory only).

What's NOT checked: whether a row's `Pointer` cell actually resolves. BC-018 shipped with 5 ✓ rows pointing at `test_telegram_mock.py::test_*` nodes that did not exist on disk — none of the existing lints caught it. The pointers were discovered during a second-pass audit; the behaviors were covered by the e2e test, but a mock-only unit regression would have slipped through until the next time someone grepped by hand.

## Problems to solve

- Shipping-evidence tables can mark rows ✓ with vaporware pointers; `make check` is silent.
- The audit protocol's most error-prone lens (Gherkin coverage) depends on human-grep vigilance; lint doesn't catch pointer drift when tests are renamed or removed.
- Second-pass audits are the current safety net — not reliable at scale.

## Goals

1. New gate `make verify-evidence-pointers` fails loud when a ✓ row's test-shaped pointer doesn't resolve (file missing OR pytest node id uncollected). (Story Scenarios 1, 2, 4, 5, 6)
2. Non-test-shaped pointers (prose, make targets, grep commands, URLs) are explicitly skipped — only strict test-shape pointers are resolved. (Story Scenario 3)
3. Parametrized node ids (`test_x.py::test_fn[case-1]`) match the parametrized collection exactly, not the base test. (Story Scenario 7)
4. New gate is wired into `make check` + `memory/workflow.md § Blocking gates` + pre-push hooks list.
5. Retroactive run against every existing `BC-NNN-*-plan.md` surfaces any other latent pointer lies as free regression data.

## Scope

- **New:** `poc/scripts/verify_evidence_pointers.py` — the lint script
- **New:** `poc/tests/integration/test_BC_019_verify_evidence_pointers.py` — red integration test encoding all 7 Gherkin scenarios
- **New:** `poc/tests/unit/test_verify_evidence_pointers.py` — unit tests for the pointer-extraction + resolution helpers
- **Modified:** `Makefile` — add `verify-evidence-pointers` target; include in `check` and `pre-push`
- **Modified:** `memory/workflow.md § Blocking gates` — add row for the new gate
- **Modified:** `docs/CAPABILITIES.md` — register CAP-T057 with bidirectional pointers
- **Modified:** `docs/stories/INDEX.md` — add BC-019 row
- **Modified:** `poc/scripts/verify_docs_drift.py` — if it enumerates make targets, pick up the new one
- **Modified:** `docs/plan/IDEAS.md` — bump the make-demo recipe from BC-019 → BC-020; drop the "audit-protocol upgrade" IDEA entry (now shipped)

## Non-goals

- No semantic check that the test asserts the row's claim (human review stays mandatory).
- No validation of non-test pointers (make targets, URLs, "live manual" recipes).
- No change to `verify_traceability.py` — the two lints stay distinct: traceability is CAPABILITIES ↔ tests; this is plan-tables ↔ tests.
- No automatic row-row-row-row-fix. Lint fails; developer fixes.

## Design decisions

- **Reuse `_parse_shipping_evidence_rows` logic from `verify_traceability.py`** rather than duplicating. Options considered: (a) extract to a shared module in `poc/scripts/_shared.py` (nicer long-term, bigger diff); (b) import the function from `verify_traceability`; (c) copy-paste. Choice: **(b) direct import** — the two scripts are already peers in `poc/scripts/` and share a parent concern.
- **Test-shape regex is strict**: pointer must match `[\w/.-]+\.py(::[\w\[\]:,.-]+)?` to be eligible for strict resolution. Prose and prose-ish refs ("`make status`", "run after Phase 3") are intentionally skipped — matching our Out-of-scope line.
- **Status-column gate**: only rows whose status cell contains `✓` get strict resolution. ⚠️ and ❌ are explicit deferrals the author owns.
- **Pytest collection snapshot**: run `pytest --collect-only -q --no-cov -m "" -q` **once** at the top of the script, parse to a set of fully-qualified node ids, then do set-containment per pointer. Avoids 1 subprocess per pointer. (`-m ""` opts into collection of all markers including `e2e`/`compose`/`real_tools`.)
- **Node-id normalization**: pytest emits `poc/tests/unit/test_x.py::test_y` whereas plans typically write `test_x.py::test_y`. Normalize by taking the file-name tail of each node id's prefix.
- **Grandfathering**: a plan with no `## Shipping evidence` heading is skipped (same rule as `verify_traceability.py`).

## Breaking changes

None. Pure additive gate; `make check` now runs one additional lint.

## Merge plan

1. Branch `feat/BC-019-verify-evidence-pointers`
2. Red integration test → red
3. Impl → integration + unit tests green
4. `make check` green on branch tip (including the new gate running retroactively against every existing plan — any surfaced dead pointer is also fixed in this branch)
5. Squash merge → `dev` (FF since no concurrent dev work)
6. Post-merge: flip story to `shipped`, update `INDEX.md`

## Rollout plan

None — change is additive. First `make check` run after merge will fail if any existing plan has a dead pointer; fix those in the same branch so merge-to-dev lands green.

## Shipping evidence

Pre-ship audit (DD-TGD step 6a). This story may only move to `shipped` when every row below is ✓.

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Scenario 1 — dead file pointer fails the gate | integration test | `test_BC_019_verify_evidence_pointers.py::test_dead_file_pointer_fails` | ✓ |
| Scenario 2 — dead node id fails the gate | integration test | `test_BC_019_verify_evidence_pointers.py::test_dead_node_id_fails` | ✓ |
| Scenario 3 — non-test-shaped pointer is skipped | integration test | `test_BC_019_verify_evidence_pointers.py::test_non_test_shaped_pointer_skipped` | ✓ |
| Scenario 4 — deferred rows (⚠️/❌) are not grep-verified | integration test | `test_BC_019_verify_evidence_pointers.py::test_deferred_rows_skipped` | ✓ |
| Scenario 5 — happy path: every pointer resolves → silent pass | integration test | `test_BC_019_verify_evidence_pointers.py::test_happy_path_silent_pass` | ✓ |
| Scenario 6 — missing shipping-evidence section is grandfathered | integration test | `test_BC_019_verify_evidence_pointers.py::test_missing_section_grandfathered` | ✓ |
| Scenario 7 — parametrized node ids match exactly | integration test | `test_BC_019_verify_evidence_pointers.py::test_parametrized_node_id_matches` | ✓ |
| Pointer-extraction regex covers all plan-table pointer shapes | unit test | `test_verify_evidence_pointers.py::test_extract_pointers_from_cell` | ✓ |
| Node-id normalization: plan writes `test_x.py::fn`, pytest emits `path/test_x.py::fn` → matches | unit test | `test_verify_evidence_pointers.py::test_normalize_node_id` | ✓ |
| Status cell containing ✓ is the only strict trigger | unit test | `test_verify_evidence_pointers.py::test_only_check_mark_rows_are_strict` | ✓ |
| Retroactive run against every existing plan surfaces zero dead pointers | live run | `make verify-evidence-pointers` on branch tip | ✓ |
| CAP-T057 registered + bidirectional traceability | verify-traceability | CAPABILITIES.md diff + Covers block | ✓ |
| Makefile target + `make check` wiring | lint | `grep verify-evidence-pointers Makefile` + `make check` output lists it | ✓ |
| `memory/workflow.md § Blocking gates` includes new row | docs | diff | ✓ |
| IDEAS.md: drop "audit-protocol upgrade" entry, bump old BC-019 → BC-020 | docs | diff | ✓ |
| Memory drift pattern added if applicable | N/A | no documented behavior deprecated | ✓ |

Legend: ✓ covered · ⚠️ partial · ❌ open (blocks shipping).

## Testing strategy

- **Red integration test** (`test_BC_019_verify_evidence_pointers.py`) encodes all 7 Gherkin scenarios using `tmp_path` + a fake pytest-collect-output stub. Shells out to the script via `subprocess.run([sys.executable, '-m', 'poc.scripts.verify_evidence_pointers', ...])` with env/arg overrides so the test can isolate from real-world plans.
- **Unit tests** cover the three parsers in isolation: pointer-cell extraction regex, node-id normalization, status-cell check. No subprocess; direct function calls.
- **Live retroactive run** executes against the real repo at the end — surfaces any dead pointers that still exist in shipped plans. (Expectation: zero after BC-018 close-gap.)

## Testing commands

```bash
source .venv/bin/activate
# Unit tests
pytest poc/tests/unit/test_verify_evidence_pointers.py -v --no-cov
# Red integration test (should go red before impl)
pytest poc/tests/integration/test_BC_019_verify_evidence_pointers.py -v --no-cov
# Live retroactive — proves no existing plan has a dead pointer
python3 -m poc.scripts.verify_evidence_pointers
# Full gate
make check
```
