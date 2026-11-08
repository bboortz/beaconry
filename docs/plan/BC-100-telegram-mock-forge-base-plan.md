# BC-100 — telegram_mock onto the forge-signed scratch chain (same recipe as BC-099)

*Branch: `feat/BC-100-telegram-mock-forge-base`*
*Start: 2026-11-08*
*Story: `docs/stories/BC-100-telegram-mock-forge-base.md`*

---

## Situation

Three of beaconry's four Dockerfiles follow the forge-signed pattern
(BC-083 → BC-095 → BC-097 → BC-098 → BC-099). One doesn't:

```dockerfile
# poc/telegram_mock/Dockerfile — pre-BC-100
FROM python:3.12-slim
WORKDIR /app
COPY telegram_mock/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY telegram_mock/mock_bot.py .
COPY beaconry_health/ ./beaconry_health/
EXPOSE 8080
CMD ["python3", "-u", "-m", "uvicorn", "mock_bot:app", ...]
```

BC-100 applies the full post-BC-099 recipe to this file. The Python
code (`mock_bot.py`) is unchanged; `beaconry_health.build_router`
already provides `/readyz` at port 8080 via BC-012 so the baked
HEALTHCHECK has an endpoint to hit.

---

## Problems to solve

- Upstream `python:3.12-slim` is unsigned, 120 MB, doesn't track
  forge's Python minor cadence.
- Image has no baked HEALTHCHECK — `docker inspect` on the image
  returns empty `.Config.Healthcheck.Test`.
- No ABI guard — a future base-image split-brain would produce a
  broken telegram-mock silently (test-only impact, but wastes CI).
- Asymmetry in operator audit: three signed, one unsigned.

---

## Goals

1. **G1** — Dockerfile first line: `# syntax=docker/dockerfile:1`.
2. **G2** — Named `runtime-src` stage + runtime stage `FROM runtime-src`.
3. **G3** — Builder stage FROM `forge-builder-trixie:latest`; BC-099
   ABI guard RUN step (identical shape to hub/scout) merged with the
   BC-098 mv into `/stage/usr/local/lib/python${BUILDER_PY_VER}/site-packages`.
4. **G4** — Runtime stage does `COPY --from=builder /stage /` (single-dir
   guarantee) plus the Python source + `beaconry_health` + VERSION.
5. **G5** — CMD adapted to the scratch runtime's baked ENTRYPOINT
   (`/usr/local/bin/python3`): first token is `-u` not `python3`.
6. **G6** — HEALTHCHECK baked in: `["/hc", "-t", "3", "http://localhost:8080/readyz"]`.
7. **G7** — Zero `python3.X` literals in instructions.
8. **G8** — `make test-e2e` passes with the new image (the BC-018
   e2e flow is the primary live oracle — it drives telegram-mock's
   `/__driver__/*` endpoints against the scratch runtime).
9. **G9** — CAP-T202 registered.

---

## Scope

### Modified files

- `poc/telegram_mock/Dockerfile` — full rewrite per hub/scout recipe.
- `docs/CAPABILITIES.md` — add CAP-T202.
- `docs/stories/INDEX.md` — BC-100 in-progress row (done).
- `CHANGELOG.md` — v0.17.1 entry.
- `VERSION` + `poc/VERSION` + `pyproject.toml` — 0.17.0 → 0.17.1.
- `poc/tests/integration/test_BC_080_cleanup.py` — the test image
  name `beaconry-test-telegram-mock:latest` is unchanged, but if
  anything else asserts the old `python:3.12-slim` FROM, it needs
  updating. Likely nothing.

### Added files

- `poc/tests/unit/test_BC_100_telegram_mock_forge_base.py` — grep
  assertions mirroring BC-099 tests but for the telegram_mock
  Dockerfile. Verifies: syntax directive, runtime-src stage,
  `FROM runtime-src AS runtime`, ABI guard RUN with the BC-099
  marker, `/stage /` COPY, HEALTHCHECK, CMD shape (no `python3`
  prefix), zero `python3.X` literals in instructions.

### Not modified

- `poc/telegram_mock/mock_bot.py` — unchanged.
- `poc/telegram_mock/requirements.txt` — same pins.
- `poc/docker-compose.test.yml` — the `build: context + dockerfile`
  path is unchanged; image naming is unchanged.
- `poc/scripts/cleanup.py` — allowlist still names
  `beaconry-test-telegram-mock:latest`.

---

## Non-goals

- Changes to mock_bot.py or its endpoints.
- Signing telegram-mock images on forge's side (separate FG ask if
  it ever becomes useful; test-only today).
- Baking an SBOM into telegram-mock (BC-096 pattern — low value for
  a test-only container; follow-up BC if asked).
- Shrinking requirements.txt.

---

## Design decisions

- **Full recipe port, no local variation.** BC-099 shipped the
  structural pattern; BC-100 applies it verbatim. If BC-099's shape
  ever changes, all three Dockerfiles (`hub`, `scout`, `telegram_mock`)
  get updated in the same PR.
