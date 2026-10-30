*Locked: 2026-10-29. Quality bar review due 2026-11-28 (co-located with container-coverage V1 review).*

## Quality bar

POC bar: **baseline library quality** — unit tests per module, ruff clean, type hints checked, ≥ 60% coverage, integration + e2e tests for the demo path. Re-evaluate 2026-11-28.

## Development process: Design-Driven, Test-Guarded (DD-TGD)

```
Per feature (BC-NNN):

0. Consult IDEAS backlog   docs/plan/IDEAS.md
                           Running list of ideas Claude + user have discussed.
                           Check before proposing a new story; append new ideas
                           as they come up (don't let discussion threads evaporate).

1. Operator scenario       docs/stories/BC-NNN-<slug>.md
                           User gives rough story → Claude asks 9 canonical questions
                           → final Gherkin acceptance criteria + capability list.
                           Status: draft → ready.

2. Branch + plan doc       feat/BC-NNN-<slug> from dev
                           docs/plan/BC-NNN-<slug>-plan.md:
                             - Story link: BC-NNN
                             - Design delta (what/why, alternatives rejected)
                             - Breaking changes
                             - Merge plan (which tests must pass, review tiers)
                             - Rollout plan (migration if any; "none" is valid)
                           Story status → in-progress.

3. Red integration test    poc/tests/integration/test_BC_NNN_<slug>.py
                           Encodes Gherkin acceptance scenario(s). Must fail before implementation.

4. Implementation          Smallest change that turns the red test green.

5. Refactor + unit tests   Extract helpers in poc/tests/unit/ where behavior is non-trivial.
                           Every test file carries a top-of-file `Covers:` docstring
                           block naming the CAPs it exercises.

5a. Register capabilities  docs/CAPABILITIES.md
                           Add or extend capability entries so bidirectional links to
                           tests are complete. verify-traceability enforces this on
                           every make check.

6. Gates                   make check (fast tier) — lint, type (pyright basic), test,
                           coverage ≥ 60%, verify-traceability, smoke. make check-full adds
                           opt-in markers (compose, real_tools).

6a. Pre-ship audit         Fill the "Shipping evidence" table in the plan doc. One row per
                           Gherkin scenario and plan goal. Three audit lenses:
                             - Gherkin coverage (every Scenario has a pointer)
                             - data-model symmetry (every write has a mirror read, and vice versa)
                             - self-consistency (story applies its own new conventions)
                           Any ⚠️ or ❌ row blocks shipping — cycle back to step 5.

7. Update DESIGN.md        Only if architecture changed. Final shape only — not the delta.

8. PR to dev               Claude writes body from branch diff; links story + plan doc.
                           Claude pings user to run /ultrareview, then Claude runs /code-review.
                           User merges when both clean. Story status → shipped.
```

**Exceptions (no story required):**
- `fix/<slug>` with a clear repro — references symptom, not a story
- `refactor/<slug>` with no behavior change — tests prove invariance
- `chore/<slug>` for tooling/deps

**Definition of Done per BC-NNN:**
- [ ] Acceptance criteria pass
- [ ] Red integration test went green via implementation
- [ ] All blocking gates pass
- [ ] DESIGN.md updated if architecture changed
- [ ] Demo scenario added to `demo.sh` if user-facing

## Stories — layout, format, lifecycle

```
docs/stories/
  INDEX.md                      — one-line status board (grep-friendly)
  _template.md                  — canonical story template
  _8-questions.md               — the canonical clarification set
  BC-NNN-<slug>.md              — one file per story
```

Lifecycle: `draft → ready → in-progress → shipped` (stored in frontmatter + INDEX).

