---
id: BC-098
title: Dockerfiles detect Python minor at build time (retire BC-097's risk)
status: in-progress
created: 2026-11-08
branch: feat/BC-098-python-version-detect
plan: docs/plan/BC-098-python-version-detect-plan.md
---

# BC-098: Stage-at-runtime-path — Dockerfiles ask the interpreter for its minor, no Python version literal remains

## Narrative

BC-083 moved beaconry's runtime to `ghcr.io/bboortz/python-scratch-trixie`
and BC-097 bumped both runtime + builder tags to `:latest`. The runtime
shipped Python 3.13.18 — matching the builder exactly — so every
`COPY --from=builder /install/lib/python3.13/site-packages …`
and `… /usr/local/lib/python3.13/site-packages` in both Dockerfiles
worked by coincidence of a hardcoded path matching a frozen minor
version. BC-097's own risk note predicted the failure mode:

> *"Risk: a future forge bump to Python 3.14 would break the
> hardcoded `COPY /install/lib/python3.13/...` paths below — cold-start
> smoke (BC-082) catches this on the next rebuild."*

Forge **FG-032** (PR `bboortz/forge#60`, open, targeting forge's `dev`)
bumps `python-scratch-trixie` + `forge-builder-trixie` in-place to
Python 3.14. Forge's release pipeline is explicitly waiting on this
beaconry companion PR before promoting the bump to forge's `main`
and letting the daily cron republish the GHCR tag.

BC-098 removes every Python version literal from the Dockerfiles:
not `3.13`, not `3.14`, not an `ARG PY_VERSION=…`. The interpreter
reports its own `sys.version_info.major.minor`; the builder stage
reads that value, moves `site-packages` into an overlay path that
mirrors the runtime root (`/stage/usr/local/lib/python${PY_VER}/site-packages`),
and the runtime stage does a single `COPY --from=builder /stage /`.
Works for 3.13, 3.14, 3.15, and every future minor without a
beaconry PR.

## Trigger

Not event-driven. Follow-on to forge FG-032 + closure of BC-097's
documented risk. Blocks forge's release pipeline per FG-032's story.

## Expected outcome

- `poc/{hub,scout}/Dockerfile` contain **zero** `python3.X` literals
  (no `3.13`, no `3.14`, no `ARG PY_VERSION=…`).
- Builder stage, after the existing `syft dir:/install` step, runs:
  ```
  RUN PY_VER="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')" \
   && mkdir -p "/stage/usr/local/lib/python${PY_VER}" \
   && mv "/install/lib/python${PY_VER}/site-packages" \
         "/stage/usr/local/lib/python${PY_VER}/site-packages"
  ```
- Runtime stage replaces the existing site-packages COPY with:
  ```
  COPY --from=builder /stage /
  ```
- Hub's prior standalone `COPY --from=builder --chown=65532:65532
  /stage/data /data` line is dropped — `COPY /stage /` subsumes it
  (ownership preserved from the builder-stage chown).
- `test_hub_can_import_all_runtime_deps` + `test_scout_can_import_all_runtime_deps`
  (BC-083, part of `make check-full`) both pass — every top-level
  requirements.txt entry imports cleanly inside the cold-start
  container.
- BC-097's CAP-T199 description updated past-tense; new CAP-T200
  registered for the version-agnostic pattern itself.

## Out of scope

- **Rolling back to `:3.13`.** Forge freezes `:3.13` on GHCR post-FG-032
  (daily cron stops rebuilding it); re-pinning would freeze beaconry
  on an unpatched Python minor. Explicitly declined.
- **Switching to `pip install --target + PYTHONPATH`.** Also valid
  but changes sys.path convention (prepended env var vs. the native
  `/usr/local/lib/python${VER}/site-packages` entry Python builds in).
  Out of scope — stage-at-runtime-path keeps conventional sys.path.
- **Baking a `HEALTHCHECK` into the Dockerfiles.** Still a compose-level
  concern per BC-097.
- **Any grype / syft / docker CLI version changes.**
- **Updating other Python literals** (sys.path hints in prose, the
  `python3.13` mention in the hub runtime stage header comment —
  refreshed as drive-by prose cleanup, not behavior change).

## Acceptance criteria (Gherkin)

```gherkin
Feature: Beaconry Dockerfiles are Python-minor-agnostic

  Scenario: No Python version literal in either Dockerfile
    Given a checkout of beaconry on this branch
    When a reader greps poc/{hub,scout}/Dockerfile for the pattern `python3\.\d+`
    Then no COPY, mkdir, or ARG line matches
     And comments may still mention historical versions (prose only)

  Scenario: Builder stage detects the Python minor at build
    When a reader greps either Dockerfile for the PY_VER shell step
    Then exactly one match exists per file
     And it uses `sys.version_info.major.minor` via `python3 -c`
     And it moves `/install/lib/python${PY_VER}/site-packages` into `/stage`

  Scenario: Runtime stage copies /stage recursively
    When a reader greps either Dockerfile for the runtime site-packages COPY
    Then the only COPY carrying site-packages is `COPY --from=builder /stage /`
     And no `COPY --from=builder /install/lib/python3.X/...` remains

  Scenario: Cold-start imports every runtime dep
    When `make check-full` runs the BC-082 cold-start stack
    Then `test_hub_can_import_all_runtime_deps` passes
     And `test_scout_can_import_all_runtime_deps` passes
     And both exit 0 with `ok` on stdout

  Scenario: verify_pinned_versions still passes
    When `make verify-pinned-versions` runs
    Then no violations are returned
     And the lint continues to accept `:latest` + `:3.13` runtime tags

  Scenario: SBOM still captures Python wheels
    When the built hub image's `/sbom.json` is scanned
    Then `fastapi` + other declared deps appear as artifacts
     And `grype sbom:/sbom.json` returns the same shape as pre-BC-098
```

## Capabilities delivered

- **CAP-T200: Python-minor-agnostic site-packages placement** (type:
  technical, status: active). Builder stage queries
  `sys.version_info.major.minor` at build time, stages site-packages
  under `/stage/usr/local/lib/python${VER}/`, runtime does
  `COPY --from=builder /stage /`. Zero Python version literals in
  either Dockerfile. Retires BC-097's documented risk.

Reference existing CAPs:
- Retires the "future forge bump would break" risk documented under
  **CAP-T199** (BC-097). CAP-T199 description updated past-tense to
  reference BC-098 as the successor.
- Supports CAP-T171 (`make verify-base-images` picks up tag drift) and
  CAP-B023 (operator trust in beaconry's own supply chain).
- Depends on forge FG-032 (image.python-3.14-bump) being promotable
  — by design, they ship as a paired companion.

## Links

- Forge FG-032 (origin): `~/git/forge/docs/stories/FG-032-python-3.14-bump.md`
- Forge PR #60: `https://github.com/bboortz/forge/pull/60`
- BC-097 (predecessor — documented the exact risk this story retires)
- BC-083 (`test_{hub,scout}_can_import_all_runtime_deps` — primary oracle)
- BC-082 (cold-start smoke — end-to-end live gate)
