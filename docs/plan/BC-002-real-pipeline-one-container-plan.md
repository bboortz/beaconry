# BC-002 — Real pipeline on one container (no auto-fix)

*Branch: `feat/BC-002-real-pipeline-one-container`*
*Start date: 2026-10-29*
*Story: `docs/stories/BC-002-real-pipeline-one-container.md`*

---

## Situation

BC-001 shipped a fully mocked demo. The virtual-user panel afterwards said unanimously: "pretty, but I don't trust it." Credibility gap. The POC code (`poc/scout/`, `poc/hub/`) was built in the brainstorm agent's handoff but never actually run end-to-end — syft, grype, Telegram, Docker socket are all real-world dependencies that have only ever been exercised through mocks.

BC-002 closes the gap by making the same `make demo` command run the real pipeline when Telegram creds are configured. Minimum real surface area: one hard-coded target image (nginx:1.24), one notification path (real Telegram), two working buttons (Snooze + Accept), one honest stub (Fix now). No auto-fix execution — the Fix-now button edits the message to "coming in BC-003."

---

## Problems to solve

- `poc/demo.py` unconditionally sets `BEACONRY_MOCK=1` at module scope — can't be bypassed without rewriting the entrypoint
- `hub.notify` only works when `hub.tg_app` is set (via FastAPI lifespan). Demo doesn't run FastAPI. Need to populate `tg_app` directly.
- `hub.on_button_click` always runs the real fix path when `action == "fix"`. BC-002 wants it to say "coming" instead — needs a feature flag so BC-003 can flip it later.
- Dev DB path (`DATA_DIR`) defaults to `/data` which requires root; needs to point into a repo-local cache dir
- Grype's DB cache (~200 MB) needs to be cached across runs under the repo (gitignored)
- No prereq-check: user with Telegram creds but without syft/grype gets a cryptic error from the subprocess
- No way to verify Telegram wiring without triggering a real scan — need `--self-test` canary
- `.env` file at `poc/.env` isn't auto-loaded by `make demo`; `python-dotenv` not in dependencies

---

## Goals

1. `make demo` unconditionally dispatches: real path if `BEACONRY_MOCK` is unset, mock path if set
2. Real path requires `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`; missing creds → clear error + fallback hint
3. `python-dotenv` loads `poc/.env` automatically before dispatch
4. Prereqs checked before scan: `syft` on PATH, `grype` on PATH, nginx:1.24 image present locally
5. Real syft runs on nginx:1.24 → SBOM; real grype matches → findings; Telegram message sent per finding
6. Telegram buttons work: Snooze and Accept update finding status in `.beaconry-cache/dev.db`; Fix-now edits message to "coming in BC-003"
7. `make demo ARGS=--self-test` sends canary and exits 0 — no scan
8. Grype DB cached under `.beaconry-cache/grype-db/` across runs
9. Dev DB at `.beaconry-cache/dev.db` — separate from Compose POC's `/data/beaconry.db`
10. Mock path (`BEACONRY_MOCK=1 make demo`) still works exactly as BC-001 shipped it
11. `make check` passes: lint, pyright, pytest (including an opt-in `real_tools` integration test), coverage ≥ 60%

---

## Scope

- `poc/demo.py` — split into mock + real dispatch; prereq check; real orchestrator (`_run_real_demo`); Telegram Application startup; press-Ctrl-C hold loop; `--self-test`
- `poc/hub/hub.py` — add `BEACONRY_FIX_ENABLED` env flag (default off); when off, `on_button_click` for `fix` action edits message to "coming in BC-003" without invoking the executor. When on, existing BC-001 behavior.
- `poc/hub/hub.py` — small helper `attach_telegram_app(app)` so demo can inject the Application instead of relying on lifespan
- `poc/hub/hub.py` — do not call `os.environ["TELEGRAM_BOT_TOKEN"]` at import time if `BEACONRY_MOCK=0` and the demo set a placeholder... actually simpler: raise with named error if token missing. The demo pre-check will prevent that path.
- `.gitignore` — `.beaconry-cache/`
- `Makefile` — `demo` target: `python -m poc.demo $(ARGS)` so `--self-test` and other flags pass through
- `requirements-dev.txt` — add `python-dotenv==1.0.1`
- `poc/tests/integration/test_BC_002_real_pipeline.py` — opt-in `real_tools` marker; skip unless `syft` + `grype` on PATH; mock Telegram; assert findings produced from nginx:1.24
- `poc/tests/unit/test_demo_dispatch.py` — unit test for mock/real dispatch logic, prereq check, self-test flag parsing
- `pyproject.toml` — register `real_tools` pytest marker (strict markers mode)

