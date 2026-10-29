# BC-004 — Capabilities documentation + bidirectional test traceability

*Branch: `feat/BC-004-capabilities-traceability`*
*Start date: 2026-10-29*
*Story: `docs/stories/BC-004-capabilities-traceability.md`*

---

## Situation

Three stories shipped (BC-000, BC-001, BC-002). The test suite has 49 tests across 8 files. When a test fails, there's no mechanism to translate `test_make_id_differs_for_different_hosts` into "operator stops being able to dedup findings across runs." BC-002's audit also showed how easy it is to miss gaps: the Gherkin had scenarios that unit tests covered, but the operator-facing capability ("I can tap a button and it works") had zero live verification yet the story shipped.

BC-004 lays a traceability layer: a single `docs/CAPABILITIES.md` listing every system guarantee, bidirectionally linked to the tests that prove it. A `make verify-traceability` script enforces consistency as part of `make check`.

---

## Problems to solve

- A failing test gives no immediate signal about operator-visible impact
- A new developer can't quickly answer "what already exists?"
- A story can ship with incomplete verification (BC-002 taught this)
- No single source for "what beaconry guarantees" — spread across stories, design, memory, tests

---

## Goals

1. `docs/CAPABILITIES.md` exists with ~5 business + ~15 technical capabilities seeded from BC-000/001/002
2. Each capability has a stable `CAP-NNN` ID, `type: business | technical`, description, origin story, design-doc link, `verified by` test list, status
3. The file groups capabilities visually by type (business section first per iter-1 feedback), while using a flat shared ID space
4. Every existing test module has a top-of-file `Covers:` docstring listing CAP ids with human names
5. `poc/scripts/verify_traceability.py` parses both directions and errors on any mismatch
6. `make check` runs the script; mismatch fails the gate
7. CLAUDE.md and memory/workflow.md updated: DD-TGD gets step 5a; 8-question list becomes 9
8. Error messages from the script are copy-pasteable templates (new devs can fix in one edit)

---

## Scope

- `docs/CAPABILITIES.md` — the matrix, seeded with ~20 capabilities
- `poc/scripts/__init__.py` + `poc/scripts/verify_traceability.py` — the validator
- `Makefile` — add `verify-traceability` target; chain it into `check`
- `pyproject.toml` — ensure `poc/scripts` is on pyright's include list
- Every file under `poc/tests/**/test_*.py` — add `Covers:` docstring
- `CLAUDE.md` — DD-TGD gets step 5a (register capabilities)
- `memory/workflow.md` — step 5a + 9th clarification question
- `docs/stories/_8-questions.md` — add the 9th question
- `docs/stories/_template.md` — add a "## Capabilities delivered" section
- Red test: `poc/tests/integration/test_BC_004_traceability.py` — exercises verify-traceability across happy path + orphan + dangling CAP + missing test file + unverified CAP

---

## Non-goals

- Function-level (`test_<name>`) linking — file-level `Covers:` is enough for BC-004
- Linking capabilities to source modules (just tests)
- Capability versioning / lifecycle beyond `active | aspirational | deprecated`
- Backfilling the `Capabilities delivered` section into BC-000/001/002 story files (not worth the churn; INDEX + CAPABILITIES.md provides the forward link)
- Code-generated CAPABILITIES.md
- A `fix` autoformatter for the script (`verify` only; manual fixes required)

---

## Design decisions

**Flat `CAP-NNN` IDs over tiered `CAP-B###` / `CAP-T###` prefixes.** Keeps renames cheap: if a technical cap gets promoted to a business cap (or vice versa), the ID doesn't change. Type is a field, not an ID encoding. Section grouping in the markdown delivers the readability the key user asked for.

**File-level `Covers:` instead of per-function.** Test functions often share setup and verify adjacent slices of the same capability. File-level keeps the matrix sparse and legible. If a single file ends up covering disparate capabilities, that's a signal to split the file — a side benefit.

**Script lives at `poc/scripts/verify_traceability.py`** so it's inside the Python project (importable, type-checked, test-able) rather than a loose bash script in `scripts/`. Testing the script itself matters because broken enforcement means silent rot.

**Orphan detection is loud by default.** Any new test file without `Covers:` fails the gate. The error message includes a one-line template:

```
poc/tests/unit/test_new_thing.py: missing Covers block.
Add at top of file:
    """<module description>

    Covers:
        - CAP-NNN: <human name>
    """
```

**Rejected: automated capability discovery via ast analysis.** Tempting (parse function names, infer capabilities) but creates false positives and dilutes the signal. Explicit > implicit here.

**Rejected: pytest marker-based linking (`@pytest.mark.cap("CAP-001")`).** Would require importing a plugin, obscures the mapping, and doesn't carry human names. Docstring is readable to greppers.

---

## Breaking changes

- `make check` now runs `verify-traceability`. Any existing branch that doesn't update CAPABILITIES.md or test headers after this merges will fail CI. **Mitigation:** this story adds the headers to every existing test as part of its own merge; follow-up branches start with a clean slate.
- 8 → 9 clarification questions. Any in-progress story (BC-003 draft) picks up the 9th when it moves to `ready`.

---

## Merge plan

1. All work on `feat/BC-004-capabilities-traceability`
2. **Red first**: integration test (poc/tests/integration/test_BC_004_traceability.py) covers all Gherkin scenarios of the verify script using tmp_path fixtures that construct synthetic cases
3. **Green**: write verify_traceability.py until red integration test passes
4. **Seed**: write CAPABILITIES.md with ~20 capabilities; reverse-link every existing test module
5. **Wire**: Makefile change (verify-traceability runs inside check)
6. **Workflow docs**: CLAUDE.md + memory/workflow.md + 8-questions and template files updated
7. `make check` passes locally (every gate including verify-traceability)
8. User reviews branch diff — focus on capability taxonomy
9. Flip story → `shipped`, update INDEX
10. Fast-forward merge to `dev`
11. Delete branch

---

## Rollout plan

Additive — no runtime behavior changes. Developers after merge need to be aware that `make check` has one more gate; the error messages are self-documenting so no README update strictly required (but a one-line in CLAUDE.md step 5a suffices).

---

## Testing strategy

**Integration test** (`poc/tests/integration/test_BC_004_traceability.py`):
- Uses `tmp_path` to build small synthetic test files + a synthetic CAPABILITIES.md
- Invokes `verify_traceability.run(docs_path, tests_path)` as a function (not subprocess) so coverage counts
- Scenarios: happy path, orphan test, dangling CAP id, missing test file in CAP, unverified active CAP, deprecated/aspirational CAPs are skipped from unverified check

**Unit tests** (optional for BC-004 — one combined integration test is sufficient given the pure-function nature of the parsers):
- Parser for test file Covers blocks (missing, present, multiple caps, malformed)
- Parser for CAPABILITIES.md structure (YAML-like header validation)

**Covers header for the integration test itself:**

```python
"""BC-004 traceability enforcement — integration-tier.

Covers:
    - CAP-T016: verify-traceability enforces bidirectional links
    - CAP-T017: Orphan tests fail the gate
"""
```

---

## Testing commands

```bash
source .venv/bin/activate

# Full gate (includes verify-traceability)
make check

# Just the verify
make verify-traceability

# Just the BC-004 integration test
pytest poc/tests/integration/test_BC_004_traceability.py -v
```

Expected: `make check` exits 0; `make verify-traceability` exits 0 and prints a one-line "✓ N capabilities, M tests, 0 mismatches" summary.
