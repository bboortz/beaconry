# BC-000 — Repo + development process scaffold

*Branch: `feat/BC-000-repo-scaffold`*
*Start date: 2026-10-29*
*Story: `docs/stories/BC-000-repo-scaffold.md`*

---

## Situation

The beaconry repo has `git init` run but zero commits. Design documents (`DESIGN.md`, `memory/*`), the POC code (`poc/scout/`, `poc/hub/`), and docs scaffolding are all untracked. We need a bootstrapped repo with `main` + `dev` branches, the DD-TGD development process baked into CLAUDE.md + `memory/workflow.md`, story templates, and the Python tooling (`Makefile`, `pyproject.toml`, pre-commit hooks) that `make check` will run as the authoritative local gate.

---

## Problems to solve

- No commit history → no branch policy to enforce yet
- Decisions about dev process (gates, testing pyramid, stories, PR flow) exist only in session transcripts; need to land in version-controlled files
- No `Makefile` → no uniform way to run gates locally
- No pre-commit framework → no fast feedback on lint/format/type violations
- No `poc/tests/` layout → cannot write the first red integration test

---

## Goals

1. `main` branch exists with a minimal initial commit (`.gitignore` + stub README)
2. `dev` branched from `main`
3. `feat/BC-000-repo-scaffold` branched from `dev`, lands all scaffold work
4. `make check` is the single local gate command and runs: `ruff check`, `ruff format --check`, `pyright`, `pytest`
5. Pre-commit framework configured: ruff + pyright on commit, pytest on push
6. Stories infrastructure in place: `docs/stories/{INDEX,_template,_8-questions}.md` + `BC-000-repo-scaffold.md`
7. Plan template augmented with Story link + Breaking changes + Merge plan + Rollout plan sections
8. Branch merged into `dev`

---

## Scope

- Create `.gitignore`, `Makefile`, `pyproject.toml`, `requirements-dev.txt`, `.pre-commit-config.yaml` at repo root
- Create `docs/stories/` directory and seed files (INDEX, _template, _8-questions, BC-000)
- Create `docs/plan/BC-000-repo-scaffold-plan.md` (this document)
- Create `poc/tests/{unit,integration,e2e,fixtures}/` with `__init__.py` stubs and a top-level `conftest.py`
- Add a placeholder `poc/tests/unit/test_sanity.py` so pytest has something to run (and the coverage gate has a baseline)
- Add `demo.sh --smoke` **stub** that prints "smoke mode not yet implemented" and exits 0 — real implementation deferred to BC-001
- Update `memory/code-quality.md` and `CLAUDE.md` to point PR target at `dev`, add `chore/` branch type, reference the DD-TGD process in `workflow.md`
- Update plan template (`docs/plan/_template.md`) with Story / Breaking changes / Merge plan / Rollout plan sections

---

## Non-goals

- Writing BC-001 story or any actual POC tests (that's BC-001)
- Making the existing POC pass `make check` end-to-end if it has preexisting lint/type violations — initial scaffold fixes only; separate `fix/<slug>` can clean up later
- GitHub Actions / CI pipeline (deferred until remote exists)
- Real `demo.sh --smoke` implementation
- Rewriting the already-locked `memory/design-decisions.md`

---

## Design decisions

**Makefile at repo root, not in `poc/`.** The project is currently just the POC; separating `make` entry points per subdirectory would duplicate work. Root Makefile shells into `poc/` for pytest paths.

**pyproject.toml at root with `testpaths = ["poc/tests"]`.** Means `pytest` from anywhere works. Same for ruff and pyright config — one source of truth.

**Pre-commit framework over hand-rolled hook.** Installable with one command (`pre-commit install`), reproducible across machines, zero-friction updates via `pre-commit autoupdate`. Scope on commit = fast (ruff + pyright on staged); scope on push = pytest. Slow things (coverage, docker build, smoke) are CI-only.

**`demo.sh --smoke` stub, not real.** Separating the smoke-mode implementation from the scaffold keeps BC-000 focused on process, not pipeline behaviour. Real smoke mode is a BC-001 responsibility (that's where it provides actual value — guarding the demo).

**`main` holds only initial scaffold commit; actual work lives on `dev` via feat branches.** Matches the "main is CI-promoted" policy. Until CI exists, operator discipline keeps main minimal.

---

## Breaking changes

None. This is the initial scaffold; nothing to break.

---

## Merge plan

1. All scaffold files land on `feat/BC-000-repo-scaffold`
2. Branch must satisfy `make check` locally — any gate failure blocks merge
3. User reviews branch diff (no `/ultrareview` on this bootstrap branch since there's no remote yet; `/code-review` runs locally)
4. Fast-forward merge `feat/BC-000-repo-scaffold` → `dev`
5. Delete `feat/BC-000-repo-scaffold`
6. Update `docs/stories/BC-000-repo-scaffold.md` frontmatter status → `shipped`
7. Update `docs/stories/INDEX.md` with new status

---

## Rollout plan

None required — scaffold is additive. No existing users, no data to migrate, no services to restart. First-time setup for any clone:

```bash
git clone <repo>
cd beaconry
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pre-commit install && pre-commit install --hook-type pre-push
make check
```

---

## Testing strategy

Scaffold is tested by itself — "`make check` passes on the branch tip" is the acceptance test for most criteria. For the pre-commit / pre-push scenarios in the Gherkin, manual verification via a disposable test commit on a throwaway branch satisfies them (no automated self-test of git hooks).

A single sanity unit test (`poc/tests/unit/test_sanity.py`) exists so pytest collects something and the coverage gate has a baseline. It will be replaced by real tests in BC-001.

---

## Testing commands

```bash
# From repo root, with venv active
make check

# Expected: lint OK, format OK, type OK, pytest 1 passed, coverage reported.

# Verify pre-commit hook
echo "x=1  " > /tmp/bad.py && cp /tmp/bad.py poc/scratch_badstyle.py
git add poc/scratch_badstyle.py
git commit -m "chore: test hook"      # should FAIL due to trailing whitespace / unused import
rm poc/scratch_badstyle.py && git reset HEAD poc/scratch_badstyle.py 2>/dev/null || true
```

Expected: `make check` exits 0; pre-commit blocks the bad-style commit with ruff output visible.
