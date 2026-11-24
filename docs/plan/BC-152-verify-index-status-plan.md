# BC-152 — SH-056 verify-index-status (beaconry side)

*Branch: `feat/SH-056-verify-index-status`*
*Start date: 2026-11-24*
*Story: `docs/stories/BC-152-verify-index-status.md`*
*Shepherd story: [SH-056](https://github.com/bboortz/shepherd/blob/main/docs/stories/SH-056-verify-index-status.md) — PR #59*

---

## Situation

Shepherd SH-056 was motivated by PR #55 shipping with 6 stories still
showing `draft` / `in-progress` in `docs/stories/INDEX.md` despite
being shipped. `verify-traceability` reads frontmatter but never looks
at the INDEX row, so the drift class slipped the existing gate.
Shepherd authored a one-file shell check (`scripts/verify-index-status.sh`)
and wired it into `check-docs`.

Beaconry's situation was worse: a first run of the shepherd script
against this repo flagged 20+ mismatches (including 15 stories whose
code was merged to `dev` months ago but whose frontmatter still said
`in-progress` or `draft`). The INDEX column order also did not match
shepherd's — beaconry had `ID | Title | Status | Branch` while
shepherd's script reads status from pipe-field 3 (`ID | Status | …`).

---

## Problems to solve

- No cross-check between `docs/stories/INDEX.md` and story frontmatter.
- INDEX column order diverges from shepherd's; the fleet script would
  silently parse the Title column as Status.
- 20+ accumulated frontmatter-status drifts (stories merged but never
  flipped from `draft` / `in-progress` to `shipped`).

---

## Goals

1. Ship `scripts/verify-index-status.sh` byte-identical to shepherd's
   reference.
2. Add `verify-index-status` as a `.PHONY` target invoking the script.
3. Wire `verify-index-status` into `check-docs` prereqs (inherits
   `PUSH_STEPS` + `BRANCH_STEPS` via BC-151).
4. Add the help: block echo line for BC-016 compliance.
5. Sync `docs/stories/INDEX.md` — reorder columns to `ID | Status | Title | Branch`,
   dedupe BC-055, add missing rows (BC-147..BC-152).
6. Sync frontmatter — flip the 15 shipped-but-draft/in-progress stories
   to `shipped`.
7. Author the test (`test_BC_152_verify_index_status.py`): file
   existence + executable bit + byte-identity to shepherd reference +
   Makefile shape + live pass + live drift-trip.

---

## Scope

- `scripts/verify-index-status.sh` — new, byte-identical to
  `/home/benni/git/shepherd/scripts/verify-index-status.sh`.
- `Makefile`:
  - `.PHONY`: add `verify-index-status`.
  - `help:` block: add one echo line (BC-016).
  - New target body `verify-index-status: ; @scripts/verify-index-status.sh`.
  - `check-docs:` prereqs: append `verify-index-status`.
- `docs/stories/INDEX.md`:
  - Column order reshaped to `ID | Status | Title | Branch`.
  - Dedupe BC-055 (two rows in prior version).
  - Add missing rows: BC-147, BC-148, BC-149, BC-150, BC-151, BC-152.
  - Flip stale rows to match frontmatter reality.
- `docs/stories/BC-088..BC-095, BC-117, BC-119, BC-129..BC-132, BC-140,
  BC-139, BC-141..BC-144` frontmatter: `status: draft|in-progress → shipped`
  (reflects already-merged code on `dev`).
- `docs/stories/BC-152-verify-index-status.md` — this story.
- `docs/plan/BC-152-verify-index-status-plan.md` — this plan.
- `docs/plan/SH-056-verify-index-status-plan.md` — pointer doc
  (branch-slug traceability, same precedent as SH-055 pointer).
- `docs/specs/CAPABILITIES.md` — new `CAP-S010` row.
- `poc/tests/integration/test_BC_152_verify_index_status.py` — 8 asserts.
- `VERSION` / `poc/VERSION` / `pyproject.toml` — sync bump (BC-062).
- `CHANGELOG.md` — new version entry.

---

## Non-goals

- Changing the drift-check algorithm. The script is a byte-identical
  copy; any logic change happens in shepherd first.
- Auto-generating INDEX.md from frontmatter. Deferred — the split
  encodes intent (branch, long/short title form) that frontmatter
  doesn't carry today.
- Enforcing an INDEX row for `draft` stories. The shepherd script only
  trips on shipped+ stories missing from the INDEX.

---

## Design decisions

- **Byte-identical to shepherd.** Fleet invariant from SH-056: the
  drift-check lives on every repo; the logic is authored once. Any
  divergence is a bug. Test asserts `sha256` match.
- **INDEX column reorder is prerequisite work, not a side-channel.**
  The shepherd script assumes `ID | Status | …`. Reordering is the
  only way to adopt the fleet script without forking it.
- **Backfill shipped frontmatter in the same PR.** The drift was
  accumulated over months; leaving it out would leave the gate failing
  on day one. The backfill is mechanical and reversible (`git log`
  proves each story shipped).
- **Script is `sh` not `bash`.** Matches shepherd's choice (portable;
  no bash-isms). Confirmed via `set -eu` + POSIX `awk` / `grep -E`.

