# BC-015 — Memory drift regression guard

*Branch: `feat/BC-015-memory-drift-guard`*
*Start date: 2026-10-30*
*Story: `docs/stories/BC-015-memory-drift-guard.md`*

---

## Situation

BC-013 shipped scout's HTTP migration but left `memory/health-checks.md` + `memory/docker-standard.md` referring to scout as using file-sentinels (post-ship fix: `909ee87`). BC-014 shipped same-track pull but left `memory/design-decisions.md` referring to `docker pull :latest` in three places (post-ship fix: `38439bf`). Both drifts caught by the self-consistency audit lens AFTER shipping. The pattern repeats because:

- CAP descriptions have a verification tie-point ("verified by: test_X.py") that forces attention when shipping
- `memory/*.md` files are untethered — no automated signal that content has drifted from the code
- Human discipline alone catches these only in post-ship audit

Two drifts in two consecutive stories is a trend. BC-015 adds a growing banned-pattern gate so these specific regressions can't recur, and the gate forces authors to think about memory impact at ship time.

---

## Problems to solve

- Past drifts can silently return as different authors revisit `memory/*.md`
- Future drifts of the same class (shipped behavior documented wrong) aren't caught until post-ship audit
- No pre-ship checklist item forces authors to ask "did I change a documented behavior?"

---

## Goals

1. New script `poc/scripts/verify_memory_drift.py` with a TOML-driven banned-pattern list
2. Seed with 2 entries from BC-013 + BC-014 (the exact drifts we closed)
3. Each entry: pattern (regex), banned_since (BC id), reason, optional exception_regex list
4. `make verify-memory-drift` hooked into `make check` fast tier
5. Pre-ship audit checklist (step 6a / plan template) grows a row: "memory drift pattern added if applicable"
6. Prove the gate works by planted-regression Phase 4 regression check
7. 1 CAP registered

---

## Scope

- `poc/scripts/verify_memory_drift.py` — new (~60 LOC)
  - Load `poc/scripts/memory_drift_bans.toml`
  - For each entry: compile pattern, iterate `memory/*.md`, check matches, apply `exception_regex` list
  - Report format: `memory/<file>:<line>: matched BC-NNN drift pattern '<pattern>': <reason>`
  - Exit 0 clean, 1 on any match
- `poc/scripts/memory_drift_bans.toml` — new
  - Entry 1 (BC-013): scout described as file-sentinel-primary (specific phrasings to ban)
  - Entry 2 (BC-014): DockerAdapter pulls `:latest` (specific phrasings to ban)
- `Makefile`:
  - New `verify-memory-drift` target
  - Added as a dep of `check`
- `poc/tests/unit/test_memory_drift_guard.py` — new
  - Loader tests, matcher tests, exception handling, failure messaging
  - Fixture-based test that plants a drift and asserts the exact failure line
- `docs/plan/_template.md` — Shipping evidence section gets a note that "if this story deprecates documented behavior, a memory_drift_bans.toml entry is required"
- `memory/workflow.md § Blocking gates`:
  - Add `verify-memory-drift` to the fast-tier table
- `docs/CAPABILITIES.md` — CAP-T047 registered

---

## Non-goals

- Code-identifier staleness checks (noisy; parked)
- Section-level ownership mapping (high maintenance; parked)
- Auto-fixing drifts — intent is to force the author to update manually
- `README.md`, `DESIGN.md`, `docs/` scope — only `memory/*.md` for V1
- Removing a drift once added (patterns are append-only; if a pattern becomes wrong, the fix is to narrow it, not remove it)
- Multi-pattern per entry (one pattern per entry keeps error messages targeted)

---

## Design decisions

**TOML, not Python.** Rejected: register bans as Python module. Reason: TOML is editable without Python knowledge, keeps the growing list a plain data file, and future contributors (including future-me) can add entries without touching code.

**Append-only, not "fix the pattern if it's wrong".** Rejected: let authors remove patterns they consider stale. Reason: patterns are historical facts — they document *that this drift happened once*. Future drifts might re-trigger an entry; keeping the entry prevents regression. Narrowing via exception_regex is the right escape hatch, not removal.

