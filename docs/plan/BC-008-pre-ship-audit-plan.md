# BC-008 — Pre-ship audit protocol

*Branch: `feat/BC-008-pre-ship-audit`*
*Start date: 2026-10-30*
*Story: `docs/stories/BC-008-pre-ship-audit.md`*

---

## Situation

Observed pattern across BC-002..BC-007: every shipped feature had a follow-up `fix/` branch triggered by a post-ship audit the user initiated. Analysis of the 11+ fix branches shows three recurring root causes:

1. **Gherkin item-by-item slip** — implementation covers 5 of 7 scenarios; shipped before the delta is noticed
2. **Data-model asymmetry** — save path updated, load path not (BC-007 tier)
3. **Self-hypocrisy** — the story introduces a convention; its own artifacts don't apply it (BC-004 CAP-list in own story, BC-005 same)

The pattern is predictable enough to codify as a gate step rather than audit-after-ship.

## Problems to solve

- The gap between "I claim shipped" and "it's actually ready" is where trust leaks
- Audit requires user prompt — not internalised
- Fix-branch load grows linearly per shipped story

## Goals

1. Plan template grows a `## Shipping evidence` section (4-column table)
2. CLAUDE.md DD-TGD process gets a new step 6a "Pre-ship audit" naming three lenses
3. memory/workflow.md DD-TGD block mirrors the CLAUDE.md change
4. BC-003 (the still-open draft) gets a preliminary Shipping evidence table
5. README names the audit protocol under Development process
6. CAP-B013 + CAP-T026 registered; test files reverse-linked

## Scope

- `docs/plan/_template.md` — add `## Shipping evidence` section with 4-col table (Scenario, Evidence type, Pointer, Status) + legend
- `CLAUDE.md` — DD-TGD step 6a inserted between `make check` and PR
- `memory/workflow.md` — matching step 6a + three-lens description
- `docs/stories/BC-003-live-verified-buttons.md` — preliminary Shipping evidence table (most rows `❌ awaits live tap session`)
- `README.md` — one line under Development process pointing to step 6a
- `docs/CAPABILITIES.md` — CAP-B013 (business trust), CAP-T026 (template section)
- `poc/tests/integration/test_BC_008_pre_ship_audit.py` — assert the template has the section + CLAUDE.md has step 6a wording + workflow memory has it + README mentions it + BC-003 has preliminary table
- This plan doc + its own Shipping evidence table (self-consistency: BC-008 ships only when BC-008's own evidence table is complete)

## Non-goals

- Automated parsing of Gherkin → evidence table (deferred to a possible BC-009 if Option A proves non-sticky)
- Enforcing table completeness via verify-traceability (same deferral)
- Retroactively filling evidence tables for BC-000..BC-007 (sunk; those shipped already with follow-up fixes)
- Changing what counts as "evidence" (any of: unit test, integration test, live-verified, manual-verified-on-date)
- A reviewer agent / second-model audit

## Design decisions

**Template-based (Option A), not automated enforcement.** Low cost; relies on discipline + user review. If audits still miss things in 2-3 BCs after this ships, promote to automation (Option B).

**Three lenses, not just Gherkin.** The audit asks three questions:
1. **Gherkin coverage** — every Scenario in the story has a pointer in the evidence table
2. **Data-model symmetry** — for every new write/save/update, is there a matching read/load/query? (vice versa)
3. **Self-consistency** — if the story introduces a convention (new template field, new CAP, new rule), do the story's own artifacts apply it?

**Evidence table columns:** Scenario, Evidence type, Pointer, Status.
- Scenario text or plan goal
- Evidence type: unit test / integration test / live-verified / manual
- Pointer: test name, log line, "verified 2026-10-30 session", README section
- Status: ✓ (covered) / ⚠️ (partial — notes required) / ❌ (open — blocks shipping)

**BC-003 gets a preliminary table.** It's draft, not shipped. The table makes concrete what's blocking the ready/shipped transitions (live tap sessions).

**Rejected: run audit at story time.** The 9 clarification questions already run then. Audit is specifically about what's shipped, so it belongs at ship time.

**Rejected: dedicated audit script.** Would need Gherkin parsing + fuzzy matching. Overkill for Option A.

## Breaking changes

None. Additive: the template grows a section; the DD-TGD process grows a step. Existing stories keep working.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Plan template has Shipping evidence section | integration test | `test_plan_template_has_shipping_evidence_section` | ✓ |
| CLAUDE.md documents step 6a | integration test | `test_claude_md_names_step_6a` | ✓ |
| memory/workflow.md matches | integration test | `test_workflow_memory_names_step_6a` | ✓ |
| BC-003 draft has preliminary table | integration test | `test_bc_003_has_shipping_evidence_table` | ✓ |
| README names audit protocol | integration test | `test_readme_mentions_pre_ship_audit` | ✓ |
| verify-traceability doesn't regress on new section | `make check` | ran 2026-10-30, 0 mismatches | ✓ |
| Three audit lenses named | integration test | assertions in `test_claude_md_names_step_6a` | ✓ |
| CAP-B013 + CAP-T026 registered + test reverse-linked | verify-traceability | 35 → 37 CAPs, 0 mismatches | ✓ |

**Data-model symmetry audit:** no new data model; the "write" side (filling the table) is operator discipline, the "read" side is human review. No save/load path to mirror. Pass.

**Self-consistency audit:** this story introduces the Shipping evidence table convention. The story's own plan doc has one (above). ✓

## Merge plan

1. Plan + story + red tests
2. Implement template + CLAUDE.md + workflow memory + README + BC-003 table + CAPs
3. Pre-ship audit on BC-008 itself — fill the Shipping evidence table rows to ✓
4. `make check-full` green
5. Fast-forward merge to dev

## Rollout plan

Additive. Next feat/fix branch picks up the new template section. BC-003 moves to ready when the preliminary table rows have real evidence.

## Testing strategy

Integration tests parse the artifacts (plan template, CLAUDE.md, workflow.md, README.md, BC-003 story) and assert the expected text is present. No new code logic; pure doc contract.

## Testing commands

```bash
source .venv/bin/activate
make check
make check-full
```