### Alternatives rejected

| Alternative | Why not |
|---|---|
| Fork the script to parse `ID \| Title \| Status \| …` column order | Breaks the fleet-invariant byte-identity. SH-056 explicitly ships to every repo. |
| Add a column-order config knob to the shepherd script | Overkill for one row of difference. The reorder is a one-time cost; the shepherd layout is also more readable (status before title). |
| Skip the frontmatter backfill, leave the gate failing | Not shippable — the pre-push hook would block every push. |
| Author a beaconry-native drift script | Duplicate logic, future shepherd improvements don't propagate. |

---

## Breaking changes

### 1. API / return shape

None. `verify-index-status` is a new target. `make` invocations are
additive.

### 2. Data model (schema + legacy rows)

None — no DB changes. `docs/stories/INDEX.md` column reshape is
cosmetic and already matches the shepherd convention.

### 3. Behavioral semantics

- **Pre-push slightly slower.** One extra `sh` invocation (< 100 ms
  measured on this tree) runs on `hook-push` via `check-docs`.
- **New failure class surfaced.** Stories whose INDEX row disagrees
  with frontmatter now block push. This is the intended effect.

---

## Merge plan

1. All work on `feat/SH-056-verify-index-status` (branch slug matches
   shepherd story ID; BC-152 is beaconry's tracking ID, same
   convention as `feat/SH-055-check-docs` ↔ BC-151).
2. `make verify-index-status` passes on branch tip (verified pre-commit).
3. `make hook-push` passes end-to-end (verified pre-commit).
4. PR to `dev`; admin-merge after CI green (user granted authority).
5. Merge strategy: merge commit (fleet default; preserves PR boundary).
6. Post-merge: story status already `shipped` on first commit.

---

## Rollout plan

None — change is additive once the one-time INDEX reshape + frontmatter
backfill lands. Developers running `make verify-index-status` on an
old clone see "no rule to make target" until they pull.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Script exists + executable | integration | `test_BC_152_verify_index_status.py::test_script_exists_and_executable` | ✓ |
| Script byte-identical to shepherd reference | integration | `test_BC_152_verify_index_status.py::test_script_byte_identical_to_shepherd_reference` | ✓ |
| `.PHONY: verify-index-status` declared | integration | `test_BC_152_verify_index_status.py::test_verify_index_status_declared_phony` | ✓ |
| `verify-index-status:` target invokes the script | integration | `test_BC_152_verify_index_status.py::test_verify_index_status_target_exists` | ✓ |
| `check-docs` prereqs include `verify-index-status` | integration | `test_BC_152_verify_index_status.py::test_check_docs_includes_verify_index_status` | ✓ |
| Help: block echoes `make verify-index-status` (BC-016) | integration | `test_BC_152_verify_index_status.py::test_help_echoes_verify_index_status` | ✓ |
| Live pass on clean tree (rc=0) | integration | `test_BC_152_verify_index_status.py::test_live_pass_on_clean_tree` | ✓ |
| Live drift trips the gate with specific diagnostic (rc=1) | integration | `test_BC_152_verify_index_status.py::test_live_drift_detection` | ✓ |
| INDEX.md column order matches shepherd (`ID \| Status \| …`) | manual | `docs/stories/INDEX.md` header row | ✓ |
| Memory drift pattern — N/A; purely additive gate | — | — | ✓ |

Legend: ✓ covered · ⚠️ partial (notes required) · ❌ open (blocks shipping).

---

## Testing strategy

- Static Makefile-shape via regex (same shape as BC-151).
- Byte-identity to shepherd reference via sha256 (skips if the sibling
  checkout isn't present — keeps CI green when shepherd isn't mounted).
- Live pass: invoke the script, expect rc=0 on current tree.
- Live drift: patch BC-000 row to `draft`, invoke script, expect rc=1
  with named diagnostic, restore in `finally`.

---

## Testing commands

```bash
.venv/bin/pytest poc/tests/integration/test_BC_152_verify_index_status.py --no-cov -v
make verify-index-status
make check-docs
```

Expected:

- 8 tests pass
- `make verify-index-status` → rc=0, "ok (INDEX.md matches frontmatter for all stories)"
- `make check-docs` → rc=0, all seven delegates green
