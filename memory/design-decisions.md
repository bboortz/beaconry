---
name: design-decisions
description: All architecture and product decisions from the 2026-10-29 brainstorm (4 iterations)
metadata:
  type: project
---

*Locked: 2026-10-29. Source: 4-iteration question session.*

## Urgency tiers

*Implemented in BC-020. Pre-BC-020 all findings stored tier=3 (DB default) because `urgency.classify_tier` was never called. BC-020 wired `kev.is_known_exploited` + `classify_tier` into `process_sbom_entry`. `internet_facing` remains hard-coded False pending a future story.*

```
Tier 0 — CISA KEV entry
  Target: 15 minutes from feed ingestion to Telegram button
  KEV + internet-facing → emergency alert immediately
  KEV + safe class (package bump) → auto-fix, notify after

Tier 1 — CVSS ≥ 9.0, internet-facing service
  Target: 15 minutes; auto-fix if safe class, else confirm button

Tier 2 — CVSS 7–8 or LAN-only service
  Target: 1 hour; confirm button

Tier 3 — CVSS < 7 or installed-not-running
  Target: 24-hour ceiling; nightly batch digest
```

## Auto-fix trust model

Safe to auto-fix when: ZFS snapshot (packages) OR `:rollback` tag (containers) taken first, AND health check waterfall passes.

**Health check waterfall** (run strongest available, fall through):
1. Docker HEALTHCHECK directive — if defined in image, use it
2. HTTP probe — auto-discover from `ss -tlnp`; GET → expect HTTP 200
3. Systemd stability window — service stays active for 60s post-fix
4. Custom operator-defined check — any command, exit 0 = pass

Operator sets minimum required level per service. No health check above Level 3 = no auto-fix.

**Auto-rollback**: if health check fails after fix:
- Package: downgrade to previous version
- Container: `docker tag <base>:rollback <source_ref> && docker compose up -d`
  (`:rollback` tag is snapped before every rebuild as a mandatory pre-step; the restore target is the same source tag the operator runs, per BC-014's same-track rule)

## Multi-host canary ring

Never fix multiple hosts in parallel. Default ring order:
```
Ring 0: workstation (laptop) — lowest risk
Ring 1: staging (hetzner-staging) — canary
Ring 2: production (server2) — only after Ring 1 health check passes
```
Each host has `role: staging | production | workstation` label in scout config.
If Ring 1 fails, Ring 2 is blocked automatically.

## Notification format

All four elements always — one message, one tap, no dashboard needed:
```
🚨 [TIER / CVSS] CVE-ID
Severity: CVSS X.X (Label)
Host: <host> · Service: <service> (<exposure>)
Why urgent: <KEV status / attack vector>
Fix: <exact command>
Estimated downtime: ~Xs

[Fix now]  [Snooze 4h]  [Accept risk]
```

## Separation of concerns

Scout is **read-only forever**. It generates SBOMs via syft, posts to hub. It never executes fixes.

Execution is a **pluggable adapter** in the hub:
- `DockerAdapter` — `docker pull <source_ref>` (same-track per BC-014; preserves `postgres:16-alpine`, `nginx:alpine`, etc. — operator restarts the container themselves)
- `ManualAdapter` — shows the command, marks as manual action required
- Future: `AnsibleAdapter`, `NixAdapter`, `WebhookAdapter`

This makes beaconry infrastructure-agnostic: swap in Ansible, Nix, or any executor without changing the scanner or hub logic.

## Inventory cadence

Hybrid model:
- **Real-time push**: pacman post-transaction hook + docker events listener → scout queues delta
- **Daily full rescan**: 02:00 host-local time — catches pip installs, port changes, drift

## Container CVE coverage V1

**Decision: 2026-10-29. Review: 2026-11-28.**

V1 covers: base image (`FROM`) + explicit dependency files (requirements.txt, package-lock.json, go.sum).
V1 does NOT cover: transitive C library vulnerabilities in container layers.

Why: full grype layer scan adds complexity and scan latency; 80% coverage, zero extra ops burden.

User-facing: dashboard shows coverage indicator per container ("Base image + explicit deps only").

**⚠️ REVIEW DUE 2026-11-28**: Did V1 coverage miss any real CVE in a container layer in the first 30 days? If yes, promote full layer scanning to V1.

## Coverage transparency

Per-package coverage indicators in the UI:
- ✓ covered by NVD + OSV.dev
- ⚠ base image only (transitive layers not scanned)
- ✗ no feed coverage — treated as unknown risk

Vendor-specific advisories pulled aggressively: Arch Linux Security Tracker, Debian Security, vendor RSS.

## Demo / POC trigger

Pull `nginx:1.24` (known CVEs in base image packages). Scout scans it → hub finds CVE → Telegram [Fix now] → DockerAdapter pulls `nginx:1.24` (same-track per BC-014; upstream re-releases the fluid tag → re-scan confirms clean).

## OSS release criterion

One-command install: `docker compose up` on a clean machine results in running hub, scout registered, CVE feeds cached, test Telegram notification sent. No manual config beyond tokens.

## syft + grype pipeline

```
scout: syft <image> -o syft-json → SBOM JSON → POST /scan
hub:   grype sbom:/tmp/x.json -o json → matches → filter CVSS ≥ 7 → notify
```

syft owns "what is installed". grype owns "what is vulnerable".
