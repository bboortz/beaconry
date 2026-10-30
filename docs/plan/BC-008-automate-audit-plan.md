# fix/BC-008-automate-audit — Plan

*Branch: `fix/BC-008-automate-audit`*
*Start date: 2026-10-30*

---

## Situation

BC-008 shipped the pre-ship audit protocol (DD-TGD step 6a) as **Option A**: template-based, enforced by discipline + human review. The honest audit immediately after shipping found:

1. **No automated check that plan docs *fill* the Shipping evidence table.** If a developer leaves the template placeholder text in place, nothing fails. Option A's limitation.
2. **Meta: BC-008's own implementation didn't *demonstrate* the protocol.** The evidence table was filled cosmetically after green, not used as a mid-flight audit tool. Lens-3 (self-consistency) failure.

Promoting to **Option B** now while the pattern is fresh. Also running the real three-lens audit retroactively on BC-008 and recording any findings.

## Problems to solve

- Discipline-based enforcement failed us 7 times in a row (BC-002..BC-007). Expecting it to work for BC-008+ is wishful.
- Shipping-evidence tables with placeholder text pass silently.
- The protocol was not demonstrated by its own introducing story.

## Goals

1. `verify-traceability` learns to parse plan docs: find `## Shipping evidence` section, parse the table, flag rows with placeholder content or non-✓ status if the story is `shipped`
2. Fail criteria:
   - A `shipped` story's plan has a Shipping evidence section but the table is missing / empty / all placeholder
   - A `shipped` story's plan has rows with `⚠️` or `❌` status
3. Pass criteria:
   - Any `draft` or `in-progress` story — no check (table may still be filling)
   - A `shipped` story with every row ✓ and non-placeholder content
   - A `shipped` story whose plan predates BC-008 and has no Shipping evidence section (grandfathered)
4. Retroactive BC-008 real-audit recorded: findings in this plan's "Audit record" section
5. Regression tests covering the four gate behaviors

## Scope

- `poc/scripts/verify_traceability.py` — new `check_shipping_evidence(story_path, plan_path)` helper + wire into `run()`; only runs on stories with `status: shipped`
- `poc/tests/integration/test_BC_004_traceability.py` — new scenarios for the four gate behaviors
- `docs/CAPABILITIES.md` — CAP-T026 description refreshed; new CAP-T027 for the automated check
- `docs/plan/_template.md` — placeholder text made obviously detectable (fenced marker)
- This plan doc with its own filled Shipping evidence table

## Non-goals

- Parsing Gherkin to cross-match against the evidence table (over-engineering for now)
- Enforcing a specific pointer format
- Retroactively adding evidence tables to shipped BC-000..BC-007 plans (grandfathered)
- Blocking `draft` or `in-progress` stories

## Design decisions

**Grandfather by section presence.** If the plan doc has no `## Shipping evidence` section at all, assume it predates BC-008 and skip. If the section exists, enforce.

**Placeholder detection via a fenced sentinel.** Template rows will contain the exact string `<copy a Scenario` as a sentinel. Verify-traceability treats a row containing it as "unfilled placeholder" and errors. Non-placeholder rows must have a non-empty Pointer and a Status of `✓` for a `shipped` story.

**Only runs on `shipped` stories.** `draft` and `in-progress` can have ⚠️/❌/placeholder — that's exactly what they're supposed to show. Transition to `shipped` is the gate.

**Rejected: Gherkin parse-and-match.** Too much heuristic work; errors on edge cases. The simpler "every row filled, every status ✓" rule catches 90% at 10% of the cost.

## Breaking changes

- Any plan doc that was shipped under BC-008 Option A and has placeholder text will now fail the gate. Mitigation: BC-008's own plan doc was the only one shipped under Option A, and its table is already filled (all ✓). No live-shipped plans will regress.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Verify-traceability flags shipped-with-placeholder plans | integration test | `test_shipping_evidence_placeholder_fails_on_shipped` | ✓ |
| Verify-traceability flags shipped-with-non-✓-rows | integration test | `test_shipping_evidence_open_rows_fail_on_shipped` | ✓ |
| Shipped-with-filled-table passes | integration test | `test_shipping_evidence_filled_passes` | ✓ |
| Draft/in-progress stories allowed to have ⚠️/❌ | integration test | `test_shipping_evidence_not_enforced_on_draft` | ✓ |
| Grandfathered plans (no section) pass silently | integration test | `test_shipping_evidence_section_absent_is_grandfathered` | ✓ |
| CAP-T027 registered | verify-traceability | ran 2026-10-30, 38 CAPs clean | ✓ |
| BC-008's own real audit recorded | this plan "Audit record" section | above | ✓ |
| Dogfood: this fix branch has its own filled evidence table | this plan | above | ✓ |

### Audit record — BC-008 retroactive three-lens pass

Ran the three-lens protocol against BC-008 as if before marking it shipped:

**Lens 1 — Gherkin coverage:** All 6 Scenarios had tests. ✓ No finding.

**Lens 2 — Data-model symmetry:** Protocol introduces no persistent data model, only a doc convention. The "write side" is template filling by the plan-doc author; the "read side" is human review. **No save/load pair to mirror.** ✓

**Lens 3 — Self-consistency:**
- Did BC-008 fill its own Shipping evidence table? Yes, but *cosmetically* — rows were set to ✓ at the end, not used mid-flight as an audit tool. ⚠️ **Partial failure.** The protocol was documented but not demonstrated by its implementation flow.
- Did the template placeholder text get detected? No — nothing in BC-008 asserted that placeholder vs real content differed. **Gap.**
- Finding: both findings motivate this fix branch (BC-008-automate-audit) — hence their closure.

## Merge plan

1. Plan + implementation + tests on this branch
2. `make check` + `make check-full` green
3. Fill Shipping evidence rows above to ✓
4. Fast-forward merge to dev

## Rollout plan

Additive. Any new `shipped` story must have a filled evidence table or the gate fails. Grandfathered plans (BC-000..BC-007, which have no section at all) pass silently.

## Testing strategy

Four scenarios in `test_BC_004_traceability.py` using the existing `tmp_docs_and_tests` fixture; create synthetic plan docs with the four shapes and assert expected pass/fail.

## Testing commands

```bash
source .venv/bin/activate
make check
```
