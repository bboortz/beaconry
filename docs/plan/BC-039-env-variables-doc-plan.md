# BC-039 — Document every env variable + drift lint

*Branch: `feat/BC-039-env-variables-doc`*
*Start date: 2026-10-31*
*Story: `docs/stories/BC-039-env-variables-doc.md`*

---

## Situation

~30 env variables shipped between BC-030 and BC-038. No single source of truth — authoritative defaults live in `poc/hub/hub.py` and `poc/scout/scout.py`, operator-facing docs partially in `poc/.env.example` and `poc/docker-compose.yml`, rationale scattered across `memory/*.md`. Developer cannot audit the configuration surface without greping four files; operators discover variables by accident during support conversations.

---

## Problems to solve

- No single document enumerates every variable.
- No lint catches a code-ships-a-new-variable PR that forgets to document it.
- No lint catches dead entries left behind when a variable is removed.
- `.env.example` + `docker-compose.yml` already drift from code (BC-011 catches env propagation to the container; this story catches "does the operator know the variable exists at all").

---

## Goals

1. `docs/VARIABLES.md` is the authoritative, grouped, grep-friendly index.
2. `poc/scripts/verify_variables_documented.py` scans Python under `poc/` for `os.environ.get("...")` + `os.environ["..."]`, cross-refs with `VARIABLES.md`, emits actionable violations.
3. `make verify-variables` runs the scanner; fails fast-tier `make check` on drift.
4. Explicit test-only allowlist in the scanner for variables that only exist in `poc/tests/` (e.g. `BEACONRY_FOO`).
5. New variables added in future BCs land with a VARIABLES.md row OR fail the lint — same shift-left pattern as BC-011 + BC-016.

---

## Scope

- NEW `docs/VARIABLES.md` — grouped by concern, one row per variable.
- NEW `poc/scripts/verify_variables_documented.py` — scanner + CLI + exit code.
- NEW `poc/tests/unit/test_verify_variables_documented.py` — unit coverage of the scanner's parse/diff/report helpers.
- `Makefile` — new `verify-variables` target; add to `check` pipeline; help entry.
- `docs/CAPABILITIES.md` — register CAP-T084, CAP-T085, CAP-B023.
- `docs/plan/IDEAS.md` — append three-way-sync followup idea.

---

## Non-goals

- Three-way sync (VARIABLES.md ↔ .env.example ↔ docker-compose.yml). This story is code→doc. Followup in IDEAS.
- Documenting system variables Python reads via libraries we don't control (e.g. `requests` reading `HTTP_PROXY`).
- Compile-time doc generation from annotations. Hand-maintained with lint is simpler + PR-reviewable.
- Operator-facing explanation beyond one-line purpose. `memory/*.md` + BC origin link is enough; VARIABLES.md is an index, not a manual.

---

## Design decisions

- **One-row-per-variable with structured columns**: Markdown table parse is trivial, maintainers can scan quickly.
- **Scanner is regex + file walk**: no AST — the project doesn't have obscure `os.environ.get(key_from_other_module)` patterns. If one lands, the scanner's unmatched-call warning makes it visible in CI.
- **Explicit test-only allowlist in the scanner source**: visible, auditable, blocks accidental additions. Reject: auto-detect "test" in path — allows real test-only vars but also hides misplaced prod reads.
- **Groups match operator mental model** (Telegram → Scanning → Fix execution → Reconcile → Heartbeat → Digest → Dev-mode), not code module. Operator opens VARIABLES.md asking "how do I tune digests?" and finds the Digest section.
- **Rejected: generate .env.example from VARIABLES.md.** Operators edit .env.example in setup; coupling the two would make the "docs" doubles as runtime config, which hurts both. Keep them aligned via lint, not generation.

---

## Breaking changes

### 1. API / return shape

**None.** New Make target + new verifier; no change to hub, scout, scripts, or test contracts.

### 2. Data model

**None.** No DB column, no table.

### 3. Behavioral semantics

- Developers who do nothing see a new fast-tier gate. The gate fails only if a variable is undocumented → fix is adding a row to `VARIABLES.md`.
- The copy-pasteable template emitted on violation makes the fix a one-line edit.
- No runtime effect on hub/scout/notification pipeline.

---

## Merge plan

1. All work on `feat/BC-039-env-variables-doc`.
2. `make check` + `make check-full` pass.
3. PR to `dev`; Claude opens, user runs `/ultrareview`, Claude runs `/code-review`.
4. Merge strategy: fast-forward.
5. Post-merge: story → `shipped`, INDEX update.

---

## Rollout plan

Additive. Developers run `make check` as usual; if their branch adds a new `os.environ.get(...)` without a `VARIABLES.md` row, the lint fails with the fix in the error message. CI picks up the gate for free.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Clean state: shipped repo passes the lint | integration | `test_verify_variables_documented.py::test_shipped_repo_passes` | ✓ |
| Undocumented var in code → lint fails with template | unit | `test_verify_variables_documented.py::test_undocumented_var_fails_with_template` | ✓ |
| Orphan entry in doc → lint fails with removal hint | unit | `test_verify_variables_documented.py::test_orphan_doc_entry_fails` | ✓ |
| Test-only allowlist is honoured | unit | `test_verify_variables_documented.py::test_test_only_allowlist_suppresses_violation` | ✓ |
| Parser extracts `os.environ.get(...)` + `os.environ[...]` forms | unit | `test_verify_variables_documented.py::test_scan_file_captures_both_forms` | ✓ |
| Parser extracts `VARIABLES.md` entries from the Markdown table | unit | `test_verify_variables_documented.py::test_parse_variables_md_returns_names` | ✓ |
| `make verify-variables` wired into `make check` | multi-gate | `make check` output includes the step | ✓ |
| `make help` lists the new target | lint | `make verify-docs-drift` | ✓ |
| CAP-T084 + CAP-T085 + CAP-B023 registered + bidirectional | verify-traceability | `docs/CAPABILITIES.md` diff | ✓ |
| Memory drift pattern added if applicable | N/A | no documented behavior deprecated | ✓ |

Legend: ✓ covered · ⚠️ partial · ❌ open (blocks shipping).

---

## Testing strategy

Unit-only — scanner is pure (file walk + regex + set diff). Lint output format tested via `capsys`. Live repo verified by the integration test running the scanner against actual `poc/` and `docs/VARIABLES.md`.

---

## Testing commands

```bash
make check
make verify-variables       # run the lint in isolation
python -m poc.scripts.verify_variables_documented    # same, CLI form
```
