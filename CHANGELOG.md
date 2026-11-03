# Changelog

All notable changes to beaconry are recorded here. The format is based
on [Keep a Changelog](https://keepachangelog.com/), and this project
follows [Semantic Versioning](https://semver.org/).

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
