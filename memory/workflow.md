*Locked: 2026-10-29. Quality bar review due 2026-11-28 (co-located with container-coverage V1 review).*

## Quality bar

POC bar: **baseline library quality** — unit tests per module, ruff clean, type hints checked, ≥ 60% coverage, integration + e2e tests for the demo path. Re-evaluate 2026-11-28.

## Development process: Design-Driven, Test-Guarded (DD-TGD)

```
Per feature (BC-NNN):

1. Operator scenario       docs/stories/BC-NNN-<slug>.md
                           User gives rough story → Claude asks 8 canonical questions
                           → final Gherkin acceptance criteria. Status: draft → ready.

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

6. Gates                   make check — lint, type (pyright basic), test, coverage ≥ 60%,
                           docker build, smoke (bash demo.sh --smoke).

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

**The 8 canonical clarification questions** (asked for every new story):
1. **Who** is the operator in this moment? (role, mental state, context)
2. **When** does the story fire? (trigger — event, state, cadence)
3. **What** does success look like? (observable, not internal state)
4. **How urgent?** (Tier 0/1/2/3 mapping or N/A)
5. **Happy path** — exact sequence of operator-visible steps?
6. **Edge cases / failure modes** that matter?
7. **Out of scope** — what could creep in but shouldn't?
8. **How do we know it's done?** (acceptance test wording = Gherkin)

## Branch & PR mechanics

- All work branches off `dev`. Names: `feat/BC-NNN-<slug>`, `fix/<slug>`, `docs/<slug>`, `refactor/<slug>`, `chore/<slug>`.
- `main` is **CI-promoted only** — never pushed to directly. On every green `dev` merge, CI fast-forwards `main` to `dev`. (CI setup deferred until remote exists.)
- Commit format: `<type>(<scope>): <description>`. Scopes in `code-quality.md`.
- PR flow: Claude opens → Claude pings user for `/ultrareview` → Claude runs `/code-review` → user merges.

## Blocking gates (must pass to merge to `dev`)

| Gate | Tool | Where |
|------|------|-------|
| Lint | `ruff check` | pre-commit (staged) + CI (full) |
| Format | `ruff format --check` | pre-commit (staged) + CI (full) |
| Type check | `pyright` (basic / default) | pre-commit (staged) + CI (full) |
| Tests | `pytest` | **pre-push** + CI |
| Coverage | `pytest --cov`, threshold ≥ 60% | CI |
| Docker build | `docker compose build` | CI |
| Smoke test | `bash demo.sh --smoke` | CI |

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