---

## Non-goals

- Validating `docker compose up` path (Compose POC) — separate story
- Actual fix execution (BC-003)
- Scout as a running service — BC-002 uses a one-shot subprocess call to syft
- Pacman package scanning (BC-004+)
- Open-port scanning / exposure inference
- Multi-host canary
- Dotenv file at repo root — `.env` lives at `poc/.env` per existing convention
- Keeping the real demo running after Ctrl-C — process exits cleanly
- Auto-pulling nginx:1.24 if missing — operator does `docker pull nginx:1.24` first

---

## Design decisions

**One `make demo` command, mode selected by env flag.** User's directive. Keeps muscle memory from BC-001. Mock path still reachable (`BEACONRY_MOCK=1 make demo`) for CI, tests, and no-Telegram previews. Rejected alternative: `make real-demo` separate target — splits the UX unnecessarily.

**`python-dotenv` auto-loads `poc/.env`.** Operator fills `poc/.env`, runs `make demo` from repo root — no manual `source` needed. One extra dev dep. Rejected: documenting a `source poc/.env && make demo` chain in README — error-prone.

**`BEACONRY_FIX_ENABLED` feature flag.** Instead of removing the fix path or branching on BC-NNN inside `on_button_click`, gate it with an env flag that defaults off. BC-003 just flips the default. Clean path forward. Rejected: deleting the fix code in BC-002 and re-adding in BC-003 — wasteful.

**Telegram Application lives in `demo.py`, not `hub.py`.** The hub currently starts Telegram inside its FastAPI lifespan. Demo doesn't run FastAPI, so demo starts Telegram directly and injects it into hub via `hub.tg_app = app`. For BC-002 that direct assignment is pragmatic debt — a cleaner `attach_telegram_app` helper is also added so BC-003+ don't have to touch the global directly.

**Dev DB and grype cache co-located under `.beaconry-cache/`.** Gitignored, repo-local, no root required. Operator can `rm -rf .beaconry-cache` to reset. Compose POC continues to use `/data` so the two don't conflict and either can be tried without disturbing the other.

**`--self-test` sends canary via `tg_app.bot.send_message` directly, bypassing hub's notification format.** The canary isn't a finding — it's a wiring check. Minimal code, minimal failure modes.

**Integration test is opt-in with `real_tools` marker.** Running it in CI without syft/grype installed would silently skip, which is fine. Running it accidentally during local dev would hit real Telegram if token were set — undesirable. Marker makes activation explicit: `pytest -m real_tools`.

---

## Breaking changes

- `make demo` without args previously ran the mock pipeline (BC-001). Now it requires Telegram config and runs the real pipeline. **Mitigation:** error message explicitly points to `BEACONRY_MOCK=1 make demo` fallback. Documented in README dev-quickstart.
- `DATA_DIR` env default changes: when demo.py runs, it sets `DATA_DIR=.beaconry-cache` (previously `/data`). Compose POC path still uses `/data` (set via docker-compose env), so the two paths remain disjoint.
- `on_button_click` fix action behavior changes: with default `BEACONRY_FIX_ENABLED` unset, Fix-now button no longer triggers execution — just shows "coming" message. Compose POC continues to work the same way if operator exports `BEACONRY_FIX_ENABLED=1`; otherwise Compose demo now ALSO shows the stub message. **This is acceptable:** fix path hasn't been proven to work and the stub is more honest than attempting a potentially-broken execution. BC-003 will flip the default once the fix path is validated.

