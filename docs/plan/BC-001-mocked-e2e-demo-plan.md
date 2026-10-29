# BC-001 — End-to-end mocked POC demo

*Branch: `feat/BC-001-mocked-e2e-demo`*
*Start date: 2026-10-29*
*Story: `docs/stories/BC-001-mocked-e2e-demo.md`*

---

## Situation

BC-000 shipped the scaffold, process, and the POC code — but the POC calls real `syft`, real `grype`, real Docker socket, and real Telegram. The homelab self-hoster who clones the repo cannot see it working until they create a Telegram bot and pull a vulnerable Docker image. That friction kills the "aha moment" that would convert them from sceptic to contributor.

The three virtual-user iterations on the story landed on: ship a fully mocked, 90-second terminal demo that (a) shows two findings with different fix paths, (b) labels every mocked action, (c) is backed by an integration test and unit tests. See the story for Gherkin acceptance.

---

## Problems to solve

- POC code assumes real `syft`/`grype`/`docker`/`telegram` at import time (globals, env requirements, side effects)
- Urgency tiers from `design-decisions.md` aren't implemented in code; findings just filter on `MIN_CVSS`
- No in-process demo entrypoint; the only way to run beaconry today is `docker compose up`
- `demo.sh --smoke` is a stub returning 0 — zero actual coverage
- No fixtures for mocked external calls
- Coverage threshold is temporarily 0% from the BC-000 chore; needs to go back to ≥ 60% here

---

## Goals

1. `make demo` runs the full pipeline in-process with `BEACONRY_MOCK=1`, exits 0, shows every stage with `[mock]` labels
2. Two findings surface: one Tier 0 safe-class (auto-fix), one Tier 1 (interactive confirm via `Enter`)
3. `python -m poc.demo --non-interactive` skips the Enter prompt and still exits 0 (for CI)
4. `bash poc/demo.sh --smoke` delegates to `python -m poc.demo --non-interactive`
5. Integration test `poc/tests/integration/test_BC_001_mocked_e2e.py` passes without stdin
6. Unit tests for `make_id`, `extract_cvss`, `format_notification` markdown escape, executor dispatch, tier classification, safe-class detection
7. Coverage of `poc/` ≥ 60% (raised back from the BC-000 floor of 0)
8. `make check` passes on the branch tip

---

## Scope

- `poc/__init__.py`, `poc/hub/__init__.py`, `poc/scout/__init__.py` — package markers so `python -m poc.demo` works
- `poc/mock.py` — fixture SBOMs + grype matches + helpers; central place for mock data
- `poc/hub/hub.py` — gated by `BEACONRY_MOCK`:
  - `run_grype`: returns fixture matches
  - `verify_fix`: first call returns False (still present), second returns True (fixed)
  - `notify`: prints a boxed Telegram message to stdout
  - `lifespan`: skips `grype db update` and Telegram startup
