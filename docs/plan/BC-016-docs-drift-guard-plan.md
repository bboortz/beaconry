# BC-016 — Docs drift guard

*Branch: `feat/BC-016-docs-drift-guard`*
*Start date: 2026-10-30*
*Story: `docs/stories/BC-016-docs-drift-guard.md`*

---

## Situation

`make help` is drifting silently. Two concrete instances this session alone:

1. **Targets going missing** (fixed in `94d1a45` by eyeball during BC-015 close-gap): `make status`, `make verify-dockerfile-health-copy`, `make verify-memory-drift` all shipped but didn't appear in `make help` until a manual pass caught them.
2. **Enum values going stale** (just surfaced): `make help` lists `STATUS=open|snoozed|accepted|fixed` but BC-011 extended `STATUS_ORDER` to six values; `BEACONRY_FINDINGS_LOCAL=1` (BC-007 close-gap) isn't documented either.

README has the same drift risk — same fragility, same operator-first visibility, same lack of automation.

---

## Problems to solve

- `make help` enumerations go stale when code adds new values
- `make help` targets vanish when code adds new .PHONY entries
- README references obsolete env vars that no code reads
- No gate catches any of these until a human reads the doc or an operator hits an undocumented path

---

## Goals

1. New `poc/scripts/verify_docs_drift.py` scans `Makefile`'s `help:` block + `README.md`, cross-checks three enumeration classes against code
2. Hooked into `make check` fast tier
3. Current drifts (STATUS enum + `BEACONRY_FINDINGS_LOCAL` doc miss) fixed as regression proof — lint turns the gate red before, green after
4. Live-proven by planted-drift Phase 4 (same shape as BC-015's close-gap)
5. CAP-T053 registered

---

## Scope

- `poc/scripts/verify_docs_drift.py` — new (~150 LOC):
  - Parse `Makefile` for `help:` block → extract `make <target>` strings + `STATUS=foo|bar|...` enumerations + `BEACONRY_<NAME>` references
  - Parse `Makefile` for `.PHONY: ...` list
  - Parse `README.md` for the same three classes
  - Parse `poc/scripts/findings.py` for `STATUS_ORDER = (...)` tuple
  - Parse `poc/**.py` for `os.environ.get("BEACONRY_...")` reads
  - Cross-check and emit violations with file:line + copy-pasteable hint
- `Makefile` — new `verify-docs-drift` target + added to `check` deps
- `poc/tests/unit/test_docs_drift_guard.py` — new, 10+ tests covering:
  - Parsers for Makefile help, .PHONY, STATUS_ORDER, env reads
  - Each of the three enumeration classes catches a planted drift
  - Bidirectional check for .PHONY ↔ help
  - Real-repo invariant: `make verify-docs-drift` passes on the shipped tree post-fix
- `Makefile` `help:` block — fix the two known drifts (full STATUS list + `BEACONRY_FINDINGS_LOCAL` mention)
- `docs/CAPABILITIES.md` — CAP-T053 registered

---

## Non-goals

- Prose grammar/style
- Descriptions matching semantics (only membership)
- Enforcing an ordering
- Scanning `docs/` tree (reviewed via other channels; this story targets the two surfaces that have drifted)
- Checking `memory/` (BC-015 owns that)
- Auto-fixing drift — operator must update docs manually (intentional discipline)

---

## Design decisions

**Scan `Makefile` + `README.md` only.** Rejected: scan all `*.md`. Reason: docs/ files are per-story and churn with stories; `README.md` + `Makefile` are the operator-first surfaces that need to stay stable.

**Regex extraction, not full Markdown/Makefile parsing.** Rejected: proper parsers. Reason: the patterns we care about (`make <target>`, `STATUS=foo|bar|...`, `BEACONRY_<NAME>`) are stable enough to regex reliably; a parser would be overkill.

**Bidirectional check only on make targets.** Rejected: bidirectional on everything. Reason: STATUS_ORDER code → docs is useful (new value should be documented); but every `.beaconry-cache/beaconry.db` or `/data/beaconry.db` path isn't a documented enum. Make targets have a clean .PHONY list; other classes don't have a comparable "complete set" declaration.

**Fix the drifts in this same branch, not a separate close-gap.** Rejected: lint first, drift fix later. Reason: shipping a lint that would fail on dev would block every subsequent merge. Fix + lint in one atomic change.

---

## Breaking changes

None. Operator-facing behavior unchanged. The lint is a pre-merge discipline.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Clean docs pass the gate | unit test | `test_docs_drift_guard.py::test_clean_docs_pass` | ✓ |
| Stray STATUS enum value caught | unit test | `test_docs_drift_guard.py::test_stray_status_value_caught` | ✓ |
| Missing make target caught | unit test | `test_docs_drift_guard.py::test_missing_make_target_caught` | ✓ |
| Undocumented .PHONY entry caught | unit test | `test_docs_drift_guard.py::test_undocumented_phony_target_caught` | ✓ |
| Bogus BEACONRY_* env var caught | unit test | `test_docs_drift_guard.py::test_bogus_env_var_caught` | ✓ |
| Real-repo state passes post-fix | unit test | `test_docs_drift_guard.py::test_shipped_repo_passes_the_current_lint` | ✓ |
| make help STATUS enum updated with all 6 values | Makefile diff | help block | ✓ |
| make help documents BEACONRY_FINDINGS_LOCAL override | Makefile diff | help block | ✓ |
| CAP-T053 registered | verify-traceability | make check green | ✓ |
| Live: before-fix lint fails on BC-011 STATUS drift | planted-drift proof | 2026-10-30 — ran against unfixed Makefile, lint caught 5 drifts (3 undocumented .PHONY targets, 1 incomplete-status-enum missing fixed_verified+verification_failed, 1 README false positive since fixed with backtick-only matching). After fixing help text, lint goes clean. | ✓ |
| Memory drift pattern added if applicable | N/A | N/A — this story is itself a drift guard; nothing to ban | ✓ |

---

## Testing commands

```bash
source .venv/bin/activate
make check
pytest poc/tests/unit/test_docs_drift_guard.py -v --no-cov
# Phase 4 regression proof:
git stash   # if any Makefile fix is uncommitted
make verify-docs-drift   # expect: fails on BC-011 STATUS drift + BEACONRY_FINDINGS_LOCAL miss
git stash pop
make verify-docs-drift   # expect: clean
```