- **CMD adapts, ENTRYPOINT inherits.** Scratch base bakes
  `ENTRYPOINT ["/usr/local/bin/python3"]`. The existing CMD prefixed
  with `python3` would run `python3 python3 -u ...` under the
  scratch base. Drop the leading `python3`; the CMD becomes `["-u",
  "-m", "uvicorn", "mock_bot:app", "--host", "0.0.0.0", "--port",
  "8080", "--log-level", "warning"]`.
- **Keep `EXPOSE 8080`.** Documents the service port; survives the
  scratch migration unchanged.
- **Keep the `/data` chown step out.** Hub needs `/data` for sqlite;
  telegram-mock doesn't persist state. No `/stage/data` in this
  Dockerfile — just `/stage/usr/local/lib/...` from the mv.

---

## Breaking changes

### 1. API / return shape
**None.** mock_bot.py endpoints unchanged.

### 2. Data model
**None.** No storage.

### 3. Behavioral semantics
- **ABI guard applies to telegram-mock too.** During a future forge
  publish race, telegram-mock's build fails instead of silently
  producing a broken image. Loud ≫ silent, same policy as hub+scout.
- **Image size shrinks** (~120 MB → ~50-60 MB). Not a semantic
  change, but noted in the CHANGELOG.

---

## Merge plan

1. All work on `feat/BC-100-telegram-mock-forge-base`.
2. `make check` + `make test-e2e` + `make check-full` pass. Primary
   oracle is `test-e2e` — it instantiates telegram-mock from source
   and drives its endpoints end-to-end.
3. PR to `dev`. Claude opens, user runs `/ultrareview`, Claude runs
   `/code-review`, user merges on green.
4. Squash-merge.

---

## Rollout plan

- **Dev loop**: next `make test-e2e` on this branch builds the new
  image + exercises the stack. No operator-visible change.
- **CI**: next GitHub Actions run of the test suite rebuilds the
  image with the new Dockerfile; cold-start + e2e verify behavior.
- **Operator action**: none. telegram-mock is test-only — never runs
  in production.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| G1 — syntax directive | unit | `test_BC_100_telegram_mock_forge_base.py::test_syntax_directive_present` | ✓ |
| G2 — runtime-src + FROM runtime-src | unit | `test_BC_100_telegram_mock_forge_base.py::test_runtime_src_stage_present` + `test_runtime_stage_from_runtime_src` | ✓ |
| G3 — ABI guard RUN step | unit | `test_BC_100_telegram_mock_forge_base.py::test_abi_guard_run_step_present` + `test_abi_guard_emits_marker` | ✓ |
| G4 — `COPY --from=builder /stage /` | unit | `test_BC_100_telegram_mock_forge_base.py::test_runtime_copies_stage_root` | ✓ |
| G5 — CMD adapted (no `python3` prefix) | unit | `test_BC_100_telegram_mock_forge_base.py::test_cmd_adapted_to_scratch_entrypoint` | ✓ |
| G6 — HEALTHCHECK baked | unit | `test_BC_100_telegram_mock_forge_base.py::test_healthcheck_baked` | ✓ |
| G7 — Zero `python3.X` literals | unit | `test_BC_100_telegram_mock_forge_base.py::test_no_python_minor_literals` | ✓ |
| G8 — E2E passes end-to-end | integration | `make test-e2e` (BC-018 flow) exits 0 | ✓ |
| G9 — CAP-T202 registered | lint | `make verify-traceability` | ✓ |
| Memory drift pattern added if this story deprecates documented behavior | TOML diff / N/A | N/A | ✓ |

Legend: ✓ covered · ⚠️ partial · ❌ open (blocks shipping).

---

## Testing strategy

- Unit tests (grep-based) mirror the BC-099 shape.
- `make test-e2e` is the live oracle: brings up the BC-018 compose
  stack (`docker-compose.test.yml`) which builds telegram-mock from
  source + wires it to hub-test + drives the probe flow.
- `make check-full` runs cold-start + scenarios + e2e; this story
  only affects the e2e side, but the full tier should stay green.

---

## Testing commands

```bash
# Fast tier
make check

# E2E — primary oracle, instantiates telegram-mock from source
make test-e2e

# Full tier
BEACONRY_SKIP_DOGFOOD=1 BEACONRY_SKIP_CLI_SMOKE=1 make check-full

# Live spot-check (after test-e2e — image is beaconry-test-telegram-mock:latest)
docker inspect beaconry-test-telegram-mock:latest --format '{{.Config.Healthcheck.Test}}'
# Expected: [CMD /hc -t 3 http://localhost:8080/readyz]

docker exec telegram-mock /usr/local/bin/python3 -c "import fastapi, uvicorn; print('ok', fastapi.__version__)"
# Expected: ok 0.115.0
```

---

## Links

- Story: `docs/stories/BC-100-telegram-mock-forge-base.md`
- BC-099 (source pattern)
- BC-018 (telegram-mock e2e origin)
- BC-012 (`beaconry_health.build_router` — provides `/readyz`)
- Current file: `poc/telegram_mock/Dockerfile`
- E2E consumer: `poc/docker-compose.test.yml`
