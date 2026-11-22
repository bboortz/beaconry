# BC-147 — docs/ three-folder layout (specs / plan / stories)

*Branch: `feat/BC-147-docs-specs-layout`*
*Start date: 2026-11-21*
*Story: `docs/stories/BC-147-docs-specs-layout.md`*

---

## Situation

`docs/` grew organically. Today:

- Top-level `docs/*.md`: `CAPABILITIES.md`, `INSTALL.md`, `LIFECYCLE.md`, `METRICS.md`, `VARIABLES.md` — five specs sitting together with no label.
- Subdirs: `docs/plan/` (148), `docs/stories/` (129 + templates + INDEX), `docs/operations/` (1 runbook).
- `DESIGN.md` at repo root — the canonical architecture spec, sitting outside `docs/` for discoverability but inconsistent with the other specs.

Nine code files, the Makefile, `CLAUDE.md`, and two memory files reference the current paths.

---

## Problems to solve

- No place-to-put-the-next-spec convention. Operator has to guess.
- `DESIGN.md` at repo root is discoverable but inconsistent with the five spec siblings in `docs/`.
- Nothing lints "no new stray `.md` at `docs/` top-level" — drift is silent.

---

## Goals

1. Create `docs/specs/` with `_template.md` + `INDEX.md` mirroring the stories scaffold. *(Gherkin: Specs live under docs/specs/)*
2. Move `CAPABILITIES.md`, `LIFECYCLE.md`, `METRICS.md`, `VARIABLES.md`, `INSTALL.md`, `DESIGN.md` into `docs/specs/`. *(Gherkin: Specs live under docs/specs/, DESIGN.md moved from repo root)*
3. Update every in-repo reference (scripts, tests, Makefile, memory, CLAUDE.md, cross-links between moved docs). *(Gherkin: Every in-repo reference resolves)*
4. Add `verify-docs-layout` lint + wire into `make check`. *(Gherkin: Layout lint blocks regression, No stray specs at docs/ top-level)*
5. Update memory + CLAUDE.md to name the three folders. *(Gherkin: Memory + CLAUDE.md names the three folders)*

---

## Scope

**Files moved (with old → new):**
- `docs/CAPABILITIES.md` → `docs/specs/CAPABILITIES.md`
- `docs/LIFECYCLE.md` → `docs/specs/LIFECYCLE.md`
- `docs/METRICS.md` → `docs/specs/METRICS.md`
- `docs/VARIABLES.md` → `docs/specs/VARIABLES.md`
- `docs/INSTALL.md` → `docs/specs/INSTALL.md`
- `DESIGN.md` → `docs/specs/DESIGN.md`

**Files created:**
- `docs/specs/_template.md` — specification template
- `docs/specs/INDEX.md` — grep-friendly list of specs
- `poc/scripts/verify_docs_layout.py` — layout lint
- `poc/tests/integration/test_BC_147_docs_specs_layout.py` — red test

**Files updated (references):**
- `poc/scripts/verify_traceability.py`, `verify_feature_complete.py`, `verify_evidence_pointers.py`, `verify_pinned_versions.py`, `dogfood_sbom.py`
- `poc/tests/integration/test_BC_001_mocked_e2e.py`, `test_BC_002_real_pipeline.py`, `test_BC_004_traceability.py`, `test_BC_008_pre_ship_audit.py`
- `Makefile` — add `verify-docs-layout` target + wire into `check`; update any paths
- `CLAUDE.md` — repo layout section
- `memory/workflow.md`, `memory/code-quality.md` — updated to reference new layout
- Any `README.md`, `CHANGELOG.md`, or `docs/**/*.md` that cross-links to a moved file

---

## Non-goals

- Rewriting the content of any moved doc.
- Reclassifying plans or stories — those directories are untouched.
- Changing `docs/operations/` (operations runbooks are a distinct class).
- Changing `CAPABILITIES.md`'s internal CAP-NNN structure.

---

## Design decisions

- **`docs/operations/` stays separate** — runbooks are neither spec nor plan nor story; forcing them under `specs/` would blur a useful distinction.
- **`DESIGN.md` moves into `docs/specs/`** rather than staying at repo root — consistency with the five sibling specs wins over the small discoverability loss (README.md still points at it).
- **`INDEX.md` for specs** mirrors `docs/stories/INDEX.md` (one-line status board). Specs don't have a lifecycle enum like stories, so each INDEX row is `| ID | Title | Owner | Last updated |`.
- **Lint via `git ls-files docs/*.md`** (Python stdlib + `git` subprocess) — same cheap-and-fast shape as `verify_variables_documented.py`.
- **Rejected: flatten `docs/operations/` into `docs/specs/`.** Runbooks and specs serve different reader goals (do-this-when vs reference). Keep them separate; let the layout lint explicitly allow `docs/operations/`.
- **Rejected: keep `DESIGN.md` at repo root.** The small symmetry win outweighs a reader having to click one more folder.

---

## Breaking changes

### 1. API / return shape

**None.** No code emits paths like `docs/CAPABILITIES.md` as part of a public API. Scripts that *read* those paths are updated in-branch so the resolved file still loads.

### 2. Data model (schema + legacy rows)

**None.** No database schema changes. No serialized state contains a doc path.

### 3. Behavioral semantics

- **Operators or external readers who bookmarked `docs/CAPABILITIES.md` etc. get 404s** when following old links. Mitigation: GitHub preserves URLs for moved files within the same repo via its diff view, but hard links from external sites (if any) break. CHANGELOG entry documents the moves for anyone searching for the old path.
- **In-tree cross-links in other beaconry docs** (stories, plans referencing `docs/CAPABILITIES.md`) are updated in this branch — the Gherkin "Every in-repo reference resolves" scenario + its test enforce this.