- `poc/hub/urgency.py` (new) — tier classification: `classify_tier(finding, exposure) -> int`, `is_safe_class(finding) -> bool`
- `poc/hub/hub.py` — call `urgency.classify_tier` and log it; call `urgency.is_safe_class` to decide auto-fix vs confirm
- `poc/hub/executor/docker_adapter.py` — gated by `BEACONRY_MOCK`: subprocess calls become `[mock] docker tag ...` / `[mock] docker pull ...` prints
- `poc/demo.py` (new) — the orchestrator:
  - sets `BEACONRY_MOCK=1`, dummy `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, tmpdir `DATA_DIR`
  - imports hub, feeds it a fixture scan payload with two findings
  - prints boxed output for each stage
  - prompts `Press Enter to simulate [Fix now] tap` for Tier 1 finding (unless `--non-interactive`)
  - orchestrates the auto-fix for the Tier 0 finding
  - triggers verify_fix for each; prints final `✓ fixed and verified`
- `poc/demo.sh` — real `--smoke` body: `python -m poc.demo --non-interactive`
- `Makefile` — add `demo` target: `python -m poc.demo`
- `poc/tests/fixtures/` — fixture JSON files for SBOM + grype matches (also importable from `poc.mock`)
- `poc/tests/integration/test_BC_001_mocked_e2e.py` — Gherkin as pytest
- `poc/tests/unit/test_hub_helpers.py`, `test_executor_dispatch.py`, `test_urgency.py` — unit coverage
- `pyproject.toml` — `fail_under = 60`

---

## Non-goals

- Dependency-injection refactor of hub/scout internals (deferred — BC-002+ candidate; the env-flag approach is intentional pragmatic debt)
- Full exposure scoring (fixture flags `internet-facing` on one finding)
- Full health-check waterfall (stub returns pass)
- Real smoke mode that uses Docker Compose (that's a separate story if ever needed; `--smoke` is Python-only)
- CISA KEV feed integration (V2)
- Pacman SBOM path (fixtures only cover Docker)

---

## Design decisions

**Env-flag mocking (`BEACONRY_MOCK=1`), not DI.** DI would require refactoring every external touchpoint through a Protocol or constructor-injected adapter. That's weeks of refactor to make BC-001's demo work. The env flag adds ~5 `if MOCK:` checks and lets us ship. Rejected alternatives: (a) full DI refactor — scope too big; (b) parallel `poc/mocked/` package — duplicates logic; (c) `unittest.mock.patch` at import time — brittle and only works in tests, not for the demo entrypoint.

**Tier classification lives in `poc/hub/urgency.py`.** Keeping it separate from `hub.py` makes it unit-testable without the hub's side effects. Hub imports it and uses it in `process_sbom_entry`.

**Safe-class rule for BC-001:** Tier 0 KEV + container image = safe (rollback tag exists, `docker pull` is reversible). Tier 1 and below = operator confirm. KEV status itself comes from the fixture flag `kev: true` on the match. Real KEV feed integration is V2.

**Interactive Enter prompt stays in `poc/demo.py` only.** The hub itself never calls `input()`. Non-interactive mode passes a sentinel `auto_confirm=True` into the orchestrator so the integration test and CI don't need to simulate stdin.

**`notify()` in mock mode prints a Unicode-box rendering** of the formatted Telegram message to stdout, including the inline-keyboard button labels. Operator can visually distinguish "notification sent" from other log output.

**Rejected: start FastAPI as a background task and have scout POST over localhost.** Pure-import orchestration is simpler and faster; the demo doesn't need the HTTP boundary to prove the pipeline.

---

## Breaking changes

- `hub.lifespan` becomes conditional on `BEACONRY_MOCK` — no runtime behavior change when the flag is unset
- `notify`, `verify_fix`, `run_grype`, `DockerAdapter.execute` gain a mock branch — behavior unchanged when flag is unset
- New module `poc/hub/urgency.py` consumed by `process_sbom_entry` — adds a `tier` field to the `Finding` dataclass? Or passes it as side data? **Decision: add `tier: int` to `Finding`** (default 3 for backward compat). Downstream consumers that didn't expect it get the lowest tier by default.
- `poc/__init__.py` created — the directory becomes an importable package. Existing `from executor.base import …` inside hub.py starts conflicting with `poc.hub.executor` namespace. **Mitigation: switch hub.py's relative imports to absolute `from poc.hub.executor.base import …`.** Container entrypoint still works because the Dockerfile sets `WORKDIR /app` and copies `.` — adjust the Dockerfile import path or set `PYTHONPATH=/app`.
- Coverage floor raised 0 → 60 — any future PR that drops coverage below 60% will fail the gate

---

## Merge plan

1. All work on `feat/BC-001-mocked-e2e-demo`
2. **Red first**: fixtures + integration test committed first in a state that fails
3. **Green**: urgency module + mock gating + demo entrypoint land; integration test passes
4. **Refactor**: unit tests, Dockerfile/import path reconciliation
5. `make check` passes locally (lint, pyright, pytest, coverage ≥ 60%, docker build check, smoke)
6. User reviews branch diff (no remote yet, so manual review; `/ultrareview` + `/code-review` once remote exists)
7. Flip story status → `shipped`, update INDEX.md as last commit on the branch
8. Fast-forward merge `feat/BC-001-mocked-e2e-demo` → `dev`
9. Delete branch

---

## Rollout plan

None required — change is additive within the POC. Existing `docker compose up` path remains functional; it just gets a new peer entrypoint (`python -m poc.demo`). First-time demo for any clone:

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
pip install -r poc/hub/requirements.txt     # if demo imports hub runtime
make demo
```

---

## Testing strategy

**Integration test** (`poc/tests/integration/test_BC_001_mocked_e2e.py`) runs the orchestrator programmatically with `auto_confirm=True`, captures stdout, asserts the Gherkin scenarios: stage ordering present, both tiers logged, `[mock]` labels on externally-impactful lines, `✓ fixed and verified` final state, no HTTP calls (via monkeypatching `requests.post` to raise — if called, test fails).

**Unit tests:**

- `test_hub_helpers.py`: `make_id` stability + dedup, `extract_cvss` picks best score across entries, `format_notification` escapes markdown-sensitive chars
- `test_executor_dispatch.py`: DockerAdapter claims docker findings, ManualAdapter is the fallback, dispatcher picks first match
- `test_urgency.py`: tier matrix (KEV→0, CVSS≥9+internet-facing→1, CVSS 7–8→2, <7→3); safe-class rule (KEV+container→True, else→False)

**No e2e-tier tests in BC-001.** The integration test *is* the e2e for the mocked path. A real-infra e2e requires Docker Compose + Telegram and is deferred.

---

## Testing commands

```bash
source .venv/bin/activate

# Full gate
make check

# Just the BC-001 integration test
pytest poc/tests/integration/test_BC_001_mocked_e2e.py -v

# Demo interactively
make demo

# Demo non-interactive (what CI runs)
python -m poc.demo --non-interactive

# Smoke mode
make smoke
```

Expected: `make check` exits 0; `make demo` prints the full pipeline in ~5 seconds (no network); `make smoke` exits 0.
