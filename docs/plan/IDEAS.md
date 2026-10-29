# Ideas backlog

Running list of ideas and discussions that may become future BC-NNN stories.

**Role in the development workflow:** Claude consults this file at the start of any planning conversation and before proposing new stories — the user has already thought about many of these; don't re-discover them. New ideas (from the user, from virtual-user panels, from debugging sessions) get appended here immediately with their trigger context so the thread isn't lost.

**Format per idea:**
- Short bullet, one-line description
- `trigger:` where/when it came up (session date + context)
- `priority:` `now` (next 1–2 stories) / `soon` (next 3–6) / `later` / `parked` (likely won't do)
- `links:` story IDs this would depend on or supersede

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

## Parking lot (ideas we discussed but likely won't do)

- *(empty — add here if we decide something is explicitly not worth doing)*
