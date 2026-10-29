# Beaconry — Design Document

*First draft: 2026-10-29*

---

## Problem statement

A CVE is published. It affects a package you run. You find out three days later, by accident, while reading a forum post. By then the window for a targeted attack was open for 72 hours against infrastructure you could have patched in 30 seconds.

This is the normal experience for every self-hosted operator who isn't a full-time security team. Existing tools either solve half the problem or are priced for enterprises that have dedicated people to run them:

- **Trivy / Grype / Dependabot** — scan code in CI, not running infrastructure. Silent after deployment.
- **OpenVAS / Qualys / Tenable** — enterprise pricing, complex setup, built for compliance teams, not for operators.
- **NVD email alerts / CISA KEV** — blast notifications with no knowledge of your specific stack. High noise, no triage.
- **Package manager security advisories** — Arch Linux Security Tracker, Debian Security, GHSA — all require you to check manually.

Nobody has solved: *you have a specific running stack, a CVE just dropped, here is exactly what to do, here is me doing it.*

---

## Vision

**Beaconry is the immune system for self-hosted infrastructure: it knows what you run, watches for threats to it, and heals automatically — before you need to act.**

The goal is to compress the time between "CVE published" and "infrastructure patched and verified" from days to minutes, without requiring the operator to do anything except approve high-risk changes.

---

## Goals

1. Know exactly what software is running on every host — packages, containers, exposed services, language runtimes.
2. Match that inventory against CVE feeds within minutes of publication.
3. Score each finding by actual risk: CVSS severity × network exposure (a local-only package with a critical CVE is lower urgency than the same CVE on an internet-facing service).
4. Notify through the channels operators already use (Telegram, Discord, email, webhook).
5. Provide an exact, host-specific remediation command — not generic advice.
6. Execute that remediation on approval, or automatically for safe classes of fix.
7. Verify the fix worked by re-scanning immediately after.
8. Track state: what was found, what was done, when, by whom.

---

## Non-goals

