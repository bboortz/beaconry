# Changelog

All notable changes to beaconry are recorded here. The format is based
on [Keep a Changelog](https://keepachangelog.com/), and this project
follows [Semantic Versioning](https://semver.org/).

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
