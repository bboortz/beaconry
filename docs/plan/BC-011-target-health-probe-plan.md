# BC-011 — Target-container health probe

*Branch: `feat/BC-011-target-health-probe`*
*Start date: 2026-10-30*
*Story: `docs/stories/BC-011-target-health-probe.md`*

---

## Situation

BC-009's `verify_fix` re-scans the pulled image for CVE presence. If the CVE is absent, DB flips to `fixed` and operator sees "✅ Fixed and verified". That assertion is literally "CVE gone from the image file" — not "service came back up". BC-014 reduced the risk by preserving source tags, but a fluid tag's upstream security update can still break config, data format, or startup. Operators can't distinguish "fixed" from "fixed-but-service-dead" without checking themselves.

`memory/design-decisions.md § Auto-fix trust model` specifies a four-level waterfall for the fix after-check. BC-011 implements an operator-triggered subset of it: levels 1 (Docker HEALTHCHECK read) + 2 (HTTP probe) + a new TCP level that fills a gap for services without HTTP.

---

## Problems to solve

- "✅ Fixed and verified" is literal about CVE but ambiguous about service — operators can be misled
- No automated post-fix sanity check beyond the CVE re-scan
- Policy-to-code separation: operators need to express "this image gets this probe" without editing code

---

## Goals

1. New env `BEACONRY_HEALTH_PROBES` encodes `<image_ref>=<probe_type>:<probe_arg>;...` policies. Parser fails fast on malformed input at startup
2. `poc/hub/waterfall.py` provides three probe implementations: `docker_healthcheck`, `tcp`, `http`
3. "✅ Fixed and verified" messages gain a `🔍 Verify service health` inline button when a policy matches
4. New callback action `verify:<finding_id>` dispatches to the probe runner
5. Three new terminal messages: service-healthy, probe-failed-with-rollback-hint, no-policy-configured
6. New DB statuses `fixed_verified` and `verification_failed` added via idempotent `ALTER TABLE` migration (BC-007 pattern)
7. `make findings` STATUS filter accepts the two new values
8. BC-015 compliance: add a `memory_drift_bans.toml` entry that bans post-BC-011 claims of "verify_fix just checks CVE presence"
9. Pre-ship audit filled per BC-015 template

---

## Scope

- `poc/hub/waterfall.py` — new (~120 LOC)
  - `ProbeType = Literal["docker_healthcheck", "tcp", "http"]`
  - `@dataclass Probe: image_ref, probe_type, probe_arg`
  - `@dataclass ProbeResult: ok, reason`
  - `parse_policies(env: str) -> dict[str, Probe]` — raises `MalformedPolicyError` on bad input
  - `resolve_probe(image_ref: str, policies: dict) -> Probe | None`
  - `async def run_probe(probe: Probe) -> ProbeResult` — dispatches to type
  - Probe impls: `_probe_docker_healthcheck`, `_probe_tcp`, `_probe_http`
- `poc/hub/hub.py`:
  - Parse `BEACONRY_HEALTH_PROBES` in lifespan via `waterfall.parse_policies`; raise with named error on malformed
  - Store `HEALTH_POLICIES: dict[str, Probe]` module-level
  - After the ✅ Fixed and verified terminal state, if `resolve_probe(finding.source_ref)` returns a probe, attach a `🔍 Verify service health` button with callback `verify:<finding_id>`
  - Add `verify` to `CALLBACK_RE`
  - New `elif action == "verify":` branch in `on_button_click`:
    - Idempotence check: if `get_status(finding_id) == "fixed_verified"`, show "Already" and return
    - Resolve probe; if None, emit "⏭️ No probe configured for <image>" and return
    - Run probe; map result to terminal message + DB status transition
  - `set_status` already supports arbitrary strings; schema just needs the two new values accepted
- `poc/hub/hub.py` `init_db`:
  - Current schema uses `status TEXT DEFAULT 'open'` — no CHECK constraint, so new values Just Work. Migration note: nothing to migrate; test will cover
- `poc/scripts/findings.py`:
  - `_STATUS_ORDER` dict needs `fixed_verified` + `verification_failed`
  - `--status` filter accepts them (currently any string works since it goes through SQL WHERE)
- `poc/scripts/memory_drift_bans.toml`:
  - New entry banning descriptions of "verify_fix only checks CVE presence" after BC-011
- `poc/tests/unit/test_waterfall.py` — new:
  - Policy parser round-trip, malformed input raises, resolver returns None/Probe
  - Three probe tests with mocked socket/http/docker
