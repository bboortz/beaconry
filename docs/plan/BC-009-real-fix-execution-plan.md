# BC-009 — Real fix execution via Telegram

*Branch: `feat/BC-009-real-fix-execution`*
*Start date: 2026-10-30*
*Story: `docs/stories/BC-009-real-fix-execution.md`*

---

## Situation

BC-003 shipped the Telegram button handling and live-verified Snooze/Accept/Fix-stub + double-tap idempotence. The Fix-now button edits to "⚙️ Fix execution coming in a later story" because `BEACONRY_FIX_ENABLED` is unset by default. The DockerAdapter + verify_fix code paths already exist (shipped in BC-001; refined through BC-002). BC-009 unlocks them.

The demo arc currently ends on a disappointment at Fix. The product promise is "CVE arrives → tap → patched and verified within ~90 seconds." Without BC-009, no live demo can close that loop.

Three safety considerations shape the design:
1. **Supply-chain risk** of pulling `:latest` → mitigated partly by `:rollback` tag before pull; digest pinning deferred to a future story per IDEAS "KEV auto-fix is scary".
2. **Upstream hasn't patched** — pulling `:latest` may still carry the same CVE for base-image vulnerabilities. We detect this via `verify_fix` and emit a distinct "⚠️ Pulled, but CVE still present upstream" message so operator doesn't think beaconry is broken.
3. **Container restart is the operator's responsibility** — beaconry pulls the image; it doesn't `docker compose restart`. README documents this clearly.

## Problems to solve

- Fix-now is a stub; demo arc ends on anticlimax
- `BEACONRY_FIX_ENABLED` default is off, forcing operators to flip it manually
- Current `on_button_click` fix path doesn't distinguish "pull failed" (our bug) from "upstream not patched" (not our bug) — would mislead operators
- No live verification that the real DockerAdapter.execute + verify_fix combination works end-to-end against a real image

## Goals

1. Flip `BEACONRY_FIX_ENABLED` default to on. Opt-out via `BEACONRY_FIX_ENABLED=0`
2. Three distinct terminal messages for the three outcomes (fixed / upstream-not-patched / pull-failed)
3. DB status flips to `fixed` only when verify_fix returns True
4. Rollback tag (`<base>:rollback`) is set before every pull — operator can `docker tag <base>:rollback <base>:latest` to restore
5. ManualAdapter still handles non-docker findings (unchanged)
6. Live tap session verifies at least one of the three outcomes (which one depends on real nginx:latest state)
7. README adds a "Fix now behavior" section explaining the three outcomes + operator responsibilities
8. CAP-B014 + CAP-T030/T031/T032/T033 registered

## Scope

- `poc/hub/hub.py`:
  - `BEACONRY_FIX_ENABLED` default flips to `True` (on_button_click reads it)
  - `on_button_click` fix branch grows a third terminal state: distinguish verify_fix returned True vs False (two cases from one "clean" check)
  - Message text copy for the three outcomes
- `poc/hub/executor/docker_adapter.py`:
  - No logic change needed (already does tag + pull); may add better error-string handling
- `poc/hub/Dockerfile`:
  - Install pinned static docker CLI (`DOCKER_CLI_VERSION=27.5.1`). Needed because DockerAdapter runs `docker tag` + `docker pull` as shell subprocesses from inside the hub container. **Caught mid-Phase 3** when preflight showed the base `python:3.12-slim` image has no docker binary — a Compose-path tap would otherwise fail with "docker: not found". Compose-marker test `test_hub_image_bundles_docker_cli` guards against regression.
- `poc/tests/unit/test_hub_helpers.py` or `test_executor_dispatch.py`:
  - Unit tests for the three terminal states (mocking DockerAdapter + verify_fix outcomes)
- `poc/tests/integration/test_BC_005_compose.py`:
  - Compose-marker test that builds the hub image and asserts `docker --version` works inside it
- `README.md` — "Fix now behavior" section
- `docs/CAPABILITIES.md` — 1 business + 5 technical CAPs (CAP-T034 added mid-Phase 3)
- This plan doc with Shipping evidence (will be filled progressively)

## Non-goals

- Health check waterfall from design-decisions.md (deferred to a dedicated story)
- Auto-rollback on health check failure
- Automatic `docker compose restart` after pull
- Fix for alpm / pip (ManualAdapter unchanged)
- SHA256 digest pinning
- Audit log entry for fix actions (IDEAS entry; deferred)

## Design decisions

**`BEACONRY_FIX_ENABLED` default flips to True.** Rationale: BC-009 ships the real path; the opt-out exists for demo environments or safety-conscious operators. Rejected alternative: separate feature flag per Tier (Tier 0 auto, Tier 1 confirm). Overkill for V1; every tap already requires operator action in current flow.

**Three terminal states, three distinct messages.** This is the big UX win over BC-003's stub. Operator sees:
- `✅ Fixed and verified` → tap worked, image patched, CVE gone
- `⚠️ Pulled, but CVE still present upstream` → tap worked, image pulled, upstream still carries CVE (not our bug)
- `❌ Fix failed: <reason>` → pull errored, nothing changed

Rejected alternative: single terminal "done" message with hidden log. Opaque; operator doesn't know whether to believe status.

