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

---

## 2026-11-03 — beaconry alerted on itself (GO-2026-6604)

- **Trigger**: Telegram, hetzner-staging — Tier-1 alert against
  `beaconry-hub:latest`: `GO-2026-6604 · stdlib go1.22.11 → fixed
  in 1.26.9`.
- **Root cause**: `poc/hub/Dockerfile` had `ARG DOCKER_CLI_VERSION=27.5.1`
  pinned since BC-009. Docker 27.5.1 shipped with Go stdlib 1.22.11
  embedded; grype (correctly) matched it against GO-2026-6604 (a
  Windows-only path traversal in `os.Root.Mkdir`). Reachability on
  Linux + distroless: zero. But beaconry's own hub alerting the
  operator about its own tooling erodes trust in every real alert.
  Pinned-dep decay is silent: a pin that was clean at merge-time
  becomes vulnerable the day a new CVE lands against the embedded
  stdlib.
- **Fix**: BC-081 — bump `DOCKER_CLI_VERSION 27.5.1 → 29.9.2`
  (ships Go 1.26.10). Verified post-rebuild: grype no longer
  flags any Go stdlib finding against the hub image.
- **Regression test**: BC-082 CAP-T166 (`verify-pinned-versions`
  floor lint — asserts `DOCKER_CLI_VERSION ≥ 29.9.2`) + CAP-T167
  (`make dogfood-scan` — grype rescan of beaconry's own images,
  fails on High+ fixable).
- **Lesson**: pinned third-party binary deps need a dogfood
  grype gate; version pins drift silently as new CVEs land
  against old builds. Any "pin for reproducibility" has an
  implicit decay rate; the gate has to run after rebuild, not
  only at pin-time.

---

## 2026-11-03 — dogfood-grype first-run baseline: 10+ findings

- **Trigger**: BC-082's `make dogfood-scan` run against the
  shipped `beaconry-hub:latest` + `beaconry-scout:latest` images.
- **Root cause**: baseline `python:3.11-slim-bookworm` + distroless
  `python3-debian12:nonroot` shipped debian12 system libs that
  had accumulated High/Critical CVEs since those tags were
  pinned (BC-078). Specifically: `libssl3 3.0.19 → 3.0.22` (one
  Critical), `libc6 2.36-9+deb12u13 → u14` (3 High), `libexpat1
  2.5.0-1+deb12u2 → u5` (3 High), `perl-base`. Plus Python-side
  `starlette 0.38.6-0.41.3 → 1.1+` (3 High CVEs).
- **Fix**: BC-082 (same commit as the gate itself) — bumped
  builder to `python:3.13-slim-trixie`, runtime to
  `gcr.io/distroless/python3-debian13:nonroot`, and
  `fastapi 0.115 → 0.162` with an explicit `starlette==1.3.1`
  pin. All findings cleared.
- **Regression test**: the gate itself (CAP-T167) — now green
  against the current baseline, will fire again when any pinned
  dep decays.
- **Lesson**: the "all our pins are reviewed and clean" story
  decays silently at the OS-package level too, not only
  third-party binaries like docker CLI. A dogfood gate against
  shipped images is the only way to measure that decay. First
  run of a new gate is the honest baseline reading — any gate
  worth adding will find something, and that's the signal.
