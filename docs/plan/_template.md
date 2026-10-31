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

Enumerate under all three dimensions. "None" per dimension is a valid answer and MUST state WHY (not "nothing changed" — "existing users of X see the same behavior because Y").

### 1. API / return shape

What callers (scout, Telegram client, external tooling, tests) see a different interface shape? For each: do they tolerate extra keys, or do they strict-match?

### 2. Data model (schema + legacy rows)

For every added column, removed column, or changed status/enum value: **what happens to rows that existed before this change?** State for each:
- Default value for legacy rows (0, "", NULL, etc.)
- Does that default render correctly in operator-facing output (notifications, `make findings`, audit log, dashboards)?
- If NO → migration or backfill goes in this branch, not a close-gap. Specify exact SQL / Python / migration step.

Example done-right (BC-031 after close-gap): "Added `last_fixed_ts` column. Legacy `fixed`/`fixed_verified` rows default to 0 → would render 'regressed since unknown' in notification body. **Migration**: in `init_db()`, after `ALTER TABLE ADD COLUMN last_fixed_ts`, `UPDATE findings SET last_fixed_ts = found_at WHERE status IN ('fixed','fixed_verified') AND last_fixed_ts = 0`. Approximates "fix time" from "find time" (upper-bound; the fix happened at or after find)."

### 3. Behavioral semantics

Does a function change its implicit meaning? Could an operator who depended on the old behavior get:
- Noisier notifications (e.g. BC-031's regression alerts for operators who thought "fixed" was final)?
- Quieter notifications (e.g. a filter now suppresses something that used to surface)?
- Different-shaped output (e.g. new column in findings CLI)?

For each: who are the operators affected, and what's the config knob (env var, feature flag) to opt out of the new behavior if needed?

**If any breaking change is discovered during implementation or testing** that was not identified at planning time: fix in-branch BEFORE merging, do NOT ship as a close-gap. See [[breaking-changes-in-planning]] memory. The planning dimension that missed it also gets reinforced for future stories.

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