**Regex, not substring.** Rejected: exact-substring match. Reason: wording often varies (`:latest`, `":latest"`, `the :latest tag`). Regex lets each entry capture the semantic drift without being brittle to wording. Downside: patterns need author discipline to not over-match.

**Explicit exception_regex, not inferred from context.** Rejected: let the script infer "this is historical" from surrounding words like "pre-BC-NNN". Reason: inference is fragile; explicit exceptions make the exception auditable.

**Scope only `memory/*.md`, not all docs.** Rejected: expand to `docs/`, `README.md`. Reason: `README.md` + `docs/` are user-facing and already get reviewed on every PR for correctness. `memory/*.md` is Claude's design canon — read first, not reviewed as rigorously by humans. That's where drift silently lives.

**Hook into `make check` fast tier**, not `make check-full`. Rejected: full tier only. Reason: the check is cheap (~ms), zero flake surface, and the whole point is pre-push protection.

---

## Breaking changes

- `make check` gets a new sub-check. Any contributor whose branch happens to introduce one of the two seeded drift patterns will fail the gate. That's the intent, but it means someone updating `memory/*.md` for a different reason might trip the gate accidentally — they'll need to use the exception_regex escape hatch or revise their wording.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Clean memory passes | unit test | `test_memory_drift_guard.py::test_clean_memory_passes` | ✓ |
| Planted BC-014 drift caught with named error | unit test | `test_memory_drift_guard.py::test_bc014_drift_is_caught` | ✓ |
| Planted BC-013 drift caught | unit test | `test_memory_drift_guard.py::test_bc013_drift_is_caught` | ✓ |
| Exception_regex excepts a legitimate historical mention | unit test | `test_memory_drift_guard.py::test_exception_regex_passes` | ✓ |
| TOML loader rejects malformed entries | unit test | `test_memory_drift_guard.py::test_malformed_toml_rejected` | ✓ |
| Script hooked into `make check` fast tier | integration test | `make check` output includes `verify-memory-drift` passing | ✓ |
| Regression proof: real BC-014 drift (via git revert) → script catches it | live manual | 2026-10-30 — reverted BC-014 memory close via `git show 38439bf -- memory/design-decisions.md \| git apply --reverse` → script reported 2 BC-014 violations with file:line, pattern, reason, hint. Restored → script reported "3 patterns, 7 files, 0 drifts". Live-proven. | ✓ |
| Plan template gets memory-drift Shipping evidence row | self-consistency | `docs/plan/_template.md` diff | ✓ |
| memory/workflow.md gate table updated | self-consistency | `memory/workflow.md` diff | ✓ |
| CAP-T047 registered | verify-traceability | `make check` traceability green | ✓ |
| Memory drift pattern added if this story deprecates documented behavior | N/A — self-consistency declaration | N/A: BC-015 adds a new gate; it does not deprecate any documented behavior that would need banning. Satisfies the template row that BC-015 itself introduced. | ✓ |

Legend: ✓ · ⚠️ · ❌.

---

## Testing strategy

Unit (fast tier):
- TOML loader round-trip; malformed input raises named error
- Match tests with hand-crafted memory fixtures (tmp_path)
- Exception_regex applies correctly
- Error message format includes `banned_since` + `reason` + file:line

Integration (fast tier, via `make check`):
- Script runs on real `memory/*.md` and passes clean (post-BC-014 state)

Live (Phase 4):
- `git revert 38439bf` → run script → assert non-zero exit + BC-014 pattern name in output
- `git revert --abort` or re-apply → script passes clean

---

## Testing commands

```bash
source .venv/bin/activate
make check
pytest poc/tests/unit/test_memory_drift_guard.py -v --no-cov

# Phase 4 planted-regression proof:
git show 38439bf | git apply --reverse   # undo BC-014 memory close
make verify-memory-drift                 # expect: non-zero exit, BC-014 named
git show 38439bf | git apply             # restore
make verify-memory-drift                 # expect: clean
```
