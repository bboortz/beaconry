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

Lifecycle: `draft → ready → in-progress → shipped`. Status matches each story's frontmatter.
