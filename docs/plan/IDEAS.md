# Ideas backlog

Running list of ideas and discussions that may become future BC-NNN stories.

**Role in the development workflow:** Claude consults this file at the start of any planning conversation and before proposing new stories — the user has already thought about many of these; don't re-discover them. New ideas (from the user, from virtual-user panels, from debugging sessions) get appended here immediately with their trigger context so the thread isn't lost.

**Format per idea:**
- Short bullet, one-line description
- `trigger:` where/when it came up (session date + context)
- `priority:` `now` (next 1–2 stories) / `soon` (next 3–6) / `later` / `parked` (likely won't do)
- `links:` story IDs this would depend on or supersede

---

## Doc staleness noted (fix opportunistically)

- **CAP-T026 description claims automation is deferred, but CAP-T027 shipped it**
  trigger: BC-008 audit 2026-10-30. CAP-T026 says "Template-based for now; automated enforcement is a future follow-up". CAP-T027 then landed exactly that automation (parse Shipping evidence tables, fail gate on placeholder/non-✓).
  priority: later
  Update CAP-T026 to reference CAP-T027 and remove the "future follow-up" note. Fold into the next change that touches CAPABILITIES.md near those entries — not worth a dedicated branch.

---

## Process lessons learned

- **Live-smoke required before marking a BC story shipped if it changes a container runtime**
  trigger: BC-005 shipped 2026-10-29, live `make demo` run 2026-10-30 immediately surfaced two in-container regressions (token leak via httpx logs, no cap on /scan endpoint). Unit tests and compose-config tests couldn't catch either because they didn't exercise the actual hub.py entry point under docker runtime.
  priority: now
  Rule candidate: any story that modifies code reached when `docker compose up` runs needs a manual live-smoke step before `status: shipped`. Add to DD-TGD step 6 or create a step 6a.

- **"Hypocrisy" audit: new BC story must apply its own template conventions**
  trigger: BC-004 and BC-005 both shipped missing their own "Capabilities delivered" section updates when fix branches added new CAPs.
  priority: later
  Rule candidate: fix branches that add capabilities must update the originating story's Capabilities-delivered section in the same commit.

---

## BC-002 open gaps (not covered by BC-003 draft)

- **Terminal does not mirror the full notification content**
  trigger: BC-002 audit 2026-10-29
  priority: later
  Real-mode terminal prints a one-line "→ New finding …" summary. Gherkin says "terminal mirrors the notification content" — ambiguous but weak. Options: render the HTML to a stdout box like the mock path does, or re-word the Gherkin to "terminal summarizes."

- **Grype DB cold-vs-warm cache not explicitly verified**
  trigger: BC-002 audit 2026-10-29
  priority: later
  `.beaconry-cache/grype-db/` exists after a run; nothing proves second runs skip the ~200 MB download. Add either a bash smoke script that stats the dir before/after, or a verify step in the real_tools integration test.

- **MIN_CVSS=9.0 default (real mode) not unit-tested**
  trigger: BC-002 audit 2026-10-29
  priority: later
  demo.py sets `os.environ.setdefault("MIN_CVSS", "9.0")` for real mode. No test asserts this. Low-risk but a one-line regression could silently un-raise the floor.

- **Group-by-package UX redundancy**
  trigger: BC-002 real-pipeline test 2026-10-29 — libexpat1 appeared in two consecutive messages for CVE-2024-45492 + CVE-2024-45491
  priority: later
  Correct behavior (two different CVEs → two notifications) but the operator reads the same package name twice in a row. Candidate: secondary sort to interleave packages, or a "group by package" digest flavor.

- **Integration test with `real_tools` marker never actually run**
  trigger: BC-002 audit 2026-10-29
  priority: moved to BC-003 (covered there)

---

## Interactive behavior / UX

- ~~**Per-run finding cap + digest mode**~~ **Landed in BC-002 fix branch 2026-10-29**
  `BEACONRY_MAX_FINDINGS_PER_RUN=5` default, top-N by (tier, -cvss), one HTML digest for the rest, CVE-id dedup across packages. Keep as a parked follow-up: *group by package too* (two different CVEs on libexpat1 both surface — reasonable but produces repeat-package UX).

- **Button taps actually work live during the demo**
  trigger: user asked 2026-10-29 "which buttons work?" after tapping Fix-now outside the demo window
  priority: soon
  BC-002 shipped the handlers but a one-shot `make demo` exits after findings; buttons only route while the hold loop is active. Options: (a) daemonize beaconry-dev for hands-off testing, (b) extend hold loop timeout, (c) persistent `beaconry serve` command.

- **Clear-DB / reset ergonomics**
  trigger: BC-002 testing — dedup made second demo run near-silent, no obvious way to reset
  priority: later
  `make demo-reset` or `beaconry reset` that nukes `.beaconry-cache/beaconry.db` plus a confirm prompt.

---

## Trust / safety / security

- ~~**DockerAdapter hardcodes `:latest` — potential major-version jump foot-gun**~~ **Landed in BC-014 2026-10-30 as "same-track pull" (strategy 1 from this entry).** Preserved for history below; strategies 2–4 remain future refinements if operators ask.

- **DockerAdapter hardcodes `:latest` — potential major-version jump foot-gun (historical, pre-BC-014)**
  trigger: user 2026-10-30 during BC-009 Phase 3 tap session. For `postgres:16-alpine` tapping Fix would pull `postgres:latest` = currently postgres 18 (two major jumps, risks DB-engine incompatibility + data corruption). Got lucky on nginx-proxy where `:latest` was a benign patch bump.
  priority: now
  Current code: `new_image = f"{base}:latest"` in `poc/hub/executor/docker_adapter.py`. Candidate approaches: (1) **same-track pull** — preserve the suffix (`postgres:16-alpine` → next `:16.X-alpine` tag), likely by querying Docker Hub registry API for tags matching a pattern; (2) **operator-approved diff** — show "16.3-alpine → 16.9-alpine, these packages change: …" in Telegram before pulling; (3) **grype-fixed-version mapping** — grype returns `fixed_version`, map it back to image tag (not 1:1 for base images); (4) **operator-configured tag policy** per image (`BEACONRY_TAG_POLICY=postgres:16-alpine,nginx:1.24-alpine`). Default pick: #1 (minimal surprise, preserves variant). Interacts with "KEV auto-fix is scary" (digest pinning) and "Rollback as a first-class ExecutorPlugin method".
  links: BC-009 (hardcoded `:latest` shipped there), KEV-auto-fix entry below.

- ~~**BC-011 — Target container health check waterfall (post-fix verification)**~~ **Landed as "operator-triggered probe" MVP in BC-011 2026-10-30.** Three probe types shipped (tcp, http, docker_healthcheck); waterfall levels 3 (systemd) + 4 (custom command) deferred to a future refinement if operators ask. Preserved for history below.

- **BC-011 — Target container health check waterfall (post-fix verification) (historical scope — see shipped BC-011 for actual MVP)**
  trigger: user 2026-10-30 health-check discussion. Design-decisions.md already specifies the four-level waterfall for the containers *beaconry restarts* (Docker HEALTHCHECK → HTTP probe → systemd stability window → operator-defined). BC-009 shipped `verify_fix` using only `syft + grype` to re-check CVE presence — strong for "is the CVE gone" but silent on "did my service come back up cleanly". A target container that pulled `:latest`, started, but immediately crashlooped would currently flip to `fixed` in beaconry's DB.
  priority: soon
  Scope sketch: per-service `required_health_level` config (0 = no check, 1-4 as the waterfall), auto-detect by probing `/readyz` → `/healthz` → `ss -tlnp`-discovered HTTP → `systemctl is-active`. If chosen level fails post-pull, flip finding to `verification-failed` and emit "⚠️ Pulled but health check failed — rollback with `docker tag <base>:rollback <base>:latest`". Depends on BC-010 landing the beaconry-side convention first (so target probing and our own probing share vocabulary). Also unlocks the "always confirm" relaxation for KEV auto-fix below (silent auto-fix only if level ≥ 3 passes).
  links: BC-009 (verify_fix today), BC-010 (vocabulary), KEV-auto-fix entry below (dependency), "Rollback as first-class ExecutorPlugin method" entry (needs the state-machine extension `verification-failed`).

- **KEV auto-fix is scary (even "safe class")**
  trigger: virtual-user panel 2026-10-29 (security-conscious operator persona)
  priority: soon
  Current design: Tier 0 KEV + container = auto-fix with no operator confirmation. Pulling `:latest` is unversioned and a supply-chain-attack vector. Candidate change: always require operator confirmation (even for KEV), OR pin to specific SHA256 digests proposed in the notification.

- **Append-only audit log of remediation actions**
  trigger: virtual-user panel 2026-10-29 (compliance / "I've been bitten" sysadmin)
  priority: soon
  When auto-patches run at 3am, operator needs provenance for review. Append-only JSON log of every action (notification sent, button tapped, docker pull, verify result) with timestamps + initiator.

- **Hub docker socket attack surface**
  trigger: virtual-user panel 2026-10-29
  priority: later
  Hub has `/var/run/docker.sock:rw`. If hub ever gets RCE through the Telegram callback, attacker owns the host. Mitigation ideas: proxy socket through a least-privilege helper (whitelist `pull`, `tag`), or separate executor process with narrower perms.

- **Rollback as a first-class ExecutorPlugin method**
  trigger: virtual-user panel 2026-10-29 (contributor voice)
  priority: later
  `ExecutorPlugin` has `can_handle` + `execute` + `describe` but no `undo`. BC-003's fix execution should land this: `undo(finding, execution_record) -> ExecutionResult`.

---

## Scope / scaling

- **Real pipeline against operator's actual stack (multi-image)**
  trigger: virtual-user panel 2026-10-29 ("where's step 2 after `make demo`?")
  priority: now
  BC-003 or BC-004 candidate: point scout at the host's own Docker, scan every image, deduplicate across images. Still no auto-fix (unless combined with "always confirm" from safety section).

- **Compose POC validation**
  trigger: BC-002 plan 2026-10-29 — the existing `docker compose up` path was built but never validated end-to-end
  priority: soon
  Dedicated story to run the Compose stack, hunt bugs, make `docker compose up` the OSS-user default. Deferred from BC-002.

- **ARM / Raspberry Pi support + resource budget**
  trigger: virtual-user panel 2026-10-29 (Pi hobbyist persona)
  priority: later
  Verify ARM64 install paths for syft/grype; measure memory + scan time on Pi 4; set scan concurrency ceiling.

---

## Development / tooling

- **BC-012 — Shared `poc/beaconry_health/` package (centralize the four-endpoint convention)**
  trigger: user 2026-10-30 after BC-010 shipped — `find . -name health.py` returned only `scout/health.py`, surfacing the asymmetry that hub's endpoints are inline in `hub.py` while scout's writers live in `scout/health.py`. One convention, two shapes. A third container would reinvent either shape.
  priority: now
  Scope sketch:
    - `poc/beaconry_health/` package with: `endpoints.py` (FastAPI router factory `build_router(checks_provider, startup_flag_getter)`), `sentinels.py` (`SentinelWriter` class — current scout/health.py generalized), `checks.py` (reusable probes: `check_db`, `check_grype_db`, `check_binary`, `check_http`), `trap.py` (typed `LivezProvider` / `ReadyzProvider` stubs so the restart-loop trap is syntactically unreachable, not just memory-enforced).
    - Migrate hub endpoints to `api.include_router(build_router(hub_checks))` preserving current behavior; existing `test_hub_health.py` must pass unchanged.
    - Scout: `writer = SentinelWriter(DATA_DIR, checks=scout_checks)`; `scout/health.py` becomes a thin config module naming the scout-specific checks.
    - Each container Dockerfile adds `COPY beaconry_health/ ./beaconry_health/` next to its own entry file. Needs a lint/test rule guarding this ("every container Dockerfile that serves health must COPY the package") to catch forgetfulness at merge time, not at container-start time.
    - Update `memory/health-checks.md § Checklist` to lead with "Step 1: `COPY beaconry_health/` in your Dockerfile" instead of "implement the four endpoints".
  **The key win**: the restart-loop trap moves from "memory rule enforced by vigilance" to "typed interface — passing a DB-dependent probe to the `/livez` slot is a type error". That's the 2026-era shift from docs-as-guardrail to compiler-as-guardrail.
  links: BC-010 (the asymmetry this fixes), BC-011 (target-container waterfall should consume the same shared probes), lours.me sidecar pattern (parked — overkill for POC).

- **BC-013 — Scout live-probe HTTP endpoints (end the pull-vs-snapshot asymmetry)**
  trigger: user 2026-10-30 after BC-010 shipped — observed that `make status` queries hub via live HTTP (`/healthz`) but reads scout from a snapshot file (`scout.healthz.json`). Current staleness guard is `scout.live` mtime vs `SCOUT_LIVE_MAX_AGE_S` (300s), so a dead scout is caught — but only within 5 minutes. Within that window, "scanning happily" and "SIGKILL'd 2 minutes ago" are indistinguishable from the host.
  priority: soon  (BC-012 is a prerequisite — do that first and this becomes ~10 lines)
  Scope sketch:
    - Promote scout from file-sentinel to HTTP probe. Scout runs a tiny asyncio HTTP server (stdlib `http.server` or aiohttp) on an internal-only port, exposing `/livez /readyz /startupz /healthz` with the same shape hub does.
    - `make status` queries both services identically — one code path, one timeout, one failure mode. Delete the `_fetch_scout_body_via_docker` branch and the `SCOUT_LIVE_MAX_AGE_S` env knob (both become unnecessary once scout answers live).
    - Keep file-sentinel writers as a fallback readable by compose healthcheck (compose healthcheck can still `test -f /data/scout.ready && stat scout.live` — don't need to break that).
    - Scout Dockerfile: expose internal port (not published to host, only in Compose network).
    - Compose: `make status`'s scout URL defaults to `http://scout:8001/healthz` when run inside the network; from host, needs port-forward or `docker exec curl` (document the tradeoff).
  **Expected wins**:
    - Scout crash detection latency: 5min → ~1s
    - Deadlock detection: no → yes (HTTP won't respond even if scout.live is current)
    - "Slow scan >5min" false-positive degraded: eliminated
    - Code uniformity: parallels hub tests shape; same `build_router()` factory from BC-012
  **Depends on**: BC-012 (shared `beaconry_health/` package) — without the shared factory this is a bespoke 80-line scout HTTP server; after BC-012 it's `include_router(build_router(scout_checks))`.
  links: BC-010 (the asymmetry this closes), BC-012 (prerequisite), BC-011 (the target-container waterfall will also want HTTP probes — same shape).

- **DI refactor to replace env-flag mocking**
  trigger: BC-001 plan 2026-10-29 — explicit "pragmatic debt" called out
  priority: later
  BEACONRY_MOCK=1 branches sprinkled across hub.py, docker_adapter.py, demo.py. Clean approach: inject dependencies (syft runner, grype runner, Telegram sender, Docker client) through constructor so tests pass fakes. Hard to retrofit; cheaper to do before the surface grows.

- **Configurable urgency tier thresholds**
  trigger: virtual-user panel 2026-10-29 (OSS evaluator)
  priority: later
  Current thresholds (KEV / CVSS≥9 internet / CVSS 7–8 / <7) are hardcoded. Expose via config file so operators can disagree without forking.

- **Observability: structured logs + Prometheus metrics**
  trigger: virtual-user panel 2026-10-29 (contributor voice)
  priority: later
  Right now `log.info` with `logging.basicConfig`. For OSS release: JSON-structured logs, `/metrics` endpoint, basic SLIs (scan duration, findings count, Telegram latency).

- **State machine for Finding status**
  trigger: virtual-user panel 2026-10-29
  priority: later
  Current statuses: `open / fixed / snoozed / accepted`. Missing: `fixing` (in-flight), `verification-failed`, `rolled-back`, `superseded`. Formal state machine with allowed transitions.

- **Per-finding dry-run mode in real mode**
  trigger: virtual-user panel 2026-10-29 (OSS evaluator — "no --dry-run")
  priority: later
  `make demo ARGS=--dry-run` previews what would be fixed without executing. Candidate for BC-003 alongside real fix execution.

- **`--chat` override for dev-vs-prod Telegram chats**
  trigger: BC-002 virtual-user iteration 2026-10-29
  priority: later
  Operators running the demo during dev would appreciate a `BEACONRY_DEV_CHAT_ID` fallback so prod notifications don't fire from a dev iteration.

---

## Decisions parked for review

See per-decision review dates in `memory/`:
- 2026-11-05 — syft+grype install location (currently in-repo `.beaconry-cache/bin/`)
- 2026-11-28 — Quality bar (currently baseline library)
- 2026-11-28 — Container coverage V1 scope (currently base image + explicit deps only)

---

- **Lint: every hub-local .py is COPY'd by the hub Dockerfile**
  trigger: BC-020 live-smoke 2026-10-31 — `kev.py` and `urgency.py` landed in `poc/hub/` and were imported by hub.py, but the Dockerfile's explicit `COPY hub/hub.py hub/waterfall.py ./` line didn't carry them. `make check` was green; only `make test-e2e` surfaced the `ModuleNotFoundError`. Same class of regression as BC-012 (which solved it for `beaconry_health/` specifically via `verify-dockerfile-health-copy`).
  priority: soon
  Scope sketch: a `make verify-hub-sources-coped` lint that enumerates `poc/hub/*.py` and asserts every file (except `__init__.py` and sibling test files) is present in a `COPY hub/*.py` line in `poc/hub/Dockerfile`. Wire into `make check`.
  Alternative: switch to `COPY hub/*.py ./` (glob) and lose the explicit list — simpler, but loses the intent-visible signal of "exactly these files ship."
  links: BC-012 (prior art — same class of guard, different directory), BC-019 (same "move vigilance to lint" ethos).

- **e2e DB fixtures: use hub.init_db() instead of raw CREATE TABLE**
  trigger: BC-020 live-smoke 2026-10-31 — the BC-011 e2e test seeds findings via positional `INSERT INTO findings VALUES (?,?,...)` inside a `docker exec` snippet. BC-020 added a column (`kev`) → the snippet broke silently (`table has 15 columns but 14 values were supplied`). The fixture duplicates the schema; schema changes diverge both copies.
  priority: later
  Scope sketch: refactor the e2e `_seed_finding` helper to shell out to `hub.save_finding()` instead of raw SQL. Requires passing a Finding dict/JSON across `docker exec` — slightly heavier, but any schema change going forward is automatically picked up.
  links: BC-011 (test file), BC-020 (regression surface).

## Parking lot (ideas we discussed but likely won't do)

- **`make demo` works with host-run telegram-mock (BC-018 Scenario 7's literal recipe)**
  parked: 2026-10-31 after reviewing the two existing demo modes. (Originally queued as BC-020 before the KEV-ingest story claimed that number.)
  Why parked: the dividend overlaps `BEACONRY_MOCK=1 make demo` (already stubs Telegram with no real token needed) and `make test-e2e` (already exercises real python-telegram-bot against telegram-mock end-to-end). The marginal slice BC-020 would add — real PTB library hitting the mock *through the `make demo` entrypoint* — has no second real-world use case not already covered. Compose plumbing (env propagation + `host.docker.internal` `extra_hosts` for Linux) adds complexity nobody will reach for.
  Original scope sketch (if revived): add `BEACONRY_TELEGRAM_BASE_URL` + `BEACONRY_SKIP_GRYPE_DB` to `docker-compose.yml` hub env, drop both from the test-only allowlist, add `extra_hosts: - "host.docker.internal:host-gateway"`, document the recipe in README.
  Links: BC-018 (origin), CAP-T056 (behavior proven elsewhere), `docs/plan/BC-018-close-gap-unit-coverage-plan.md § Scenario 7 live-verification — DEFERRED`.
