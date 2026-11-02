# Stories index

One-line status board for operator scenarios. Full stories in `BC-NNN-<slug>.md`.

| ID     | Title                                  | Status      | Branch                              |
|--------|----------------------------------------|-------------|-------------------------------------|
| BC-000 | Repo + development process scaffold    | shipped     | — (merged to dev)                   |
| BC-001 | End-to-end mocked POC demo             | shipped     | — (merged to dev)                   |
| BC-002 | Real pipeline on one container         | shipped     | — (merged to dev)                   |
| BC-003 | Live-verified Telegram button handling | shipped     | — (merged to dev)                   |
| BC-004 | Capabilities docs + test traceability  | shipped     | — (merged to dev)                   |
| BC-005 | Multi-image Compose demo               | shipped     | — (merged to dev)                   |
| BC-006 | Tighten the shipping gate (tiers)      | shipped     | — (merged to dev)                   |
| BC-007 | `make findings` CLI view of CVE state  | shipped     | — (merged to dev)                   |
| BC-008 | Pre-ship audit protocol (DD-TGD 6a)    | shipped     | — (merged to dev)                   |
| BC-009 | Real fix execution via Telegram        | shipped     | — (merged to dev)                   |
| BC-010 | Container health observability (4 endpoints) | shipped     | — (merged to dev)                   |
| BC-012 | Shared `beaconry_health/` package (typed trap guard) | shipped     | — (merged to dev)                   |
| BC-013 | Scout live HTTP probes + unified status table | shipped     | — (merged to dev)                   |
| BC-014 | DockerAdapter same-track pull (no :latest jump) | shipped     | — (merged to dev)                   |
| BC-011 | Target-container health probe (operator-triggered post-restart verification) | shipped     | — (merged to dev)                   |
| BC-015 | Memory drift regression guard (growing banned-pattern list) | shipped     | — (merged to dev)                   |
| BC-016 | Docs drift guard — make help + README stay in sync with code | shipped     | — (merged to dev)                   |
| BC-017 | Automated end-to-end BC-011 probe flow (no Telegram) | shipped     | — (merged to dev)                   |
| BC-018 | telegram-mock service (supersedes BC-017 test-endpoint approach) | shipped     | — (merged to dev)                   |
| BC-019 | Grep-verify every shipping-evidence pointer | shipped     | — (merged to dev)                   |
| BC-020 | KEV ingestion + wire urgency.classify_tier | shipped     | — (merged to dev)                   |
| BC-021 | Dockerfile sources lint | shipped     | — (merged to dev)                   |
| BC-022 | Digest shows KEV markers for suppressed findings | shipped     | — (merged to dev)                   |
| BC-023 | KEV findings bypass MIN_CVSS filter | shipped     | — (merged to dev)                   |
| BC-024 | verify-traceability enforces reverse direction | shipped     | — (merged to dev)                   |
| BC-028 | KEV lookup resolves grype related aliases | shipped     | — (merged to dev)                   |
| BC-029 | Append-only audit log of operator-visible state changes | shipped     | — (merged to dev)                   |
| BC-030 | internet_facing detection → Tier 1 fires | shipped     | — (merged to dev)                   |
| BC-031 | Scan reconciliation + regression detection — fix is not forget | shipped     | — (merged to dev)                   |
| BC-032 | Fix viability indicator — "fixed in X" + will-this-work signal | shipped     | — (merged to dev)                   |
| BC-033 | verify_fix scans operator's actual source_ref (not hardcoded :latest) | shipped     | — (merged to dev)                   |
| BC-034 | Hub persists last_seen_ts per finding | shipped     | — (merged to dev)                   |
| BC-035 | Notification adapter architecture + heartbeat for stale open findings | shipped     | — (merged to dev)                   |
| BC-036 | Decouple digest cadence from scan cadence | shipped     | — (merged to dev)                   |
| BC-037 | On-demand scout scan trigger (`make scan`) | shipped     | — (merged to dev)                   |
| BC-038 | Live-stack scenario harness (`make test-scenarios`) | shipped     | — (merged to dev)                   |
| BC-039 | docs/VARIABLES.md + verify-variables drift lint | shipped     | — (merged to dev)                   |
| BC-040 | Three-way env sync — VARIABLES.md ↔ .env.example ↔ docker-compose.yml | shipped     | — (merged to dev)                   |
| BC-041 | verify-evidence-pointers reverse check | shipped     | — (merged to dev)                   |
| BC-042 | `make audit` CLI + `scan_received` audit event | shipped     | — (merged to dev)                   |
| BC-043 | CVE alias on non-CVE primary ids (KEV paper trail) | shipped     | — (merged to dev)                   |
| BC-044 | Live-smoke CLI harness (`make test-cli`) | shipped     | — (merged to dev)                   |