---

## Merge plan

1. All work on `feat/BC-002-real-pipeline-one-container`
2. **Red first**: integration test with `real_tools` marker (skipped if tools missing); unit test for dispatch logic
3. **Green**: dispatch split, prereq check, real orchestrator, Telegram startup, `--self-test`, feature flag
4. **Refactor**: `attach_telegram_app` helper; dev DB path; documentation touch-ups
5. **Manual verification**: operator runs `make demo` with real `.env`, confirms Telegram message arrives, taps Snooze (check DB status), taps Fix (check "coming" message)
6. `make check` passes locally
7. User reviews branch diff; manual-smoke the real demo together
8. Flip story status → `shipped`, update INDEX.md
9. Fast-forward merge → `dev`
10. Delete branch

---

## Rollout plan

Additive change — no migration. First-time real-demo setup for any clone:

```bash
# One-time
pip install -r requirements-dev.txt   # includes python-dotenv now
curl -sSfL https://raw.githubusercontent.com/anchore/syft/main/install.sh | sh -s -- -b ~/.local/bin
curl -sSfL https://raw.githubusercontent.com/anchore/grype/main/install.sh | sh -s -- -b ~/.local/bin
cp poc/.env.example poc/.env && $EDITOR poc/.env   # fill Telegram creds
docker pull nginx:1.24

# Each run
make demo             # real pipeline
make demo ARGS=--self-test   # canary only
BEACONRY_MOCK=1 make demo    # mock, no Telegram
```

Operators who were using the Compose POC continue to use `cd poc/ && docker compose up` — unchanged.

---

## Testing strategy

**Integration test** (`poc/tests/integration/test_BC_002_real_pipeline.py`, marker `real_tools`):
- Skips unless `shutil.which("syft")` and `shutil.which("grype")` both resolve
- Verifies nginx:1.24 image exists via `docker image inspect`
- Runs the demo's real orchestrator with Telegram mocked (so no real messages)
- Asserts: SBOM produced (JSON parseable), grype matches ≥ 1, Finding objects have cve_id + cvss + tier
- Does NOT verify Telegram message delivery — that's manual, outside pytest scope

**Unit tests** (`poc/tests/unit/test_demo_dispatch.py`):
- Prereq check surfaces missing syft/grype/nginx with named errors
- Dispatch picks mock when `BEACONRY_MOCK=1`
- Dispatch picks real when `BEACONRY_MOCK` unset AND `TELEGRAM_BOT_TOKEN` set
- Dispatch errors (exit code non-zero) when `BEACONRY_MOCK` unset AND `TELEGRAM_BOT_TOKEN` unset, with fallback hint in stderr
- `--self-test` flag parsed from `sys.argv`

**Manual smoke** (not in pytest):
1. `cp poc/.env.example poc/.env`, fill real Telegram creds
2. `docker pull nginx:1.24`
3. `make demo` → Telegram message arrives on phone
4. Tap [⏰ Snooze 4h] → message edited, DB status "snoozed"
5. `make demo` again → no new notification (dedup via finding ID)
6. Delete `.beaconry-cache/dev.db` or new finding → tap [🔧 Fix now] → message says "coming in BC-003"
7. `make demo ARGS=--self-test` → "beaconry is alive" message, no scan
8. `BEACONRY_MOCK=1 make demo` → BC-001 mock pipeline runs

---

## Testing commands

```bash
source .venv/bin/activate

# Full gate
make check

# Unit tests only
pytest poc/tests/unit/test_demo_dispatch.py -v

# Opt-in integration test (requires syft + grype)
pytest -m real_tools -v

# Smoke (unchanged — still mocked)
make smoke
```

Expected: `make check` exits 0; `pytest -m real_tools` either skips or passes depending on tool availability; `make smoke` exits 0.