No feature-flag or env-var opt-out needed — this is a layout change, not a behavior change.

---

## Merge plan

1. All work on `feat/BC-147-docs-specs-layout`
2. `make check` passes on branch tip (fast tier — lint + the new `verify-docs-layout` lint)
3. PR to `dev`; CI full tier re-runs
4. Merge strategy: squash
5. Post-merge: story status → `shipped`, `docs/stories/INDEX.md` updated

---

## Rollout plan

Operator action: none. The move is transparent to anyone running beaconry in production; it only affects where files live in the source tree.

Post-merge: any external bookmarks to `docs/CAPABILITIES.md` etc. need to flip to `docs/specs/CAPABILITIES.md`. CHANGELOG entry carries the mapping.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| docs/specs/ directory exists | integration | `test_BC_147_docs_specs_layout.py::test_specs_dir_exists` | ✓ |
| Specs live under docs/specs/ | integration | `test_BC_147_docs_specs_layout.py::test_all_six_specs_live_under_docs_specs` | ✓ |
| Spec template exists | integration | `test_BC_147_docs_specs_layout.py::test_specs_template_exists` | ✓ |
| Spec INDEX exists | integration | `test_BC_147_docs_specs_layout.py::test_specs_index_exists` | ✓ |
| INDEX lists every spec | integration | `test_BC_147_docs_specs_layout.py::test_specs_index_lists_every_spec` | ✓ |
| No stray specs at docs/ top-level | integration | `test_BC_147_docs_specs_layout.py::test_no_stray_md_at_docs_toplevel` | ✓ |
| DESIGN.md moved from repo root | integration | `test_BC_147_docs_specs_layout.py::test_design_md_not_at_repo_root` | ✓ |
| Every in-repo reference resolves | integration | `test_BC_147_docs_specs_layout.py::test_every_in_repo_reference_resolves` | ✓ |
| Layout lint script present | integration | `test_BC_147_docs_specs_layout.py::test_verify_docs_layout_script_present` | ✓ |
| Layout lint wired into make check | integration | `test_BC_147_docs_specs_layout.py::test_verify_docs_layout_wired_into_make_check` | ✓ |
| Layout lint blocks regression | integration | `test_BC_147_docs_specs_layout.py::test_verify_docs_layout_catches_stray_toplevel_md` | ✓ |
| Layout lint passes on live repo | integration | `test_BC_147_docs_specs_layout.py::test_verify_docs_layout_passes_on_live_repo` | ✓ |
| Layout lint handles missing docs/ | integration | `test_BC_147_docs_specs_layout.py::test_verify_docs_layout_handles_missing_docs` | ✓ |
| Layout lint flags unexpected subdir | integration | `test_BC_147_docs_specs_layout.py::test_verify_docs_layout_flags_unexpected_subdir` | ✓ |
| Layout lint flags missing specs scaffold | integration | `test_BC_147_docs_specs_layout.py::test_verify_docs_layout_flags_missing_specs_scaffold` | ✓ |
| Layout lint flags DESIGN.md at root | integration | `test_BC_147_docs_specs_layout.py::test_verify_docs_layout_flags_design_at_repo_root` | ✓ |
| Lint CLI main prints and returns 0 | integration | `test_BC_147_docs_specs_layout.py::test_verify_docs_layout_main_prints_and_returns` | ✓ |
| Lint CLI main returns 1 on violation | integration | `test_BC_147_docs_specs_layout.py::test_verify_docs_layout_main_returns_1_on_violation` | ✓ |
| Memory + CLAUDE.md names the three folders | integration | `test_BC_147_docs_specs_layout.py::test_claude_md_and_memory_name_the_three_folders` | ✓ |
| CAP-D001 registered in CAPABILITIES.md | integration | `test_BC_147_docs_specs_layout.py::test_cap_d001_registered` | ✓ |
| Each moved spec is readable (CAPABILITIES) | integration | `test_BC_147_docs_specs_layout.py::test_each_moved_spec_readable[CAPABILITIES.md]` | ✓ |
| Each moved spec is readable (LIFECYCLE) | integration | `test_BC_147_docs_specs_layout.py::test_each_moved_spec_readable[LIFECYCLE.md]` | ✓ |
| Each moved spec is readable (METRICS) | integration | `test_BC_147_docs_specs_layout.py::test_each_moved_spec_readable[METRICS.md]` | ✓ |
| Each moved spec is readable (VARIABLES) | integration | `test_BC_147_docs_specs_layout.py::test_each_moved_spec_readable[VARIABLES.md]` | ✓ |
| Each moved spec is readable (INSTALL) | integration | `test_BC_147_docs_specs_layout.py::test_each_moved_spec_readable[INSTALL.md]` | ✓ |
| Each moved spec is readable (DESIGN) | integration | `test_BC_147_docs_specs_layout.py::test_each_moved_spec_readable[DESIGN.md]` | ✓ |
| Memory drift pattern (N/A — no documented behavior deprecated) | TOML | N/A | ✓ |

Legend: ✓ covered · ⚠️ partial · ❌ open.

---

## Testing strategy

One integration test file (`poc/tests/integration/test_BC_147_docs_specs_layout.py`) encodes every Gherkin scenario. Tests are pure filesystem assertions — no subprocess, no network — so they run in <1 s.

Reference-resolution test walks every tracked `.md`, `.py`, and `Makefile` line and extracts path-looking tokens that match the moved docs; each must resolve to an extant file post-move.

---

## Testing commands

```bash
make check
pytest poc/tests/integration/test_BC_147_docs_specs_layout.py -v
```

Expected:

```
8 passed in 0.5s
✓ Fast tier (make check) passed
```
