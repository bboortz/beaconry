---
id: BC-015
title: Memory drift regression guard — static-pattern gate
status: shipped
created: 2026-10-30
branch: feat/BC-015-memory-drift-guard
plan: docs/plan/BC-015-memory-drift-guard-plan.md
---

# BC-015: Memory drift regression guard

## Narrative

BC-013 shipped scout's HTTP migration but left `memory/health-checks.md` and `memory/docker-standard.md` describing scout as using file-sentinels — caught in post-ship audit, closed as a follow-up docs commit. BC-014 shipped same-track pull but left `memory/design-decisions.md` describing `docker pull :latest` in three places — same drift pattern, same post-ship catch, same follow-up docs commit. Two in a row is a trend, not a coincidence. BC-015 adds `make verify-memory-drift` to the fast gate: a growing list of banned regex patterns (seeded with the BC-013 and BC-014 drifts) prevents any future PR from re-introducing a stale pre-change claim into `memory/*.md`. Future drifts get captured as new banned patterns at ship time — the list is deliberately append-only.

## Trigger

Developer-facing: after shipping BC-013 and BC-014, both had `memory/*.md` referring to pre-change behavior that the self-consistency audit lens caught manually. Operator-facing: `memory/*.md` files are the design canon Claude + humans read first; stale claims there mislead decisions.

## Urgency

N/A — meta/process work.

## Expected outcome

- New script `poc/scripts/verify_memory_drift.py` reads `poc/scripts/memory_drift_bans.toml` and scans every `memory/*.md` for banned regex patterns
- Each ban entry carries: `pattern` (regex), `banned_since` (BC id), `reason` (one-liner), optional `exception_regex` list for legitimate historical mentions
- Script exits 0 clean, non-zero with named-entry errors on any match
- Seed `memory_drift_bans.toml` with two entries: BC-013 (scout-as-file-sentinel claims) + BC-014 (`:latest`-wording in DockerAdapter semantics)
- Hooked into `make check` via a new `verify-memory-drift` target
- Process rule in `memory/workflow.md`: when shipping a story that deprecates a documented behavior, add a banned pattern to `memory_drift_bans.toml` in the same commit — becomes part of the pre-ship audit checklist (step 6a)
- The script proves it works by catching a planted regression (we temporarily revert the BC-014 memory close, run the script, assert the exact error line, restore)
- 1 technical CAP registered

## Out of scope

- Code-identifier staleness (grep memory for function names, check against the codebase) — noisy, parked
- `memory/*.md` section-level ownership mapping (which code file "owns" which memory section) — manually curated, high maintenance; parked
- AI-driven drift detection — overkill for a growing-regex-list problem
- Auto-fixing drifts — intent is to force the author to update the memory file manually, not silently rewrite documentation
- Non-memory docs (README, DESIGN.md, docs/) — covered by other reviews; memory files are the specific class we've seen drift on

## Acceptance criteria (Gherkin)

```gherkin
Feature: Memory drift regression guard

  Scenario: Clean memory files pass the gate
    Given memory/*.md matches no banned pattern
    When `make verify-memory-drift` runs
    Then exit code is 0
    And stdout confirms "N patterns checked, 0 drifts found"

  Scenario: Regression against a shipped BC gets caught
    Given a BC-014 drift is planted: memory/design-decisions.md re-adds "DockerAdapter — docker pull :latest"
    When `make verify-memory-drift` runs
    Then exit code is non-zero
    And stderr names the file + line + the specific `banned_since` BC id + the reason
    And stderr includes a copy-pasteable hint

  Scenario: Legitimate historical mention of ":latest" passes via exception_regex
    Given memory/health-checks.md contains "pre-BC-014 behavior pulled `:latest`" (clearly historical)
    When `make verify-memory-drift` runs
    Then the match is excepted and the gate passes
    And the exception is explicitly declared in memory_drift_bans.toml, not implicit

  Scenario: New drift pattern added at ship time via the pre-ship audit checklist
    Given a developer deprecates documented behavior X in BC-NNN
    When they fill the Shipping evidence table
    Then there is a required row "memory drift pattern added for X" that must be ✓
    And the row references the entry added to memory_drift_bans.toml

  Scenario: Script is self-testing against committed drift bans
    Given the committed memory_drift_bans.toml with entries from BC-013 and BC-014
    When Phase 4 regression proof runs: git revert the BC-014 memory close, run the script
    Then the script exits non-zero and names the BC-014 entry
    When the memory file is restored
    Then the script exits 0
```

## Capabilities delivered

- **CAP-T047: `make verify-memory-drift` catches regressions of shipped documentation-reality changes** (technical, active) — new. Growing banned-pattern list seeded with BC-013 + BC-014 drifts; hooked into `make check`; pre-ship audit checklist (step 6a) requires an entry when a story deprecates documented behavior.

Updates: CAP-T026 (plan template's Shipping evidence) gets a new default row for "memory drift pattern added if applicable", CAP-T027 (verify-traceability) stays unchanged.

## Open questions

- (none — resolved by the "two drifts is a trend" observation)

## Links

- Triggered by: BC-013 memory drift close (commit `909ee87`), BC-014 memory drift close (commit `38439bf`)
- Related: CAP-B013 (shipping discipline), CAP-T027 (traceability gate), BC-008 (pre-ship audit protocol)
