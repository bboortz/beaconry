# BC-NNN — <title>

*Branch: `feat/BC-NNN-<slug>`*
*Start date: YYYY-MM-DD*
*Story: `docs/stories/BC-NNN-<slug>.md`*

---

## Situation

Current state. What exists, what's missing or broken.

---

## Problems to solve

- Problem 1
- Problem 2

---

## Goals

1. Goal 1 (map 1:1 to acceptance scenarios in the story)
2. Goal 2

---

## Scope

Files, directories, modules changed. Be specific — list every path.

---

## Non-goals

What this branch explicitly does NOT do.

---

## Design decisions

Key choices and why. Rejected alternatives in one line each.

---

## Breaking changes

API, config, CLI, or data-model changes that would break existing deployments. "None" is a valid answer and should be stated.

---

## Merge plan

1. All work on `feat/BC-NNN-<slug>`
2. `make check` must pass on branch tip
3. PR to `dev`; Claude opens, user runs `/ultrareview`, Claude runs `/code-review`
4. Merge strategy (ff, squash, merge-commit — default: squash)
5. Post-merge: update story status → `shipped`, update `docs/stories/INDEX.md`

---

## Rollout plan

Migration or operator action required by this change. "None — change is additive" is a valid answer.

---

## Shipping evidence

Pre-ship audit (DD-TGD step 6a). This story may only move to `shipped` when every row below has non-empty evidence. The three audit lenses behind this table: **Gherkin coverage** (every Scenario has a pointer), **data-model symmetry** (every write path has a mirror read, and vice versa), **self-consistency** (if the story introduces a convention — new template field, new CAP, new rule — the story's own artifacts apply it).

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| <copy a Scenario title or plan goal> | unit / integration / live / manual | <test name, log line, "verified YYYY-MM-DD"> | ❌ |
| Memory drift pattern added if this story deprecates documented behavior | TOML diff / N/A | `poc/scripts/memory_drift_bans.toml` entry OR "N/A — no documented behavior deprecated" | ❌ |

Legend: ✓ covered · ⚠️ partial (notes required) · ❌ open (blocks shipping).

---

## Testing strategy

How correctness is verified: which integration test encodes the Gherkin, which unit tests cover helpers.

---

## Testing commands

```bash
# Exact commands to verify the branch works
make check
pytest poc/tests/integration/test_BC_NNN_<slug>.py -v
```

Expected output:

```
...
```