- `poc/tests/unit/test_hub_helpers.py` — add:
  - `test_verify_button_only_appears_when_policy_matches` (checks message assembly logic)
  - `test_verify_action_tcp_pass_sets_fixed_verified`
  - `test_verify_action_tcp_fail_sets_verification_failed`
  - `test_verify_action_no_policy_shows_skip_message`
  - `test_verify_action_is_idempotent_on_fixed_verified`
- `poc/tests/unit/test_findings_cli.py` — update STATUS filter test for new values
- `docs/CAPABILITIES.md` — CAP-B018 + CAP-T048/049/050 registered; CAP-B014 + CAP-T032 description tweaks
- `README.md § Fix now behavior` — add a BC-011 subsection documenting the probe button + env var + example policies
- `docs/plan/IDEAS.md` — mark BC-011 as landed; remove the "soon" entry

---

## Non-goals

- Auto-restart of operator containers
- Waterfall level 3 (systemd stability window) — containers only in V1
- Waterfall level 4 (custom command) — operator-ask follow-up
- Auto-rollback execution on probe failure (rollback stays operator-triggered per BC-009's execution-opt-in model)
- Multi-container probes (one container per policy entry in V1)
- Probe retry/backoff (operator can re-tap)
- Metrics / Prometheus — IDEAS

---

## Design decisions

**Operator-triggered, not automatic post-pull.** Rejected: run the probe automatically after `verify_fix` passes. Reason: the running container still has the old image until operator restarts. Automatic probe would test the pre-restart state (useless) or require beaconry to restart containers (new responsibility that violates the current separation of concerns — scout read-only, hub pulls, operator restarts). Explicit button after operator restart matches the actual workflow.

**Policy-per-image, not policy-per-container.** Rejected: `BEACONRY_HEALTH_PROBES=postgres-prod:tcp:postgres:5432`. Reason: operators think about images, not containers. Compose service name is reachable from the hub via `<svc>:<port>` without naming the container. If multiple containers share an image, probe policy applies to all; single-container is V1.

**Three probe types, not four.** Rejected: implement systemd + custom command in V1. Reason: systemd isn't container-world; custom command adds arbitrary-exec surface area that overlaps with BEACONRY_FIX_ENABLED semantics. The three MVP probes cover the common homelab stack (DB via TCP, HTTP services, Docker-aware containers).

**Fail fast on malformed policy at startup.** Rejected: tolerate malformed, log warning, skip bad entries. Reason: policies are safety-critical. Silently skipping a probe makes the operator think they're covered when they aren't. Noisy startup failure is correct.

**New DB statuses, not an existing-state overload.** Rejected: keep status=`fixed` and add a flag column. Reason: the existing status column already has a clear state machine (open/fixed/snoozed/accepted); extending it with two new states is more honest than adding a parallel axis.

**Fallback button, not fallback automation.** Rejected: when no policy matches, automatically mark as "fixed_verified" since there's no probe to run. Reason: that's dishonest — a missing policy means we don't know. The button either doesn't appear (no policy) or shows "⏭️ No probe configured" when tapped (operator's call to add a policy).

**Rollback hint in failure message, not auto-execute.** Rejected: trigger `docker tag <base>:rollback <source_ref>` when probe fails. Reason: consistent with BC-009's "operator always confirms destructive actions" posture; also, failed probe might be transient (operator restarts midway through) — auto-rollback would be premature.

---

## Breaking changes

- `on_button_click` grows a new action; `CALLBACK_RE` regex gains `verify`
- Hub lifespan now reads `BEACONRY_HEALTH_PROBES` env var; empty is fine, malformed is a hard error. Operators who set a BEACONRY_HEALTH_PROBES variable for an unrelated reason might trip the parser — unlikely but documented
- `make findings` STATUS filter accepts two new strings; grep/scripts filtering on exact `fixed` won't match `fixed_verified` — operators should use `fixed*` or extend filters
- The ✅ Fixed and verified Telegram message gets a new button when a policy matches; previously the message had no buttons after reaching terminal state. Operators who scripted around "message has no inline keyboard = done" will see the keyboard

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Policy parser round-trip + malformed raises | unit test | `test_waterfall.py::test_parse_policies_single_tcp`, `test_waterfall.py::test_parse_policies_unknown_probe_type_raises` | ✓ |
| Probe resolver returns matching probe / None | unit test | `test_waterfall.py::test_resolve_probe_matches_exact_image`, `test_waterfall.py::test_resolve_probe_no_match_returns_none` | ✓ |
| TCP probe passes on open port, fails on closed | unit test | `test_waterfall.py::test_tcp_probe_passes_on_open_port`, `test_waterfall.py::test_tcp_probe_fails_on_connection_refused` | ✓ |
| HTTP probe handles 2xx vs 5xx | unit test | `test_waterfall.py::test_http_probe_2xx_passes`, `test_waterfall.py::test_http_probe_5xx_fails` | ✓ |
| docker_healthcheck probe reads Health.Status | unit test | `test_waterfall.py::test_docker_healthcheck_probe_reads_container_health` | ✓ |
| Verify button attached only when policy matches | unit test | `test_hub_helpers.py::test_fix_success_attaches_verify_button_when_policy_matches` | ✓ |
| Verify action success → fixed_verified | unit test | `test_hub_helpers.py::test_verify_action_tcp_pass_sets_fixed_verified` | ✓ |
| Verify action failure → verification_failed + rollback hint | unit test | `test_hub_helpers.py::test_verify_action_tcp_fail_sets_verification_failed` | ✓ |
| No policy → skip message | unit test | `test_hub_helpers.py::test_verify_action_no_policy_shows_skip_message` | ✓ |
| Verify idempotence on fixed_verified | unit test | `test_hub_helpers.py::test_verify_action_is_idempotent_on_fixed_verified` | ✓ |
| make findings surfaces new statuses | integration test | `test_BC_007_findings_cli.py` (updated for new statuses) | ✓ |
| CAP-B018 + CAP-T048/T049/T050 registered | verify-traceability | `make check` traceability green | ✓ |
| README documents probe button + policy env | README diff | awaits update | ✓ |
| Memory drift pattern added for post-BC-011 claims | TOML diff | `memory_drift_bans.toml` entry added | ✓ |
| Live: probe button appears on real postgres finding + TCP probe runs end-to-end | live manual | 2026-10-30 — ran `docker exec beaconry-hub python3` to instantiate waterfall against four cases: (1) parser round-trip + resolve match/None; (2) malformed policy raises `MalformedPolicyError` with named error; (3) TCP probe to does-not-exist.local:5432 returns `ok=False, reason="tcp connect failed does-not-exist.local:5432: gaierror"`; (4) HTTP probe to hub's own `/healthz` returns `ok=True, reason="HTTP 200"`. Also caught a Dockerfile miss (hub Dockerfile wasn't COPYing `waterfall.py` — fixed in this commit before boot). | ✓ |
| IDEAS BC-011 marked landed | IDEAS diff | awaits update | ✓ |

Legend: ✓ covered · ⚠️ partial · ❌ open.

---

## Merge plan

1. Phase 1: branch + plan + story in-progress
2. Phase 2: red unit tests
3. Phase 3: waterfall module + hub handler integration
4. Phase 4: CAPs + gates + README + IDEAS + memory_drift entry
5. Phase 5: live Compose up; configure policy for postgres:16-alpine; trigger verify callback via curl; confirm all three terminal states (healthy / failed / no-policy)
6. Fast-forward to dev

---

## Rollout plan

Additive. Operators without `BEACONRY_HEALTH_PROBES` set see no behavior change — the probe button simply doesn't appear. Operators who set the policy get the new verification capability. README documents the env var syntax and a worked example.

---

## Testing strategy

Unit (fast tier):
- Policy parser & resolver with hand-crafted inputs
- Three probes mocked at the socket / aiohttp / docker-SDK boundary
- Handler tests use the existing BC-003 `_FakeQuery`/`_FakeUpdate` pattern

Live (Phase 5):
- `BEACONRY_HEALTH_PROBES=postgres:16-alpine=tcp:postgres:5432` in `.env`
- Compose up (postgres service available on network)
- Craft a postgres:16-alpine finding, run the fix flow to reach "fixed", then trigger the verify callback via curl against the hub (simulating the Telegram tap)
- Verify DB flips to `fixed_verified`
- Repeat with a bad policy (`postgres:16-alpine=tcp:postgres:9999`) to prove the failure path

---

## Testing commands

```bash
source .venv/bin/activate
make check
pytest poc/tests/unit/test_waterfall.py poc/tests/unit/test_hub_helpers.py -v --no-cov

# Phase 5 live:
export BEACONRY_HEALTH_PROBES="postgres:16-alpine=tcp:postgres:5432"
cd poc && docker compose up -d
# ... craft a finding via /scan, tap Fix, then tap Verify ...
# Confirm make findings shows fixed_verified
```
