# Stories index

One-line status board for operator scenarios. Full stories in `BC-NNN-<slug>.md`.

Lifecycle: `draft → ready → in-progress → shipped`. Status matches each story's frontmatter (verified by `make verify-traceability` and `scripts/verify-index-status.sh`, SH-056).

| ID     | Status      | Title                                                                       | Branch                              |
|--------|-------------|-----------------------------------------------------------------------------|-------------------------------------|
| BC-000 | shipped     | Repo + development process scaffold                                         | — (merged to dev)                   |
| BC-001 | shipped     | End-to-end mocked POC demo                                                  | — (merged to dev)                   |
| BC-002 | shipped     | Real pipeline on one container                                              | — (merged to dev)                   |
| BC-003 | shipped     | Live-verified Telegram button handling                                      | — (merged to dev)                   |
| BC-004 | shipped     | Capabilities docs + test traceability                                       | — (merged to dev)                   |
| BC-005 | shipped     | Multi-image Compose demo                                                    | — (merged to dev)                   |
| BC-006 | shipped     | Tighten the shipping gate (tiers)                                           | — (merged to dev)                   |
| BC-007 | shipped     | `make findings` CLI view of CVE state                                       | — (merged to dev)                   |
| BC-008 | shipped     | Pre-ship audit protocol (DD-TGD 6a)                                         | — (merged to dev)                   |
| BC-009 | shipped     | Real fix execution via Telegram                                             | — (merged to dev)                   |
| BC-010 | shipped     | Container health observability (4 endpoints)                                | — (merged to dev)                   |
| BC-011 | shipped     | Target-container health probe (operator-triggered post-restart verification) | — (merged to dev)                  |
| BC-012 | shipped     | Shared `beaconry_health/` package (typed trap guard)                        | — (merged to dev)                   |
| BC-013 | shipped     | Scout live HTTP probes + unified status table                               | — (merged to dev)                   |
| BC-014 | shipped     | DockerAdapter same-track pull (no :latest jump)                             | — (merged to dev)                   |
| BC-015 | shipped     | Memory drift regression guard (growing banned-pattern list)                 | — (merged to dev)                   |
| BC-016 | shipped     | Docs drift guard — make help + README stay in sync with code                | — (merged to dev)                   |
| BC-017 | shipped     | Automated end-to-end BC-011 probe flow (no Telegram)                        | — (merged to dev)                   |
| BC-018 | shipped     | telegram-mock service (supersedes BC-017 test-endpoint approach)            | — (merged to dev)                   |
| BC-019 | shipped     | Grep-verify every shipping-evidence pointer                                 | — (merged to dev)                   |
| BC-020 | shipped     | KEV ingestion + wire urgency.classify_tier                                  | — (merged to dev)                   |
| BC-021 | shipped     | Dockerfile sources lint                                                     | — (merged to dev)                   |
| BC-022 | shipped     | Digest shows KEV markers for suppressed findings                            | — (merged to dev)                   |
| BC-023 | shipped     | KEV findings bypass MIN_CVSS filter                                         | — (merged to dev)                   |
| BC-024 | shipped     | verify-traceability enforces reverse direction                              | — (merged to dev)                   |
| BC-028 | shipped     | KEV lookup resolves grype related aliases                                   | — (merged to dev)                   |
| BC-029 | shipped     | Append-only audit log of operator-visible state changes                     | — (merged to dev)                   |
| BC-030 | shipped     | internet_facing detection → Tier 1 fires                                    | — (merged to dev)                   |
| BC-031 | shipped     | Scan reconciliation + regression detection — fix is not forget              | — (merged to dev)                   |
| BC-032 | shipped     | Fix viability indicator — "fixed in X" + will-this-work signal              | — (merged to dev)                   |
| BC-033 | shipped     | verify_fix scans operator's actual source_ref (not hardcoded :latest)       | — (merged to dev)                   |
| BC-034 | shipped     | Hub persists last_seen_ts per finding                                       | — (merged to dev)                   |
| BC-035 | shipped     | Notification adapter architecture + heartbeat for stale open findings       | — (merged to dev)                   |
| BC-036 | shipped     | Decouple digest cadence from scan cadence                                   | — (merged to dev)                   |
| BC-037 | shipped     | On-demand scout scan trigger (`make scan`)                                  | — (merged to dev)                   |
| BC-038 | shipped     | Live-stack scenario harness (`make test-scenarios`)                         | — (merged to dev)                   |
| BC-039 | shipped     | docs/VARIABLES.md + verify-variables drift lint                             | — (merged to dev)                   |
| BC-040 | shipped     | Three-way env sync — VARIABLES.md ↔ .env.example ↔ docker-compose.yml       | — (merged to dev)                   |
| BC-041 | shipped     | verify-evidence-pointers reverse check                                      | — (merged to dev)                   |
| BC-042 | shipped     | `make audit` CLI + `scan_received` audit event                              | — (merged to dev)                   |
| BC-043 | shipped     | CVE alias on non-CVE primary ids (KEV paper trail)                          | — (merged to dev)                   |
| BC-044 | shipped     | Live-smoke CLI harness (`make test-cli`)                                    | — (merged to dev)                   |
| BC-055 | shipped     | HTTP-only CLI → hub (laptop manages hetzner-staging; split scout/CLI tokens) | — (merged to dev)                  |
| BC-056 | shipped     | INSTALL.md + verify-install-doc (short-name reshape)                        | — (merged to dev)                   |
| BC-057 | shipped     | Multi-host polish — `make findings HOST=` + 🖥️ Telegram lead                | — (merged to dev)                   |
| BC-058 | shipped     | Mutation + audit cleanup — set_status fail-fast, rich audit, notify_sent, friendlier Telegram error | — (merged to dev) |
| BC-059 | shipped     | Telegram callback_data carries CVE id — error names the CVE                 | — (merged to dev)                   |
| BC-060 | shipped     | notify_sent_failed audit symmetry — token-rotate gaps are now auditable     | — (merged to dev)                   |
| BC-061 | shipped     | Scout fleet registry — every scout visible in FLEET with liveness status    | — (merged to dev)                   |
| BC-062 | shipped     | Software + API + schema versioning (+ SemVer auto-bump + pre-push gate)     | — (merged to dev)                   |
| BC-063 | shipped     | Fleet rows show version/health/last-scan + scout heartbeat loop             | — (merged to dev)                   |
| BC-064 | shipped     | FLEET: LAST SEEN → LAST HEARTBEAT (fixed source) + stale-heartbeat unknown  | — (merged to dev)                   |
| BC-065 | shipped     | Digest content-change bypass is now opt-out via env (strict 24h floor)      | — (merged to dev)                   |
| BC-066 | shipped     | Manual executor as default + configurable adapter + 1h reminder sweep       | — (merged to dev)                   |
| BC-067 | shipped     | Quality sweep — 5 severity-1 bugs from autonomous audit                     | — (merged to dev)                   |
| BC-068 | shipped     | Defensive cleanup — reconcile FD-leak guard + fix_version blank sanitize    | — (merged to dev)                   |
| BC-069 | shipped     | Release toolchain — CI + make soak + auto-CHANGELOG                         | — (merged to dev)                   |
| BC-070 | shipped     | Operator CLI install — `pip install -e . → beaconry …`                      | — (merged to dev)                   |
| BC-071 | shipped     | Fix CI env leak — BEACONRY_SKIP_VERSION_BUMP leaking into pytest            | — (merged to dev)                   |
| BC-072 | shipped     | pytest 8 → 9 (CVE-2025-71176) + cascading pytest-asyncio bump               | — (merged to dev)                   |
| BC-073 | shipped     | python-dotenv + requests dep bumps (closes PRs #2, #3)                      | — (merged to dev)                   |
| BC-074 | shipped     | Release automation + 24h soak + feature-complete lint + CI-failure log      | — (merged to dev)                   |
| BC-075 | shipped     | Metrics + threshold-based soak signals + privacy-first scout opt-in         | — (merged to dev)                   |
| BC-076 | shipped     | `beaconry metrics --remote` reads from hub /audit over HTTP                 | — (merged to dev)                   |
| BC-077 | shipped     | Non-root hub + scout containers                                             | — (merged to dev)                   |
| BC-078 | shipped     | Distroless runtime image for hub + scout                                    | — (merged to dev)                   |
| BC-079 | shipped     | Pre-push runs full container verification                                   | — (merged to dev)                   |
| BC-080 | shipped     | `make cleanup` — teardown + image removal after tests                       | — (merged to dev)                   |
| BC-081 | shipped     | fix — bump docker CLI to Go stdlib ≥1.26.9 (GO-2026-6604)                   | — (merged to dev)                   |
| BC-082 | shipped     | Homelab-deployment coverage — cold-start + dogfood-grype + pinned-floor     | — (merged to dev)                   |
| BC-083 | shipped     | Forge runtime base image + verify-base-images preflight                     | — (merged to dev)                   |
| BC-086 | shipped     | Auto-fix with counter threshold — snoozed/accepted/open eligible            | feat/BC-086-auto-fix-counter        |
| BC-087 | shipped     | Wire `Last seen` into notify() + BEACONRY_LOG_LEVEL env                     | feat/BC-087-last-seen-wire-and-log-level |
| BC-088 | in-progress | Finding lifecycle reference document (`docs/LIFECYCLE.md`)                  | — (merged to dev)                   |
| BC-089 | in-progress | Snooze with real expiry (⏰ Nh buttons actually re-open at expiry)          | — (merged to dev)                   |
| BC-090 | in-progress | Auto-fix regression body actually reaches Telegram                          | — (merged to dev)                   |
| BC-091 | in-progress | KEV fetch User-Agent fix — CISA 403 escape                                  | — (merged to dev)                   |
| BC-092 | in-progress | Canary ring — production fixes gated on staging precedent                   | — (merged to dev)                   |
| BC-093 | in-progress | Rescan triggers — daily + docker events + pacman hook                       | — (merged to dev)                   |
| BC-094 | in-progress | Homelab consumes forge-signed ghcr images for beaconry                      | — (merged to dev)                   |
| BC-095 | in-progress | Builder stage FROM `ghcr.io/bboortz/forge-builder-trixie:latest`            | — (merged to dev)                   |
| BC-096 | shipped     | SBOM at builder → baked into runtime + `/sbom` + `make dogfood-sbom`        | — (merged to dev)                   |
| BC-097 | shipped     | Compose healthchecks use forge's `/hc`; runtime base tracks `:latest`       | — (merged to dev)                   |
| BC-098 | shipped     | Stage-at-runtime-path: Dockerfiles detect Python minor at build time        | — (merged to dev)                   |
| BC-099 | shipped     | Build-time ABI guard: fail if builder Python minor ≠ runtime minor          | — (merged to dev)                   |
| BC-100 | shipped     | telegram_mock joins the forge-signed scratch base + ABI guard family        | — (merged to dev)                   |
| BC-101 | shipped     | Dev-compose containers renamed to avoid collision with systemd scout        | — (merged to dev)                   |
| BC-102 | shipped     | Hub-side scout-offline Telegram alert + stale-scout findings reconcile      | — (merged to dev)                   |
| BC-103 | shipped     | Scout rescans on image/container `delete` + `destroy` + `die` events        | — (merged to dev)                   |
| BC-104 | shipped     | Notification retry queue — Telegram outage no longer drops alerts           | — (merged to dev)                   |
| BC-105 | shipped     | `make findings` shows FIRST SEEN + LAST CHANGE + auto-close reason          | — (merged to dev)                   |
| BC-106 | shipped     | Partial-inventory guard strengthened — scout reports expected/collected     | — (merged to dev)                   |
| BC-107 | shipped     | Scout POST retry queue — no SBOM silently dropped on hub outage             | — (merged to dev)                   |
| BC-108 | shipped     | Docker events watchdog — reconnect on daemon restart                        | — (merged to dev)                   |
| BC-109 | shipped     | Grype DB update retry — hub recovers from transient network failure         | — (merged to dev)                   |
| BC-110 | shipped     | Verify probe retry — handle post-restart port-binding race                  | — (merged to dev)                   |
| BC-111 | shipped     | DockerAdapter pull retry — survive transient registry failures              | — (merged to dev)                   |
| BC-112 | shipped     | Scan trigger proxy retry — survive transient scout unreachability           | — (merged to dev)                   |
| BC-113 | shipped     | /scan payload size limit — DoS guard                                        | — (merged to dev)                   |
| BC-114 | shipped     | Reconcile TOCTOU lock — serialize concurrent writes during auto-close       | — (merged to dev)                   |
| BC-115 | shipped     | Audit write failure sticky flag — surface disk-full degradation             | — (merged to dev)                   |
| BC-116 | shipped     | Finding state machine extension — add `fixing` + `rolled_back` statuses     | — (merged to dev)                   |
| BC-117 | in-progress | Cross-repo CVE fix workflow — awaiting_upstream status + Telegram hint      | — (merged to dev)                   |
| BC-118 | shipped     | `beaconry status` — probed URLs + running host + fleet (you) marker         | — (merged to dev)                   |
| BC-119 | in-progress | Scout /scan gzip compression — fit large fleets without operator tuning     | — (merged to dev)                   |
| BC-120 | shipped     | Scout state DB path hardening — degrade loudly, never drop silently         | — (merged to dev)                   |
| BC-121 | shipped     | Track forge-builder-trixie → forge-builder-python-trixie rename             | — (merged to dev)                   |
| BC-122 | shipped     | Pipeline integrity hashes + runtime verifier                                | — (merged to dev)                   |
| BC-123 | shipped     | Operator trust bundle — audit every silent drop + policies + CVE filter     | — (merged to dev)                   |
| BC-124 | shipped     | Heartbeat retry queue + HUB_HTTP_PORT + BC-117/119 integration tests        | — (merged to dev)                   |
| BC-125 | shipped     | EXECUTOR env default hygiene — empty string resolves to `manual`            | — (merged to dev)                   |
| BC-126 | shipped     | /audit/integrity endpoint + beaconry verify-integrity HTTP routing          | — (merged to dev)                   |
| BC-127 | shipped     | `beaconry findings --severity` filter (Critical/High/Medium/Low)            | — (merged to dev)                   |
| BC-128 | shipped     | Persist every unique CVE; notification cap is separate                      | — (merged to dev)                   |
| BC-129 | draft       | Loud alert for newly-persisted Tier ≤ 1 findings even in suppressed tail    | — (merged to dev)                   |
| BC-130 | draft       | Diagnose + fix hub-shutdown-race source of heartbeat_integrity_mismatch     | — (merged to dev)                   |
| BC-131 | draft       | Audit-read performance — tail-chunked audit.log + batched finding enrichment | — (merged to dev)                  |
| BC-132 | draft       | HTTP latency budget gate + sidecar SQLite audit index                       | — (merged to dev)                   |
| BC-139 | draft       | Audit-index write-amplitude fix — skip + batch + WAL bounds                 | feat/BC-139-BC-140-audit-index-live-load-fix |
| BC-140 | draft       | Audit-index async decoupling — bounded queue + overflow metrics + drain     | — (merged to dev)                   |
| BC-141 | draft       | Container + systemd memory/CPU caps — contain OOM to the container          | feat/BC-141-BC-144-memory-safety    |
| BC-142 | draft       | Memory metrics on /healthz + beaconry status                                | feat/BC-141-BC-144-memory-safety    |
| BC-143 | draft       | tracemalloc toggle + memory-leak playbook                                   | feat/BC-141-BC-144-memory-safety    |
| BC-144 | draft       | Perf gate asserts bounded memory growth                                     | feat/BC-141-BC-144-memory-safety    |
| BC-145 | in-progress | Shepherd Release 0 POC (second pilot after healthy)                         | feat/BC-145-shepherd-poc            |
| BC-146 | ready       | Fix Dockerfile-shape test drift (BC-070/082/083/097/099/100)                | — (filed during BC-145)             |
| BC-147 | in-progress | docs/ three-folder layout — specs / plan / stories                          | feat/BC-147-docs-specs-layout       |
| BC-148 | ready       | Swap pre-push hook from check-full to check (fast tier)                     | —                                   |
| BC-149 | ready       | pytest-xdist + real test-affected scoping                                   | —                                   |
| BC-150 | shipped     | hook-commit + hook-push fleet tiers (SH-042 mirror)                         | — (merged to dev)                   |
| BC-151 | shipped     | check-docs fleet-tier wrapper (SH-055 mirror)                               | — (merged to dev)                   |
| BC-152 | shipped     | verify-index-status drift check (SH-056 mirror)                             | feat/SH-056-verify-index-status     |