- Network intrusion detection / traffic analysis (that is tracehound's domain).
- Compliance reporting for SOC2 / ISO27001 (a later paid feature, not the core).
- Vulnerability research or zero-day discovery.
- Scanning code repositories (that is Dependabot's domain).
- Supporting Windows or macOS hosts in v1.

---

## Target users

### Primary: the self-hosted operator

Running 2–20 machines: a home server, a VPS or two, maybe a staging environment. Uses Ansible, Docker, systemd. Reads HN and r/selfhosted. Cares deeply about security but has no dedicated security tooling because nothing built for this scale exists at a price that makes sense. This user is the beachhead — they are vocal, they share what works, and there are hundreds of thousands of them.

### Secondary: the small engineering team

5–50 engineers, running their own infrastructure. No dedicated security engineer. Has CI/CD but nobody watching the runtime stack for CVEs. Spends real money on cloud but runs critical services self-hosted to control cost and data. Willing to pay $50–100/month for something that works.

### Tertiary: the MSP / managed service provider

Manages infrastructure for multiple clients. Needs multi-tenant CVE visibility in one place. The approval workflow and audit log become critical. Willing to pay per-client.

---

## Market context

The self-hosted / homelab community is larger than it looks:

- r/selfhosted: 600K+ members
- r/homelab: 1M+ members
- GitHub stars on self-hosted tools (Nextcloud, Home Assistant, Jellyfin) range from 20K–80K each
- The EU Cyber Resilience Act (2025) and US Executive Order 14028 make SBOM generation and vulnerability tracking a compliance requirement for software shipped commercially — creating a forcing function for exactly the tooling Beaconry provides

No self-hosted-first CVE remediation tool exists. The gap is real.

---

## Solving strategy

### Why not wrap an existing tool

Trivy, Grype, and Syft are excellent at what they do. Beaconry uses them as the engine — not compete with them. The pipeline is: **syft generates the SBOM → grype matches it against CVE feeds**. The value Beaconry adds is everything around that pipeline: inventory collection, exposure scoring, notification, remediation execution, state tracking. Building yet another CVE matcher is not where the effort belongs.

**syft** handles the inventory side — it extracts a complete software bill of materials from container images (layer by layer), directories, package manager lock files (requirements.txt, package-lock.json, go.sum), and system package databases. Its output is a structured SBOM (CycloneDX or SPDX) that grype consumes directly.

**grype** handles the matching side — it takes a syft SBOM and matches every component against NVD, OSV.dev, GHSA, and ecosystem-specific advisories. It returns structured findings with CVE ID, severity, affected version, and fixed version.

This split is clean: syft owns "what is installed", grype owns "what is vulnerable".

### The inventory problem is the hard part

Every other tool assumes you tell it what to scan. Beaconry needs to discover the full picture automatically:

- Arch Linux: `pacman -Q` → exact package + version
- Debian/Ubuntu: `dpkg-query -W` → same
- Docker/Podman: `docker inspect` → base image + digest + layers
- Python: `pip list` per virtualenv
- Node: `package-lock.json` per project
- Go/Rust binaries: extract build info from the binary itself (`go version -m`, `cargo auditable`)
- Exposed ports: `ss -tlnp` — which of the above are actually reachable

The scout runs this inventory collection. It produces a structured JSON payload. Everything else flows from that.

### Exposure scoring

A CVSS 9.8 in a package that is installed but never called by a network-reachable service is not the same emergency as a CVSS 7.0 in nginx itself. Risk = CVSS × reachability:

```
exposure_score = cvss_base × reachability_multiplier

reachability:
  internet-facing service   → 1.0
  LAN-only service          → 0.6
  installed, not running    → 0.2
  installed, not listening  → 0.1
```

This halves the noise. Operators stop ignoring alerts when alerts are accurate.

### Remediation as a first-class citizen

Most tools stop at "here is the CVE". Beaconry's answer to every finding is an executable plan:

```
Package CVE (Arch):        sudo pacman -Syu <package>
Package CVE (Debian):      sudo apt-get install --only-upgrade <package>
Container base image CVE:  rebuild + redeploy (trigger Ansible / docker compose pull)
Custom Python dep:         pip install --upgrade <package> in the right virtualenv
Exposed service, no patch: sudo ufw deny <port>  # interim mitigation
Config vulnerability:      exact sed / Ansible task to apply the fix
```

For complex cases — where the right fix depends on the running config — Claude generates the remediation steps from CVE description + host context. This is where the LLM adds real value: not as a scanner, but as a remediation writer that knows your specific stack.

### The approval flow

```
Level 0 — Inform only        alert sent, no action
Level 1 — Suggest            alert + exact command, human runs it
Level 2 — Confirm + execute  Telegram button "Fix now" → executes → reports result
Level 3 — Auto-execute       fires immediately for pre-approved fix classes
```

Safe auto-execute classes (default opt-in):
- Package update where the only change is a version bump (no config migration)
- Docker base image rebuild where the service has a health check
- `pacman -Syu` / `apt upgrade` for packages below a configurable severity threshold

Unsafe, always requires approval:
- Config changes
- Service restarts during business hours (configurable maintenance windows)
- Any fix that touches authentication or TLS

### Verification loop

After executing a fix, the scout re-scans the affected package/image immediately. The notification is updated: "CVE-2024-XXXX — patched and verified on server2 (openssl 3.3.1 → 3.3.2, re-scan clean)". This closes the loop and creates an audit trail.

---

## Urgency tiers

Not all CVEs are equal. The time target varies by tier:

```
Tier 0 — CISA KEV entry
  Target: 15 minutes from feed ingestion to Telegram button
  Internet-facing + KEV → emergency alert immediately
  Safe class (package bump) + KEV → auto-fix, notify after
  Always wakes the operator regardless of time

Tier 1 — CVSS ≥ 9.0, internet-facing service
  Target: 15 minutes
  Auto-fix if safe class, otherwise confirm button

Tier 2 — CVSS 7–8 or LAN-only service
  Target: 1 hour
  Batch with other findings, confirm button

Tier 3 — CVSS < 7 or installed-not-running
  Target: 24-hour ceiling
  Nightly digest, no immediate alert
```

The exploit window for Tier 0/1 is measured in hours. For those tiers, 24 hours is too slow — the attack window is already open.

---

## Notification format

Every Telegram alert includes all four elements. No omissions — the operator makes the decision from a single message without opening a dashboard:

```
🚨 [TIER 0 — CISA KEV] CVE-2024-XXXX
Severity: CVSS 9.8 (Critical)
Host: server2 · Service: nextcloud (internet-facing)
Why urgent: Active exploitation confirmed in the wild. Network-exploitable, no auth required.
Fix: docker compose pull nextcloud && docker compose up -d
Estimated downtime: ~45s

[Fix now]  [Snooze 4h]  [Mark accepted risk]
```

---

## Multi-host canary ring

When the same CVE affects multiple hosts, fixes are applied sequentially through deployment rings — never in parallel. Default ring order:

```
Ring 0: workstation (laptop) — lowest risk, not serving traffic
Ring 1: staging (hetzner-staging) — canary for production changes
Ring 2: production (server2) — only after Ring 1 health check passes
```

Each host carries a `role` label in the scout config (`staging | production | workstation`). If Ring 1 fails the health check after a fix, Ring 2 is blocked automatically. The operator is notified.

Parallel execution is never used for coordinated multi-host fixes. The speed cost is small (minutes); the blast-radius protection is significant.

---

## Health check waterfall

Before accepting any auto-fix as verified, Beaconry runs the strongest available health check in order:

```
1. Docker HEALTHCHECK directive   — if defined in the image, use it
2. HTTP probe                     — auto-discover from ss -tlnp; GET → expect HTTP 200
3. Systemd stability window       — service stays active for 60s post-fix
4. Custom check (operator-defined) — optional per-service override; any command, exit 0 = pass
```

The operator can set a minimum required level per service. Services with no check above Level 3 are not eligible for auto-fix — they require confirmation.

### Auto-rollback

If the health check fails after an auto-fix:

- **Package fix**: scout runs the inverse operation (downgrade to previous version) and re-scans
- **Container rebuild**: `docker tag <name>:rollback <name>:latest && docker compose up -d` (the `:rollback` tag was snapped before the rebuild started as a mandatory pre-step)

Auto-rollback fires without operator input. The notification becomes: "CVE-2024-XXXX — fix attempted on server2, health check failed, rolled back automatically. Manual review required."

---

## Inventory cadence

Hybrid model: real-time push on change + daily full rescan.

```
Real-time (push):
  pacman post-transaction hook → scout queues delta inventory
  docker events listener → scout queues delta on image pull/create/remove
  Captures most changes within seconds of occurrence

Daily full rescan (pull):
  Catches drift not covered by hooks: pip installs, manual file changes,
  language runtime updates outside package managers, new open ports
  Runs at 02:00 host-local time
```

The hub always has a current inventory. CVE matching runs on every push delta and on the daily rescan.

---

## Container CVE coverage — V1 scoping decision

*Decision recorded: 2026-10-29. Review date: 2026-11-28.*

**V1 covers**: base image declared in `FROM` + explicit dependency files (`requirements.txt`, `package-lock.json`, `go.sum`). Matched against NVD + OSV.dev + GHSA.

**V1 does not cover**: transitive C library vulnerabilities baked into container layers (e.g. libssl inside a `python:3-slim` image that is not in requirements.txt). This is a known blind spot.

**Why**: full layer scanning via grype requires extracting and walking the container filesystem. It works but adds significant complexity to the scout and scan latency. The V1 tradeoff: 80% coverage, zero extra ops burden.

**User-facing disclosure**: the dashboard shows a coverage indicator per container — "Base image + explicit deps only. Full layer scan: not enabled." No silent gaps.

**⚠️ REVIEW DUE 2026-11-28**: evaluate whether to promote full grype layer scanning to V1 scope based on 30 days of real-world usage. Question: did the V1 coverage miss any real CVE in a container layer during this period?

---

## Coverage transparency

Beaconry shows per-package and per-container coverage status explicitly:

```
openssl 3.3.1        ✓ NVD + OSV.dev + Arch Security Tracker
python:3-slim        ✓ base image (NVD) + requirements.txt (GHSA)
                     ⚠ transitive layers: not scanned (V1 limitation)
custom-binary-1.0    ✗ no feed coverage — treated as unknown risk
```

Packages with no CVE history in any feed are flagged, not silently trusted. Vendor-specific advisories (Arch Linux Security Tracker, Debian Security, vendor RSS) are pulled aggressively for packages not covered by OSV.dev.

---

## Open source release criteria

Beaconry is ready for other operators when:

**One-command install**: `docker compose up` on a clean machine results in a running hub with a scout registered, CVE feeds cached, and a test notification sent to Telegram. No manual config beyond tokens.

This is the bar — not "caught a real CVE" (which proves security value) but "works without hand-holding" (which proves distribution readiness). The self-hosted community will not adopt a tool that requires debugging to install.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  beaconry-scout  (lightweight, per host)                        │
│  ─────────────────────────────────────────────────────────────  │
│  inventory collector  →  JSON SBOM                             │
│  port / service mapper                                          │
│  exec agent (runs approved remediations, reports result)        │
│  push to hub on schedule + on-demand                           │
└──────────────────────────┬──────────────────────────────────────┘
                           │ HTTPS + bearer token
┌──────────────────────────▼──────────────────────────────────────┐
│  beaconry-hub  (self-hosted or SaaS)                            │
│  ─────────────────────────────────────────────────────────────  │
│  CVE feed ingestion   NVD + OSV.dev + GHSA + CISA KEV          │
│  inventory × CVE match  (grype as library)                      │
│  exposure scorer                                                │
│  remediation planner  (templates + Claude for complex cases)   │
│  notification dispatcher  (Telegram, Discord, email, webhook)  │
│  approval handler  (Telegram bot callbacks)                     │
│  remediation executor  (dispatches to scout exec agent)        │
│  state store  (SQLite self-hosted, Postgres SaaS)              │
│  REST API + dashboard                                           │
└──────────────────────────┬──────────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────────┐
│  beaconry-cli  (operator workstation)                           │
│  ─────────────────────────────────────────────────────────────  │
│  beaconry scan           on-demand scan                         │
│  beaconry status         show open CVEs across all hosts        │
│  beaconry fix <cve-id>   approve and execute a remediation      │
│  beaconry ack <cve-id>   mark as accepted risk with reason      │
│  beaconry report         generate audit report                  │
└─────────────────────────────────────────────────────────────────┘
```

### Scout design principles

- Single static binary (Go). No runtime dependencies, no daemon required for basic use.
- Can run as a one-shot cron or as a persistent service.
- Communicates with hub over HTTPS. Token auth. Works through NAT.
- Inventory collection is read-only. Exec agent is a separate process, started only when a remediation is dispatched and approved.
- All secrets stay on the host. The hub never sees SSH keys, credentials, or config file contents.

### Hub design principles

- Self-hosted first. Docker Compose, one command to start.
- SQLite for self-hosted (zero ops). PostgreSQL for multi-tenant SaaS.
- CVE feeds pulled and cached locally. No dependency on third-party availability for matching.
- Stateless API — the hub can be restarted without losing state.
- Multi-host, multi-user from day one even in the self-hosted version.

---

## Tech stack

| Component | Language | Rationale |
|-----------|----------|-----------|
| beaconry-scout | Go | single static binary, cross-compile for ARM/x86, no runtime deps |
| beaconry-hub | Python (FastAPI) | fast iteration, rich CVE/SBOM ecosystem, grype/trivy as libraries |
| beaconry-cli | Go | ships with the scout binary, one download |
| Dashboard | HTMX + minimal JS | no build step, works without a JS framework, fits the self-hosted ethos |
| SBOM generation | syft | generates CycloneDX/SPDX SBOMs from container images, dirs, archives, package files |
| CVE matching engine | grype (library) | consumes syft SBOM; battle-tested, covers all ecosystems, actively maintained |
| CVE feeds | NVD + OSV.dev + GHSA | OSV.dev covers 20+ ecosystems including Arch and Debian |
| State | SQLite / PostgreSQL | SQLite for simplicity in self-hosted; Postgres for SaaS scale |
| Notifications | pluggable handler interface | Telegram first, Discord, email, generic webhook |

---

## Open source and business model

### Open source everything except the SaaS

The entire stack — scout, hub, CLI, dashboard — is open source (MIT or Apache 2). This is the only path to trust in a security tool. An operator who cannot read the code of the agent running on their servers will not install it.

The SaaS offering is the hosted version of the hub. No feature gating in the open source version. The commercial value is: you don't have to run it yourself.

### Tiers

```
Self-hosted (free, always)
  Unlimited hosts, unlimited CVEs
  Full auto-remediation
  Telegram + Discord + webhook
  SQLite state store

Cloud-hosted free
  Up to 3 hosts
  Community CVE feeds
  Email + webhook notification
  Hosted hub, no ops required

Cloud-hosted Pro  ($15/month)
  Unlimited hosts
  Priority feeds (CISA KEV, vendor advisories)
  Auto-remediation with approval workflow
  Audit log + CVE history
  Slack + PagerDuty integration

Cloud-hosted Team  ($60/month)
  Multi-user, roles (viewer / approver / admin)
  Maintenance windows
  Compliance export (CVE inventory for SOC2 evidence)
  SLA for notification latency

Enterprise
  On-premise hub deployment
  SSO/SAML
  Custom CVE feeds
  Dedicated support
```

### Growth strategy

The self-hosted operator community is the beachhead. They adopt early, write about it, and the r/selfhosted / HN audience amplifies. The product's own Telegram notification is a virality hook: every alert is a reminder that the tool is working, and a shareable screenshot.

The MSP market is the expansion move: once multi-tenant works, an MSP managing 20 clients is worth more than 20 individual Pro subscribers, and their clients all get the scout installed.

The compliance angle (CRA, EO 14028) is the enterprise pull: regulators are requiring SBOM + CVE tracking, and Beaconry produces both as a side effect of doing its primary job.

---

## MVP scope

The smallest version that proves the loop works end-to-end:

1. Scout collects `pacman -Q` + `docker images` output on one host
2. Hub matches against OSV.dev API
3. Hub filters CVSS ≥ 7 findings that are new since last scan
4. Sends Telegram message with CVE ID, package, severity, and fix command
5. Telegram "Fix now" button → hub dispatches to scout → scout runs `pacman -Syu <pkg>` → re-scans → confirms in Telegram

No dashboard. No CLI. No multi-host. Just the loop, proved on one host, with a real CVE.

---

## Phases

**Phase 1 — Own infrastructure (MVP)**
One host, Arch Linux packages + Docker images, Telegram notifications, manual approval, re-scan verification. Used daily on the author's own servers.

**Phase 2 — Multi-host + multi-ecosystem**
Debian support, Python/Node/Go runtimes, multiple hosts, exposure scoring, CLI.

**Phase 3 — Open source release**
Dashboard, Docker Compose one-liner setup, documentation, public GitHub release. HN / r/selfhosted launch.

**Phase 4 — SaaS**
Hosted hub, account management, billing. Pro tier. Hosted CVE feed caching (faster than direct NVD API).

**Phase 5 — Team + compliance**
Multi-user, audit log, compliance export, Slack/PagerDuty, maintenance windows.

---

## Open questions

- **CVE feed latency**: OSV.dev has a publish lag vs raw NVD. Is there a faster path for CISA KEV / critical vendor advisories? Consider direct RSS + webhook from major vendors.
- **Binary CVE coverage**: Go and Rust binaries embed version info, but only if built with `cargo auditable` or `go build` (not stripped). What is the fallback for stripped binaries?
- **Rollback**: ✅ resolved — health check waterfall + auto-rollback is a core feature. Package: downgrade. Container: `:rollback` tag snapped before every rebuild, restored on health check failure. See "Health check waterfall" and "Auto-rollback" sections.
- **False positive rate**: how noisy is OSV.dev for Arch Linux packages specifically? Need empirical data from running on real servers before tuning defaults.
- **Scout trust model**: the exec agent has significant privilege (can run package managers as root). What is the right isolation model? Separate privilege, signed remediation payloads, audit log of every command?
- **Encrypted inventory**: the SBOM sent from scout to hub contains the full software stack of a host. This is sensitive. Should it be encrypted at rest and in transit? How does this interact with the SaaS model?
- **Windows/macOS**: winget / homebrew inventory is straightforward. Is there enough demand in the self-hosted community to justify the effort?
- **Container runtime coverage**: Docker is the priority, but Podman, LXC/LXD, and systemd-nspawn are common in homelabs. What is the cost of supporting each?