Lifecycle: `draft → ready → in-progress → shipped`. Status matches each story's frontmatter.
| BC-055 | HTTP-only CLI → hub (laptop manages hetzner-staging; split scout/CLI tokens) | shipped     | branch `feat/BC-055-multi-host-deploy`     |
| BC-055 | HTTP-only CLI → hub (laptop manages hetzner-staging; split scout/CLI tokens) | shipped     | — (merged to dev)                   |
| BC-056 | INSTALL.md + verify-install-doc (short-name reshape) | shipped     | branch `feat/BC-056-install-doc`     |
| BC-057 | Multi-host polish — `make findings HOST=` + 🖥️ Telegram lead | shipped     | — (merged to dev)                   |
| BC-058 | Mutation + audit cleanup — set_status fail-fast, rich audit, notify_sent, friendlier Telegram error | shipped     | — (merged to dev)                   |
| BC-059 | Telegram callback_data carries CVE id — error names the CVE | shipped     | — (merged to dev)                   |
| BC-060 | notify_sent_failed audit symmetry — token-rotate gaps are now auditable | shipped     | — (merged to dev)                   |
| BC-061 | Scout fleet registry — every scout visible in FLEET with liveness status | shipped     | — (merged to dev)                   |
| BC-062 | Software + API + schema versioning (+ SemVer auto-bump + pre-push gate) | shipped     | — (merged to dev)                   |
| BC-063 | Fleet rows show version/health/last-scan + scout heartbeat loop         | shipped     | — (merged to dev)                   |
| BC-064 | FLEET: LAST SEEN → LAST HEARTBEAT (fixed source) + stale-heartbeat unknown | shipped     | — (merged to dev)                   |
| BC-065 | Digest content-change bypass is now opt-out via env (strict 24h floor)    | shipped     | — (merged to dev)                   |
| BC-066 | Manual executor as default + configurable adapter + 1h reminder sweep     | shipped     | — (merged to dev)                   |
| BC-067 | Quality sweep — 5 severity-1 bugs from autonomous audit                   | shipped     | — (merged to dev)                   |
| BC-068 | Defensive cleanup — reconcile FD-leak guard + fix_version blank sanitize  | shipped     | — (merged to dev)                   |
| BC-069 | Release toolchain — CI + make soak + auto-CHANGELOG                       | shipped     | — (merged to dev)                   |
| BC-070 | Operator CLI install — `pip install -e . → beaconry …`                    | shipped     | — (merged to dev)                   |
| BC-071 | Fix CI env leak — BEACONRY_SKIP_VERSION_BUMP leaking into pytest          | shipped     | — (merged to dev)                   |
| BC-072 | pytest 8 → 9 (CVE-2025-71176) + cascading pytest-asyncio bump             | shipped     | — (merged to dev)                   |
| BC-073 | python-dotenv + requests dep bumps (closes PRs #2, #3)                    | shipped     | — (merged to dev)                   |
| BC-074 | Release automation + 24h soak + feature-complete lint + CI-failure log   | shipped     | — (merged to dev)                   |
| BC-075 | Metrics + threshold-based soak signals + privacy-first scout opt-in       | shipped     | — (merged to dev)                   |