**Rollback tag before every pull.** `:rollback` is a tag; takes no extra disk space (just a label on the existing image id). Zero cost; safety net for operator to restore manually. Rejected alternative: snapshot via tar + full image file. Overkill.

**DB status only flips to `fixed` on verify_fix True.** If upstream still carries CVE after pull, status stays `open`. Operator can see this in `make findings` and decide next steps.

**No automatic container restart.** Operator has their own compose/docker setup. Beaconry pulls the image; running containers continue with old image until operator restarts. README documents this explicitly.

**Rejected: hide `BEACONRY_FIX_ENABLED` from the operator entirely.** The opt-out is valuable for demos and dev environments; keeping it in the env surface area means a quick `BEACONRY_FIX_ENABLED=0 make demo` returns to stub behavior.

## Breaking changes

- `BEACONRY_FIX_ENABLED` default flips off → on. **Operators running `make demo` after this change will see real `docker pull` subprocesses when they tap Fix.** Mitigation: README's "Fix now behavior" section documents this; `BEACONRY_FIX_ENABLED=0` restores pre-BC-009 behavior.
- The stub "⚙️ Fix execution coming in a later story" message is replaced by one of three real-fix outcomes OR the explicit disabled message when flag is 0.
- CAP-B003 description changes: previously described BC-004 as the fix-execution landing; now BC-009 is that story.
- `poc/hub/Dockerfile` grows a docker CLI install layer. **Hub image size increases by ~70 MB** (static docker 27.5.1 binary). Mitigation: pinned version for reproducibility; no apt-list dependency; one additional `RUN` layer. Rebuild (`docker compose build hub`) required after pulling this branch.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Pull succeeds + verify-fix clean → status fixed | unit test | `test_fix_now_pull_clean_sets_status_fixed` | ✓ |
| Pull succeeds + CVE still upstream → "upstream not patched" | unit test | `test_fix_now_pull_upstream_still_present` | ✓ |
| Pull fails → "fix failed" | unit test | `test_fix_now_pull_fails_message` | ✓ |
| Opt-out via BEACONRY_FIX_ENABLED=0 → stub message | unit test | `test_fix_now_disabled_shows_stub` | ✓ |
| Non-docker → ManualAdapter fallback, no docker subprocess | unit test | `test_fix_now_non_docker_runs_manual_adapter` | ✓ |
| Rollback tag survives | unit test | `test_docker_adapter_sets_rollback_tag_before_pull` | ✓ |
| Live tap session: one terminal state verified on phone | live manual | 2026-10-30 — two of three terminal states live-verified: (1) Fix on `GO-2026-4337` (stdlib in postgres:16-alpine) → "⚠️ Pulled, but CVE still present upstream"; DB status stayed `open`; `postgres:rollback` tag matches pre-pull image id. (2) Fix on `CVE-2026-111497` (brotli-libs in nginxproxy/nginx-proxy:alpine) → "✅ Fixed and verified"; DB status flipped to `fixed`; verify_fix re-scan of :latest found no match. The "❌ Fix failed" state has unit coverage; a live trigger would need an upstream pull failure (deferred — would require intentionally breaking docker.io, out of scope). | ✓ |
| README documents three outcomes + redeploy caveat | README diff | README.md § Fix now behavior (BC-009) | ✓ |
| CAP-B014 / T030 / T031 / T032 / T033 / T034 registered | verify-traceability | `make check` traceability green (2026-10-30) | ✓ |
| Hub image bundles docker CLI (closes mid-Phase-3 gap) | compose-tier test | `test_hub_image_bundles_docker_cli` | ✓ |
| CAP-B003 description refreshed (BC-004 → BC-009 reference) | verify-traceability | `CAPABILITIES.md § CAP-B003` says "default on in BC-009" | ✓ |

(Rows flip to ✓ as evidence lands. Live row is the gating one.)

## Merge plan

1. Phase 1: plan + red unit tests
2. Phase 2: implement three terminal states; flip default; refresh CAP-B003
3. Phase 3: live tap session (user + Claude) to confirm one of the three outcomes
4. `make check-full` passes; Shipping evidence rows all ✓
5. Flip story → `shipped`; fast-forward merge to dev

## Rollout plan

Additive but behavior-changing. Operators should know:
- Default behavior changed from stub to real fix execution
- `BEACONRY_FIX_ENABLED=0` to opt out
- Operator must `docker compose up -d` (or equivalent) to actually run the new image — beaconry only pulls

README's "Fix now behavior" section covers this.

## Testing strategy

**Unit** (fast tier):
- Mock DockerAdapter.execute to return success/failure
- Mock verify_fix to return True/False
- Four combinations tested; three terminal states + one opt-out stub

**Live** (Phase 3):
- Clear DB; `make demo` with fresh nginx:1.24 scan
- Operator taps 🔧 Fix now on one of the 5 messages
- Expect: message walks through "⏳ Fixing" → "🔍 Verifying" → terminal state
- Which terminal state depends on whether nginx:latest has patched the specific CVE
- All three terminal states are legitimate; we document which fired

## Testing commands

```bash
source .venv/bin/activate
make check                       # unit tier
pytest poc/tests/unit/test_hub_helpers.py -k "fix_now" -v   # specific tests
PATH=".beaconry-cache/bin:$PATH" python -u -m poc.demo      # live tap session
```
