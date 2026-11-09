# Changelog

All notable changes to beaconry are recorded here. The format is based
on [Keep a Changelog](https://keepachangelog.com/), and this project
follows [Semantic Versioning](https://semver.org/).

## [v0.20.1] — 2026-11-09

### Added (BC-108)

- **Docker events watchdog.** Scout's `docker.events()` subscription
  is now wrapped in a `while True` reconnection loop with capped
  exponential backoff (`[1s, 60s]` defaults). Pre-BC-108, a daemon
  restart or socket tear ended the single-shot consume thread and
  event-driven scans silently stopped until scout process restart.
  Scout now reconnects within 1s of daemon recovery on the common
  case; prolonged outages still resume within ≤60s.
- `/healthz` exposes `docker_events_reconnects` (cumulative) +
  `docker_events_last_reconnect_ts` so operators see the drift at
  a glance — steady growth signals daemon-health issues.
- Two new env vars: `BEACONRY_DOCKER_EVENTS_BACKOFF_MIN_S` + `_MAX_S`.
- **CAP-T210** registered.

## [v0.20.0] — 2026-11-09

### Added (BC-107)

- **Scout POST retry queue.** Scout gets its first persistent file
  at `/data/scout-state.db` (configurable via
  `BEACONRY_SCOUT_STATE_DB`) with a `pending_scans` table. When
  the `/scan` POST to hub fails (503, timeout, connection refused),
  the SBOM payload is persisted and a background sweep replays it
  with exponential backoff (`BASE_S × 2^attempts`:
  30→60→120→240→480s by default). Success → DELETE + `scan_delivered`
  log; beyond `MAX_ATTEMPTS` (default 5) → DELETE + `scan_abandoned`
  log. Mirror of BC-104's hub-side `pending_notifications` — both
  halves of the scout ↔ hub wire now survive transient outages
  without silent data loss. State survives scout restart.
- `/healthz` surfaces `pending_scans_queue_depth` so operators
  spot a growing backlog at a glance.
- Three new env vars: `BEACONRY_SCOUT_POST_RETRY_MAX_ATTEMPTS`,
  `BEACONRY_SCOUT_POST_RETRY_BASE_S`,
  `BEACONRY_SCOUT_POST_RETRY_SWEEP_INTERVAL_S`.
- **CAP-T209** registered.

## [v0.19.2] — 2026-11-09

### Added (BC-106)

- **Scout-reported inventory completeness.** Scout now includes
  `images_expected`, `images_collected`, and `scan_complete`
  in every /scan POST. Hub reads them; when
  `collected / expected < BEACONRY_PARTIAL_SCAN_MIN_RATIO`
  (default 0.95), reconcile is skipped and
  `reconcile_skipped_partial_inventory` audit fires with
  `reason="bc_106_explicit_ratio"` and the explicit numbers.
  Strengthens BC-067's baseline heuristic (which remains as
  fallback for pre-BC-106 scouts).
- **CAP-T208** registered. New env `BEACONRY_PARTIAL_SCAN_MIN_RATIO`
  documented. `0.0` disables the explicit check; `1.0` demands a
  fully-successful scan.

## [v0.19.1] — 2026-11-09

### Added (BC-105)

- **Findings CLI lifecycle columns** — `make findings` now shows
  FIRST SEEN + LAST CHANGE (age-formatted) + the auto-close reason
  as a suffix on `auto_fixed` rows (`auto_fixed (stale-scout)`,
  `auto_fixed (post)`, `auto_fixed (operator)`). Operators no
  longer need to grep audit.log to see when a finding appeared,
  when it last changed state, or why it was auto-closed.
- Schema v9 → v10: `close_reason TEXT` + `last_status_change_ts
  INTEGER NOT NULL DEFAULT 0` on `findings`. Three write sites
  (BC-086 reconcile, BC-102 stale-scout reconcile, operator
  `set_status`) stamp them. Legacy rows render LAST CHANGE as
  `—`.
- **CAP-T207** registered.

## [v0.19.0] — 2026-11-09

### Added (BC-104)

- **Persistent notification retry queue** for Telegram deliveries.
  Pre-BC-104: a transient Telegram outage during `notify_*` dispatch
  emitted `notify_sent_failed` + the alert was LOST. Now every failed
  delivery persists to the new `pending_notifications` table
  (schema v8 → v9). Background `_notify_retry_loop_forever` sweep
  retries due rows with exponential backoff
  (`BASE_S × 2^attempt` — default 30→60→120→240→480s).
  Success → `notify_delivered` audit + row deleted. After
  `BEACONRY_NOTIFY_RETRY_MAX_ATTEMPTS` tries →
  `notify_abandoned` audit + row deleted. State survives hub
  restart. Three new env vars documented in VARIABLES.md.
- **CAP-T206** registered.

## [v0.18.1] — 2026-11-08

### Changed (BC-103)

- **Scout rescans on image/container removal events too.** The
  BC-093 docker events listener filter widened from
  `[pull, create, start]` to
  `[pull, create, start, destroy, die, delete]`. Operator runs
  `docker rmi X` → next scan fires inside the BC-093 debounce
  window (~10 s) instead of waiting up to `SCAN_INTERVAL` (300 s
  default). Combined with BC-086's threshold, auto-close latency
  for a removed image drops from up to 15 min to roughly
  `3 × SCAN_INTERVAL` with the first absent_count bump landing
  within seconds. Bulk removals still coalesce via the existing
  debounce — no change to the "compose down of 10 containers →
  1 scan" guarantee.
- **CAPABILITIES**: CAP-T205 registered; CAP-T192 description
  notes the widened filter.

## [v0.18.0] — 2026-11-08

### Added (BC-102)

- **Edge-triggered scout-offline Telegram alert.** When a scout stays
  silent for longer than
  `BEACONRY_SCOUT_OFFLINE_ALERT_THRESHOLD_S` (default 600 s / 10 min),
  hub sends exactly one `⚠️ Scout offline` message naming the host,
  outage duration, last-known health, and the auto-close timeline.
  State is stored in the new `scouts.last_offline_alert_ts` column
  (schema v7 → v8) so the alert survives hub restarts without
  re-firing.
- **Recovery alert.** When a previously-offline scout POSTs again,
  hub sends exactly one `✓ Scout back online` message with the
  outage duration + clears the alert stamp.
- **Stale-scout findings reconcile.** Opt-out via
  `BEACONRY_AUTO_CLOSE_STALE_SCOUT=0`. When enabled (default), the
  sweep bumps `absent_count` on offline-scout findings every tick
  (open / regressed / snoozed / accepted statuses). After
  `BEACONRY_AUTO_FIX_THRESHOLD` ticks the finding flips to
  `auto_fixed` and emits the distinct
  `finding_auto_fixed_stale_scout` audit event (separate from
  BC-086's `finding_auto_fixed` so forensics can filter).
- **Background task** `_scout_offline_sweep_loop_forever` registered
  in lifespan alongside the heartbeat + snooze loops. Cadence:
  `BEACONRY_SCOUT_OFFLINE_SWEEP_INTERVAL_S` (default 60 s).
- **Audit events**: `scout_offline`, `scout_online`,
  `finding_auto_fixed_stale_scout`.
- **Notifier interface extended** — abstract `send_scout_offline` +
  `send_scout_online` (default no-op so out-of-tree adapters keep
  booting); `TelegramAdapter` overrides both with concise HTML
  bodies, no inline keyboard (operator action is on the host).

### Changed (BC-102)

- **Schema version 7 → 8.** `scouts.last_offline_alert_ts INTEGER
  NOT NULL DEFAULT 0` added via idempotent PRAGMA-driven ALTER —
  same pattern BC-063/BC-092 used. Legacy rows get 0 (no false
  "already alerted" state).

### Fixes the class of bug behind the 2026-11-08 bb-82jq incident

Pre-BC-102: scout on bb-82jq exited silently; hub saw
`heartbeat_stale=True` in `/fleet` but took no action; 17 open + 7
regressed findings froze for 54 min until manual recovery.
Post-BC-102: same scenario fires a Telegram inside 10 min + closes
the findings inside ~13 min (`threshold + 3 × sweep_interval`).

### Documentation
- **story:** BC-102 scout-offline alerts + stale-scout reconcile
- CAP-B050 (operator-visible detection) + CAP-T204 (sweep task) registered.
- Three new env vars documented in `docs/VARIABLES.md` + `poc/.env.example`.

## [v0.17.2] — 2026-11-08

### Fixed (BC-101)

- **Dev-compose + operator-systemd container name collision.**
  2026-11-08 ~21:20 UTC incident: `docker rm -f beaconry-scout`
  during BC-099 cold-start troubleshooting killed the operator's
  systemd-wrapped production scout on bb-82jq. Root cause: both
  `poc/docker-compose.yml` (dev) and
  `poc/docker-compose.scout-only.yml` (operator/systemd) declared
  `container_name: beaconry-scout`. Operator scout stayed dark
  54 min without alert; 17 open + 7 regressed findings frozen on
  hetzner-side hub DB because BC-086 reconcile can't fire without
  scout POSTs.

### Changed (BC-101)

- **`poc/docker-compose.yml` container_name namespaced**:
  `beaconry-{hub,scout}` → `beaconry-dev-{hub,scout}`. Operator
  `docker-compose.scout-only.yml` keeps the bare `beaconry-scout`
  name (systemd/ansible expect this).
- **CLI script defaults track dev names**:
  `BEACONRY_HUB_CONTAINER` default `beaconry-hub` → `beaconry-dev-hub`
  (in `audit.py`, `findings.py`, `cli_smoke.py`). New
  **`BEACONRY_SCOUT_CONTAINER`** env (default `beaconry-dev-scout`)
  replaces the hardcoded `SCOUT_CONTAINER = "beaconry-scout"` in
  `scan_cli.py` + `cli_smoke.py`. Operators on homelab/systemd
  override via `.env` (back to `beaconry-{hub,scout}`).
- **Makefile** `live-rebuild` + `live-status` targets use the new
  dev names.
- **Docs**: `poc/.env.example` + `docs/VARIABLES.md` updated with
  the new default + `BEACONRY_SCOUT_CONTAINER` row. Memory files
  (`workflow.md`, `docker-standard.md`) refreshed where they named
  the dev containers explicitly.

### Unaffected

- `poc/docker-compose.scout-only.yml` + `docker-compose.cold-start.yml`
  + `docker-compose.test.yml` — operator and test-harness names
  already distinct or intentionally kept.
- `status.py` display labels — service-level table headers, not
  container names; they stay `beaconry-hub` / `beaconry-scout`.
- `poc/scripts/cleanup.py` — allowlist is by IMAGE name, unaffected.

CAP-T203 registered. Version 0.17.1 → 0.17.2.

## [v0.17.1] — 2026-11-08

### Changed (BC-100)

- **telegram_mock now on the forge-signed scratch base** — last
  beaconry-authored Dockerfile migrated off upstream `python:3.12-slim`
  onto the same BC-099 recipe hub + scout use: multi-stage with
  `forge-builder-trixie:latest` + `python-scratch-trixie:latest`,
  BuildKit syntax directive, named `runtime-src` stage,
  `RUN --mount=from=runtime-src` ABI guard with the
  `BC-099 ABI guard tripped` marker, Python-minor-agnostic
  `/stage/usr/local/lib/python${BUILDER_PY_VER}/site-packages` mv,
  `COPY --from=builder /stage /` (single-dir guarantee).
- **HEALTHCHECK baked in** — `["/hc", "-t", "3",
  "http://localhost:8080/readyz"]` — `docker inspect
  beaconry-test-telegram-mock:latest` now surfaces
  `.Config.Healthcheck.Test`.
- **CMD adapted** — `python3` prefix removed (scratch base bakes
  `ENTRYPOINT ["/usr/local/bin/python3"]`); CMD is the arg list
  passed to the baked interpreter.
- **mock_bot.py unchanged** — BC-012's `beaconry_health.build_router`
  already provides `/readyz` at port 8080.
- **CAPABILITIES**: CAP-T202 registered. CAP-T201 description
  extended to include telegram_mock in the covered-files list.

## [v0.17.0] — 2026-11-08

### Fixed (BC-099)

- **Build-time ABI guard against base-image Python-minor drift.**
  2026-11-08 ~17:26 UTC the shipped `beaconry-hub:latest` crashed on
  hetzner + server2 with `ModuleNotFoundError: No module named
  'uvicorn'`. Root cause: forge republished `forge-builder-trixie:latest`
  (Python 3.14) **five minutes** before `python-scratch-trixie:latest`
  (still Python 3.13) during FG-032's in-place Python bump, and
  forge's `beaconry-hub` build fired in the gap. The shipped image
  had wheels at `/usr/local/lib/python3.14/site-packages/` and a
  Python 3.13 runtime whose sys.path pointed at an empty
  `/usr/local/lib/python3.13/site-packages/`.
- **Guard mechanism**: both Dockerfiles gain a `# syntax=docker/dockerfile:1`
  directive (requires BuildKit) + a named `runtime-src` stage that
  references `python-scratch-trixie:latest`. The runtime stage now
  FROMs `runtime-src` (same resolved digest). BC-098's PY_VER step
  is merged into a single BC-099 RUN that mounts
  `--mount=from=runtime-src,source=/usr/local/lib,target=/runtime-lib`,
  asserts exactly one `python3.X` dir in the runtime base, reads
  the minor from that dir name, compares with the builder's minor,
  and exits non-zero with a `BC-099 ABI guard tripped` marker + remedy
  if they disagree.
- **Single-version guarantee.** When the guard passes, the final
  image contains exactly one `/usr/local/lib/python3.X/` directory
  by construction — the builder's mv target matches the runtime
  base's stdlib dir.

### Added (BC-099)

- **`HEALTHCHECK` baked into the image itself.** Pre-BC-099
  `docker inspect ghcr.io/bboortz/beaconry-hub` (on the image) and
  `docker run`-launched containers without a compose healthcheck
  reported `no-healthcheck` / empty `.State.Health.Status`. Homelab's
  smoke script relied on that status field. Both Dockerfiles now
  ship `HEALTHCHECK --interval=60s --timeout=10s|5s
  --start-period=120s|60s --retries=3 CMD ["/hc", "-t", "3",
  "http://localhost:<port>/readyz"]` — matches the BC-097 compose
  shape. Compose healthchecks (BC-097) still override when set;
  composed path unchanged.

### Changed (BC-099)

- **BuildKit is now required** for beaconry builds (Dockerfile
  `# syntax=` directive enforces it; Makefile exports
  `DOCKER_BUILDKIT=1` + `COMPOSE_DOCKER_CLI_BUILD=1` so every
  subshelled `docker build` and `docker compose build` goes through
  the BuildKit frontend). Docker ≥ 23 defaults to BuildKit via
  `buildx`, so no operator action required. Classic-builder
  installs now fail with a clear error pointing at the syntax
  directive. Local-dev machines without `docker-buildx` plugin
  installed need the one-time install (Arch: `pacman -S docker-buildx`).
- **CAPABILITIES**: CAP-T201 registered (ABI guard + image
  HEALTHCHECK). CAP-T200 description updated to note BC-099
  enforces the invariant BC-098 required.

## [v0.16.6] — 2026-11-08

### Changed (BC-098)

- **Dockerfiles are Python-minor-agnostic.** `poc/{hub,scout}/Dockerfile`
  no longer carry `python3.X` literals in any instruction. The builder
  stage runs `PY_VER="$(python3 -c 'import sys; …')"` to detect the
  interpreter's `major.minor` at build time, then moves site-packages
  from pip's `--prefix=/install` layout into a staging path mirroring
  runtime (`/stage/usr/local/lib/python${PY_VER}/site-packages`). The
  runtime stage replaces the previous hardcoded site-packages COPY
  with a single `COPY --from=builder /stage /` — site-packages land
  at the exact path Python's native sys.path expects.
- **Retires BC-097's documented risk.** Forge FG-032 (PR #60) bumped
  `python-scratch-trixie` + `forge-builder-trixie` to Python 3.14
  in-place — exactly the failure mode BC-097's risk note predicted
  for the hardcoded `python3.13` paths. BC-098 ships the companion
  fix so forge's release pipeline can promote; next daily cron
  republishes `:latest` with Python 3.14, beaconry's next
  `make live-rebuild` picks it up transparently.
- **CAPABILITIES**: CAP-T200 registered (stage-at-runtime-path pattern).
  CAP-T199 description updated past-tense to point at CAP-T200 as the
  successor.
- **Hub Dockerfile cleanup**: standalone `COPY --from=builder
  --chown=65532:65532 /stage/data /data` dropped — now subsumed by
  `COPY --from=builder /stage /` (ownership preserved from the
  builder-stage `chown -R`).

## [v0.16.5] — 2026-11-08

### Changed (BC-097)

- **compose healthchecks**: all 6 probe sites across
  `poc/docker-compose.yml`, `poc/docker-compose.cold-start.yml`,
  `poc/docker-compose.scout-only.yml`, and `poc/scenarios/compose.yml`
  moved from `["CMD", "python3", "-c", "import urllib.request..."]`
  to `["CMD", "/hc", "-t", "3", "http://localhost:<port>/<path>"]`.
  Same semantics (exit 0 iff 2xx), ~400 KB static binary instead of a
  CPython cold-start per probe. Possible thanks to forge FG-031
  baking `/hc` into every `*-scratch-trixie` image.
- **runtime base**: both `poc/{hub,scout}/Dockerfile` runtime FROM
  `ghcr.io/bboortz/python-scratch-trixie:3.13` →
  `ghcr.io/bboortz/python-scratch-trixie:latest`. Symmetry with the
  `forge-builder-trixie:latest` builder (BC-095); picks up forge's
  daily rebuild cadence immediately. `verify_pinned_versions.py`
  accepts both tags.
- **memory docs**: `memory/docker-standard.md` and
  `memory/health-checks.md` now lead with the `/hc` recipe. The
  python-inline form is retained as a documented fallback for
  services on bases without `/hc`.

### Documentation
- **status:** BC-096 shipped — flip INDEX + story frontmatter

## [v0.16.4] — 2026-11-07

### Added (BC-096)

- **SBOM at builder stage**: both `poc/{hub,scout}/Dockerfile` builder
  stages now run `syft dir:/install -o syft-json=/sbom.json`; both
  runtime stages `COPY --from=builder /sbom.json /sbom.json`. The
  frozen SBOM travels with the image — adopts forge's FG-020 pattern.
- **`GET /sbom` endpoint** on hub + scout returns the baked-in SBOM
  as `application/json`. 503 when the file is missing (dev/host runs
  without a built image degrade cleanly).
- **`make dogfood-sbom`** + `poc/scripts/dogfood_sbom.py` runs
  `grype sbom:/sbom.json` inside each image. No `docker.sock` mount
  required. Same severity floor + skip env (`BEACONRY_SKIP_DOGFOOD=1`)
  as `make dogfood-scan`. Wired into `make check-full`.

### Changed (BC-096)

- **`make check-full`** now runs both dogfood passes (image-scan
  from BC-082 + frozen-SBOM-scan from BC-096). The two signals are
  complementary: image-scan catches Go-stdlib bugs in built binaries;
  SBOM-scan isolates "image regressed" from "grype DB learned a new
  CVE". Opt-out env is shared.

## [v0.16.3] — 2026-11-07

### Changed (BC-095)

- **builder base**: both `poc/{hub,scout}/Dockerfile` builder stages
  moved from upstream `python:3.13-slim-trixie` to forge-signed
  `ghcr.io/bboortz/forge-builder-trixie:latest`. Extends BC-083's
  runtime migration one stage up — the full build chain is now
  cosign-verifiable against forge's keyless-OIDC identity.
- **scout builder shrinks**: syft + curl install RUN lines deleted
  (both already in the forge builder base). Builder prelude becomes
  zero RUN lines before `WORKDIR /app`.
- **hub builder consolidates**: syft install deleted; the remaining
  grype + docker CLI static fetches share a single RUN that
  temporarily apt-installs curl, runs both fetches, and purges curl
  before the layer closes.
- **runtime stage unchanged**: still
  `ghcr.io/bboortz/python-scratch-trixie:3.13` — scratch-based, no
  shell/apt, USER 65532. BC-083 stands.
- CAP-T196 registered. Follow-up BC-096 candidate for SBOM-at-builder
  pattern (forge's FG-020) tracked in `docs/plan/IDEAS.md`.

### No runtime change

Build-time edits only. Runtime image is byte-equivalent modulo
transitive apt/pip deps that would have moved anyway between builds.
HTTP contracts, callback_data, audit events, env vars all unchanged.

## [v0.16.2] — 2026-11-06

### Added (BC-094)

- **compose overrides**: new opt-in files
  `poc/docker-compose.hub-ghcr.yml` + `poc/docker-compose.scout-
  ghcr.yml`. Each defines a single service with
  `image: ghcr.io/bboortz/beaconry-{hub,scout}:${BEACONRY_IMAGE_
  TAG:-latest}`. Lets homelab `docker compose pull && up -d`
  the forge-signed artifact without touching the base compose or
  the 19+ downstream refs to `beaconry-{hub,scout}:latest`
  (cleanup.py, dogfood_scan.py, demo.sh, cold-start/test
  stacks). CAP-T194.
- **env**: `BEACONRY_IMAGE_TAG` (default `latest`). Documented in
  `docs/VARIABLES.md` + `poc/.env.example`. Compose-substituted
  only; no Python module reads it. CAP-T195.
- **lint**: `verify_variables_documented.py` gains a
  `DEPLOY_ONLY_ALLOWLIST` category so compose-substituted vars
  skip the orphan check.

### No runtime change

Local dev's `make demo` / `make live-rebuild` / `make
test-scenarios` unchanged — still build locally, still tag as
`beaconry-{hub,scout}:latest`. Override files are opt-in via
`docker compose -f` chain.

### Follow-up (homelab repo, not shipped here)

- `bb-claw/homelab:feat/beaconry-deploy` needs a sibling
  `ansible/tasks/docker-compose-pull-deploy.yml` that replaces
  `docker compose build --pull` with `docker compose pull` +
  cosign verify against forge's keyless-OIDC identity
  (`https://github.com/bboortz/forge/.github/workflows/build.yml@refs/heads/main`).
- Both `beaconry_hub` + `beaconry_scout` roles swap include
  target to the new task and add the `-f docker-compose.*-ghcr.yml`
  flag.
- Systemd unit templates for beaconry-{hub,scout} swap
  `ExecStartPre=docker compose build` → `ExecStartPre=docker
  compose pull`.
- Cosign binary installed as a prerequisite (common `docker`
  role is the obvious home).

## [v0.16.1] — 2026-11-05

### Documentation

- **lifecycle**: consolidated `docs/LIFECYCLE.md` sync covering
  BC-090/091/092/093. State diagram, timers section (8 new env
  vars), audit events (4 new events) updated. Known-flaw entries
  closed by these BCs struck through with their resolving CAP id
  (§15.1, §16.1, §16.3, §16.4, §17.2, §17.3). Also expands
  `test_lifecycle_doc.py::TIMERS` to assert the 8 new env vars
  appear in the doc.
- **no runtime change**: pure docs + test-list update.

### Added
- **poc:** scout + hub + executor plugins + compose stack
- **hub,demo:** BC-001 end-to-end mocked POC demo
- **demo,hub:** BC-002 green — real pipeline on nginx:1.24, shipped
- **docs,scripts:** BC-004 capabilities + bidirectional traceability
- **scout,compose,make:** BC-005 multi-image Compose demo
- **make,docs:** BC-006 fast + full gate tiers
- **cli:** BC-007 `make findings` — read-only view of CVE state
- **process:** BC-008 pre-ship audit protocol (DD-TGD step 6a)
- **hub:** BC-003 Phase 1+2 — callback validation + idempotence
- **hub:** BC-003 shipped — Phase 3 live tap session complete
- **hub:** BC-009 shipped — real fix execution via Telegram
- **hub+scout:** BC-010 shipped — container health observability (4 endpoints)
- **beaconry_health:** BC-012 shipped — shared package + compiler-enforced trap guard
- **scout+status:** BC-013 shipped — scout live HTTP probes + Docker-compose-ps-like status table
- **executor:** BC-014 shipped — DockerAdapter same-track pull (no silent :latest jump)
- **process:** BC-015 shipped — memory drift regression guard
- **hub:** BC-011 shipped — target-container health probe (operator-triggered)
- **process:** BC-016 shipped — docs drift guard (make help + README ↔ code)
- **e2e:** BC-017 shipped — automated end-to-end BC-011 probe flow (no Telegram)
- **e2e:** BC-018 shipped — telegram-mock service (supersedes BC-017 test-mode guard)
- **ci:** BC-019 — grep-verify every shipping-evidence pointer
- **hub:** BC-020 — KEV ingestion + wire urgency.classify_tier
- **ci:** BC-021 — Dockerfile sources lint
- **hub:** BC-022 — digest shows KEV markers for suppressed findings
- **hub:** BC-023 — KEV findings bypass MIN_CVSS filter
- **ci:** BC-024 — verify-traceability enforces reverse direction
- **hub:** BC-028 — KEV lookup resolves grype's relatedVulnerabilities aliases
- **hub:** BC-029 — append-only audit log of operator-visible state changes
- **hub:** BC-031 — scan reconciliation + regression detection
- **hub:** BC-032 — fix viability indicator + fixed-in parity
- **hub:** BC-033 — verify_fix scans source_ref, not hardcoded :latest
- **hub:** BC-034 — persist last_seen_ts per finding
- **hub:** BC-035 notification adapter architecture + heartbeat for stale findings
- **hub,scout:** BC-030 internet_facing detection → Tier 1 fires
- **hub:** BC-036 decouple digest cadence from scan cadence
- **scout:** BC-037 on-demand scan trigger — make scan
- **hub,scenarios:** BC-038 live-stack scenario harness — make test-scenarios
- **docs,scripts:** BC-039 docs/VARIABLES.md + verify-variables drift lint
- **docs,scripts:** BC-040 three-way env sync — VARIABLES.md ↔ .env.example ↔ docker-compose.yml
- **scripts:** BC-041 verify-evidence-pointers reverse check
- **scripts,hub:** BC-042 `make audit` CLI + `scan_received` event
- **hub:** BC-043 CVE alias on non-CVE primary ids — KEV paper trail
- **scripts:** BC-044 live-smoke CLI harness + autodetected scenario coverage
- **hub,scripts:** BC-055 HTTP-only CLI → hub (no SSH)
- **docs,scripts:** BC-056 INSTALL.md + verify-install-doc (short-name reshape)
- **scripts,hub:** BC-057 make findings HOST= + 🖥️ multi-host notification lead
- **hub:** BC-058 mutation + audit cleanup
- **hub:** BC-059 callback_data carries CVE id
- **hub:** BC-060 notify_sent_failed audit symmetry
- **hub,scripts:** BC-061 scout fleet registry with liveness status
- **hub,scout,scripts:** BC-062 versioning (software + API + schema) + SemVer bump + pre-push gate
- **hub,scout,scripts:** BC-063 fleet health visibility + scout heartbeat
- **hub,scripts:** BC-064 FLEET LAST HEARTBEAT + stale-heartbeat unknown health
- **hub:** BC-066 manual executor as default + configurable adapter + 1h reminder loop
- **ci,scripts:** BC-069 release toolchain — CI + make soak + auto-CHANGELOG
- **scripts,build:** BC-070 operator CLI install — `pip install -e . → beaconry …`
- **ci,scripts:** BC-074 release automation + 24h soak + feature-complete lint + CI-failure log
- **scripts,hub,scout:** BC-075 metrics + soak threshold signals + privacy-first opt-in
- **scripts:** BC-076 beaconry metrics --remote reads from hub /audit over HTTP
- **docker,scout,hub:** BC-077 non-root hub + scout containers
- **docker,hub,scout:** BC-078 distroless runtime for hub + scout
- **ci,make,tests:** BC-079 pre-push runs full container verification
- **make,scripts:** BC-080 make cleanup — teardown + image removal after tests
- **ci,docker,python:** BC-082 homelab-deployment coverage + base bumps
- **docker,ci,tests:** BC-083 forge base image + verify-base-images preflight
- **hub,notify:** BC-086 auto-fix status + counter threshold + regressed-from-auto-fix
- **hub,notify:** BC-087 wire last_seen into notifications + BEACONRY_LOG_LEVEL
- **hub,notify:** BC-089 snooze with real expiry + configurable buttons
- **hub,notify:** BC-090 auto-fix regression body actually reaches Telegram
- **hub,scout:** BC-092 canary ring — production fixes gated on staging
- **scout:** BC-093 rescan triggers — daily + docker events + pacman hook

### Fixed
- **hub,demo:** BC-002 robust-notify — HTML parse mode, cap, dedup, digest
- **docs,scripts:** BC-004 close-gaps — CAP-B/T prefix, required fields, hook, self-consistency
- **scripts:** BC-004 design-link + origin-story validation
- **scripts:** BC-004 enforce description as required field
- **hub:** BC-005 close two in-container regressions found by make demo
- BC-005 close-gaps — tests + story self-consistency + lessons
- **scripts:** enforce plan doc on fix/* and feat/* branches
- smoke mode broken + CAP-id regex truncates at hyphens
- BC-006 close-gaps — test graceful-skip + README skip-note
- BC-007 close-gaps — tier in sort key, schema migration, docs
- BC-007 load_finding reads tier + migration + round-trip tests
- **scripts:** BC-008 automate-audit — Shipping evidence enforced on shipped stories
- BC-003 close-gaps — run real_tools, add missing row, warn on scenario-count
- **beaconry_health:** BC-012 close-gap — pyright actually enforces the trap guard
- **hub+findings:** BC-011 close-gaps — findings CLI accepts new statuses + button-attach test
- **compose:** BC-011 env propagation — BEACONRY_HEALTH_PROBES/FIX_ENABLED/MOCK now actually propagate
- **findings:** BC-007 close-gap — auto-route to compose hub's volume DB
- **hub:** BC-011 close-gap — use module-level _waterfall binding in verify branch
- **tests:** BC-018 close-gap — unit coverage for telegram-mock + hub env dials
- **hub:** BC-020 close-gap — ship kev.py + urgency.py in the image
- **memory:** BC-020 close-gap — add memory drift ban
- **hub:** BC-020 close-gap — treat empty BEACONRY_KEV_FEED_URL as unset
- **docs:** BC-023 close-gap — Covers block + design-decisions annotation
- **docs:** BC-029 close-gap — add Gherkin block + gitignore audit.log
- **hub:** BC-032 close-gap — handle grype's 'not available' sentinel
- **docs:** BC-033 close-gap — add Scenario 3 test coverage
- **hub:** BC-035 — tolerate empty-string env vars from compose \${VAR:-}
- **hub:** BC-035/BC-030 close-gap — notify path works inside container
- **scenarios:** BC-038 close-gap — live-run fixes
- **audit:** BC-043 close-gap — make audit CVE=X matches alias too
- **audit:** FOLLOW output buffering + inline-script error handling
- **hub:** BC-065 digest content-change bypass is now opt-out via env
- **hub,executor,audit:** BC-067 quality sweep — 5 severity-1 bugs
- **hub:** BC-068 defensive cleanup — reconcile FD-leak + fix_version blank sanitize
- **ci,tests:** BC-071 CI env leak — BEACONRY_SKIP_VERSION_BUMP leaked into pytest
- **build:** explicit packages list to bypass flat-layout detection
- **hooks:** use .venv/bin/pytest instead of system pytest
- **deps:** BC-072 pytest 8.3.3 → 9.0.3 + pytest-asyncio 0.24.0 → 1.4.1.post1
- **deps:** BC-073 python-dotenv 1.0.1 → 1.2.2 + requests 2.32.3 → 2.33.0
- **docker:** BC-081 bump docker CLI 27.5.1 → 29.9.2 for Go stdlib ≥1.26.9
- **kev:** BC-091 identifiable User-Agent — CISA stops 403'ing

### Changed
- initial commit (gitignore only; main is CI-promoted)
- **build:** Makefile, pyproject, pre-commit, dev requirements
- **poc:** pytest pyramid skeleton + sanity test
- **build:** lint + type cleanup, lower coverage floor until BC-001
- **poc:** BC-001 red integration test + plan doc
- **poc,hub:** BC-002 red state — tests + dispatch stubs + plan
- **poc:** BC-004 red state — traceability enforcement scenarios
- **poc:** BC-005 red — scout glob filter stubs + Compose demo plan
- **e2e:** use hub.save_finding instead of raw INSERT for seeding
- **ci:** run test-e2e as part of make check-full
- **ci:** complexity gates + 85/65% coverage floors
- **scout:** extract parse_ss_output; pragma subprocess shells; un-omit from coverage
- **hub:** on_button_click → dispatch dict of per-action handlers
- **ci:** verify_traceability.run → gates registry
- **make:** fix 3 polish issues surfaced by running all make targets
- **ops:** live-stack rebuild discipline — make live-rebuild + live-status
- **process:** breaking-change-in-planning discipline + BC-031 close-gap
- **hub:** BC-035 close-gap — cover migration backfill, format_heartbeat, mock-print
- **hub:** BC-030 close-gap — audit event carries internet_facing + .env.example
- **docker:** remove BC-083 librt.so.1 workaround — forge v3 bundles it
- **hub,scripts:** BC-086 close-gaps — CLI auto_fixed + digest host + reconcile diag

### Documentation
- **design:** product design + locked decisions
- **process:** DD-TGD process, stories, plan docs (BC-000)
- **stories:** mark BC-000 shipped
- **stories:** add BC-001 end-to-end mocked POC demo (ready)
- **stories:** mark BC-001 shipped
- **process:** add IDEAS backlog + wire it into DD-TGD
- **stories:** BC-003 draft + note BC-002 audit gaps in IDEAS
- **ideas:** capture CAP-T026 staleness as fix-opportunistically
- **memory:** BC-013 close-gap — scout migrated to HTTP; memory files updated
- **memory:** BC-014 close-gap — design-decisions.md matches same-track pull reality
- close BC-015 self-consistency gap + refresh make help
- **memory:** BC-016 close-gap — gate table lists all shipped drift guards
- **ideas:** log audit-protocol upgrade — grep-verify evidence pointers
- **ideas:** park BC-020 — dividend overlaps existing demo modes
- **ideas:** log 2 lint gaps surfaced by BC-020 live-smoke
- **ideas:** KEV lookup should resolve CVE aliases via grype 'related' field
- **ideas:** BC-025/026/027 close the big refactor entries
- **stories:** draft BC-030 (internet_facing detection) as ready
- **ideas:** log AVG-2909 operator action + promote BC-031 KEV-alias context
- **stories:** BC-031 draft — scan reconciliation + regression detection
- **plan:** BC-031 close-gap evidence rows for backfill tests
- **ideas:** verify-evidence-pointers should catch missing rows too
- **ideas:** log 3 BC-034 consumer candidates (heartbeat / digest refresh / purge)
- **stories:** mark BC-035 shipped
- **stories:** mark BC-030 shipped
- **bc-037:** post-ship audit — record scout-down verification date
- **bc-038:** post-ship audit — .env.example + check-full inclusion
- **bc-039:** post-ship audit — IDEAS followup for three-way sync
- **bc-040:** post-ship audit — Makefile wording + IDEAS cleanup
- **bc-041:** post-ship audit — remove stale IDEAS entry (shipped)
- **bc-042:** post-ship audit — remove stale IDEAS entry
- **stories:** mark BC-062 shipped in INDEX
- **lifecycle:** BC-088 add docs/LIFECYCLE.md + flaw inventory
- **lifecycle:** BC-088 fix state diagram — show OPEN → AUTO_FIXED edge
- **lifecycle:** BC-089 updates — snooze expiry paths
- **lifecycle:** BC-090/091/092/093 updates

### Other
- Initial commit

## [v0.16.0] — 2026-11-05

### Added (BC-093)

- **scout daily rescan**: new wall-clock loop fires a full rescan
  at `BEACONRY_DAILY_RESCAN_HOUR` host-local (default 02:00). `-1`
  disables. Catches slow drift between regular `SCAN_INTERVAL`
  ticks. CAP-T191.
- **scout docker events listener**: subscribes to
  `docker events` for image `pull` / container `create` + `start`,
  schedules a debounced scan via `BEACONRY_DOCKER_EVENTS_DEBOUNCE_S`
  (default 10s). `BEACONRY_DOCKER_EVENTS_ENABLED=0` disables.
  A compose up pulling N images coalesces into one trailing scan.
  CAP-T192.
- **pacman hook template**: `poc/hooks/beaconry-pacman.hook` +
  `poc/hooks/README.md`. Operator installs manually on Arch hosts;
  hook POSTs to scout's `/scan/trigger` after every pacman
  transaction. Failure is swallowed so pacman transactions never
  block. CAP-T193.

### ⚠ Behavioural note

- **More scout → hub traffic by default**: docker events listener
  fires scans on image pulls. Operators with heavy container churn
  can opt out (`BEACONRY_DOCKER_EVENTS_ENABLED=0`) or raise the
  debounce window.

### Added
- **poc:** scout + hub + executor plugins + compose stack
- **hub,demo:** BC-001 end-to-end mocked POC demo
- **demo,hub:** BC-002 green — real pipeline on nginx:1.24, shipped
- **docs,scripts:** BC-004 capabilities + bidirectional traceability
- **scout,compose,make:** BC-005 multi-image Compose demo
- **make,docs:** BC-006 fast + full gate tiers
- **cli:** BC-007 `make findings` — read-only view of CVE state
- **process:** BC-008 pre-ship audit protocol (DD-TGD step 6a)
- **hub:** BC-003 Phase 1+2 — callback validation + idempotence
- **hub:** BC-003 shipped — Phase 3 live tap session complete
- **hub:** BC-009 shipped — real fix execution via Telegram
- **hub+scout:** BC-010 shipped — container health observability (4 endpoints)
- **beaconry_health:** BC-012 shipped — shared package + compiler-enforced trap guard
- **scout+status:** BC-013 shipped — scout live HTTP probes + Docker-compose-ps-like status table
- **executor:** BC-014 shipped — DockerAdapter same-track pull (no silent :latest jump)
- **process:** BC-015 shipped — memory drift regression guard
- **hub:** BC-011 shipped — target-container health probe (operator-triggered)
- **process:** BC-016 shipped — docs drift guard (make help + README ↔ code)
- **e2e:** BC-017 shipped — automated end-to-end BC-011 probe flow (no Telegram)
- **e2e:** BC-018 shipped — telegram-mock service (supersedes BC-017 test-mode guard)
- **ci:** BC-019 — grep-verify every shipping-evidence pointer
- **hub:** BC-020 — KEV ingestion + wire urgency.classify_tier
- **ci:** BC-021 — Dockerfile sources lint
- **hub:** BC-022 — digest shows KEV markers for suppressed findings
- **hub:** BC-023 — KEV findings bypass MIN_CVSS filter
- **ci:** BC-024 — verify-traceability enforces reverse direction
- **hub:** BC-028 — KEV lookup resolves grype's relatedVulnerabilities aliases
- **hub:** BC-029 — append-only audit log of operator-visible state changes
- **hub:** BC-031 — scan reconciliation + regression detection
- **hub:** BC-032 — fix viability indicator + fixed-in parity
- **hub:** BC-033 — verify_fix scans source_ref, not hardcoded :latest
- **hub:** BC-034 — persist last_seen_ts per finding
- **hub:** BC-035 notification adapter architecture + heartbeat for stale findings
- **hub,scout:** BC-030 internet_facing detection → Tier 1 fires
- **hub:** BC-036 decouple digest cadence from scan cadence
- **scout:** BC-037 on-demand scan trigger — make scan
- **hub,scenarios:** BC-038 live-stack scenario harness — make test-scenarios
- **docs,scripts:** BC-039 docs/VARIABLES.md + verify-variables drift lint
- **docs,scripts:** BC-040 three-way env sync — VARIABLES.md ↔ .env.example ↔ docker-compose.yml
- **scripts:** BC-041 verify-evidence-pointers reverse check
- **scripts,hub:** BC-042 `make audit` CLI + `scan_received` event
- **hub:** BC-043 CVE alias on non-CVE primary ids — KEV paper trail
- **scripts:** BC-044 live-smoke CLI harness + autodetected scenario coverage
- **hub,scripts:** BC-055 HTTP-only CLI → hub (no SSH)
- **docs,scripts:** BC-056 INSTALL.md + verify-install-doc (short-name reshape)
- **scripts,hub:** BC-057 make findings HOST= + 🖥️ multi-host notification lead
- **hub:** BC-058 mutation + audit cleanup
- **hub:** BC-059 callback_data carries CVE id
- **hub:** BC-060 notify_sent_failed audit symmetry
- **hub,scripts:** BC-061 scout fleet registry with liveness status
- **hub,scout,scripts:** BC-062 versioning (software + API + schema) + SemVer bump + pre-push gate
- **hub,scout,scripts:** BC-063 fleet health visibility + scout heartbeat
- **hub,scripts:** BC-064 FLEET LAST HEARTBEAT + stale-heartbeat unknown health
- **hub:** BC-066 manual executor as default + configurable adapter + 1h reminder loop
- **ci,scripts:** BC-069 release toolchain — CI + make soak + auto-CHANGELOG
- **scripts,build:** BC-070 operator CLI install — `pip install -e . → beaconry …`
- **ci,scripts:** BC-074 release automation + 24h soak + feature-complete lint + CI-failure log
- **scripts,hub,scout:** BC-075 metrics + soak threshold signals + privacy-first opt-in
- **scripts:** BC-076 beaconry metrics --remote reads from hub /audit over HTTP
- **docker,scout,hub:** BC-077 non-root hub + scout containers
- **docker,hub,scout:** BC-078 distroless runtime for hub + scout
- **ci,make,tests:** BC-079 pre-push runs full container verification
- **make,scripts:** BC-080 make cleanup — teardown + image removal after tests
- **ci,docker,python:** BC-082 homelab-deployment coverage + base bumps
- **docker,ci,tests:** BC-083 forge base image + verify-base-images preflight
- **hub,notify:** BC-086 auto-fix status + counter threshold + regressed-from-auto-fix
- **hub,notify:** BC-087 wire last_seen into notifications + BEACONRY_LOG_LEVEL
- **hub,notify:** BC-089 snooze with real expiry + configurable buttons
- **hub,notify:** BC-090 auto-fix regression body actually reaches Telegram
- **hub,scout:** BC-092 canary ring — production fixes gated on staging

### Fixed
- **hub,demo:** BC-002 robust-notify — HTML parse mode, cap, dedup, digest
- **docs,scripts:** BC-004 close-gaps — CAP-B/T prefix, required fields, hook, self-consistency
- **scripts:** BC-004 design-link + origin-story validation
- **scripts:** BC-004 enforce description as required field
- **hub:** BC-005 close two in-container regressions found by make demo
- BC-005 close-gaps — tests + story self-consistency + lessons
- **scripts:** enforce plan doc on fix/* and feat/* branches
- smoke mode broken + CAP-id regex truncates at hyphens
- BC-006 close-gaps — test graceful-skip + README skip-note
- BC-007 close-gaps — tier in sort key, schema migration, docs
- BC-007 load_finding reads tier + migration + round-trip tests
- **scripts:** BC-008 automate-audit — Shipping evidence enforced on shipped stories
- BC-003 close-gaps — run real_tools, add missing row, warn on scenario-count
- **beaconry_health:** BC-012 close-gap — pyright actually enforces the trap guard
- **hub+findings:** BC-011 close-gaps — findings CLI accepts new statuses + button-attach test
- **compose:** BC-011 env propagation — BEACONRY_HEALTH_PROBES/FIX_ENABLED/MOCK now actually propagate
- **findings:** BC-007 close-gap — auto-route to compose hub's volume DB
- **hub:** BC-011 close-gap — use module-level _waterfall binding in verify branch
- **tests:** BC-018 close-gap — unit coverage for telegram-mock + hub env dials
- **hub:** BC-020 close-gap — ship kev.py + urgency.py in the image
- **memory:** BC-020 close-gap — add memory drift ban
- **hub:** BC-020 close-gap — treat empty BEACONRY_KEV_FEED_URL as unset
- **docs:** BC-023 close-gap — Covers block + design-decisions annotation
- **docs:** BC-029 close-gap — add Gherkin block + gitignore audit.log
- **hub:** BC-032 close-gap — handle grype's 'not available' sentinel
- **docs:** BC-033 close-gap — add Scenario 3 test coverage
- **hub:** BC-035 — tolerate empty-string env vars from compose \${VAR:-}
- **hub:** BC-035/BC-030 close-gap — notify path works inside container
- **scenarios:** BC-038 close-gap — live-run fixes
- **audit:** BC-043 close-gap — make audit CVE=X matches alias too
- **audit:** FOLLOW output buffering + inline-script error handling
- **hub:** BC-065 digest content-change bypass is now opt-out via env
- **hub,executor,audit:** BC-067 quality sweep — 5 severity-1 bugs
- **hub:** BC-068 defensive cleanup — reconcile FD-leak + fix_version blank sanitize
- **ci,tests:** BC-071 CI env leak — BEACONRY_SKIP_VERSION_BUMP leaked into pytest
- **build:** explicit packages list to bypass flat-layout detection
- **hooks:** use .venv/bin/pytest instead of system pytest
- **deps:** BC-072 pytest 8.3.3 → 9.0.3 + pytest-asyncio 0.24.0 → 1.4.1.post1
- **deps:** BC-073 python-dotenv 1.0.1 → 1.2.2 + requests 2.32.3 → 2.33.0
- **docker:** BC-081 bump docker CLI 27.5.1 → 29.9.2 for Go stdlib ≥1.26.9
- **kev:** BC-091 identifiable User-Agent — CISA stops 403'ing

### Changed
- initial commit (gitignore only; main is CI-promoted)
- **build:** Makefile, pyproject, pre-commit, dev requirements
- **poc:** pytest pyramid skeleton + sanity test
- **build:** lint + type cleanup, lower coverage floor until BC-001
- **poc:** BC-001 red integration test + plan doc
- **poc,hub:** BC-002 red state — tests + dispatch stubs + plan
- **poc:** BC-004 red state — traceability enforcement scenarios
- **poc:** BC-005 red — scout glob filter stubs + Compose demo plan
- **e2e:** use hub.save_finding instead of raw INSERT for seeding
- **ci:** run test-e2e as part of make check-full
- **ci:** complexity gates + 85/65% coverage floors
- **scout:** extract parse_ss_output; pragma subprocess shells; un-omit from coverage
- **hub:** on_button_click → dispatch dict of per-action handlers
- **ci:** verify_traceability.run → gates registry
- **make:** fix 3 polish issues surfaced by running all make targets
- **ops:** live-stack rebuild discipline — make live-rebuild + live-status
- **process:** breaking-change-in-planning discipline + BC-031 close-gap
- **hub:** BC-035 close-gap — cover migration backfill, format_heartbeat, mock-print
- **hub:** BC-030 close-gap — audit event carries internet_facing + .env.example
- **docker:** remove BC-083 librt.so.1 workaround — forge v3 bundles it
- **hub,scripts:** BC-086 close-gaps — CLI auto_fixed + digest host + reconcile diag

### Documentation
- **design:** product design + locked decisions
- **process:** DD-TGD process, stories, plan docs (BC-000)
- **stories:** mark BC-000 shipped
- **stories:** add BC-001 end-to-end mocked POC demo (ready)
- **stories:** mark BC-001 shipped
- **process:** add IDEAS backlog + wire it into DD-TGD
- **stories:** BC-003 draft + note BC-002 audit gaps in IDEAS
- **ideas:** capture CAP-T026 staleness as fix-opportunistically
- **memory:** BC-013 close-gap — scout migrated to HTTP; memory files updated
- **memory:** BC-014 close-gap — design-decisions.md matches same-track pull reality
- close BC-015 self-consistency gap + refresh make help
- **memory:** BC-016 close-gap — gate table lists all shipped drift guards
- **ideas:** log audit-protocol upgrade — grep-verify evidence pointers
- **ideas:** park BC-020 — dividend overlaps existing demo modes
- **ideas:** log 2 lint gaps surfaced by BC-020 live-smoke
- **ideas:** KEV lookup should resolve CVE aliases via grype 'related' field
- **ideas:** BC-025/026/027 close the big refactor entries
- **stories:** draft BC-030 (internet_facing detection) as ready
- **ideas:** log AVG-2909 operator action + promote BC-031 KEV-alias context
- **stories:** BC-031 draft — scan reconciliation + regression detection
- **plan:** BC-031 close-gap evidence rows for backfill tests
- **ideas:** verify-evidence-pointers should catch missing rows too
- **ideas:** log 3 BC-034 consumer candidates (heartbeat / digest refresh / purge)
- **stories:** mark BC-035 shipped
- **stories:** mark BC-030 shipped
- **bc-037:** post-ship audit — record scout-down verification date
- **bc-038:** post-ship audit — .env.example + check-full inclusion
- **bc-039:** post-ship audit — IDEAS followup for three-way sync
- **bc-040:** post-ship audit — Makefile wording + IDEAS cleanup
- **bc-041:** post-ship audit — remove stale IDEAS entry (shipped)
- **bc-042:** post-ship audit — remove stale IDEAS entry
- **stories:** mark BC-062 shipped in INDEX
- **lifecycle:** BC-088 add docs/LIFECYCLE.md + flaw inventory
- **lifecycle:** BC-088 fix state diagram — show OPEN → AUTO_FIXED edge
- **lifecycle:** BC-089 updates — snooze expiry paths

### Other
- Initial commit

## [v0.15.0] — 2026-11-05

### Added (BC-092)

- **canary ring**: scouts advertise `SCOUT_HOST_ROLE`
  (`workstation | staging | production`, default `workstation`) on
  `/heartbeat` + `/scan`. Hub persists via schema v6→v7 (`ALTER
  TABLE scouts ADD COLUMN role TEXT NOT NULL DEFAULT 'workstation'`).
  CAP-T188.
- **production fix gate**: `_handle_fix` blocks a production-role
  fix unless the same CVE is in `fixed_verified` on at least one
  staging scout. Blocked fix shows a Telegram message naming the
  available staging hosts (or configuration hint if none). Audit
  trail: `canary_gate_passed`, `canary_gate_blocked`,
  `canary_gate_force_allowed`. CAP-T189.
- **env**: `BEACONRY_CANARY_RING_ENABLED` (default `1`) global
  toggle; `BEACONRY_CANARY_PRODUCTION_FORCE_ALLOW` (default `0`)
  emergency one-off override.
- **`make status`**: new `ROLE` column (W/S/P glyph) rendered when
  at least one scout is non-workstation. Single-host operators see
  unchanged output. CAP-T190.

### ⚠ Breaking behaviour

- **Production fixes blocked by default** when no staging scout
  has verified the same CVE. Operators without a staging host
  must either flag one (`SCOUT_HOST_ROLE=staging` on at least one
  scout) or disable the gate (`BEACONRY_CANARY_RING_ENABLED=0`).
- **Schema v6 → v7**. `scouts.role` column (default `workstation`).
  Legacy scouts unaffected (hub falls back to the DB default).

### Added
- **poc:** scout + hub + executor plugins + compose stack
- **hub,demo:** BC-001 end-to-end mocked POC demo
- **demo,hub:** BC-002 green — real pipeline on nginx:1.24, shipped
- **docs,scripts:** BC-004 capabilities + bidirectional traceability
- **scout,compose,make:** BC-005 multi-image Compose demo
- **make,docs:** BC-006 fast + full gate tiers
- **cli:** BC-007 `make findings` — read-only view of CVE state
- **process:** BC-008 pre-ship audit protocol (DD-TGD step 6a)
- **hub:** BC-003 Phase 1+2 — callback validation + idempotence
- **hub:** BC-003 shipped — Phase 3 live tap session complete
- **hub:** BC-009 shipped — real fix execution via Telegram
- **hub+scout:** BC-010 shipped — container health observability (4 endpoints)
- **beaconry_health:** BC-012 shipped — shared package + compiler-enforced trap guard
- **scout+status:** BC-013 shipped — scout live HTTP probes + Docker-compose-ps-like status table
- **executor:** BC-014 shipped — DockerAdapter same-track pull (no silent :latest jump)
- **process:** BC-015 shipped — memory drift regression guard
- **hub:** BC-011 shipped — target-container health probe (operator-triggered)
- **process:** BC-016 shipped — docs drift guard (make help + README ↔ code)
- **e2e:** BC-017 shipped — automated end-to-end BC-011 probe flow (no Telegram)
- **e2e:** BC-018 shipped — telegram-mock service (supersedes BC-017 test-mode guard)
- **ci:** BC-019 — grep-verify every shipping-evidence pointer
- **hub:** BC-020 — KEV ingestion + wire urgency.classify_tier
- **ci:** BC-021 — Dockerfile sources lint
- **hub:** BC-022 — digest shows KEV markers for suppressed findings
- **hub:** BC-023 — KEV findings bypass MIN_CVSS filter
- **ci:** BC-024 — verify-traceability enforces reverse direction
- **hub:** BC-028 — KEV lookup resolves grype's relatedVulnerabilities aliases
- **hub:** BC-029 — append-only audit log of operator-visible state changes
- **hub:** BC-031 — scan reconciliation + regression detection
- **hub:** BC-032 — fix viability indicator + fixed-in parity
- **hub:** BC-033 — verify_fix scans source_ref, not hardcoded :latest
- **hub:** BC-034 — persist last_seen_ts per finding
- **hub:** BC-035 notification adapter architecture + heartbeat for stale findings
- **hub,scout:** BC-030 internet_facing detection → Tier 1 fires
- **hub:** BC-036 decouple digest cadence from scan cadence
- **scout:** BC-037 on-demand scan trigger — make scan
- **hub,scenarios:** BC-038 live-stack scenario harness — make test-scenarios
- **docs,scripts:** BC-039 docs/VARIABLES.md + verify-variables drift lint
- **docs,scripts:** BC-040 three-way env sync — VARIABLES.md ↔ .env.example ↔ docker-compose.yml
- **scripts:** BC-041 verify-evidence-pointers reverse check
- **scripts,hub:** BC-042 `make audit` CLI + `scan_received` event
- **hub:** BC-043 CVE alias on non-CVE primary ids — KEV paper trail
- **scripts:** BC-044 live-smoke CLI harness + autodetected scenario coverage
- **hub,scripts:** BC-055 HTTP-only CLI → hub (no SSH)
- **docs,scripts:** BC-056 INSTALL.md + verify-install-doc (short-name reshape)
- **scripts,hub:** BC-057 make findings HOST= + 🖥️ multi-host notification lead
- **hub:** BC-058 mutation + audit cleanup
- **hub:** BC-059 callback_data carries CVE id
- **hub:** BC-060 notify_sent_failed audit symmetry
- **hub,scripts:** BC-061 scout fleet registry with liveness status
- **hub,scout,scripts:** BC-062 versioning (software + API + schema) + SemVer bump + pre-push gate
- **hub,scout,scripts:** BC-063 fleet health visibility + scout heartbeat
- **hub,scripts:** BC-064 FLEET LAST HEARTBEAT + stale-heartbeat unknown health
- **hub:** BC-066 manual executor as default + configurable adapter + 1h reminder loop
- **ci,scripts:** BC-069 release toolchain — CI + make soak + auto-CHANGELOG
- **scripts,build:** BC-070 operator CLI install — `pip install -e . → beaconry …`
- **ci,scripts:** BC-074 release automation + 24h soak + feature-complete lint + CI-failure log
- **scripts,hub,scout:** BC-075 metrics + soak threshold signals + privacy-first opt-in
- **scripts:** BC-076 beaconry metrics --remote reads from hub /audit over HTTP
- **docker,scout,hub:** BC-077 non-root hub + scout containers
- **docker,hub,scout:** BC-078 distroless runtime for hub + scout
- **ci,make,tests:** BC-079 pre-push runs full container verification
- **make,scripts:** BC-080 make cleanup — teardown + image removal after tests
- **ci,docker,python:** BC-082 homelab-deployment coverage + base bumps
- **docker,ci,tests:** BC-083 forge base image + verify-base-images preflight
- **hub,notify:** BC-086 auto-fix status + counter threshold + regressed-from-auto-fix
- **hub,notify:** BC-087 wire last_seen into notifications + BEACONRY_LOG_LEVEL
- **hub,notify:** BC-089 snooze with real expiry + configurable buttons
- **hub,notify:** BC-090 auto-fix regression body actually reaches Telegram

### Fixed
- **hub,demo:** BC-002 robust-notify — HTML parse mode, cap, dedup, digest
- **docs,scripts:** BC-004 close-gaps — CAP-B/T prefix, required fields, hook, self-consistency
- **scripts:** BC-004 design-link + origin-story validation
- **scripts:** BC-004 enforce description as required field
- **hub:** BC-005 close two in-container regressions found by make demo
- BC-005 close-gaps — tests + story self-consistency + lessons
- **scripts:** enforce plan doc on fix/* and feat/* branches
- smoke mode broken + CAP-id regex truncates at hyphens
- BC-006 close-gaps — test graceful-skip + README skip-note
- BC-007 close-gaps — tier in sort key, schema migration, docs
- BC-007 load_finding reads tier + migration + round-trip tests
- **scripts:** BC-008 automate-audit — Shipping evidence enforced on shipped stories
- BC-003 close-gaps — run real_tools, add missing row, warn on scenario-count
- **beaconry_health:** BC-012 close-gap — pyright actually enforces the trap guard
- **hub+findings:** BC-011 close-gaps — findings CLI accepts new statuses + button-attach test
- **compose:** BC-011 env propagation — BEACONRY_HEALTH_PROBES/FIX_ENABLED/MOCK now actually propagate
- **findings:** BC-007 close-gap — auto-route to compose hub's volume DB
- **hub:** BC-011 close-gap — use module-level _waterfall binding in verify branch
- **tests:** BC-018 close-gap — unit coverage for telegram-mock + hub env dials
- **hub:** BC-020 close-gap — ship kev.py + urgency.py in the image
- **memory:** BC-020 close-gap — add memory drift ban
- **hub:** BC-020 close-gap — treat empty BEACONRY_KEV_FEED_URL as unset
- **docs:** BC-023 close-gap — Covers block + design-decisions annotation
- **docs:** BC-029 close-gap — add Gherkin block + gitignore audit.log
- **hub:** BC-032 close-gap — handle grype's 'not available' sentinel
- **docs:** BC-033 close-gap — add Scenario 3 test coverage
- **hub:** BC-035 — tolerate empty-string env vars from compose \${VAR:-}
- **hub:** BC-035/BC-030 close-gap — notify path works inside container
- **scenarios:** BC-038 close-gap — live-run fixes
- **audit:** BC-043 close-gap — make audit CVE=X matches alias too
- **audit:** FOLLOW output buffering + inline-script error handling
- **hub:** BC-065 digest content-change bypass is now opt-out via env
- **hub,executor,audit:** BC-067 quality sweep — 5 severity-1 bugs
- **hub:** BC-068 defensive cleanup — reconcile FD-leak + fix_version blank sanitize
- **ci,tests:** BC-071 CI env leak — BEACONRY_SKIP_VERSION_BUMP leaked into pytest
- **build:** explicit packages list to bypass flat-layout detection
- **hooks:** use .venv/bin/pytest instead of system pytest
- **deps:** BC-072 pytest 8.3.3 → 9.0.3 + pytest-asyncio 0.24.0 → 1.4.1.post1
- **deps:** BC-073 python-dotenv 1.0.1 → 1.2.2 + requests 2.32.3 → 2.33.0
- **docker:** BC-081 bump docker CLI 27.5.1 → 29.9.2 for Go stdlib ≥1.26.9
- **kev:** BC-091 identifiable User-Agent — CISA stops 403'ing

### Changed
- initial commit (gitignore only; main is CI-promoted)
- **build:** Makefile, pyproject, pre-commit, dev requirements
- **poc:** pytest pyramid skeleton + sanity test
- **build:** lint + type cleanup, lower coverage floor until BC-001
- **poc:** BC-001 red integration test + plan doc
- **poc,hub:** BC-002 red state — tests + dispatch stubs + plan
- **poc:** BC-004 red state — traceability enforcement scenarios
- **poc:** BC-005 red — scout glob filter stubs + Compose demo plan
- **e2e:** use hub.save_finding instead of raw INSERT for seeding
- **ci:** run test-e2e as part of make check-full
- **ci:** complexity gates + 85/65% coverage floors
- **scout:** extract parse_ss_output; pragma subprocess shells; un-omit from coverage
- **hub:** on_button_click → dispatch dict of per-action handlers
- **ci:** verify_traceability.run → gates registry
- **make:** fix 3 polish issues surfaced by running all make targets
- **ops:** live-stack rebuild discipline — make live-rebuild + live-status
- **process:** breaking-change-in-planning discipline + BC-031 close-gap
- **hub:** BC-035 close-gap — cover migration backfill, format_heartbeat, mock-print
- **hub:** BC-030 close-gap — audit event carries internet_facing + .env.example
- **docker:** remove BC-083 librt.so.1 workaround — forge v3 bundles it
- **hub,scripts:** BC-086 close-gaps — CLI auto_fixed + digest host + reconcile diag

### Documentation
- **design:** product design + locked decisions
- **process:** DD-TGD process, stories, plan docs (BC-000)
- **stories:** mark BC-000 shipped
- **stories:** add BC-001 end-to-end mocked POC demo (ready)
- **stories:** mark BC-001 shipped
- **process:** add IDEAS backlog + wire it into DD-TGD
- **stories:** BC-003 draft + note BC-002 audit gaps in IDEAS
- **ideas:** capture CAP-T026 staleness as fix-opportunistically
- **memory:** BC-013 close-gap — scout migrated to HTTP; memory files updated
- **memory:** BC-014 close-gap — design-decisions.md matches same-track pull reality
- close BC-015 self-consistency gap + refresh make help
- **memory:** BC-016 close-gap — gate table lists all shipped drift guards
- **ideas:** log audit-protocol upgrade — grep-verify evidence pointers
- **ideas:** park BC-020 — dividend overlaps existing demo modes
- **ideas:** log 2 lint gaps surfaced by BC-020 live-smoke
- **ideas:** KEV lookup should resolve CVE aliases via grype 'related' field
- **ideas:** BC-025/026/027 close the big refactor entries
- **stories:** draft BC-030 (internet_facing detection) as ready
- **ideas:** log AVG-2909 operator action + promote BC-031 KEV-alias context
- **stories:** BC-031 draft — scan reconciliation + regression detection
- **plan:** BC-031 close-gap evidence rows for backfill tests
- **ideas:** verify-evidence-pointers should catch missing rows too
- **ideas:** log 3 BC-034 consumer candidates (heartbeat / digest refresh / purge)
- **stories:** mark BC-035 shipped
- **stories:** mark BC-030 shipped
- **bc-037:** post-ship audit — record scout-down verification date
- **bc-038:** post-ship audit — .env.example + check-full inclusion
- **bc-039:** post-ship audit — IDEAS followup for three-way sync
- **bc-040:** post-ship audit — Makefile wording + IDEAS cleanup
- **bc-041:** post-ship audit — remove stale IDEAS entry (shipped)
- **bc-042:** post-ship audit — remove stale IDEAS entry
- **stories:** mark BC-062 shipped in INDEX
- **lifecycle:** BC-088 add docs/LIFECYCLE.md + flaw inventory
- **lifecycle:** BC-088 fix state diagram — show OPEN → AUTO_FIXED edge
- **lifecycle:** BC-089 updates — snooze expiry paths

### Other
- Initial commit

## [v0.14.1] — 2026-11-05

### Fixed (BC-091)

- **kev:** CISA KEV endpoint stopped 403'ing anonymous fetches.
  Pre-BC-091 `_http_fetch` sent hardcoded `User-Agent: beaconry/0.1
  (+kev)` — stale version, no repo reference. CISA started blocking
  bot-looking anonymous requests on 2026-11-05; hetzner hub saw
  hourly 403s for ~4 h with `/healthz` reporting `"kev":
  "unfetched", "kev_count": 0`. BC-091 switches to dynamic
  `beaconry/<SOFTWARE_VERSION> (+https://github.com/bboortz/
  beaconry)` — identifiable UA with contact URL per CISA policy.
  Falls back to in-process cache on any 403 unchanged. CAP-T187.

### Added (BC-091)

- **env:** `BEACONRY_KEV_USER_AGENT` operator override for air-
  gapped / proxied setups that need a specific UA to traverse a
  gateway. Empty env falls back to the auto-generated default.
  Documented in `docs/VARIABLES.md` + `.env.example` +
  `docker-compose.yml`.

### No schema change

Pure network-header fix + one override env var.
- **hub:** BC-003 Phase 1+2 — callback validation + idempotence
- **hub:** BC-003 shipped — Phase 3 live tap session complete
- **hub:** BC-009 shipped — real fix execution via Telegram
- **hub+scout:** BC-010 shipped — container health observability (4 endpoints)
- **beaconry_health:** BC-012 shipped — shared package + compiler-enforced trap guard
- **scout+status:** BC-013 shipped — scout live HTTP probes + Docker-compose-ps-like status table
- **executor:** BC-014 shipped — DockerAdapter same-track pull (no silent :latest jump)
- **process:** BC-015 shipped — memory drift regression guard
- **hub:** BC-011 shipped — target-container health probe (operator-triggered)
- **process:** BC-016 shipped — docs drift guard (make help + README ↔ code)
- **e2e:** BC-017 shipped — automated end-to-end BC-011 probe flow (no Telegram)
- **e2e:** BC-018 shipped — telegram-mock service (supersedes BC-017 test-mode guard)
- **ci:** BC-019 — grep-verify every shipping-evidence pointer
- **hub:** BC-020 — KEV ingestion + wire urgency.classify_tier
- **ci:** BC-021 — Dockerfile sources lint
- **hub:** BC-022 — digest shows KEV markers for suppressed findings
- **hub:** BC-023 — KEV findings bypass MIN_CVSS filter
- **ci:** BC-024 — verify-traceability enforces reverse direction
- **hub:** BC-028 — KEV lookup resolves grype's relatedVulnerabilities aliases
- **hub:** BC-029 — append-only audit log of operator-visible state changes
- **hub:** BC-031 — scan reconciliation + regression detection
- **hub:** BC-032 — fix viability indicator + fixed-in parity
- **hub:** BC-033 — verify_fix scans source_ref, not hardcoded :latest
- **hub:** BC-034 — persist last_seen_ts per finding
- **hub:** BC-035 notification adapter architecture + heartbeat for stale findings
- **hub,scout:** BC-030 internet_facing detection → Tier 1 fires
- **hub:** BC-036 decouple digest cadence from scan cadence
- **scout:** BC-037 on-demand scan trigger — make scan
- **hub,scenarios:** BC-038 live-stack scenario harness — make test-scenarios
- **docs,scripts:** BC-039 docs/VARIABLES.md + verify-variables drift lint
- **docs,scripts:** BC-040 three-way env sync — VARIABLES.md ↔ .env.example ↔ docker-compose.yml
- **scripts:** BC-041 verify-evidence-pointers reverse check
- **scripts,hub:** BC-042 `make audit` CLI + `scan_received` event
- **hub:** BC-043 CVE alias on non-CVE primary ids — KEV paper trail
- **scripts:** BC-044 live-smoke CLI harness + autodetected scenario coverage
- **hub,scripts:** BC-055 HTTP-only CLI → hub (no SSH)
- **docs,scripts:** BC-056 INSTALL.md + verify-install-doc (short-name reshape)
- **scripts,hub:** BC-057 make findings HOST= + 🖥️ multi-host notification lead
- **hub:** BC-058 mutation + audit cleanup
- **hub:** BC-059 callback_data carries CVE id
- **hub:** BC-060 notify_sent_failed audit symmetry
- **hub,scripts:** BC-061 scout fleet registry with liveness status
- **hub,scout,scripts:** BC-062 versioning (software + API + schema) + SemVer bump + pre-push gate
- **hub,scout,scripts:** BC-063 fleet health visibility + scout heartbeat
- **hub,scripts:** BC-064 FLEET LAST HEARTBEAT + stale-heartbeat unknown health
- **hub:** BC-066 manual executor as default + configurable adapter + 1h reminder loop
- **ci,scripts:** BC-069 release toolchain — CI + make soak + auto-CHANGELOG
- **scripts,build:** BC-070 operator CLI install — `pip install -e . → beaconry …`
- **ci,scripts:** BC-074 release automation + 24h soak + feature-complete lint + CI-failure log
- **scripts,hub,scout:** BC-075 metrics + soak threshold signals + privacy-first opt-in
- **scripts:** BC-076 beaconry metrics --remote reads from hub /audit over HTTP
- **docker,scout,hub:** BC-077 non-root hub + scout containers
- **docker,hub,scout:** BC-078 distroless runtime for hub + scout
- **ci,make,tests:** BC-079 pre-push runs full container verification
- **make,scripts:** BC-080 make cleanup — teardown + image removal after tests
- **ci,docker,python:** BC-082 homelab-deployment coverage + base bumps
- **docker,ci,tests:** BC-083 forge base image + verify-base-images preflight
- **hub,notify:** BC-086 auto-fix status + counter threshold + regressed-from-auto-fix
- **hub,notify:** BC-087 wire last_seen into notifications + BEACONRY_LOG_LEVEL
- **hub,notify:** BC-089 snooze with real expiry + configurable buttons
- **hub,notify:** BC-090 auto-fix regression body actually reaches Telegram

### Fixed
- **hub,demo:** BC-002 robust-notify — HTML parse mode, cap, dedup, digest
- **docs,scripts:** BC-004 close-gaps — CAP-B/T prefix, required fields, hook, self-consistency
- **scripts:** BC-004 design-link + origin-story validation
- **scripts:** BC-004 enforce description as required field
- **hub:** BC-005 close two in-container regressions found by make demo
- BC-005 close-gaps — tests + story self-consistency + lessons
- **scripts:** enforce plan doc on fix/* and feat/* branches
- smoke mode broken + CAP-id regex truncates at hyphens
- BC-006 close-gaps — test graceful-skip + README skip-note
- BC-007 close-gaps — tier in sort key, schema migration, docs
- BC-007 load_finding reads tier + migration + round-trip tests
- **scripts:** BC-008 automate-audit — Shipping evidence enforced on shipped stories
- BC-003 close-gaps — run real_tools, add missing row, warn on scenario-count
- **beaconry_health:** BC-012 close-gap — pyright actually enforces the trap guard
- **hub+findings:** BC-011 close-gaps — findings CLI accepts new statuses + button-attach test
- **compose:** BC-011 env propagation — BEACONRY_HEALTH_PROBES/FIX_ENABLED/MOCK now actually propagate
- **findings:** BC-007 close-gap — auto-route to compose hub's volume DB
- **hub:** BC-011 close-gap — use module-level _waterfall binding in verify branch
- **tests:** BC-018 close-gap — unit coverage for telegram-mock + hub env dials
- **hub:** BC-020 close-gap — ship kev.py + urgency.py in the image
- **memory:** BC-020 close-gap — add memory drift ban
- **hub:** BC-020 close-gap — treat empty BEACONRY_KEV_FEED_URL as unset
- **docs:** BC-023 close-gap — Covers block + design-decisions annotation
- **docs:** BC-029 close-gap — add Gherkin block + gitignore audit.log
- **hub:** BC-032 close-gap — handle grype's 'not available' sentinel
- **docs:** BC-033 close-gap — add Scenario 3 test coverage
- **hub:** BC-035 — tolerate empty-string env vars from compose \${VAR:-}
- **hub:** BC-035/BC-030 close-gap — notify path works inside container
- **scenarios:** BC-038 close-gap — live-run fixes
- **audit:** BC-043 close-gap — make audit CVE=X matches alias too
- **audit:** FOLLOW output buffering + inline-script error handling
- **hub:** BC-065 digest content-change bypass is now opt-out via env
- **hub,executor,audit:** BC-067 quality sweep — 5 severity-1 bugs
- **hub:** BC-068 defensive cleanup — reconcile FD-leak + fix_version blank sanitize
- **ci,tests:** BC-071 CI env leak — BEACONRY_SKIP_VERSION_BUMP leaked into pytest
- **build:** explicit packages list to bypass flat-layout detection
- **hooks:** use .venv/bin/pytest instead of system pytest
- **deps:** BC-072 pytest 8.3.3 → 9.0.3 + pytest-asyncio 0.24.0 → 1.4.1.post1
- **deps:** BC-073 python-dotenv 1.0.1 → 1.2.2 + requests 2.32.3 → 2.33.0
- **docker:** BC-081 bump docker CLI 27.5.1 → 29.9.2 for Go stdlib ≥1.26.9

### Changed
- initial commit (gitignore only; main is CI-promoted)
- **build:** Makefile, pyproject, pre-commit, dev requirements
- **poc:** pytest pyramid skeleton + sanity test
- **build:** lint + type cleanup, lower coverage floor until BC-001
- **poc:** BC-001 red integration test + plan doc
- **poc,hub:** BC-002 red state — tests + dispatch stubs + plan
- **poc:** BC-004 red state — traceability enforcement scenarios
- **poc:** BC-005 red — scout glob filter stubs + Compose demo plan
- **e2e:** use hub.save_finding instead of raw INSERT for seeding
- **ci:** run test-e2e as part of make check-full
- **ci:** complexity gates + 85/65% coverage floors
- **scout:** extract parse_ss_output; pragma subprocess shells; un-omit from coverage
- **hub:** on_button_click → dispatch dict of per-action handlers
- **ci:** verify_traceability.run → gates registry
- **make:** fix 3 polish issues surfaced by running all make targets
- **ops:** live-stack rebuild discipline — make live-rebuild + live-status
- **process:** breaking-change-in-planning discipline + BC-031 close-gap
- **hub:** BC-035 close-gap — cover migration backfill, format_heartbeat, mock-print
- **hub:** BC-030 close-gap — audit event carries internet_facing + .env.example
- **docker:** remove BC-083 librt.so.1 workaround — forge v3 bundles it
- **hub,scripts:** BC-086 close-gaps — CLI auto_fixed + digest host + reconcile diag

### Documentation
- **design:** product design + locked decisions
- **process:** DD-TGD process, stories, plan docs (BC-000)
- **stories:** mark BC-000 shipped
- **stories:** add BC-001 end-to-end mocked POC demo (ready)
- **stories:** mark BC-001 shipped
- **process:** add IDEAS backlog + wire it into DD-TGD
- **stories:** BC-003 draft + note BC-002 audit gaps in IDEAS
- **ideas:** capture CAP-T026 staleness as fix-opportunistically
- **memory:** BC-013 close-gap — scout migrated to HTTP; memory files updated
- **memory:** BC-014 close-gap — design-decisions.md matches same-track pull reality
- close BC-015 self-consistency gap + refresh make help
- **memory:** BC-016 close-gap — gate table lists all shipped drift guards
- **ideas:** log audit-protocol upgrade — grep-verify evidence pointers
- **ideas:** park BC-020 — dividend overlaps existing demo modes
- **ideas:** log 2 lint gaps surfaced by BC-020 live-smoke
- **ideas:** KEV lookup should resolve CVE aliases via grype 'related' field
- **ideas:** BC-025/026/027 close the big refactor entries
- **stories:** draft BC-030 (internet_facing detection) as ready
- **ideas:** log AVG-2909 operator action + promote BC-031 KEV-alias context
- **stories:** BC-031 draft — scan reconciliation + regression detection
- **plan:** BC-031 close-gap evidence rows for backfill tests
- **ideas:** verify-evidence-pointers should catch missing rows too
- **ideas:** log 3 BC-034 consumer candidates (heartbeat / digest refresh / purge)
- **stories:** mark BC-035 shipped
- **stories:** mark BC-030 shipped
- **bc-037:** post-ship audit — record scout-down verification date
- **bc-038:** post-ship audit — .env.example + check-full inclusion
- **bc-039:** post-ship audit — IDEAS followup for three-way sync
- **bc-040:** post-ship audit — Makefile wording + IDEAS cleanup
- **bc-041:** post-ship audit — remove stale IDEAS entry (shipped)
- **bc-042:** post-ship audit — remove stale IDEAS entry
- **stories:** mark BC-062 shipped in INDEX
- **lifecycle:** BC-088 add docs/LIFECYCLE.md + flaw inventory
- **lifecycle:** BC-088 fix state diagram — show OPEN → AUTO_FIXED edge
- **lifecycle:** BC-089 updates — snooze expiry paths

### Other
- Initial commit

## [v0.14.0] — 2026-11-05

### Fixed (BC-090)

- **notify:** auto-fix regressions now render the **distinct** `🔥
  AUTO-FIX REGRESSED` body on Telegram, not the generic BC-031 `🔥
  FIX REGRESSED` body. Pre-BC-090 the `format_auto_fix_regression`
  helper existed and was unit-tested but was dead code on the real
  path — `notify_auto_fix_regression` fell through to
  `_dispatch_regression` → `TelegramAdapter.send_regression` →
  `format_regression`. Fix: new `send_auto_fix_regression` adapter
  method + dedicated `notify_auto_fix_regression` dispatcher in
  the `notifiers` package + `"auto_fix_regression"` formatter key.
  Closes the CAP-T178 gap BC-086 intended. CAP-T185.

### Added (BC-090)

- **env:** `BEACONRY_AUTO_FIX_REGRESSION_NOTIFY_ENABLED` (default
  `1`). `0` → audit-only: status still flips to
  `regressed_from_auto_fix`, that audit event still fires, plus a
  new `notify_suppressed` event records the deliberate silence. No
  Telegram ping. Per-kind silence knob matching the BC-089 pattern.
  CAP-T186.
- **adapter:** `NotificationAdapter.send_auto_fix_regression`
  default no-op (base.py) — out-of-tree adapters stay back-compat
  without changes.

### No schema change

Pure notifier wiring + one env var. Finding row shape unchanged.

### Added
- **poc:** scout + hub + executor plugins + compose stack
- **hub,demo:** BC-001 end-to-end mocked POC demo
- **demo,hub:** BC-002 green — real pipeline on nginx:1.24, shipped
- **docs,scripts:** BC-004 capabilities + bidirectional traceability
- **scout,compose,make:** BC-005 multi-image Compose demo
- **make,docs:** BC-006 fast + full gate tiers
- **cli:** BC-007 `make findings` — read-only view of CVE state
- **process:** BC-008 pre-ship audit protocol (DD-TGD step 6a)
- **hub:** BC-003 Phase 1+2 — callback validation + idempotence
- **hub:** BC-003 shipped — Phase 3 live tap session complete
- **hub:** BC-009 shipped — real fix execution via Telegram
- **hub+scout:** BC-010 shipped — container health observability (4 endpoints)
- **beaconry_health:** BC-012 shipped — shared package + compiler-enforced trap guard
- **scout+status:** BC-013 shipped — scout live HTTP probes + Docker-compose-ps-like status table
- **executor:** BC-014 shipped — DockerAdapter same-track pull (no silent :latest jump)
- **process:** BC-015 shipped — memory drift regression guard
- **hub:** BC-011 shipped — target-container health probe (operator-triggered)
- **process:** BC-016 shipped — docs drift guard (make help + README ↔ code)
- **e2e:** BC-017 shipped — automated end-to-end BC-011 probe flow (no Telegram)
- **e2e:** BC-018 shipped — telegram-mock service (supersedes BC-017 test-mode guard)
- **ci:** BC-019 — grep-verify every shipping-evidence pointer
- **hub:** BC-020 — KEV ingestion + wire urgency.classify_tier
- **ci:** BC-021 — Dockerfile sources lint
- **hub:** BC-022 — digest shows KEV markers for suppressed findings
- **hub:** BC-023 — KEV findings bypass MIN_CVSS filter
- **ci:** BC-024 — verify-traceability enforces reverse direction
- **hub:** BC-028 — KEV lookup resolves grype's relatedVulnerabilities aliases
- **hub:** BC-029 — append-only audit log of operator-visible state changes
- **hub:** BC-031 — scan reconciliation + regression detection
- **hub:** BC-032 — fix viability indicator + fixed-in parity
- **hub:** BC-033 — verify_fix scans source_ref, not hardcoded :latest
- **hub:** BC-034 — persist last_seen_ts per finding
- **hub:** BC-035 notification adapter architecture + heartbeat for stale findings
- **hub,scout:** BC-030 internet_facing detection → Tier 1 fires
- **hub:** BC-036 decouple digest cadence from scan cadence
- **scout:** BC-037 on-demand scan trigger — make scan
- **hub,scenarios:** BC-038 live-stack scenario harness — make test-scenarios
- **docs,scripts:** BC-039 docs/VARIABLES.md + verify-variables drift lint
- **docs,scripts:** BC-040 three-way env sync — VARIABLES.md ↔ .env.example ↔ docker-compose.yml
- **scripts:** BC-041 verify-evidence-pointers reverse check
- **scripts,hub:** BC-042 `make audit` CLI + `scan_received` event
- **hub:** BC-043 CVE alias on non-CVE primary ids — KEV paper trail
- **scripts:** BC-044 live-smoke CLI harness + autodetected scenario coverage
- **hub,scripts:** BC-055 HTTP-only CLI → hub (no SSH)
- **docs,scripts:** BC-056 INSTALL.md + verify-install-doc (short-name reshape)
- **scripts,hub:** BC-057 make findings HOST= + 🖥️ multi-host notification lead
- **hub:** BC-058 mutation + audit cleanup
- **hub:** BC-059 callback_data carries CVE id
- **hub:** BC-060 notify_sent_failed audit symmetry
- **hub,scripts:** BC-061 scout fleet registry with liveness status
- **hub,scout,scripts:** BC-062 versioning (software + API + schema) + SemVer bump + pre-push gate
- **hub,scout,scripts:** BC-063 fleet health visibility + scout heartbeat
- **hub,scripts:** BC-064 FLEET LAST HEARTBEAT + stale-heartbeat unknown health
- **hub:** BC-066 manual executor as default + configurable adapter + 1h reminder loop
- **ci,scripts:** BC-069 release toolchain — CI + make soak + auto-CHANGELOG
- **scripts,build:** BC-070 operator CLI install — `pip install -e . → beaconry …`
- **ci,scripts:** BC-074 release automation + 24h soak + feature-complete lint + CI-failure log
- **scripts,hub,scout:** BC-075 metrics + soak threshold signals + privacy-first opt-in
- **scripts:** BC-076 beaconry metrics --remote reads from hub /audit over HTTP
- **docker,scout,hub:** BC-077 non-root hub + scout containers
- **docker,hub,scout:** BC-078 distroless runtime for hub + scout
- **ci,make,tests:** BC-079 pre-push runs full container verification
- **make,scripts:** BC-080 make cleanup — teardown + image removal after tests
- **ci,docker,python:** BC-082 homelab-deployment coverage + base bumps
- **docker,ci,tests:** BC-083 forge base image + verify-base-images preflight
- **hub,notify:** BC-086 auto-fix status + counter threshold + regressed-from-auto-fix
- **hub,notify:** BC-087 wire last_seen into notifications + BEACONRY_LOG_LEVEL
- **hub,notify:** BC-089 snooze with real expiry + configurable buttons

### Fixed
- **hub,demo:** BC-002 robust-notify — HTML parse mode, cap, dedup, digest
- **docs,scripts:** BC-004 close-gaps — CAP-B/T prefix, required fields, hook, self-consistency
- **scripts:** BC-004 design-link + origin-story validation
- **scripts:** BC-004 enforce description as required field
- **hub:** BC-005 close two in-container regressions found by make demo
- BC-005 close-gaps — tests + story self-consistency + lessons
- **scripts:** enforce plan doc on fix/* and feat/* branches
- smoke mode broken + CAP-id regex truncates at hyphens
- BC-006 close-gaps — test graceful-skip + README skip-note
- BC-007 close-gaps — tier in sort key, schema migration, docs
- BC-007 load_finding reads tier + migration + round-trip tests
- **scripts:** BC-008 automate-audit — Shipping evidence enforced on shipped stories
- BC-003 close-gaps — run real_tools, add missing row, warn on scenario-count
- **beaconry_health:** BC-012 close-gap — pyright actually enforces the trap guard
- **hub+findings:** BC-011 close-gaps — findings CLI accepts new statuses + button-attach test
- **compose:** BC-011 env propagation — BEACONRY_HEALTH_PROBES/FIX_ENABLED/MOCK now actually propagate
- **findings:** BC-007 close-gap — auto-route to compose hub's volume DB
- **hub:** BC-011 close-gap — use module-level _waterfall binding in verify branch
- **tests:** BC-018 close-gap — unit coverage for telegram-mock + hub env dials
- **hub:** BC-020 close-gap — ship kev.py + urgency.py in the image
- **memory:** BC-020 close-gap — add memory drift ban
- **hub:** BC-020 close-gap — treat empty BEACONRY_KEV_FEED_URL as unset
- **docs:** BC-023 close-gap — Covers block + design-decisions annotation
- **docs:** BC-029 close-gap — add Gherkin block + gitignore audit.log
- **hub:** BC-032 close-gap — handle grype's 'not available' sentinel
- **docs:** BC-033 close-gap — add Scenario 3 test coverage
- **hub:** BC-035 — tolerate empty-string env vars from compose \${VAR:-}
- **hub:** BC-035/BC-030 close-gap — notify path works inside container
- **scenarios:** BC-038 close-gap — live-run fixes
- **audit:** BC-043 close-gap — make audit CVE=X matches alias too
- **audit:** FOLLOW output buffering + inline-script error handling
- **hub:** BC-065 digest content-change bypass is now opt-out via env
- **hub,executor,audit:** BC-067 quality sweep — 5 severity-1 bugs
- **hub:** BC-068 defensive cleanup — reconcile FD-leak + fix_version blank sanitize
- **ci,tests:** BC-071 CI env leak — BEACONRY_SKIP_VERSION_BUMP leaked into pytest
- **build:** explicit packages list to bypass flat-layout detection
- **hooks:** use .venv/bin/pytest instead of system pytest
- **deps:** BC-072 pytest 8.3.3 → 9.0.3 + pytest-asyncio 0.24.0 → 1.4.1.post1
- **deps:** BC-073 python-dotenv 1.0.1 → 1.2.2 + requests 2.32.3 → 2.33.0
- **docker:** BC-081 bump docker CLI 27.5.1 → 29.9.2 for Go stdlib ≥1.26.9

### Changed
- initial commit (gitignore only; main is CI-promoted)
- **build:** Makefile, pyproject, pre-commit, dev requirements
- **poc:** pytest pyramid skeleton + sanity test
- **build:** lint + type cleanup, lower coverage floor until BC-001
- **poc:** BC-001 red integration test + plan doc
- **poc,hub:** BC-002 red state — tests + dispatch stubs + plan
- **poc:** BC-004 red state — traceability enforcement scenarios
- **poc:** BC-005 red — scout glob filter stubs + Compose demo plan
- **e2e:** use hub.save_finding instead of raw INSERT for seeding
- **ci:** run test-e2e as part of make check-full
- **ci:** complexity gates + 85/65% coverage floors
- **scout:** extract parse_ss_output; pragma subprocess shells; un-omit from coverage
- **hub:** on_button_click → dispatch dict of per-action handlers
- **ci:** verify_traceability.run → gates registry
- **make:** fix 3 polish issues surfaced by running all make targets
- **ops:** live-stack rebuild discipline — make live-rebuild + live-status
- **process:** breaking-change-in-planning discipline + BC-031 close-gap
- **hub:** BC-035 close-gap — cover migration backfill, format_heartbeat, mock-print
- **hub:** BC-030 close-gap — audit event carries internet_facing + .env.example
- **docker:** remove BC-083 librt.so.1 workaround — forge v3 bundles it
- **hub,scripts:** BC-086 close-gaps — CLI auto_fixed + digest host + reconcile diag

### Documentation
- **design:** product design + locked decisions
- **process:** DD-TGD process, stories, plan docs (BC-000)
- **stories:** mark BC-000 shipped
- **stories:** add BC-001 end-to-end mocked POC demo (ready)
- **stories:** mark BC-001 shipped
- **process:** add IDEAS backlog + wire it into DD-TGD
- **stories:** BC-003 draft + note BC-002 audit gaps in IDEAS
- **ideas:** capture CAP-T026 staleness as fix-opportunistically
- **memory:** BC-013 close-gap — scout migrated to HTTP; memory files updated
- **memory:** BC-014 close-gap — design-decisions.md matches same-track pull reality
- close BC-015 self-consistency gap + refresh make help
- **memory:** BC-016 close-gap — gate table lists all shipped drift guards
- **ideas:** log audit-protocol upgrade — grep-verify evidence pointers
- **ideas:** park BC-020 — dividend overlaps existing demo modes
- **ideas:** log 2 lint gaps surfaced by BC-020 live-smoke
- **ideas:** KEV lookup should resolve CVE aliases via grype 'related' field
- **ideas:** BC-025/026/027 close the big refactor entries
- **stories:** draft BC-030 (internet_facing detection) as ready
- **ideas:** log AVG-2909 operator action + promote BC-031 KEV-alias context
- **stories:** BC-031 draft — scan reconciliation + regression detection
- **plan:** BC-031 close-gap evidence rows for backfill tests
- **ideas:** verify-evidence-pointers should catch missing rows too
- **ideas:** log 3 BC-034 consumer candidates (heartbeat / digest refresh / purge)
- **stories:** mark BC-035 shipped
- **stories:** mark BC-030 shipped
- **bc-037:** post-ship audit — record scout-down verification date
- **bc-038:** post-ship audit — .env.example + check-full inclusion
- **bc-039:** post-ship audit — IDEAS followup for three-way sync
- **bc-040:** post-ship audit — Makefile wording + IDEAS cleanup
- **bc-041:** post-ship audit — remove stale IDEAS entry (shipped)
- **bc-042:** post-ship audit — remove stale IDEAS entry
- **stories:** mark BC-062 shipped in INDEX
- **lifecycle:** BC-088 add docs/LIFECYCLE.md + flaw inventory
- **lifecycle:** BC-088 fix state diagram — show OPEN → AUTO_FIXED edge
- **lifecycle:** BC-089 updates — snooze expiry paths

### Other
- Initial commit

## [v0.13.0] — 2026-11-05

### Fixed (BC-089)

- **snooze:** the `[⏰ Snooze 4h]` button now actually snoozes for 4
  hours. Pre-BC-089 the hour count was parsed from the callback_data
  and printed in the operator reply but never stored — "4h" meant
  "indefinitely". New schema column `snooze_until_ts` + periodic sweep
  (every `BEACONRY_SNOOZE_SWEEP_INTERVAL_S`, default 60s) flips
  `snoozed → open` at expiry and fires a distinct `⏰ Snooze expired`
  notification unless `BEACONRY_SNOOZE_NOTIFY_ON_EXPIRE=0`. CAP-T183.
- **snooze:** inline keyboard now renders one button per entry in
  `BEACONRY_SNOOZE_DEFAULT_HOURS` (default `1,4,24,168` →
  `[⏰ 1h] [⏰ 4h] [⏰ 1d] [⏰ 1w]`). Operator can collapse to a
  single button (`BEACONRY_SNOOZE_DEFAULT_HOURS=4`) or add arbitrary
  durations. CAP-T184.

### ⚠ Breaking behaviour (operator-visible)

- **New snoozes WILL re-open at expiry.** Before: snooze = silence
  forever. After: snooze = defer by N hours. Operators who depended
  on indefinite silence should use `[✅ Accept risk]` instead, or
  set `BEACONRY_SNOOZE_NOTIFY_ON_EXPIRE=0` for silent re-open
  (the audit event still fires, so expiry is observable without the
  Telegram ping).
- **Legacy snoozes (pre-v0.13.0) stay indefinite.** Rows with
  `snooze_until_ts=0` (the schema default for legacy rows) are
  filtered out of the sweep (`WHERE snooze_until_ts > 0`). Pre-BC-089
  state is preserved across upgrade — no surprise re-opens.

### Schema

- v5 → **v6**. `ALTER TABLE findings ADD COLUMN snooze_until_ts
  INTEGER DEFAULT 0`. Idempotent (sqlite silently ignores if column
  exists). `EXPECTED_SCHEMA_VERSION` bumped to `6`.

## [v0.12.1] — 2026-11-05

### Documentation (BC-088)

- **lifecycle reference:** new `docs/LIFECYCLE.md` — single authoritative
  reference for how a finding moves from grype match through
  notification, operator action, fix execution, verification, and
  reconcile. Twelve descriptive sections (state diagram, ingest, row
  timestamps, notifications, buttons, fix pipeline, health waterfall,
  reconcile scope, isolation, timers, audit events, precedence) plus
  five known-flaw inventories (unclearities, necessary improvements,
  contradictions/inconsistencies, missing implementation, known bugs).
  Each flaw entry carries a code-grep pointer or a
  `memory/design-decisions.md` pointer. Future BCs materially changing
  the lifecycle are expected to update this doc as part of DoD (honour
  system; no lint today).
- **capability:** CAP-T182 registered in `docs/CAPABILITIES.md`
  pointing at the new doc.
- **test:** `poc/tests/unit/test_lifecycle_doc.py` — doc-structure test
  (every status mentioned, every timer env var mentioned, every flaw
  section populated, CAP-T182 registered). Catches drift when a future
  BC renames a status enum or timer var.

### No code behavior changes

Pure docs + a doc-structure test. No runtime code touched; no new env
var; no new state transition. Operator-visible behaviour of beaconry
v0.12.1 is identical to v0.12.0.

## [v0.12.0] — 2026-11-05

### Fixed (BC-087)

- **notify:** every Telegram alert body now carries the `Last seen: X ago`
  line. BC-086 added the format-helper hook (`format_last_seen` +
  `last_seen_ts` kwarg on every `format_*`) and unit-tested it in
  isolation, but the production `notify*()` call sites never loaded
  `last_seen_ts` from the DB → kwarg defaulted to `None` → line
  omitted from every real alert. BC-087 adds `_load_last_seen` + wires
  it through `hub.notify / notify_regression / notify_heartbeat /
  notify_auto_fix_regression → _dispatch_* → adapter.send_* →
  format_*`. Adapter base signatures gained an optional
  `last_seen_ts` kwarg; TelegramAdapter forwards it. CAP-T180.

### Added (BC-087)

- **env:** `BEACONRY_LOG_LEVEL` (default `INFO`; values `DEBUG|INFO|
  WARNING|ERROR|CRITICAL`; invalid values silently fall back to INFO
  with a one-time warning). Applies to **both** hub and scout root
  loggers at process boot — a single operator dial instead of two.
  Set to `DEBUG` to re-enable BC-086's per-finding reconcile trace +
  `bump_last_seen` diagnostic. Documented in `docs/VARIABLES.md` +
  `poc/.env.example` + wired into `poc/docker-compose.yml`. CAP-T181.

### Changed (BC-087)

- **hub:** BC-086's `reconcile_scan ENTER`, per-finding `reconcile
  fid=...`, and `bump_last_seen host=...` diagnostic lines downgraded
  from `log.info` to `log.debug`. These were shipped at INFO in
  v0.11.1 to prove natural counter progression; once proven, the
  ~20-lines-per-scan-per-host noise is net-negative at INFO.
  Operator re-enables with `BEACONRY_LOG_LEVEL=DEBUG` + rebuild.

## [v0.11.1] — 2026-11-05

### Fixed
- **scripts:** `make findings STATUS=auto_fixed` now works. The CLI's
  argparse choices list was not updated when BC-086 added the
  `auto_fixed` status; operator saw "invalid choice: 'auto_fixed'".
  Caught during BC-086 ship-verification on the live stack.

### Changed
- **notify:** digest body now shows the host as a header line AND the
  source_ref (image/target) per bullet. Operator can tell which
  service each suppressed finding belongs to without cross-referencing
  `make findings`. Source refs > 40 chars get truncated with "…" to
  keep bullets readable.

### Added (temporary diagnostic)
- **hub:** `reconcile_scan` + `bump_last_seen` emit INFO-level logs
  during BC-086 ship-verification so we can watch counter progression
  in production. During the v0.11.0 roll-out we observed 11 findings
  stuck at `absent_count=2` after 3 post-upgrade scans (expected 3)
  — manual probe correctly flipped them to `auto_fixed`, so the
  counter logic itself is sound. Logging left in place to prove (or
  disprove) that natural scans increment correctly on every pass.
  Removal (downgrade to `log.debug`) follows once the puzzle is
  understood.

## [v0.11.0] — 2026-11-05

### ⚠ Breaking behavior changes (BC-086)

- **Status enum gains `auto_fixed`**. Previously `open|regressed +
  absent` flipped to `fixed_verified`; it now flips to `auto_fixed`
  so operator can distinguish "system-inferred mitigation" from
  "operator-triggered fix + re-scan confirmed clean". Any external
  dashboard parsing `beaconry metrics` / `make findings` output needs
  to handle the new value.
- **Reconcile cadence is counter-based.** Pre-BC-086 reconcile closed
  on first missing scan; BC-086 requires `BEACONRY_AUTO_FIX_THRESHOLD`
  consecutive misses (default 3 — operator can set `=1` to reproduce
  old cadence). Transient scan glitches no longer silently resolve
  long-lived findings.
- **Schema version 4 → 5** via two new columns on `findings`:
  `absent_count INT DEFAULT 0`, `auto_fixed_ts INT DEFAULT 0`.
  ALTER TABLE migration is backward-compat on existing DBs.
- **Audit event `auto_closed` renamed to `finding_auto_fixed`**.
  Pre-existing audit log entries are preserved; new events use the
  BC-086 name. `make audit EVENT=finding_auto_fixed` surfaces them.

### Added
- **hub,notify:** BC-086 covers `snoozed` and `accepted` findings in
  reconcile. A snoozed finding whose image is removed now auto-closes
  after the threshold; previously it stayed snoozed forever. Closes
  the gap observed with `python:3.11-slim-bookworm · libc-bin` on
  hetzner-staging: operator removed the image, re-scanned, but the
  finding never cleared.
- **hub,notify:** Distinct `🔥 AUTO-FIX REGRESSED` Telegram alert +
  `regressed_from_auto_fix` audit event. If an auto-closed finding
  reappears in a later scan, the operator sees it's the system's
  inference (not an operator fix) that was overturned. Different
  root-cause investigation path.
- **hub,notify:** Every finding notification (new alert, heartbeat
  reminder, regression, auto-fix regression) now carries a
  `Last seen: X ago` line. Operator can tell at a glance whether the
  CVE was observed seconds ago, hours ago, or days ago.
- **hub:** `BEACONRY_AUTO_FIX_THRESHOLD` env var (default `3`,
  minimum `1`). Documented in VARIABLES.md. Surfaced in `/healthz`
  under `checks.auto_fix_threshold` so `make status` shows the
  configured value.

### Changed
- **hub:** `reconcile_scan(host, scanned_ids)` return signature
  changed from `(regressed, auto_closed)` 2-tuple to
  `(regressed, auto_fixed, regressed_from_auto_fix)` 3-tuple.
  Only in-process callers (pre-BC-086 external users: none).

## [v0.10.1] — 2026-11-04

### Removed
- **docker:** BC-083 librt.so.1 workaround (hub + scout Dockerfiles).
  Forge image v3 (`ghcr.io/bboortz/python-scratch-trixie:3.13`) now
  bundles `librt.so.1` + `libm.so.6` + `libutil.so.1` + `libresolv.so.2`
  at `/lib/x86_64-linux-gnu/`, so beaconry no longer needs to COPY the
  stub in from the builder stage. Verified: `pydantic_core` loads
  cleanly, cold-start suite green (including the generic
  `test_hub_can_import_all_runtime_deps` probe that originally caught
  the gap).

## [v0.10.0] — 2026-11-04

### Changed — runtime image provider swap

- **docker:** runtime stage FROM
  `gcr.io/distroless/python3-debian13:nonroot` →
  `ghcr.io/bboortz/python-scratch-trixie:3.13` (self-owned forge
  image). Pre-flight recorded in the BC-083 plan doc; the forge
  image bakes in the full BC-078 contract (uid 65532, ENTRYPOINT
  `/usr/local/bin/python3`, `SSL_CERT_FILE`, `/etc/passwd` entry
  for nonroot, `PYTHONDONTWRITEBYTECODE`, `PYTHONFAULTHANDLER`,
  no shell). Distroless debian12/13 stays accepted by
  `verify-pinned-versions` for one release window (soft
  migration). Zero chown migration for operators already on
  BC-082's debian13 — uid 65532 preserved.

### Added

- **make,scripts:** `make verify-base-images` (BC-083 CAP-T171)
  — standing preflight. Enumerates every `FROM` across all
  Dockerfiles, does an anonymous bearer-token + manifest fetch
  against each registry (ghcr.io / gcr.io / docker.io), fails if
  any returns 401/403/404/5xx. 24h on-disk cache in
  `.beaconry-cache/verify-base-images.json` keeps the fast tier
  from being network-bound every run. Catches: forge repo goes
  private, `:3.13` floating tag gets retagged, registry outage.
  `BEACONRY_SKIP_BASE_IMAGES=1` opts out (dev-only / air-gapped CI).
- **tests:** `test_BC_083_forge_image.py` + `test_BC_083_forge_runtime.py`
  — Dockerfile-level + live-runtime assertions. The runtime file
  (@cold_start marker) verifies inside the booted hub/scout
  container: no shell, uid=65532, HTTPS works, grype/syft/docker
  binaries runnable, `pwd.getpwuid(65532)` resolves to `nonroot`.

## [v0.9.0] — 2026-11-03

### Added — BC-082 homelab-deployment coverage

Three new gates close the "will fresh homelab deploy succeed?" gap
that BC-078/BC-079/BC-081 ship-verification kept finding:

- **`make verify-pinned-versions`** (fast tier) — Dockerfile floor
  lint. `DOCKER_CLI_VERSION ≥ 29.9.2` (BC-081 floor), builder
  base `python:3.11+-slim-{bookworm,trixie}`, runtime
  `gcr.io/distroless/python3-debian12:nonroot` exactly. A silent
  Dockerfile revert can't bring back the BC-081 vuln pin.
- **`make dogfood-scan`** (full tier) — grype rescan of
  `beaconry-hub:latest` + `beaconry-scout:latest`, fails on any
  High/Critical with `fix-available: true`. Catches the next
  "beaconry alerts on itself" case at pre-push time, not on an
  operator's Telegram.
- **`make test-cold-start`** (full tier) — brand-new compose
  project (`beaconry-cold-start`, ports 9100/9101) with throwaway
  volumes and **real** grype DB download. Asserts all 8 health
  endpoints (hub + scout × livez/readyz/startupz/healthz) go
  green within 180 s. The honest "fresh homelab install" probe.

### Changed — baseline bumps the dogfood gate forced

- **docker:** builder base `python:3.11-slim-bookworm` →
  `python:3.13-slim-trixie`. Runtime
  `gcr.io/distroless/python3-debian12:nonroot` →
  `gcr.io/distroless/python3-debian13:nonroot` (ships Python
  3.13.5). This fixes ~10 High/Critical fixable CVEs in base
  system libs (libc6, libssl3, libexpat1, perl-base) that
  dogfood-grype flagged on its first run against the
  pre-BC-082 image. Operators upgrading from v0.8.x will
  rebuild with debian13 on next `make live-rebuild` — same
  distroless contract (uid 65532, no shell), just a newer OS
  underneath.
- **python:** hub + scout `fastapi 0.115 → 0.162`;
  `starlette` pinned explicitly to `1.3.1` because fastapi
  0.162 allows `>=0.46` which could resolve older. Fixes 3
  High CVEs (GHSA-82w8-qh3p-5jfq, GHSA-wqp7-x3pw-xc5r,
  GHSA-7f5h-v6xp-fcq8).
- `make check-full` adds ~2:00 (cold-start) + ~20 s (dogfood).
  New pre-push time ~5 min. `BEACONRY_SKIP_DOGFOOD=1` is the
  dev-only emergency escape.
- `docs/plan/CI-FAILURES.md` gets a BC-081 entry so dogfood-grype's
  origin stays traceable, plus a BC-082 entry documenting the
  first-run baseline findings as the "learn" moment.

## [v0.8.2] — 2026-11-03

### Fixed
- **docker:** bump pinned `DOCKER_CLI_VERSION` 27.5.1 → 29.9.2 in
  `poc/hub/Dockerfile`. The 27.5.1 static binary was built with
  Go 1.22.11; grype matched it against GO-2026-6604 (Windows
  path-traversal in `os.Root.Mkdir`) and beaconry fired a
  self-alert on its own hub image. Not reachable from a Linux
  container, but false positives on your own tooling erode trust.
  29.9.2 ships Go 1.26.10 (fixed version).

## [v0.8.1] — 2026-11-03

### Added
- **make,scripts:** BC-080 `make cleanup` target — tears down beaconry
  test compose projects (`beaconry-test`, `beaconry-scenarios`) and
  removes images beaconry's own tests pulled or built
  (`beaconry-hub:ci-test`, `beaconry-hub:scenarios`,
  `beaconry-test-telegram-mock:*`, `nginx:1.24`, `redis:6.2.0`, …).
  Hard-coded safety list keeps `beaconry-hub:latest`,
  `beaconry-scout:latest`, and all base images alive.
- **make:** `make test-e2e`, `make test-scenarios`, `make check-full`
  auto-invoke `make cleanup` on exit (success or failure). No more
  false-positive Telegram alerts from leaked test images.

### Fixed
- **docs:** CLAUDE.md's 33-line "container cleanup after testing"
  prose block is replaced with a one-liner pointer to `make cleanup`.
  Instructions-as-safety was easy to forget; tooling-as-safety is the
  gate.

## [v0.8.0] — 2026-11-03

### ⚠ Developer-visible: pre-push now runs `make check-full` (BC-079)

Pre-push verification used to be `pytest` + lints. It now runs
`make check-full` — same gates as CI, including the compose +
real_tools tiers, test-e2e, and test-scenarios. Push time goes
from ~45 s to ~2:30. `BEACONRY_SKIP_CLI_SMOKE=1` is set in the
hook so the live-stack CLI smoke step skips cleanly on developer
machines without a running stack.

Why: BC-078 ship-verification caught two pre-existing silent
failures — a stale source-scan test (`BEACONRY_TEST_MODE` string)
and a Makefile `-$(PYTHON)` prefix swallowing test-e2e exit codes.
Combined, those meant a shipped-and-staged story could silently
regress the container boot path. The right fix is a gate that
boots the container pre-push.

### Fixed
- **tests:** `test_hub_container_has_no_test_mode_code` was a stale
  source-string scan (red since BC-038 added `BEACONRY_TEST_MODE`
  back legitimately); rewritten as a runtime probe of
  `GET /test/__driver__/*` → expects 404.
- **make:** `Makefile:test-e2e` had `-$(PYTHON) -m pytest` where the
  `-` made Make ignore the pytest exit code; `exit $$EXIT` already
  propagates. The `-` is gone.

### Changed
- **ci:** pre-push hook invokes `make check-full` instead of
  standalone `pytest` + `verify-traceability` (the latter is now
  called by `make check` transitively).

## [v0.7.0] — 2026-11-03

### ⚠ Upgrade step for existing operators (BC-078)

The runtime image is now Google distroless (`gcr.io/distroless/python3-debian12:nonroot`,
uid 65532). This cuts the attack surface — no shell, no apt, no coreutils —
but changes the non-root uid from BC-077's 1000 → 65532. Operators who
already upgraded past BC-077 need a **second** one-time chown of the data
volume:

```bash
docker run --rm --user root -v beaconry-data:/data python:3.12-slim \
    chown -R 65532:65532 /data
```

Fresh v0.7.0 installs skip this. The `DOCKER_GID` knob moves from a
build-arg to a compose `group_add` entry (runtime, not build-time) —
change `.env` and `docker compose up -d` without rebuilding.

### ⚠ Breaking
- **docker:** BC-078 distroless runtime — no `/bin/sh`, no `docker exec … /bin/sh`. Debug via `docker logs`, `make audit`, `make findings`, `make status`.
- **docker:** uid 1000 → uid 65532 in-container; see upgrade step above.
- **docker:** compose `DOCKER_GID` is now a runtime `group_add` (not a build-arg); operators who had `DOCKER_GID=…` in `.env` for BC-077 keep working with no change.
- **python:** `requires-python >= 3.11` (was `>= 3.12`); runtime Python is 3.11 inside the distroless image. Local dev + CI stay on 3.12.

### Added
- **docker:** BC-078 — distroless multi-stage build for hub + scout.

## [v0.6.1] — 2026-11-03

### ⚠ Upgrade step for existing operators (BC-077)

Hub + scout now run as non-root `beaconry` user (uid 1000) inside the
container. Fresh deploys work automatically. Operators upgrading from
pre-BC-077 need to chown the existing data volume **once** before the
rebuild, otherwise the hub can't write to `/data`:

```bash
docker run --rm --user root -v beaconry-data:/data python:3.12-slim \
    chown -R 1000:100 /data
```

Non-Debian hosts also need to set the Docker socket gid in `.env`
before `make live-rebuild`:

```bash
# Check your host:
getent group docker | cut -d: -f3
# Then set in poc/.env (e.g. for Arch Linux):
DOCKER_GID=989
```

Debian/Ubuntu (gid 999) is the default — no .env change needed.

### ⚠ Breaking
- **hub,scout,scripts:** BC-062 versioning (software + API + schema) + SemVer bump + pre-push gate
- **ci,scripts:** BC-069 release toolchain — CI + make soak + auto-CHANGELOG

### Added
- **poc:** scout + hub + executor plugins + compose stack
- **hub,demo:** BC-001 end-to-end mocked POC demo
- **demo,hub:** BC-002 green — real pipeline on nginx:1.24, shipped
- **docs,scripts:** BC-004 capabilities + bidirectional traceability
- **scout,compose,make:** BC-005 multi-image Compose demo
- **make,docs:** BC-006 fast + full gate tiers
- **cli:** BC-007 `make findings` — read-only view of CVE state
- **process:** BC-008 pre-ship audit protocol (DD-TGD step 6a)
- **hub:** BC-003 Phase 1+2 — callback validation + idempotence
- **hub:** BC-003 shipped — Phase 3 live tap session complete
- **hub:** BC-009 shipped — real fix execution via Telegram
- **hub+scout:** BC-010 shipped — container health observability (4 endpoints)
- **beaconry_health:** BC-012 shipped — shared package + compiler-enforced trap guard
- **scout+status:** BC-013 shipped — scout live HTTP probes + Docker-compose-ps-like status table
- **executor:** BC-014 shipped — DockerAdapter same-track pull (no silent :latest jump)
- **process:** BC-015 shipped — memory drift regression guard
- **hub:** BC-011 shipped — target-container health probe (operator-triggered)
- **process:** BC-016 shipped — docs drift guard (make help + README ↔ code)
- **e2e:** BC-017 shipped — automated end-to-end BC-011 probe flow (no Telegram)
- **e2e:** BC-018 shipped — telegram-mock service (supersedes BC-017 test-mode guard)
- **ci:** BC-019 — grep-verify every shipping-evidence pointer
- **hub:** BC-020 — KEV ingestion + wire urgency.classify_tier
- **ci:** BC-021 — Dockerfile sources lint
- **hub:** BC-022 — digest shows KEV markers for suppressed findings
- **hub:** BC-023 — KEV findings bypass MIN_CVSS filter
- **ci:** BC-024 — verify-traceability enforces reverse direction
- **hub:** BC-028 — KEV lookup resolves grype's relatedVulnerabilities aliases
- **hub:** BC-029 — append-only audit log of operator-visible state changes
- **hub:** BC-031 — scan reconciliation + regression detection
- **hub:** BC-032 — fix viability indicator + fixed-in parity
- **hub:** BC-033 — verify_fix scans source_ref, not hardcoded :latest
- **hub:** BC-034 — persist last_seen_ts per finding
- **hub:** BC-035 notification adapter architecture + heartbeat for stale findings
- **hub,scout:** BC-030 internet_facing detection → Tier 1 fires
- **hub:** BC-036 decouple digest cadence from scan cadence
- **scout:** BC-037 on-demand scan trigger — make scan
- **hub,scenarios:** BC-038 live-stack scenario harness — make test-scenarios
- **docs,scripts:** BC-039 docs/VARIABLES.md + verify-variables drift lint
- **docs,scripts:** BC-040 three-way env sync — VARIABLES.md ↔ .env.example ↔ docker-compose.yml
- **scripts:** BC-041 verify-evidence-pointers reverse check
- **scripts,hub:** BC-042 `make audit` CLI + `scan_received` event
- **hub:** BC-043 CVE alias on non-CVE primary ids — KEV paper trail
- **scripts:** BC-044 live-smoke CLI harness + autodetected scenario coverage
- **hub,scripts:** BC-055 HTTP-only CLI → hub (no SSH)
- **docs,scripts:** BC-056 INSTALL.md + verify-install-doc (short-name reshape)
- **scripts,hub:** BC-057 make findings HOST= + 🖥️ multi-host notification lead
- **hub:** BC-058 mutation + audit cleanup
- **hub:** BC-059 callback_data carries CVE id
- **hub:** BC-060 notify_sent_failed audit symmetry
- **hub,scripts:** BC-061 scout fleet registry with liveness status
- **hub,scout,scripts:** BC-062 versioning (software + API + schema) + SemVer bump + pre-push gate
- **hub,scout,scripts:** BC-063 fleet health visibility + scout heartbeat
- **hub,scripts:** BC-064 FLEET LAST HEARTBEAT + stale-heartbeat unknown health
- **hub:** BC-066 manual executor as default + configurable adapter + 1h reminder loop
- **ci,scripts:** BC-069 release toolchain — CI + make soak + auto-CHANGELOG
- **scripts,build:** BC-070 operator CLI install — `pip install -e . → beaconry …`
- **ci,scripts:** BC-074 release automation + 24h soak + feature-complete lint + CI-failure log
- **scripts,hub,scout:** BC-075 metrics + soak threshold signals + privacy-first opt-in
- **scripts:** BC-076 beaconry metrics --remote reads from hub /audit over HTTP

### Fixed
- **hub,demo:** BC-002 robust-notify — HTML parse mode, cap, dedup, digest
- **docs,scripts:** BC-004 close-gaps — CAP-B/T prefix, required fields, hook, self-consistency
- **scripts:** BC-004 design-link + origin-story validation
- **scripts:** BC-004 enforce description as required field
- **hub:** BC-005 close two in-container regressions found by make demo
- BC-005 close-gaps — tests + story self-consistency + lessons
- **scripts:** enforce plan doc on fix/* and feat/* branches
- smoke mode broken + CAP-id regex truncates at hyphens
- BC-006 close-gaps — test graceful-skip + README skip-note
- BC-007 close-gaps — tier in sort key, schema migration, docs
- BC-007 load_finding reads tier + migration + round-trip tests
- **scripts:** BC-008 automate-audit — Shipping evidence enforced on shipped stories
- BC-003 close-gaps — run real_tools, add missing row, warn on scenario-count
- **beaconry_health:** BC-012 close-gap — pyright actually enforces the trap guard
- **hub+findings:** BC-011 close-gaps — findings CLI accepts new statuses + button-attach test
- **compose:** BC-011 env propagation — BEACONRY_HEALTH_PROBES/FIX_ENABLED/MOCK now actually propagate
- **findings:** BC-007 close-gap — auto-route to compose hub's volume DB
- **hub:** BC-011 close-gap — use module-level _waterfall binding in verify branch
- **tests:** BC-018 close-gap — unit coverage for telegram-mock + hub env dials
- **hub:** BC-020 close-gap — ship kev.py + urgency.py in the image
- **memory:** BC-020 close-gap — add memory drift ban
- **hub:** BC-020 close-gap — treat empty BEACONRY_KEV_FEED_URL as unset
- **docs:** BC-023 close-gap — Covers block + design-decisions annotation
- **docs:** BC-029 close-gap — add Gherkin block + gitignore audit.log
- **hub:** BC-032 close-gap — handle grype's 'not available' sentinel
- **docs:** BC-033 close-gap — add Scenario 3 test coverage
- **hub:** BC-035 — tolerate empty-string env vars from compose \${VAR:-}
- **hub:** BC-035/BC-030 close-gap — notify path works inside container
- **scenarios:** BC-038 close-gap — live-run fixes
- **audit:** BC-043 close-gap — make audit CVE=X matches alias too
- **audit:** FOLLOW output buffering + inline-script error handling
- **hub:** BC-065 digest content-change bypass is now opt-out via env
- **hub,executor,audit:** BC-067 quality sweep — 5 severity-1 bugs
- **hub:** BC-068 defensive cleanup — reconcile FD-leak + fix_version blank sanitize
- **ci,tests:** BC-071 CI env leak — BEACONRY_SKIP_VERSION_BUMP leaked into pytest
- **build:** explicit packages list to bypass flat-layout detection
- **hooks:** use .venv/bin/pytest instead of system pytest
- **deps:** BC-072 pytest 8.3.3 → 9.0.3 + pytest-asyncio 0.24.0 → 1.4.1.post1
- **deps:** BC-073 python-dotenv 1.0.1 → 1.2.2 + requests 2.32.3 → 2.33.0

### Changed
- initial commit (gitignore only; main is CI-promoted)
- **build:** Makefile, pyproject, pre-commit, dev requirements
- **poc:** pytest pyramid skeleton + sanity test
- **build:** lint + type cleanup, lower coverage floor until BC-001
- **poc:** BC-001 red integration test + plan doc
- **poc,hub:** BC-002 red state — tests + dispatch stubs + plan
- **poc:** BC-004 red state — traceability enforcement scenarios
- **poc:** BC-005 red — scout glob filter stubs + Compose demo plan
- **e2e:** use hub.save_finding instead of raw INSERT for seeding
- **ci:** run test-e2e as part of make check-full
- **ci:** complexity gates + 85/65% coverage floors
- **scout:** extract parse_ss_output; pragma subprocess shells; un-omit from coverage
- **hub:** on_button_click → dispatch dict of per-action handlers
- **ci:** verify_traceability.run → gates registry
- **make:** fix 3 polish issues surfaced by running all make targets
- **ops:** live-stack rebuild discipline — make live-rebuild + live-status
- **process:** breaking-change-in-planning discipline + BC-031 close-gap
- **hub:** BC-035 close-gap — cover migration backfill, format_heartbeat, mock-print
- **hub:** BC-030 close-gap — audit event carries internet_facing + .env.example

### Documentation
- **design:** product design + locked decisions
- **process:** DD-TGD process, stories, plan docs (BC-000)
- **stories:** mark BC-000 shipped
- **stories:** add BC-001 end-to-end mocked POC demo (ready)
- **stories:** mark BC-001 shipped
- **process:** add IDEAS backlog + wire it into DD-TGD
- **stories:** BC-003 draft + note BC-002 audit gaps in IDEAS
- **ideas:** capture CAP-T026 staleness as fix-opportunistically
- **memory:** BC-013 close-gap — scout migrated to HTTP; memory files updated
- **memory:** BC-014 close-gap — design-decisions.md matches same-track pull reality
- close BC-015 self-consistency gap + refresh make help
- **memory:** BC-016 close-gap — gate table lists all shipped drift guards
- **ideas:** log audit-protocol upgrade — grep-verify evidence pointers
- **ideas:** park BC-020 — dividend overlaps existing demo modes
- **ideas:** log 2 lint gaps surfaced by BC-020 live-smoke
- **ideas:** KEV lookup should resolve CVE aliases via grype 'related' field
- **ideas:** BC-025/026/027 close the big refactor entries
- **stories:** draft BC-030 (internet_facing detection) as ready
- **ideas:** log AVG-2909 operator action + promote BC-031 KEV-alias context
- **stories:** BC-031 draft — scan reconciliation + regression detection
- **plan:** BC-031 close-gap evidence rows for backfill tests
- **ideas:** verify-evidence-pointers should catch missing rows too
- **ideas:** log 3 BC-034 consumer candidates (heartbeat / digest refresh / purge)
- **stories:** mark BC-035 shipped
- **stories:** mark BC-030 shipped
- **bc-037:** post-ship audit — record scout-down verification date
- **bc-038:** post-ship audit — .env.example + check-full inclusion
- **bc-039:** post-ship audit — IDEAS followup for three-way sync
- **bc-040:** post-ship audit — Makefile wording + IDEAS cleanup
- **bc-041:** post-ship audit — remove stale IDEAS entry (shipped)
- **bc-042:** post-ship audit — remove stale IDEAS entry
- **stories:** mark BC-062 shipped in INDEX

### Other
- Initial commit

## [v0.6.0] — 2026-11-03

## [v0.5.0] — 2026-11-02

## [v0.4.0] — 2026-11-02

### ⚠ Breaking
- **hub,scout,scripts:** BC-062 versioning (software + API + schema) + SemVer bump + pre-push gate
- **ci,scripts:** BC-069 release toolchain — CI + make soak + auto-CHANGELOG

### Added
- **poc:** scout + hub + executor plugins + compose stack
- **hub,demo:** BC-001 end-to-end mocked POC demo
- **demo,hub:** BC-002 green — real pipeline on nginx:1.24, shipped
- **docs,scripts:** BC-004 capabilities + bidirectional traceability
- **scout,compose,make:** BC-005 multi-image Compose demo
- **make,docs:** BC-006 fast + full gate tiers
- **cli:** BC-007 `make findings` — read-only view of CVE state
- **process:** BC-008 pre-ship audit protocol (DD-TGD step 6a)
- **hub:** BC-003 Phase 1+2 — callback validation + idempotence
- **hub:** BC-003 shipped — Phase 3 live tap session complete
- **hub:** BC-009 shipped — real fix execution via Telegram
- **hub+scout:** BC-010 shipped — container health observability (4 endpoints)
- **beaconry_health:** BC-012 shipped — shared package + compiler-enforced trap guard
- **scout+status:** BC-013 shipped — scout live HTTP probes + Docker-compose-ps-like status table
- **executor:** BC-014 shipped — DockerAdapter same-track pull (no silent :latest jump)
- **process:** BC-015 shipped — memory drift regression guard
- **hub:** BC-011 shipped — target-container health probe (operator-triggered)
- **process:** BC-016 shipped — docs drift guard (make help + README ↔ code)
- **e2e:** BC-017 shipped — automated end-to-end BC-011 probe flow (no Telegram)
- **e2e:** BC-018 shipped — telegram-mock service (supersedes BC-017 test-mode guard)
- **ci:** BC-019 — grep-verify every shipping-evidence pointer
- **hub:** BC-020 — KEV ingestion + wire urgency.classify_tier
- **ci:** BC-021 — Dockerfile sources lint
- **hub:** BC-022 — digest shows KEV markers for suppressed findings
- **hub:** BC-023 — KEV findings bypass MIN_CVSS filter
- **ci:** BC-024 — verify-traceability enforces reverse direction
- **hub:** BC-028 — KEV lookup resolves grype's relatedVulnerabilities aliases
- **hub:** BC-029 — append-only audit log of operator-visible state changes
- **hub:** BC-031 — scan reconciliation + regression detection
- **hub:** BC-032 — fix viability indicator + fixed-in parity
- **hub:** BC-033 — verify_fix scans source_ref, not hardcoded :latest
- **hub:** BC-034 — persist last_seen_ts per finding
- **hub:** BC-035 notification adapter architecture + heartbeat for stale findings
- **hub,scout:** BC-030 internet_facing detection → Tier 1 fires
- **hub:** BC-036 decouple digest cadence from scan cadence
- **scout:** BC-037 on-demand scan trigger — make scan
- **hub,scenarios:** BC-038 live-stack scenario harness — make test-scenarios
- **docs,scripts:** BC-039 docs/VARIABLES.md + verify-variables drift lint
- **docs,scripts:** BC-040 three-way env sync — VARIABLES.md ↔ .env.example ↔ docker-compose.yml
- **scripts:** BC-041 verify-evidence-pointers reverse check
- **scripts,hub:** BC-042 `make audit` CLI + `scan_received` event
- **hub:** BC-043 CVE alias on non-CVE primary ids — KEV paper trail
- **scripts:** BC-044 live-smoke CLI harness + autodetected scenario coverage
- **hub,scripts:** BC-055 HTTP-only CLI → hub (no SSH)
- **docs,scripts:** BC-056 INSTALL.md + verify-install-doc (short-name reshape)
- **scripts,hub:** BC-057 make findings HOST= + 🖥️ multi-host notification lead
- **hub:** BC-058 mutation + audit cleanup
- **hub:** BC-059 callback_data carries CVE id
- **hub:** BC-060 notify_sent_failed audit symmetry
- **hub,scripts:** BC-061 scout fleet registry with liveness status
- **hub,scout,scripts:** BC-062 versioning (software + API + schema) + SemVer bump + pre-push gate
- **hub,scout,scripts:** BC-063 fleet health visibility + scout heartbeat
- **hub,scripts:** BC-064 FLEET LAST HEARTBEAT + stale-heartbeat unknown health
- **hub:** BC-066 manual executor as default + configurable adapter + 1h reminder loop
- **ci,scripts:** BC-069 release toolchain — CI + make soak + auto-CHANGELOG
- **scripts,build:** BC-070 operator CLI install — `pip install -e . → beaconry …`

### Fixed
- **hub,demo:** BC-002 robust-notify — HTML parse mode, cap, dedup, digest
- **docs,scripts:** BC-004 close-gaps — CAP-B/T prefix, required fields, hook, self-consistency
- **scripts:** BC-004 design-link + origin-story validation
- **scripts:** BC-004 enforce description as required field
- **hub:** BC-005 close two in-container regressions found by make demo
- BC-005 close-gaps — tests + story self-consistency + lessons
- **scripts:** enforce plan doc on fix/* and feat/* branches
- smoke mode broken + CAP-id regex truncates at hyphens
- BC-006 close-gaps — test graceful-skip + README skip-note
- BC-007 close-gaps — tier in sort key, schema migration, docs
- BC-007 load_finding reads tier + migration + round-trip tests
- **scripts:** BC-008 automate-audit — Shipping evidence enforced on shipped stories
- BC-003 close-gaps — run real_tools, add missing row, warn on scenario-count
- **beaconry_health:** BC-012 close-gap — pyright actually enforces the trap guard
- **hub+findings:** BC-011 close-gaps — findings CLI accepts new statuses + button-attach test
- **compose:** BC-011 env propagation — BEACONRY_HEALTH_PROBES/FIX_ENABLED/MOCK now actually propagate
- **findings:** BC-007 close-gap — auto-route to compose hub's volume DB
- **hub:** BC-011 close-gap — use module-level _waterfall binding in verify branch
- **tests:** BC-018 close-gap — unit coverage for telegram-mock + hub env dials
- **hub:** BC-020 close-gap — ship kev.py + urgency.py in the image
- **memory:** BC-020 close-gap — add memory drift ban
- **hub:** BC-020 close-gap — treat empty BEACONRY_KEV_FEED_URL as unset
- **docs:** BC-023 close-gap — Covers block + design-decisions annotation
- **docs:** BC-029 close-gap — add Gherkin block + gitignore audit.log
- **hub:** BC-032 close-gap — handle grype's 'not available' sentinel
- **docs:** BC-033 close-gap — add Scenario 3 test coverage
- **hub:** BC-035 — tolerate empty-string env vars from compose \${VAR:-}
- **hub:** BC-035/BC-030 close-gap — notify path works inside container
- **scenarios:** BC-038 close-gap — live-run fixes
- **audit:** BC-043 close-gap — make audit CVE=X matches alias too
- **audit:** FOLLOW output buffering + inline-script error handling
- **hub:** BC-065 digest content-change bypass is now opt-out via env
- **hub,executor,audit:** BC-067 quality sweep — 5 severity-1 bugs
- **hub:** BC-068 defensive cleanup — reconcile FD-leak + fix_version blank sanitize
- **ci,tests:** BC-071 CI env leak — BEACONRY_SKIP_VERSION_BUMP leaked into pytest
- **build:** explicit packages list to bypass flat-layout detection
- **hooks:** use .venv/bin/pytest instead of system pytest
- **deps:** BC-072 pytest 8.3.3 → 9.0.3 + pytest-asyncio 0.24.0 → 1.4.1.post1
- **deps:** BC-073 python-dotenv 1.0.1 → 1.2.2 + requests 2.32.3 → 2.33.0

### Changed
- initial commit (gitignore only; main is CI-promoted)
- **build:** Makefile, pyproject, pre-commit, dev requirements
- **poc:** pytest pyramid skeleton + sanity test
- **build:** lint + type cleanup, lower coverage floor until BC-001
- **poc:** BC-001 red integration test + plan doc
- **poc,hub:** BC-002 red state — tests + dispatch stubs + plan
- **poc:** BC-004 red state — traceability enforcement scenarios
- **poc:** BC-005 red — scout glob filter stubs + Compose demo plan
- **e2e:** use hub.save_finding instead of raw INSERT for seeding
- **ci:** run test-e2e as part of make check-full
- **ci:** complexity gates + 85/65% coverage floors
- **scout:** extract parse_ss_output; pragma subprocess shells; un-omit from coverage
- **hub:** on_button_click → dispatch dict of per-action handlers
- **ci:** verify_traceability.run → gates registry
- **make:** fix 3 polish issues surfaced by running all make targets
- **ops:** live-stack rebuild discipline — make live-rebuild + live-status
- **process:** breaking-change-in-planning discipline + BC-031 close-gap
- **hub:** BC-035 close-gap — cover migration backfill, format_heartbeat, mock-print
- **hub:** BC-030 close-gap — audit event carries internet_facing + .env.example

### Documentation
- **design:** product design + locked decisions
- **process:** DD-TGD process, stories, plan docs (BC-000)
- **stories:** mark BC-000 shipped
- **stories:** add BC-001 end-to-end mocked POC demo (ready)
- **stories:** mark BC-001 shipped
- **process:** add IDEAS backlog + wire it into DD-TGD
- **stories:** BC-003 draft + note BC-002 audit gaps in IDEAS
- **ideas:** capture CAP-T026 staleness as fix-opportunistically
- **memory:** BC-013 close-gap — scout migrated to HTTP; memory files updated
- **memory:** BC-014 close-gap — design-decisions.md matches same-track pull reality
- close BC-015 self-consistency gap + refresh make help
- **memory:** BC-016 close-gap — gate table lists all shipped drift guards
- **ideas:** log audit-protocol upgrade — grep-verify evidence pointers
- **ideas:** park BC-020 — dividend overlaps existing demo modes
- **ideas:** log 2 lint gaps surfaced by BC-020 live-smoke
- **ideas:** KEV lookup should resolve CVE aliases via grype 'related' field
- **ideas:** BC-025/026/027 close the big refactor entries
- **stories:** draft BC-030 (internet_facing detection) as ready
- **ideas:** log AVG-2909 operator action + promote BC-031 KEV-alias context
- **stories:** BC-031 draft — scan reconciliation + regression detection
- **plan:** BC-031 close-gap evidence rows for backfill tests
- **ideas:** verify-evidence-pointers should catch missing rows too
- **ideas:** log 3 BC-034 consumer candidates (heartbeat / digest refresh / purge)
- **stories:** mark BC-035 shipped
- **stories:** mark BC-030 shipped
- **bc-037:** post-ship audit — record scout-down verification date
- **bc-038:** post-ship audit — .env.example + check-full inclusion
- **bc-039:** post-ship audit — IDEAS followup for three-way sync
- **bc-040:** post-ship audit — Makefile wording + IDEAS cleanup
- **bc-041:** post-ship audit — remove stale IDEAS entry (shipped)
- **bc-042:** post-ship audit — remove stale IDEAS entry
- **stories:** mark BC-062 shipped in INDEX

### Other
- Initial commit

## [v0.3.7] — 2026-11-02

### ⚠ Breaking
- **hub,scout,scripts:** BC-062 versioning (software + API + schema) + SemVer bump + pre-push gate
- **ci,scripts:** BC-069 release toolchain — CI + make soak + auto-CHANGELOG

### Added
- **poc:** scout + hub + executor plugins + compose stack
- **hub,demo:** BC-001 end-to-end mocked POC demo
- **demo,hub:** BC-002 green — real pipeline on nginx:1.24, shipped
- **docs,scripts:** BC-004 capabilities + bidirectional traceability
- **scout,compose,make:** BC-005 multi-image Compose demo
- **make,docs:** BC-006 fast + full gate tiers
- **cli:** BC-007 `make findings` — read-only view of CVE state
- **process:** BC-008 pre-ship audit protocol (DD-TGD step 6a)
- **hub:** BC-003 Phase 1+2 — callback validation + idempotence
- **hub:** BC-003 shipped — Phase 3 live tap session complete
- **hub:** BC-009 shipped — real fix execution via Telegram
- **hub+scout:** BC-010 shipped — container health observability (4 endpoints)
- **beaconry_health:** BC-012 shipped — shared package + compiler-enforced trap guard
- **scout+status:** BC-013 shipped — scout live HTTP probes + Docker-compose-ps-like status table
- **executor:** BC-014 shipped — DockerAdapter same-track pull (no silent :latest jump)
- **process:** BC-015 shipped — memory drift regression guard
- **hub:** BC-011 shipped — target-container health probe (operator-triggered)
- **process:** BC-016 shipped — docs drift guard (make help + README ↔ code)
- **e2e:** BC-017 shipped — automated end-to-end BC-011 probe flow (no Telegram)
- **e2e:** BC-018 shipped — telegram-mock service (supersedes BC-017 test-mode guard)
- **ci:** BC-019 — grep-verify every shipping-evidence pointer
- **hub:** BC-020 — KEV ingestion + wire urgency.classify_tier
- **ci:** BC-021 — Dockerfile sources lint
- **hub:** BC-022 — digest shows KEV markers for suppressed findings
- **hub:** BC-023 — KEV findings bypass MIN_CVSS filter
- **ci:** BC-024 — verify-traceability enforces reverse direction
- **hub:** BC-028 — KEV lookup resolves grype's relatedVulnerabilities aliases
- **hub:** BC-029 — append-only audit log of operator-visible state changes
- **hub:** BC-031 — scan reconciliation + regression detection
- **hub:** BC-032 — fix viability indicator + fixed-in parity
- **hub:** BC-033 — verify_fix scans source_ref, not hardcoded :latest
- **hub:** BC-034 — persist last_seen_ts per finding
- **hub:** BC-035 notification adapter architecture + heartbeat for stale findings
- **hub,scout:** BC-030 internet_facing detection → Tier 1 fires
- **hub:** BC-036 decouple digest cadence from scan cadence
- **scout:** BC-037 on-demand scan trigger — make scan
- **hub,scenarios:** BC-038 live-stack scenario harness — make test-scenarios
- **docs,scripts:** BC-039 docs/VARIABLES.md + verify-variables drift lint
- **docs,scripts:** BC-040 three-way env sync — VARIABLES.md ↔ .env.example ↔ docker-compose.yml
- **scripts:** BC-041 verify-evidence-pointers reverse check
- **scripts,hub:** BC-042 `make audit` CLI + `scan_received` event
- **hub:** BC-043 CVE alias on non-CVE primary ids — KEV paper trail
- **scripts:** BC-044 live-smoke CLI harness + autodetected scenario coverage
- **hub,scripts:** BC-055 HTTP-only CLI → hub (no SSH)
- **docs,scripts:** BC-056 INSTALL.md + verify-install-doc (short-name reshape)
- **scripts,hub:** BC-057 make findings HOST= + 🖥️ multi-host notification lead
- **hub:** BC-058 mutation + audit cleanup
- **hub:** BC-059 callback_data carries CVE id
- **hub:** BC-060 notify_sent_failed audit symmetry
- **hub,scripts:** BC-061 scout fleet registry with liveness status
- **hub,scout,scripts:** BC-062 versioning (software + API + schema) + SemVer bump + pre-push gate
- **hub,scout,scripts:** BC-063 fleet health visibility + scout heartbeat
- **hub,scripts:** BC-064 FLEET LAST HEARTBEAT + stale-heartbeat unknown health
- **hub:** BC-066 manual executor as default + configurable adapter + 1h reminder loop
- **ci,scripts:** BC-069 release toolchain — CI + make soak + auto-CHANGELOG
- **scripts,build:** BC-070 operator CLI install — `pip install -e . → beaconry …`

### Fixed
- **hub,demo:** BC-002 robust-notify — HTML parse mode, cap, dedup, digest
- **docs,scripts:** BC-004 close-gaps — CAP-B/T prefix, required fields, hook, self-consistency
- **scripts:** BC-004 design-link + origin-story validation
- **scripts:** BC-004 enforce description as required field
- **hub:** BC-005 close two in-container regressions found by make demo
- BC-005 close-gaps — tests + story self-consistency + lessons
- **scripts:** enforce plan doc on fix/* and feat/* branches
- smoke mode broken + CAP-id regex truncates at hyphens
- BC-006 close-gaps — test graceful-skip + README skip-note
- BC-007 close-gaps — tier in sort key, schema migration, docs
- BC-007 load_finding reads tier + migration + round-trip tests
- **scripts:** BC-008 automate-audit — Shipping evidence enforced on shipped stories
- BC-003 close-gaps — run real_tools, add missing row, warn on scenario-count
- **beaconry_health:** BC-012 close-gap — pyright actually enforces the trap guard
- **hub+findings:** BC-011 close-gaps — findings CLI accepts new statuses + button-attach test
- **compose:** BC-011 env propagation — BEACONRY_HEALTH_PROBES/FIX_ENABLED/MOCK now actually propagate
- **findings:** BC-007 close-gap — auto-route to compose hub's volume DB
- **hub:** BC-011 close-gap — use module-level _waterfall binding in verify branch
- **tests:** BC-018 close-gap — unit coverage for telegram-mock + hub env dials
- **hub:** BC-020 close-gap — ship kev.py + urgency.py in the image
- **memory:** BC-020 close-gap — add memory drift ban
- **hub:** BC-020 close-gap — treat empty BEACONRY_KEV_FEED_URL as unset
- **docs:** BC-023 close-gap — Covers block + design-decisions annotation
- **docs:** BC-029 close-gap — add Gherkin block + gitignore audit.log
- **hub:** BC-032 close-gap — handle grype's 'not available' sentinel
- **docs:** BC-033 close-gap — add Scenario 3 test coverage
- **hub:** BC-035 — tolerate empty-string env vars from compose \${VAR:-}
- **hub:** BC-035/BC-030 close-gap — notify path works inside container
- **scenarios:** BC-038 close-gap — live-run fixes
- **audit:** BC-043 close-gap — make audit CVE=X matches alias too
- **audit:** FOLLOW output buffering + inline-script error handling
- **hub:** BC-065 digest content-change bypass is now opt-out via env
- **hub,executor,audit:** BC-067 quality sweep — 5 severity-1 bugs
- **hub:** BC-068 defensive cleanup — reconcile FD-leak + fix_version blank sanitize
- **ci,tests:** BC-071 CI env leak — BEACONRY_SKIP_VERSION_BUMP leaked into pytest
- **build:** explicit packages list to bypass flat-layout detection
- **hooks:** use .venv/bin/pytest instead of system pytest
- **deps:** BC-072 pytest 8.3.3 → 9.0.3 + pytest-asyncio 0.24.0 → 1.4.1.post1

### Changed
- initial commit (gitignore only; main is CI-promoted)
- **build:** Makefile, pyproject, pre-commit, dev requirements
- **poc:** pytest pyramid skeleton + sanity test
- **build:** lint + type cleanup, lower coverage floor until BC-001
- **poc:** BC-001 red integration test + plan doc
- **poc,hub:** BC-002 red state — tests + dispatch stubs + plan
- **poc:** BC-004 red state — traceability enforcement scenarios
- **poc:** BC-005 red — scout glob filter stubs + Compose demo plan
- **e2e:** use hub.save_finding instead of raw INSERT for seeding
- **ci:** run test-e2e as part of make check-full
- **ci:** complexity gates + 85/65% coverage floors
- **scout:** extract parse_ss_output; pragma subprocess shells; un-omit from coverage
- **hub:** on_button_click → dispatch dict of per-action handlers
- **ci:** verify_traceability.run → gates registry
- **make:** fix 3 polish issues surfaced by running all make targets
- **ops:** live-stack rebuild discipline — make live-rebuild + live-status
- **process:** breaking-change-in-planning discipline + BC-031 close-gap
- **hub:** BC-035 close-gap — cover migration backfill, format_heartbeat, mock-print
- **hub:** BC-030 close-gap — audit event carries internet_facing + .env.example

### Documentation
- **design:** product design + locked decisions
- **process:** DD-TGD process, stories, plan docs (BC-000)
- **stories:** mark BC-000 shipped
- **stories:** add BC-001 end-to-end mocked POC demo (ready)
- **stories:** mark BC-001 shipped
- **process:** add IDEAS backlog + wire it into DD-TGD
- **stories:** BC-003 draft + note BC-002 audit gaps in IDEAS
- **ideas:** capture CAP-T026 staleness as fix-opportunistically
- **memory:** BC-013 close-gap — scout migrated to HTTP; memory files updated
- **memory:** BC-014 close-gap — design-decisions.md matches same-track pull reality
- close BC-015 self-consistency gap + refresh make help
- **memory:** BC-016 close-gap — gate table lists all shipped drift guards
- **ideas:** log audit-protocol upgrade — grep-verify evidence pointers
- **ideas:** park BC-020 — dividend overlaps existing demo modes
- **ideas:** log 2 lint gaps surfaced by BC-020 live-smoke
- **ideas:** KEV lookup should resolve CVE aliases via grype 'related' field
- **ideas:** BC-025/026/027 close the big refactor entries
- **stories:** draft BC-030 (internet_facing detection) as ready
- **ideas:** log AVG-2909 operator action + promote BC-031 KEV-alias context
- **stories:** BC-031 draft — scan reconciliation + regression detection
- **plan:** BC-031 close-gap evidence rows for backfill tests
- **ideas:** verify-evidence-pointers should catch missing rows too
- **ideas:** log 3 BC-034 consumer candidates (heartbeat / digest refresh / purge)
- **stories:** mark BC-035 shipped
- **stories:** mark BC-030 shipped
- **bc-037:** post-ship audit — record scout-down verification date
- **bc-038:** post-ship audit — .env.example + check-full inclusion
- **bc-039:** post-ship audit — IDEAS followup for three-way sync
- **bc-040:** post-ship audit — Makefile wording + IDEAS cleanup
- **bc-041:** post-ship audit — remove stale IDEAS entry (shipped)
- **bc-042:** post-ship audit — remove stale IDEAS entry
- **stories:** mark BC-062 shipped in INDEX

### Other
- Initial commit

## [v0.3.6] — 2026-11-02

### ⚠ Breaking
- **hub,scout,scripts:** BC-062 versioning (software + API + schema) + SemVer bump + pre-push gate
- **ci,scripts:** BC-069 release toolchain — CI + make soak + auto-CHANGELOG

### Added
- **poc:** scout + hub + executor plugins + compose stack
- **hub,demo:** BC-001 end-to-end mocked POC demo
- **demo,hub:** BC-002 green — real pipeline on nginx:1.24, shipped
- **docs,scripts:** BC-004 capabilities + bidirectional traceability
- **scout,compose,make:** BC-005 multi-image Compose demo
- **make,docs:** BC-006 fast + full gate tiers
- **cli:** BC-007 `make findings` — read-only view of CVE state
- **process:** BC-008 pre-ship audit protocol (DD-TGD step 6a)
- **hub:** BC-003 Phase 1+2 — callback validation + idempotence
- **hub:** BC-003 shipped — Phase 3 live tap session complete
- **hub:** BC-009 shipped — real fix execution via Telegram
- **hub+scout:** BC-010 shipped — container health observability (4 endpoints)
- **beaconry_health:** BC-012 shipped — shared package + compiler-enforced trap guard
- **scout+status:** BC-013 shipped — scout live HTTP probes + Docker-compose-ps-like status table
- **executor:** BC-014 shipped — DockerAdapter same-track pull (no silent :latest jump)
- **process:** BC-015 shipped — memory drift regression guard
- **hub:** BC-011 shipped — target-container health probe (operator-triggered)
- **process:** BC-016 shipped — docs drift guard (make help + README ↔ code)
- **e2e:** BC-017 shipped — automated end-to-end BC-011 probe flow (no Telegram)
- **e2e:** BC-018 shipped — telegram-mock service (supersedes BC-017 test-mode guard)
- **ci:** BC-019 — grep-verify every shipping-evidence pointer
- **hub:** BC-020 — KEV ingestion + wire urgency.classify_tier
- **ci:** BC-021 — Dockerfile sources lint
- **hub:** BC-022 — digest shows KEV markers for suppressed findings
- **hub:** BC-023 — KEV findings bypass MIN_CVSS filter
- **ci:** BC-024 — verify-traceability enforces reverse direction
- **hub:** BC-028 — KEV lookup resolves grype's relatedVulnerabilities aliases
- **hub:** BC-029 — append-only audit log of operator-visible state changes
- **hub:** BC-031 — scan reconciliation + regression detection
- **hub:** BC-032 — fix viability indicator + fixed-in parity
- **hub:** BC-033 — verify_fix scans source_ref, not hardcoded :latest
- **hub:** BC-034 — persist last_seen_ts per finding
- **hub:** BC-035 notification adapter architecture + heartbeat for stale findings
- **hub,scout:** BC-030 internet_facing detection → Tier 1 fires
- **hub:** BC-036 decouple digest cadence from scan cadence
- **scout:** BC-037 on-demand scan trigger — make scan
- **hub,scenarios:** BC-038 live-stack scenario harness — make test-scenarios
- **docs,scripts:** BC-039 docs/VARIABLES.md + verify-variables drift lint
- **docs,scripts:** BC-040 three-way env sync — VARIABLES.md ↔ .env.example ↔ docker-compose.yml
- **scripts:** BC-041 verify-evidence-pointers reverse check
- **scripts,hub:** BC-042 `make audit` CLI + `scan_received` event
- **hub:** BC-043 CVE alias on non-CVE primary ids — KEV paper trail
- **scripts:** BC-044 live-smoke CLI harness + autodetected scenario coverage
- **hub,scripts:** BC-055 HTTP-only CLI → hub (no SSH)
- **docs,scripts:** BC-056 INSTALL.md + verify-install-doc (short-name reshape)
- **scripts,hub:** BC-057 make findings HOST= + 🖥️ multi-host notification lead
- **hub:** BC-058 mutation + audit cleanup
- **hub:** BC-059 callback_data carries CVE id
- **hub:** BC-060 notify_sent_failed audit symmetry
- **hub,scripts:** BC-061 scout fleet registry with liveness status
- **hub,scout,scripts:** BC-062 versioning (software + API + schema) + SemVer bump + pre-push gate
- **hub,scout,scripts:** BC-063 fleet health visibility + scout heartbeat
- **hub,scripts:** BC-064 FLEET LAST HEARTBEAT + stale-heartbeat unknown health
- **hub:** BC-066 manual executor as default + configurable adapter + 1h reminder loop
- **ci,scripts:** BC-069 release toolchain — CI + make soak + auto-CHANGELOG
- **scripts,build:** BC-070 operator CLI install — `pip install -e . → beaconry …`

### Fixed
- **hub,demo:** BC-002 robust-notify — HTML parse mode, cap, dedup, digest
- **docs,scripts:** BC-004 close-gaps — CAP-B/T prefix, required fields, hook, self-consistency
- **scripts:** BC-004 design-link + origin-story validation
- **scripts:** BC-004 enforce description as required field
- **hub:** BC-005 close two in-container regressions found by make demo
- BC-005 close-gaps — tests + story self-consistency + lessons
- **scripts:** enforce plan doc on fix/* and feat/* branches
- smoke mode broken + CAP-id regex truncates at hyphens
- BC-006 close-gaps — test graceful-skip + README skip-note
- BC-007 close-gaps — tier in sort key, schema migration, docs
- BC-007 load_finding reads tier + migration + round-trip tests
- **scripts:** BC-008 automate-audit — Shipping evidence enforced on shipped stories
- BC-003 close-gaps — run real_tools, add missing row, warn on scenario-count
- **beaconry_health:** BC-012 close-gap — pyright actually enforces the trap guard
- **hub+findings:** BC-011 close-gaps — findings CLI accepts new statuses + button-attach test
- **compose:** BC-011 env propagation — BEACONRY_HEALTH_PROBES/FIX_ENABLED/MOCK now actually propagate
- **findings:** BC-007 close-gap — auto-route to compose hub's volume DB
- **hub:** BC-011 close-gap — use module-level _waterfall binding in verify branch
- **tests:** BC-018 close-gap — unit coverage for telegram-mock + hub env dials
- **hub:** BC-020 close-gap — ship kev.py + urgency.py in the image
- **memory:** BC-020 close-gap — add memory drift ban
- **hub:** BC-020 close-gap — treat empty BEACONRY_KEV_FEED_URL as unset
- **docs:** BC-023 close-gap — Covers block + design-decisions annotation
- **docs:** BC-029 close-gap — add Gherkin block + gitignore audit.log
- **hub:** BC-032 close-gap — handle grype's 'not available' sentinel
- **docs:** BC-033 close-gap — add Scenario 3 test coverage
- **hub:** BC-035 — tolerate empty-string env vars from compose \${VAR:-}
- **hub:** BC-035/BC-030 close-gap — notify path works inside container
- **scenarios:** BC-038 close-gap — live-run fixes
- **audit:** BC-043 close-gap — make audit CVE=X matches alias too
- **audit:** FOLLOW output buffering + inline-script error handling
- **hub:** BC-065 digest content-change bypass is now opt-out via env
- **hub,executor,audit:** BC-067 quality sweep — 5 severity-1 bugs
- **hub:** BC-068 defensive cleanup — reconcile FD-leak + fix_version blank sanitize
- **ci,tests:** BC-071 CI env leak — BEACONRY_SKIP_VERSION_BUMP leaked into pytest
- **build:** explicit packages list to bypass flat-layout detection
- **hooks:** use .venv/bin/pytest instead of system pytest

### Changed
- initial commit (gitignore only; main is CI-promoted)
- **build:** Makefile, pyproject, pre-commit, dev requirements
- **poc:** pytest pyramid skeleton + sanity test
- **build:** lint + type cleanup, lower coverage floor until BC-001
- **poc:** BC-001 red integration test + plan doc
- **poc,hub:** BC-002 red state — tests + dispatch stubs + plan
- **poc:** BC-004 red state — traceability enforcement scenarios
- **poc:** BC-005 red — scout glob filter stubs + Compose demo plan
- **e2e:** use hub.save_finding instead of raw INSERT for seeding
- **ci:** run test-e2e as part of make check-full
- **ci:** complexity gates + 85/65% coverage floors
- **scout:** extract parse_ss_output; pragma subprocess shells; un-omit from coverage
- **hub:** on_button_click → dispatch dict of per-action handlers
- **ci:** verify_traceability.run → gates registry
- **make:** fix 3 polish issues surfaced by running all make targets
- **ops:** live-stack rebuild discipline — make live-rebuild + live-status
- **process:** breaking-change-in-planning discipline + BC-031 close-gap
- **hub:** BC-035 close-gap — cover migration backfill, format_heartbeat, mock-print
- **hub:** BC-030 close-gap — audit event carries internet_facing + .env.example

### Documentation
- **design:** product design + locked decisions
- **process:** DD-TGD process, stories, plan docs (BC-000)
- **stories:** mark BC-000 shipped
- **stories:** add BC-001 end-to-end mocked POC demo (ready)
- **stories:** mark BC-001 shipped
- **process:** add IDEAS backlog + wire it into DD-TGD
- **stories:** BC-003 draft + note BC-002 audit gaps in IDEAS
- **ideas:** capture CAP-T026 staleness as fix-opportunistically
- **memory:** BC-013 close-gap — scout migrated to HTTP; memory files updated
- **memory:** BC-014 close-gap — design-decisions.md matches same-track pull reality
- close BC-015 self-consistency gap + refresh make help
- **memory:** BC-016 close-gap — gate table lists all shipped drift guards
- **ideas:** log audit-protocol upgrade — grep-verify evidence pointers
- **ideas:** park BC-020 — dividend overlaps existing demo modes
- **ideas:** log 2 lint gaps surfaced by BC-020 live-smoke
- **ideas:** KEV lookup should resolve CVE aliases via grype 'related' field
- **ideas:** BC-025/026/027 close the big refactor entries
- **stories:** draft BC-030 (internet_facing detection) as ready
- **ideas:** log AVG-2909 operator action + promote BC-031 KEV-alias context
- **stories:** BC-031 draft — scan reconciliation + regression detection
- **plan:** BC-031 close-gap evidence rows for backfill tests
- **ideas:** verify-evidence-pointers should catch missing rows too
- **ideas:** log 3 BC-034 consumer candidates (heartbeat / digest refresh / purge)
- **stories:** mark BC-035 shipped
- **stories:** mark BC-030 shipped
- **bc-037:** post-ship audit — record scout-down verification date
- **bc-038:** post-ship audit — .env.example + check-full inclusion
- **bc-039:** post-ship audit — IDEAS followup for three-way sync
- **bc-040:** post-ship audit — Makefile wording + IDEAS cleanup
- **bc-041:** post-ship audit — remove stale IDEAS entry (shipped)
- **bc-042:** post-ship audit — remove stale IDEAS entry
- **stories:** mark BC-062 shipped in INDEX

### Other
- Initial commit

## [v0.3.5] — 2026-11-02

### ⚠ Breaking
- **hub,scout,scripts:** BC-062 versioning (software + API + schema) + SemVer bump + pre-push gate
- **ci,scripts:** BC-069 release toolchain — CI + make soak + auto-CHANGELOG

### Added
- **poc:** scout + hub + executor plugins + compose stack
- **hub,demo:** BC-001 end-to-end mocked POC demo
- **demo,hub:** BC-002 green — real pipeline on nginx:1.24, shipped
- **docs,scripts:** BC-004 capabilities + bidirectional traceability
- **scout,compose,make:** BC-005 multi-image Compose demo
- **make,docs:** BC-006 fast + full gate tiers
- **cli:** BC-007 `make findings` — read-only view of CVE state
- **process:** BC-008 pre-ship audit protocol (DD-TGD step 6a)
- **hub:** BC-003 Phase 1+2 — callback validation + idempotence
- **hub:** BC-003 shipped — Phase 3 live tap session complete
- **hub:** BC-009 shipped — real fix execution via Telegram
- **hub+scout:** BC-010 shipped — container health observability (4 endpoints)
- **beaconry_health:** BC-012 shipped — shared package + compiler-enforced trap guard
- **scout+status:** BC-013 shipped — scout live HTTP probes + Docker-compose-ps-like status table
- **executor:** BC-014 shipped — DockerAdapter same-track pull (no silent :latest jump)
- **process:** BC-015 shipped — memory drift regression guard
- **hub:** BC-011 shipped — target-container health probe (operator-triggered)
- **process:** BC-016 shipped — docs drift guard (make help + README ↔ code)
- **e2e:** BC-017 shipped — automated end-to-end BC-011 probe flow (no Telegram)
- **e2e:** BC-018 shipped — telegram-mock service (supersedes BC-017 test-mode guard)
- **ci:** BC-019 — grep-verify every shipping-evidence pointer
- **hub:** BC-020 — KEV ingestion + wire urgency.classify_tier
- **ci:** BC-021 — Dockerfile sources lint
- **hub:** BC-022 — digest shows KEV markers for suppressed findings
- **hub:** BC-023 — KEV findings bypass MIN_CVSS filter
- **ci:** BC-024 — verify-traceability enforces reverse direction
- **hub:** BC-028 — KEV lookup resolves grype's relatedVulnerabilities aliases
- **hub:** BC-029 — append-only audit log of operator-visible state changes
- **hub:** BC-031 — scan reconciliation + regression detection
- **hub:** BC-032 — fix viability indicator + fixed-in parity
- **hub:** BC-033 — verify_fix scans source_ref, not hardcoded :latest
- **hub:** BC-034 — persist last_seen_ts per finding
- **hub:** BC-035 notification adapter architecture + heartbeat for stale findings
- **hub,scout:** BC-030 internet_facing detection → Tier 1 fires
- **hub:** BC-036 decouple digest cadence from scan cadence
- **scout:** BC-037 on-demand scan trigger — make scan
- **hub,scenarios:** BC-038 live-stack scenario harness — make test-scenarios
- **docs,scripts:** BC-039 docs/VARIABLES.md + verify-variables drift lint
- **docs,scripts:** BC-040 three-way env sync — VARIABLES.md ↔ .env.example ↔ docker-compose.yml
- **scripts:** BC-041 verify-evidence-pointers reverse check
- **scripts,hub:** BC-042 `make audit` CLI + `scan_received` event
- **hub:** BC-043 CVE alias on non-CVE primary ids — KEV paper trail
- **scripts:** BC-044 live-smoke CLI harness + autodetected scenario coverage
- **hub,scripts:** BC-055 HTTP-only CLI → hub (no SSH)
- **docs,scripts:** BC-056 INSTALL.md + verify-install-doc (short-name reshape)
- **scripts,hub:** BC-057 make findings HOST= + 🖥️ multi-host notification lead
- **hub:** BC-058 mutation + audit cleanup
- **hub:** BC-059 callback_data carries CVE id
- **hub:** BC-060 notify_sent_failed audit symmetry
- **hub,scripts:** BC-061 scout fleet registry with liveness status
- **hub,scout,scripts:** BC-062 versioning (software + API + schema) + SemVer bump + pre-push gate
- **hub,scout,scripts:** BC-063 fleet health visibility + scout heartbeat
- **hub,scripts:** BC-064 FLEET LAST HEARTBEAT + stale-heartbeat unknown health
- **hub:** BC-066 manual executor as default + configurable adapter + 1h reminder loop
- **ci,scripts:** BC-069 release toolchain — CI + make soak + auto-CHANGELOG
- **scripts,build:** BC-070 operator CLI install — `pip install -e . → beaconry …`

### Fixed
- **hub,demo:** BC-002 robust-notify — HTML parse mode, cap, dedup, digest
- **docs,scripts:** BC-004 close-gaps — CAP-B/T prefix, required fields, hook, self-consistency
- **scripts:** BC-004 design-link + origin-story validation
- **scripts:** BC-004 enforce description as required field
- **hub:** BC-005 close two in-container regressions found by make demo
- BC-005 close-gaps — tests + story self-consistency + lessons
- **scripts:** enforce plan doc on fix/* and feat/* branches
- smoke mode broken + CAP-id regex truncates at hyphens
- BC-006 close-gaps — test graceful-skip + README skip-note
- BC-007 close-gaps — tier in sort key, schema migration, docs
- BC-007 load_finding reads tier + migration + round-trip tests
- **scripts:** BC-008 automate-audit — Shipping evidence enforced on shipped stories
- BC-003 close-gaps — run real_tools, add missing row, warn on scenario-count
- **beaconry_health:** BC-012 close-gap — pyright actually enforces the trap guard
- **hub+findings:** BC-011 close-gaps — findings CLI accepts new statuses + button-attach test
- **compose:** BC-011 env propagation — BEACONRY_HEALTH_PROBES/FIX_ENABLED/MOCK now actually propagate
- **findings:** BC-007 close-gap — auto-route to compose hub's volume DB
- **hub:** BC-011 close-gap — use module-level _waterfall binding in verify branch
- **tests:** BC-018 close-gap — unit coverage for telegram-mock + hub env dials
- **hub:** BC-020 close-gap — ship kev.py + urgency.py in the image
- **memory:** BC-020 close-gap — add memory drift ban
- **hub:** BC-020 close-gap — treat empty BEACONRY_KEV_FEED_URL as unset
- **docs:** BC-023 close-gap — Covers block + design-decisions annotation
- **docs:** BC-029 close-gap — add Gherkin block + gitignore audit.log
- **hub:** BC-032 close-gap — handle grype's 'not available' sentinel
- **docs:** BC-033 close-gap — add Scenario 3 test coverage
- **hub:** BC-035 — tolerate empty-string env vars from compose \${VAR:-}
- **hub:** BC-035/BC-030 close-gap — notify path works inside container
- **scenarios:** BC-038 close-gap — live-run fixes
- **audit:** BC-043 close-gap — make audit CVE=X matches alias too
- **audit:** FOLLOW output buffering + inline-script error handling
- **hub:** BC-065 digest content-change bypass is now opt-out via env
- **hub,executor,audit:** BC-067 quality sweep — 5 severity-1 bugs
- **hub:** BC-068 defensive cleanup — reconcile FD-leak + fix_version blank sanitize

### Changed
- initial commit (gitignore only; main is CI-promoted)
- **build:** Makefile, pyproject, pre-commit, dev requirements
- **poc:** pytest pyramid skeleton + sanity test
- **build:** lint + type cleanup, lower coverage floor until BC-001
- **poc:** BC-001 red integration test + plan doc
- **poc,hub:** BC-002 red state — tests + dispatch stubs + plan
- **poc:** BC-004 red state — traceability enforcement scenarios
- **poc:** BC-005 red — scout glob filter stubs + Compose demo plan
- **e2e:** use hub.save_finding instead of raw INSERT for seeding
- **ci:** run test-e2e as part of make check-full
- **ci:** complexity gates + 85/65% coverage floors
- **scout:** extract parse_ss_output; pragma subprocess shells; un-omit from coverage
- **hub:** on_button_click → dispatch dict of per-action handlers
- **ci:** verify_traceability.run → gates registry
- **make:** fix 3 polish issues surfaced by running all make targets
- **ops:** live-stack rebuild discipline — make live-rebuild + live-status
- **process:** breaking-change-in-planning discipline + BC-031 close-gap
- **hub:** BC-035 close-gap — cover migration backfill, format_heartbeat, mock-print
- **hub:** BC-030 close-gap — audit event carries internet_facing + .env.example

### Documentation
- **design:** product design + locked decisions
- **process:** DD-TGD process, stories, plan docs (BC-000)
- **stories:** mark BC-000 shipped
- **stories:** add BC-001 end-to-end mocked POC demo (ready)
- **stories:** mark BC-001 shipped
- **process:** add IDEAS backlog + wire it into DD-TGD
- **stories:** BC-003 draft + note BC-002 audit gaps in IDEAS
- **ideas:** capture CAP-T026 staleness as fix-opportunistically
- **memory:** BC-013 close-gap — scout migrated to HTTP; memory files updated
- **memory:** BC-014 close-gap — design-decisions.md matches same-track pull reality
- close BC-015 self-consistency gap + refresh make help
- **memory:** BC-016 close-gap — gate table lists all shipped drift guards
- **ideas:** log audit-protocol upgrade — grep-verify evidence pointers
- **ideas:** park BC-020 — dividend overlaps existing demo modes
- **ideas:** log 2 lint gaps surfaced by BC-020 live-smoke
- **ideas:** KEV lookup should resolve CVE aliases via grype 'related' field
- **ideas:** BC-025/026/027 close the big refactor entries
- **stories:** draft BC-030 (internet_facing detection) as ready
- **ideas:** log AVG-2909 operator action + promote BC-031 KEV-alias context
- **stories:** BC-031 draft — scan reconciliation + regression detection
- **plan:** BC-031 close-gap evidence rows for backfill tests
- **ideas:** verify-evidence-pointers should catch missing rows too
- **ideas:** log 3 BC-034 consumer candidates (heartbeat / digest refresh / purge)
- **stories:** mark BC-035 shipped
- **stories:** mark BC-030 shipped
- **bc-037:** post-ship audit — record scout-down verification date
- **bc-038:** post-ship audit — .env.example + check-full inclusion
- **bc-039:** post-ship audit — IDEAS followup for three-way sync
- **bc-040:** post-ship audit — Makefile wording + IDEAS cleanup
- **bc-041:** post-ship audit — remove stale IDEAS entry (shipped)
- **bc-042:** post-ship audit — remove stale IDEAS entry
- **stories:** mark BC-062 shipped in INDEX

### Other
- Initial commit

## [v0.3.4] — 2026-11-02

### ⚠ Breaking
- **hub,scout,scripts:** BC-062 versioning (software + API + schema) + SemVer bump + pre-push gate
- **ci,scripts:** BC-069 release toolchain — CI + make soak + auto-CHANGELOG

### Added
- **poc:** scout + hub + executor plugins + compose stack
- **hub,demo:** BC-001 end-to-end mocked POC demo
- **demo,hub:** BC-002 green — real pipeline on nginx:1.24, shipped
- **docs,scripts:** BC-004 capabilities + bidirectional traceability
- **scout,compose,make:** BC-005 multi-image Compose demo
- **make,docs:** BC-006 fast + full gate tiers
- **cli:** BC-007 `make findings` — read-only view of CVE state
- **process:** BC-008 pre-ship audit protocol (DD-TGD step 6a)
- **hub:** BC-003 Phase 1+2 — callback validation + idempotence
- **hub:** BC-003 shipped — Phase 3 live tap session complete
- **hub:** BC-009 shipped — real fix execution via Telegram
- **hub+scout:** BC-010 shipped — container health observability (4 endpoints)
- **beaconry_health:** BC-012 shipped — shared package + compiler-enforced trap guard
- **scout+status:** BC-013 shipped — scout live HTTP probes + Docker-compose-ps-like status table
- **executor:** BC-014 shipped — DockerAdapter same-track pull (no silent :latest jump)
- **process:** BC-015 shipped — memory drift regression guard
- **hub:** BC-011 shipped — target-container health probe (operator-triggered)
- **process:** BC-016 shipped — docs drift guard (make help + README ↔ code)
- **e2e:** BC-017 shipped — automated end-to-end BC-011 probe flow (no Telegram)
- **e2e:** BC-018 shipped — telegram-mock service (supersedes BC-017 test-mode guard)
- **ci:** BC-019 — grep-verify every shipping-evidence pointer
- **hub:** BC-020 — KEV ingestion + wire urgency.classify_tier
- **ci:** BC-021 — Dockerfile sources lint
- **hub:** BC-022 — digest shows KEV markers for suppressed findings
- **hub:** BC-023 — KEV findings bypass MIN_CVSS filter
- **ci:** BC-024 — verify-traceability enforces reverse direction
- **hub:** BC-028 — KEV lookup resolves grype's relatedVulnerabilities aliases
- **hub:** BC-029 — append-only audit log of operator-visible state changes
- **hub:** BC-031 — scan reconciliation + regression detection
- **hub:** BC-032 — fix viability indicator + fixed-in parity
- **hub:** BC-033 — verify_fix scans source_ref, not hardcoded :latest
- **hub:** BC-034 — persist last_seen_ts per finding
- **hub:** BC-035 notification adapter architecture + heartbeat for stale findings
- **hub,scout:** BC-030 internet_facing detection → Tier 1 fires
- **hub:** BC-036 decouple digest cadence from scan cadence
- **scout:** BC-037 on-demand scan trigger — make scan
- **hub,scenarios:** BC-038 live-stack scenario harness — make test-scenarios
- **docs,scripts:** BC-039 docs/VARIABLES.md + verify-variables drift lint
- **docs,scripts:** BC-040 three-way env sync — VARIABLES.md ↔ .env.example ↔ docker-compose.yml
- **scripts:** BC-041 verify-evidence-pointers reverse check
- **scripts,hub:** BC-042 `make audit` CLI + `scan_received` event
- **hub:** BC-043 CVE alias on non-CVE primary ids — KEV paper trail
- **scripts:** BC-044 live-smoke CLI harness + autodetected scenario coverage
- **hub,scripts:** BC-055 HTTP-only CLI → hub (no SSH)
- **docs,scripts:** BC-056 INSTALL.md + verify-install-doc (short-name reshape)
- **scripts,hub:** BC-057 make findings HOST= + 🖥️ multi-host notification lead
- **hub:** BC-058 mutation + audit cleanup
- **hub:** BC-059 callback_data carries CVE id
- **hub:** BC-060 notify_sent_failed audit symmetry
- **hub,scripts:** BC-061 scout fleet registry with liveness status
- **hub,scout,scripts:** BC-062 versioning (software + API + schema) + SemVer bump + pre-push gate
- **hub,scout,scripts:** BC-063 fleet health visibility + scout heartbeat
- **hub,scripts:** BC-064 FLEET LAST HEARTBEAT + stale-heartbeat unknown health
- **hub:** BC-066 manual executor as default + configurable adapter + 1h reminder loop
- **ci,scripts:** BC-069 release toolchain — CI + make soak + auto-CHANGELOG

### Fixed
- **hub,demo:** BC-002 robust-notify — HTML parse mode, cap, dedup, digest
- **docs,scripts:** BC-004 close-gaps — CAP-B/T prefix, required fields, hook, self-consistency
- **scripts:** BC-004 design-link + origin-story validation
- **scripts:** BC-004 enforce description as required field
- **hub:** BC-005 close two in-container regressions found by make demo
- BC-005 close-gaps — tests + story self-consistency + lessons
- **scripts:** enforce plan doc on fix/* and feat/* branches
- smoke mode broken + CAP-id regex truncates at hyphens
- BC-006 close-gaps — test graceful-skip + README skip-note
- BC-007 close-gaps — tier in sort key, schema migration, docs
- BC-007 load_finding reads tier + migration + round-trip tests
- **scripts:** BC-008 automate-audit — Shipping evidence enforced on shipped stories
- BC-003 close-gaps — run real_tools, add missing row, warn on scenario-count
- **beaconry_health:** BC-012 close-gap — pyright actually enforces the trap guard
- **hub+findings:** BC-011 close-gaps — findings CLI accepts new statuses + button-attach test
- **compose:** BC-011 env propagation — BEACONRY_HEALTH_PROBES/FIX_ENABLED/MOCK now actually propagate
- **findings:** BC-007 close-gap — auto-route to compose hub's volume DB
- **hub:** BC-011 close-gap — use module-level _waterfall binding in verify branch
- **tests:** BC-018 close-gap — unit coverage for telegram-mock + hub env dials
- **hub:** BC-020 close-gap — ship kev.py + urgency.py in the image
- **memory:** BC-020 close-gap — add memory drift ban
- **hub:** BC-020 close-gap — treat empty BEACONRY_KEV_FEED_URL as unset
- **docs:** BC-023 close-gap — Covers block + design-decisions annotation
- **docs:** BC-029 close-gap — add Gherkin block + gitignore audit.log
- **hub:** BC-032 close-gap — handle grype's 'not available' sentinel
- **docs:** BC-033 close-gap — add Scenario 3 test coverage
- **hub:** BC-035 — tolerate empty-string env vars from compose \${VAR:-}
- **hub:** BC-035/BC-030 close-gap — notify path works inside container
- **scenarios:** BC-038 close-gap — live-run fixes
- **audit:** BC-043 close-gap — make audit CVE=X matches alias too
- **audit:** FOLLOW output buffering + inline-script error handling
- **hub:** BC-065 digest content-change bypass is now opt-out via env
- **hub,executor,audit:** BC-067 quality sweep — 5 severity-1 bugs
- **hub:** BC-068 defensive cleanup — reconcile FD-leak + fix_version blank sanitize

### Changed
- initial commit (gitignore only; main is CI-promoted)
- **build:** Makefile, pyproject, pre-commit, dev requirements
- **poc:** pytest pyramid skeleton + sanity test
- **build:** lint + type cleanup, lower coverage floor until BC-001
- **poc:** BC-001 red integration test + plan doc
- **poc,hub:** BC-002 red state — tests + dispatch stubs + plan
- **poc:** BC-004 red state — traceability enforcement scenarios
- **poc:** BC-005 red — scout glob filter stubs + Compose demo plan
- **e2e:** use hub.save_finding instead of raw INSERT for seeding
- **ci:** run test-e2e as part of make check-full
- **ci:** complexity gates + 85/65% coverage floors
- **scout:** extract parse_ss_output; pragma subprocess shells; un-omit from coverage
- **hub:** on_button_click → dispatch dict of per-action handlers
- **ci:** verify_traceability.run → gates registry
- **make:** fix 3 polish issues surfaced by running all make targets
- **ops:** live-stack rebuild discipline — make live-rebuild + live-status
- **process:** breaking-change-in-planning discipline + BC-031 close-gap
- **hub:** BC-035 close-gap — cover migration backfill, format_heartbeat, mock-print
- **hub:** BC-030 close-gap — audit event carries internet_facing + .env.example

### Documentation
- **design:** product design + locked decisions
- **process:** DD-TGD process, stories, plan docs (BC-000)
- **stories:** mark BC-000 shipped
- **stories:** add BC-001 end-to-end mocked POC demo (ready)
- **stories:** mark BC-001 shipped
- **process:** add IDEAS backlog + wire it into DD-TGD
- **stories:** BC-003 draft + note BC-002 audit gaps in IDEAS
- **ideas:** capture CAP-T026 staleness as fix-opportunistically
- **memory:** BC-013 close-gap — scout migrated to HTTP; memory files updated
- **memory:** BC-014 close-gap — design-decisions.md matches same-track pull reality
- close BC-015 self-consistency gap + refresh make help
- **memory:** BC-016 close-gap — gate table lists all shipped drift guards
- **ideas:** log audit-protocol upgrade — grep-verify evidence pointers
- **ideas:** park BC-020 — dividend overlaps existing demo modes
- **ideas:** log 2 lint gaps surfaced by BC-020 live-smoke
- **ideas:** KEV lookup should resolve CVE aliases via grype 'related' field
- **ideas:** BC-025/026/027 close the big refactor entries
- **stories:** draft BC-030 (internet_facing detection) as ready
- **ideas:** log AVG-2909 operator action + promote BC-031 KEV-alias context
- **stories:** BC-031 draft — scan reconciliation + regression detection
- **plan:** BC-031 close-gap evidence rows for backfill tests
- **ideas:** verify-evidence-pointers should catch missing rows too
- **ideas:** log 3 BC-034 consumer candidates (heartbeat / digest refresh / purge)
- **stories:** mark BC-035 shipped
- **stories:** mark BC-030 shipped
- **bc-037:** post-ship audit — record scout-down verification date
- **bc-038:** post-ship audit — .env.example + check-full inclusion
- **bc-039:** post-ship audit — IDEAS followup for three-way sync
- **bc-040:** post-ship audit — Makefile wording + IDEAS cleanup
- **bc-041:** post-ship audit — remove stale IDEAS entry (shipped)
- **bc-042:** post-ship audit — remove stale IDEAS entry
- **stories:** mark BC-062 shipped in INDEX

### Other
- Initial commit

## [v0.3.3] — 2026-11-02

### ⚠ Breaking
- **hub,scout,scripts:** BC-062 versioning (software + API + schema) + SemVer bump + pre-push gate

### Added
- **poc:** scout + hub + executor plugins + compose stack
- **hub,demo:** BC-001 end-to-end mocked POC demo
- **demo,hub:** BC-002 green — real pipeline on nginx:1.24, shipped
- **docs,scripts:** BC-004 capabilities + bidirectional traceability
- **scout,compose,make:** BC-005 multi-image Compose demo
- **make,docs:** BC-006 fast + full gate tiers
- **cli:** BC-007 `make findings` — read-only view of CVE state
- **process:** BC-008 pre-ship audit protocol (DD-TGD step 6a)
- **hub:** BC-003 Phase 1+2 — callback validation + idempotence
- **hub:** BC-003 shipped — Phase 3 live tap session complete
- **hub:** BC-009 shipped — real fix execution via Telegram
- **hub+scout:** BC-010 shipped — container health observability (4 endpoints)
- **beaconry_health:** BC-012 shipped — shared package + compiler-enforced trap guard
- **scout+status:** BC-013 shipped — scout live HTTP probes + Docker-compose-ps-like status table
- **executor:** BC-014 shipped — DockerAdapter same-track pull (no silent :latest jump)
- **process:** BC-015 shipped — memory drift regression guard
- **hub:** BC-011 shipped — target-container health probe (operator-triggered)
- **process:** BC-016 shipped — docs drift guard (make help + README ↔ code)
- **e2e:** BC-017 shipped — automated end-to-end BC-011 probe flow (no Telegram)
- **e2e:** BC-018 shipped — telegram-mock service (supersedes BC-017 test-mode guard)
- **ci:** BC-019 — grep-verify every shipping-evidence pointer
- **hub:** BC-020 — KEV ingestion + wire urgency.classify_tier
- **ci:** BC-021 — Dockerfile sources lint
- **hub:** BC-022 — digest shows KEV markers for suppressed findings
- **hub:** BC-023 — KEV findings bypass MIN_CVSS filter
- **ci:** BC-024 — verify-traceability enforces reverse direction
- **hub:** BC-028 — KEV lookup resolves grype's relatedVulnerabilities aliases
- **hub:** BC-029 — append-only audit log of operator-visible state changes
- **hub:** BC-031 — scan reconciliation + regression detection
- **hub:** BC-032 — fix viability indicator + fixed-in parity
- **hub:** BC-033 — verify_fix scans source_ref, not hardcoded :latest
- **hub:** BC-034 — persist last_seen_ts per finding
- **hub:** BC-035 notification adapter architecture + heartbeat for stale findings
- **hub,scout:** BC-030 internet_facing detection → Tier 1 fires
- **hub:** BC-036 decouple digest cadence from scan cadence
- **scout:** BC-037 on-demand scan trigger — make scan
- **hub,scenarios:** BC-038 live-stack scenario harness — make test-scenarios
- **docs,scripts:** BC-039 docs/VARIABLES.md + verify-variables drift lint
- **docs,scripts:** BC-040 three-way env sync — VARIABLES.md ↔ .env.example ↔ docker-compose.yml
- **scripts:** BC-041 verify-evidence-pointers reverse check
- **scripts,hub:** BC-042 `make audit` CLI + `scan_received` event
- **hub:** BC-043 CVE alias on non-CVE primary ids — KEV paper trail
- **scripts:** BC-044 live-smoke CLI harness + autodetected scenario coverage
- **hub,scripts:** BC-055 HTTP-only CLI → hub (no SSH)
- **docs,scripts:** BC-056 INSTALL.md + verify-install-doc (short-name reshape)
- **scripts,hub:** BC-057 make findings HOST= + 🖥️ multi-host notification lead
- **hub:** BC-058 mutation + audit cleanup
- **hub:** BC-059 callback_data carries CVE id
- **hub:** BC-060 notify_sent_failed audit symmetry
- **hub,scripts:** BC-061 scout fleet registry with liveness status
- **hub,scout,scripts:** BC-062 versioning (software + API + schema) + SemVer bump + pre-push gate
- **hub,scout,scripts:** BC-063 fleet health visibility + scout heartbeat
- **hub,scripts:** BC-064 FLEET LAST HEARTBEAT + stale-heartbeat unknown health
- **hub:** BC-066 manual executor as default + configurable adapter + 1h reminder loop

### Fixed
- **hub,demo:** BC-002 robust-notify — HTML parse mode, cap, dedup, digest
- **docs,scripts:** BC-004 close-gaps — CAP-B/T prefix, required fields, hook, self-consistency
- **scripts:** BC-004 design-link + origin-story validation
- **scripts:** BC-004 enforce description as required field
- **hub:** BC-005 close two in-container regressions found by make demo
- BC-005 close-gaps — tests + story self-consistency + lessons
- **scripts:** enforce plan doc on fix/* and feat/* branches
- smoke mode broken + CAP-id regex truncates at hyphens
- BC-006 close-gaps — test graceful-skip + README skip-note
- BC-007 close-gaps — tier in sort key, schema migration, docs
- BC-007 load_finding reads tier + migration + round-trip tests
- **scripts:** BC-008 automate-audit — Shipping evidence enforced on shipped stories
- BC-003 close-gaps — run real_tools, add missing row, warn on scenario-count
- **beaconry_health:** BC-012 close-gap — pyright actually enforces the trap guard
- **hub+findings:** BC-011 close-gaps — findings CLI accepts new statuses + button-attach test
- **compose:** BC-011 env propagation — BEACONRY_HEALTH_PROBES/FIX_ENABLED/MOCK now actually propagate
- **findings:** BC-007 close-gap — auto-route to compose hub's volume DB
- **hub:** BC-011 close-gap — use module-level _waterfall binding in verify branch
- **tests:** BC-018 close-gap — unit coverage for telegram-mock + hub env dials
- **hub:** BC-020 close-gap — ship kev.py + urgency.py in the image
- **memory:** BC-020 close-gap — add memory drift ban
- **hub:** BC-020 close-gap — treat empty BEACONRY_KEV_FEED_URL as unset
- **docs:** BC-023 close-gap — Covers block + design-decisions annotation
- **docs:** BC-029 close-gap — add Gherkin block + gitignore audit.log
- **hub:** BC-032 close-gap — handle grype's 'not available' sentinel
- **docs:** BC-033 close-gap — add Scenario 3 test coverage
- **hub:** BC-035 — tolerate empty-string env vars from compose \${VAR:-}
- **hub:** BC-035/BC-030 close-gap — notify path works inside container
- **scenarios:** BC-038 close-gap — live-run fixes
- **audit:** BC-043 close-gap — make audit CVE=X matches alias too
- **audit:** FOLLOW output buffering + inline-script error handling
- **hub:** BC-065 digest content-change bypass is now opt-out via env
- **hub,executor,audit:** BC-067 quality sweep — 5 severity-1 bugs
- **hub:** BC-068 defensive cleanup — reconcile FD-leak + fix_version blank sanitize

### Changed
- initial commit (gitignore only; main is CI-promoted)
- **build:** Makefile, pyproject, pre-commit, dev requirements
- **poc:** pytest pyramid skeleton + sanity test
- **build:** lint + type cleanup, lower coverage floor until BC-001
- **poc:** BC-001 red integration test + plan doc
- **poc,hub:** BC-002 red state — tests + dispatch stubs + plan
- **poc:** BC-004 red state — traceability enforcement scenarios
- **poc:** BC-005 red — scout glob filter stubs + Compose demo plan
- **e2e:** use hub.save_finding instead of raw INSERT for seeding
- **ci:** run test-e2e as part of make check-full
- **ci:** complexity gates + 85/65% coverage floors
- **scout:** extract parse_ss_output; pragma subprocess shells; un-omit from coverage
- **hub:** on_button_click → dispatch dict of per-action handlers
- **ci:** verify_traceability.run → gates registry
- **make:** fix 3 polish issues surfaced by running all make targets
- **ops:** live-stack rebuild discipline — make live-rebuild + live-status
- **process:** breaking-change-in-planning discipline + BC-031 close-gap
- **hub:** BC-035 close-gap — cover migration backfill, format_heartbeat, mock-print
- **hub:** BC-030 close-gap — audit event carries internet_facing + .env.example

### Documentation
- **design:** product design + locked decisions
- **process:** DD-TGD process, stories, plan docs (BC-000)
- **stories:** mark BC-000 shipped
- **stories:** add BC-001 end-to-end mocked POC demo (ready)
- **stories:** mark BC-001 shipped
- **process:** add IDEAS backlog + wire it into DD-TGD
- **stories:** BC-003 draft + note BC-002 audit gaps in IDEAS
- **ideas:** capture CAP-T026 staleness as fix-opportunistically
- **memory:** BC-013 close-gap — scout migrated to HTTP; memory files updated
- **memory:** BC-014 close-gap — design-decisions.md matches same-track pull reality
- close BC-015 self-consistency gap + refresh make help
- **memory:** BC-016 close-gap — gate table lists all shipped drift guards
- **ideas:** log audit-protocol upgrade — grep-verify evidence pointers
- **ideas:** park BC-020 — dividend overlaps existing demo modes
- **ideas:** log 2 lint gaps surfaced by BC-020 live-smoke
- **ideas:** KEV lookup should resolve CVE aliases via grype 'related' field
- **ideas:** BC-025/026/027 close the big refactor entries
- **stories:** draft BC-030 (internet_facing detection) as ready
- **ideas:** log AVG-2909 operator action + promote BC-031 KEV-alias context
- **stories:** BC-031 draft — scan reconciliation + regression detection
- **plan:** BC-031 close-gap evidence rows for backfill tests
- **ideas:** verify-evidence-pointers should catch missing rows too
- **ideas:** log 3 BC-034 consumer candidates (heartbeat / digest refresh / purge)
- **stories:** mark BC-035 shipped
- **stories:** mark BC-030 shipped
- **bc-037:** post-ship audit — record scout-down verification date
- **bc-038:** post-ship audit — .env.example + check-full inclusion
- **bc-039:** post-ship audit — IDEAS followup for three-way sync
- **bc-040:** post-ship audit — Makefile wording + IDEAS cleanup
- **bc-041:** post-ship audit — remove stale IDEAS entry (shipped)
- **bc-042:** post-ship audit — remove stale IDEAS entry
- **stories:** mark BC-062 shipped in INDEX

### Other
- Initial commit
