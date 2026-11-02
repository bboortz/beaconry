# CI failures — learning log

Append-only record of every CI failure (or operator-caught
dependency-PR gotcha) + what we did so it never recurs. One
entry per failure. Keep entries short — the fixing BC's plan
doc holds the detail.

**Discipline**: when a BC fixes a CI failure, add an entry here in
the same commit. Not auto-enforced; it's muscle memory.

Format:

```
## YYYY-MM-DD — one-line symptom

- **Trigger**: commit / PR / run URL that surfaced the failure
- **Root cause**: the actual bug in one paragraph
- **Fix**: link to the BC that closed the gap
- **Regression test**: file:function that would re-catch this
- **Lesson**: one line to internalize
```

---

## 2026-11-02 — pytest false-pass under `BEACONRY_SKIP_VERSION_BUMP=1`

- **Trigger**: first real CI run on dev after shipping BC-069's
  pipeline — [run 41503095888](https://github.com/bboortz/beaconry/actions/runs/41503095888).
- **Root cause**: `.github/workflows/ci.yml` set
  `BEACONRY_SKIP_VERSION_BUMP: "1"` on the `make check` step with
  the intent "CI runs on branches, pre-push gate shouldn't fire".
  But `make check` doesn't actually run `verify-version-bumped`
  (it's a pre-push-only hook), so the env was unnecessary — AND
  the env leaked into every pytest subprocess, making
  `verify_version_bumped.check()` short-circuit to `[]`.
  `test_verify_fails_when_version_unchanged` + the backwards-bump
  test expected non-empty errors; got `[]`; false-fail.
- **Fix**: BC-071 — dropped the env from CI yaml; added
  `monkeypatch.delenv(..., raising=False)` to both tests for
  hermeticity. Local `BEACONRY_SKIP_VERSION_BUMP=1 pytest …`
  smoke confirms the regression guard.
- **Regression test**:
  `poc/tests/unit/test_bump_version.py::test_verify_fails_when_version_unchanged`
  (now hermetic via monkeypatch).
- **Lesson**: tests that call `check()` functions reading env must
  `monkeypatch.delenv()` the env at entry — don't trust CI/dev
  parent-process state.

---

## 2026-11-02 — Dependabot PR with transitive constraint trap

- **Trigger**: [Dependabot PR #1](https://github.com/bboortz/beaconry/pull/1)
  — `pytest 8.3.3 → 9.0.3`.
- **Root cause**: Dependabot sees one package at a time; it
  proposed the pytest bump without noticing `pytest-asyncio
  0.24.0` pins `pytest<9`. Merging would have failed
  `pip install -r requirements-dev.txt` on every host +
  broken CI.
- **Fix**: BC-072 — bumped both pins together in one commit
  (`pytest 8.3.3 → 9.0.3` + `pytest-asyncio 0.24.0 → 1.4.1.post1`).
  Closed PR #1 with analysis.
- **Regression test**: none code-side (test suite catches it
  automatically — `pip install` fails if the resolver can't
  find a solution; CI runs `make check-full` which exercises
  pytest-asyncio under the new pytest version).
- **Lesson**: never trust a Dependabot PR without
  `pip install --dry-run` on the full requirements files
  together. Transitive-constraint traps are invisible to
  Dependabot's per-package view.