**The 9 canonical clarification questions** (asked for every new story):
1. **Who** is the operator in this moment? (role, mental state, context)
2. **When** does the story fire? (trigger — event, state, cadence)
3. **What** does success look like? (observable, not internal state)
4. **How urgent?** (Tier 0/1/2/3 mapping or N/A)
5. **Happy path** — exact sequence of operator-visible steps?
6. **Edge cases / failure modes** that matter?
7. **Out of scope** — what could creep in but shouldn't?
8. **How do we know it's done?** (acceptance test wording = Gherkin)
9. **What capabilities does this story deliver or extend?** — Name new CAPs; link to existing ones. Added for BC-004 to make the capabilities matrix explicit from story-time, not retroactively.

## Branch & PR mechanics

- All work branches off `dev`. Names: `feat/BC-NNN-<slug>`, `fix/<slug>`, `docs/<slug>`, `refactor/<slug>`, `chore/<slug>`.
- `main` is **CI-promoted only** — never pushed to directly. On every green `dev` merge, CI fast-forwards `main` to `dev`. (CI setup deferred until remote exists.)
- Commit format: `<type>(<scope>): <description>`. Scopes in `code-quality.md`.
- PR flow: Claude opens → Claude pings user for `/ultrareview` → Claude runs `/code-review` → user merges.

## Blocking gates (must pass to merge to `dev`)

Two tiers (BC-006). Fast tier (`make check`) runs pre-push + the merge gate; full tier (`make check-full`) runs before pushing a feat/fix branch and in CI.

| Gate | Tool | Tier | Where |
|------|------|------|-------|
| Lint | `ruff check` | fast | pre-commit (staged) + `make check` |
| Format | `ruff format --check` | fast | pre-commit (staged) + `make check` |
| Type check | `pyright` (basic / default) | fast | pre-commit (staged) + `make check` |
| Tests | `pytest` | fast | **pre-push** + `make check` |
| Coverage | `pytest --cov`, threshold ≥ 60% | fast | `make check` |
| Traceability | `make verify-traceability` | fast | `make check` + pre-push |
| Dockerfile `COPY beaconry_health/` | `make verify-dockerfile-health-copy` | fast | `make check` |
| Memory drift (BC-013/014/… bans) | `make verify-memory-drift` | fast | `make check` |
| Smoke | `make smoke` (mocked pipeline) | fast | `make check` |
| Compose integration | `pytest -m compose` | full | `make check-full` |
| Real-tools integration | `pytest -m real_tools` | full | `make check-full` |

No advisory gates in the POC — if a check isn't worth blocking, it isn't worth running.

## Testing strategy

Full test pyramid under `poc/tests/`:

```
poc/tests/
  unit/         — pure functions: pacman parser, CVSS extractor, finding ID, markdown escape
  integration/  — one adapter at a time: DockerAdapter.execute, Hub /scan endpoint, grype pipeline
                  (also where BC-NNN red tests live: test_BC_NNN_<slug>.py)
  e2e/          — full pipeline: scout.collect_and_post → hub.receive_scan → notify → verify_fix
  fixtures/     — sample SBOMs, grype outputs, Telegram message captures
```

**Mocking rule: mock everything external.** syft/grype subprocess calls, Docker socket (`docker.from_env()`), Telegram (`bot.send_message`) — all stubbed with fixtures. Trade-off: real tool updates can silently diverge. Mitigation: `demo.sh --smoke` CI gate exercises the real stack end-to-end.

## Dev loop ergonomics

- **Python env**: Docker for runtime (containers), venv on host for tests. `python -m venv .venv && pip install -r requirements-dev.txt`.
- **Local gates**: `Makefile` targets:
  - `make lint` — ruff check + format check
  - `make format` — ruff format (fix)
  - `make type` — pyright
  - `make test` — pytest
  - `make check` — all of the above = what CI runs
  - `make smoke` — bash demo.sh --smoke
- **Pre-commit hook** (`.pre-commit-config.yaml`, pre-commit framework):
  - On `commit`: ruff + ruff format + pyright on staged files
  - On `push`: full `pytest` suite
- Install: `pre-commit install && pre-commit install --hook-type pre-push`
