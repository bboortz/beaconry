---
id: BC-100
title: telegram_mock joins the forge-signed scratch base + ABI guard + HEALTHCHECK family
status: shipped
created: 2026-11-08
branch: feat/BC-100-telegram-mock-forge-base
plan: docs/plan/BC-100-telegram-mock-forge-base-plan.md
---

# BC-100: Last beaconry-authored container moves off `python:3.12-slim` onto the forge chain

## Narrative

Beaconry ships four Dockerfiles. Three — `hub`, `scout`, and (via
forge CI) `beaconry-telegram-mock` on the forge side — have migrated
onto forge-signed, daily-rebuilt, cosign-verifiable, scratch-runtime
images (BC-083 → BC-095 → BC-097 → BC-098 → BC-099). One is still
on upstream Docker Hub:

```dockerfile
# poc/telegram_mock/Dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY telegram_mock/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY telegram_mock/mock_bot.py .
COPY beaconry_health/ ./beaconry_health/
EXPOSE 8080
CMD ["python3", "-u", "-m", "uvicorn", "mock_bot:app", "--host", "0.0.0.0", "--port", "8080", "--log-level", "warning"]
```

Consequences of being the odd one out:

1. **Supply-chain asymmetry**: hub + scout images are cosign-verified
   against forge's OIDC identity; telegram-mock isn't. Operators
   auditing the beaconry stack see one unsigned chain.
2. **Unpatched minor**: Python 3.12 upstream vs forge's current 3.14
   on the other containers — telegram-mock lags whenever forge bumps,
   requiring a manual beaconry-side bump.
3. **No ABI guard**: BC-099's `RUN --mount=from=runtime-src` pattern
   doesn't exist here, so any future forge-base-image mismatch would
   go undetected until the probe image crashes at runtime. Telegram-
   mock isn't production, but the same class of regression wastes
   CI time.
4. **No baked HEALTHCHECK**: `docker inspect beaconry-test-telegram-mock
   --format '{{.Config.Healthcheck.Test}}'` returns an empty value
   today — same `no-healthcheck` state that broke homelab smoke on
   the hub side before BC-099.
5. **Fatter image**: `python:3.12-slim` is ~120 MB; `python-scratch-trixie`
   is ~44 MB. The gap × every test run × CI cache × every operator
   pull = a non-zero cost.

BC-100 applies the exact BC-083 → BC-099 recipe to telegram-mock:
multi-stage with forge-builder-trixie + python-scratch-trixie,
BuildKit syntax directive, named `runtime-src` stage, ABI guard,
`/stage` layout, Python-minor detection, HEALTHCHECK baked in.

mock_bot.py already uses `beaconry_health.build_router` (BC-012) so
`/readyz` is live at port 8080 — the HEALTHCHECK invocation is
identical shape to hub/scout, just different port.

## Trigger

Not event-driven. Houskeeping follow-on to BC-099. Closes the last
upstream-base image in beaconry's container chain.

## Expected outcome

- `poc/telegram_mock/Dockerfile` structure matches
  `poc/{hub,scout}/Dockerfile` post-BC-099:
  - `# syntax=docker/dockerfile:1` directive.
  - `FROM ghcr.io/bboortz/python-scratch-trixie:latest AS runtime-src`.
  - `FROM ghcr.io/bboortz/forge-builder-trixie:latest AS builder`.
  - Builder: pip install + source COPY + `RUN --mount=from=runtime-src`
    with the BC-099 ABI guard + BC-098 Python-minor-agnostic mv into
    `/stage/usr/local/lib/python${BUILDER_PY_VER}/site-packages`.
  - `FROM runtime-src AS runtime` (reuses resolved digest).
  - `COPY --from=builder /stage /` (single-dir guarantee).
  - HEALTHCHECK baked: `["/hc", "-t", "3", "http://localhost:8080/readyz"]`.
  - CMD adapted to the scratch runtime's `ENTRYPOINT=/usr/local/bin/python3`:
    `CMD ["-u", "-m", "uvicorn", "mock_bot:app", "--host", "0.0.0.0", "--port", "8080", "--log-level", "warning"]`.
- `make test-e2e` + `make check-full` stack pass end-to-end — the
  BC-018 e2e flow still drives telegram-mock's `/__driver__/*`
  endpoints correctly against the scratch runtime.
- `docker inspect beaconry-test-telegram-mock:latest --format '{{.Config.Healthcheck.Test}}'`
  returns `[CMD /hc -t 3 http://localhost:8080/readyz]`.
- `verify-pinned-versions` + `verify-base-images` + the Dockerfile
  COPY source lint all pass with the new file.
- `poc/tests/unit/test_BC_099_base_abi_guard.py` continues to pass
  — BC-100 doesn't invalidate the hub/scout assertions.
- New `poc/tests/unit/test_BC_100_telegram_mock_forge_base.py`
  asserts telegram-mock's Dockerfile carries the same structural
  invariants as hub/scout (syntax directive, runtime-src stage,
  ABI guard shape, HEALTHCHECK, `/stage /` COPY).

## Out of scope

- **Changing mock_bot.py.** The Python code is unchanged. BC-012's
  `beaconry_health.build_router` already provides `/readyz`.
- **Baking the ABI guard failure into a nicer format.** Reuse the
  verbatim shell from hub/scout — if BC-099's wording ever changes,
  all three Dockerfiles update in the same PR.
- **Pinning the ghcr image tag for telegram-mock.** Consumer (CI
  smoke) doesn't need rollback muscle; `:latest` tracks forge's
  daily rebuild like the other beaconry images.
- **Dropping the `python3 -u` unbuffered flag.** Preserved — the
  compose e2e logs need unbuffered stdout for debugging.
- **Shrinking `requirements.txt`.** Keep `fastapi==0.115.0` +
  `uvicorn==0.32.0` pins. Future dependency maintenance is a
  separate story if security advisories fire.
- **Signing the resulting image directly.** If forge publishes a
  `ghcr.io/bboortz/beaconry-telegram-mock` under its own CI, the
  signature comes from forge's workflow identity (BC-094 pattern).
  Not required for the test-only use — the local
  `beaconry-test-telegram-mock:latest` is built fresh per CI run.

## Acceptance criteria (Gherkin)

```gherkin
Feature: telegram_mock joins the forge-signed scratch base family

  Scenario: Dockerfile syntax directive is present
    Given a checkout of beaconry on this branch
    When a reader opens poc/telegram_mock/Dockerfile
    Then the first line is a `# syntax=docker/dockerfile:1` directive

  Scenario: Named runtime-src + forge builder
    When a reader greps the file
    Then exactly one `FROM ghcr.io/bboortz/python-scratch-trixie:latest AS runtime-src`
     And exactly one `FROM ghcr.io/bboortz/forge-builder-trixie:latest AS builder`
     And exactly one `FROM runtime-src AS runtime`

  Scenario: BC-099 ABI guard present (mirrors hub/scout)
    When a reader greps the file
    Then exactly one `RUN --mount=from=runtime-src,source=/usr/local/lib,target=/runtime-lib`
     And the step reads BUILDER_PY_VER + RUNTIME_PY_VER and compares them
     And the step carries the `BC-099 ABI guard tripped` marker
     And on success it mv's /install/lib/python${BUILDER_PY_VER}/site-packages → /stage/...

  Scenario: Runtime stage uses the single staged COPY
    When a reader greps the file
    Then exactly one `COPY --from=builder /stage /` line

  Scenario: HEALTHCHECK baked in
    When a reader greps the file
    Then `HEALTHCHECK --interval=... CMD ["/hc", "-t", "3", "http://localhost:8080/readyz"]`

  Scenario: CMD is adapted to the scratch runtime's ENTRYPOINT
    When a reader greps the CMD line
    Then it does NOT start with `python3` (scratch runtime bakes that as ENTRYPOINT)
     And it starts with `-u` or `-m` arguments to the python3 interpreter

  Scenario: Zero python3.X literals in instructions
    When a reader greps instruction lines (not comments) for `python3\.\d+`
    Then no match occurs

  Scenario: End-to-end smoke still passes
    When `make test-e2e` runs with the new image
    Then the telegram-mock service reaches healthy via /hc → /readyz
     And the BC-018 e2e probe flow completes successfully
```

## Capabilities delivered

- **CAP-T202: telegram_mock multi-stage build on forge-signed scratch
  base with ABI guard + baked HEALTHCHECK** (type: technical,
  status: active). Applies CAP-T196 (builder) + CAP-T199 (runtime
  base tracks `:latest`) + CAP-T200 (Python-minor-agnostic) + CAP-T201
  (ABI guard + HEALTHCHECK) to the final beaconry-authored container
  on upstream Docker Hub. Closes the "one container unsigned" asymmetry.

Reference existing CAPs (all applied here):
- CAP-T196 (BC-095 builder base)
- CAP-T199 (BC-097 `:latest` runtime)
- CAP-T200 (BC-098 Python-minor-agnostic)
- CAP-T201 (BC-099 ABI guard + HEALTHCHECK)

## Open questions

1. **Keep the `.beaconry_health` COPY line?** mock_bot.py imports
   from it at module-load; needed for `/readyz`. Preserving.
2. **Add a `/sbom.json` baked into telegram-mock too?** BC-096 did
   this for hub + scout — gives `docker exec telegram-mock
   cat /sbom.json` for parity. Low-value for a test-only image;
   defer unless the user asks.

## Links

- BC-083 (runtime migration — source pattern)
- BC-095 (builder migration)
- BC-097 (`:latest` + `/hc`)
- BC-098 (Python-minor-agnostic)
- BC-099 (BuildKit + ABI guard + HEALTHCHECK — most recent pattern)
- BC-012 (`beaconry_health.build_router` — provides `/readyz` on mock_bot)
- BC-018 (telegram-mock service origin)
- Current file: `poc/telegram_mock/Dockerfile`
- E2E consumer: `poc/docker-compose.test.yml § telegram-mock`
