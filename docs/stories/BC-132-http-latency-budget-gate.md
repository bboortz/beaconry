---
id: BC-132
title: HTTP latency budget gate — every endpoint stays under 300 ms
status: draft
created: 2026-11-17
branch: feat/BC-132-http-latency-budget-gate
plan: docs/plan/BC-132-http-latency-budget-gate-plan.md
---

# BC-132: HTTP latency budget gate — every endpoint stays under 300 ms

## Narrative

Three times in the last 10 days, a production-impact perf regression
only surfaced because someone happened to hit the endpoint from a
timeout-bounded caller:

- **BC-131**: `/audit` + `/audit/integrity` were taking ~7 s per
  request. Monitor scripts with `--max-time 5` had been **silently
  failing** their audit polls for hours before anyone noticed. The
  regression had been there as long as the audit log existed —
  nothing in CI, nothing in `make check`, nothing in `make
  check-full` measured it.
- **Deploy-wave bloat**: when `make deploy-docker-all` grew the
  findings table 6× (500 → 3137 rows), `/audit` latency crossed the
  5 s threshold. Latency was already 7 s on the smaller table; the
  deploy wave just made it more visible.
- **Observed but unverified until now**: `/audit/integrity?limit=500`
  is still ~5 s post-BC-131 because rare-event queries blow through
  the tail-chunked read. BC-131 documented this as "future SQLite
  index work" but there's no gate flagging it when it starts
  mattering.

Same pattern each time: a performance regression exists, nothing in
the pipeline tests for it, it only surfaces when caller-side timeouts
start failing. The fix is to **make latency a first-class ship gate**,
not a postmortem artifact.

## Trigger

- Every PR to `dev` runs the perf gate as part of `make check-full`.
- Any BC that adds a new HTTP endpoint declares + measures its
  latency budget before merge.
- A latency regression (e.g., a new feature adds a full-table scan)
  fails the gate at review time, not months later when an operator
  hits a 30 s CLI timeout.

## Expected outcome

- A `make perf` target exists. It spins up a hub (or hits a live
  one if `BEACONRY_PERF_HUB_URL` is set), seeds a representative
  fleet state (1 000 findings, 10 000 audit events, 3 scouts), hits
  each HTTP endpoint 10× consecutively, and asserts:
  - **P50 ≤ 100 ms** for every endpoint
  - **P95 ≤ 300 ms** for every endpoint (the hard ceiling)
  - **P99 ≤ 1 000 ms** for every endpoint (allows cold-cache and
    rare-event-fallback cases)
- Output is a per-endpoint table with cold/warm P50/P95 and pass/
  fail markers, plus a one-line summary.
- Non-compliant endpoints documented in `memory/code-quality.md`'s
  "known performance exceptions" section with a BC-link, a reason,
  and either a fix-by date or an accepted-permanent note.
- `make check-full` calls `make perf` and fails the gate on P95
  breach.
- The operator-visible perf of `beaconry audit`, `beaconry findings`,
  `beaconry verify-integrity`, `beaconry status` stays snappy through
  every future BC.

## Out of scope

- WAN latency / hosting choice / operator hardware. The gate
  measures hub-local latency only (from test runner to hub over
  localhost).
- Non-HTTP perf: grype subprocess, syft, SBOM transfer gzip time,
  Telegram roundtrip. Those have their own BCs or are intentionally
  slow.
- Scout → Hub POST /scan latency under real SBOM payload sizes.
  Separate story if scouts start timing out (hasn't happened; /scan
  measures 67 ms with a trivial body today).
- Memory / CPU profiling. BC-132 is about end-to-end response
  latency; resource-level profiling is a separate chore if the P95
  gate forces one.
- Changing endpoint behavior to hit the budget — BC-132 just
  *measures* and *fails the gate*. The actual perf work is in the
  follow-up BCs (BC-13X for a sidecar audit index, BC-13X for
  audit-log rotation, …).

## Acceptance criteria (Gherkin)

```gherkin
Feature: Latency budget as a first-class ship gate

  Scenario: Perf gate measures every HTTP endpoint
    Given a hub running at $BEACONRY_PERF_HUB_URL (or spun up by the
      gate itself) with a representative fleet state
    When `make perf` runs
    Then every HTTP endpoint registered on `api` is hit 10 consecutive
      times with representative query params
    And a table is printed with cold/warm/P50/P95/P99 for each
    And the gate exits 0 if every endpoint's P95 ≤ 300 ms AND every
      endpoint's P99 ≤ 1 000 ms, else exits 1

  Scenario: Representative fleet state seeded deterministically
    Given the perf gate starts
    When it invokes its fixture seeder
    Then the DB contains 3 scout rows, 1 000 open findings, and
      10 000 audit events of representative event-type mix (one
      scan_integrity per 20 finding_filtered, one heartbeat_received
      per 60 s cadence, etc.)
    And the seeder is deterministic — the same fixture seed produces
      the same byte-equivalent state across machines

  Scenario: Known-permanent exception is tolerated
    Given an endpoint declared in `perf-exceptions.toml` with a
      reason + accepted-permanent flag
    When the gate runs
    Then that endpoint's latency is still measured and reported
    But it does NOT fail the gate if above budget
    And the report prints a `⚠ permanently-exempt` marker beside it

  Scenario: Known-temporary exception blocks merge after its fix-by date
    Given an endpoint declared in `perf-exceptions.toml` with
      `fix_by="2026-12-15"` and today is 2026-12-16
    When the gate runs
    Then the exception is treated as expired
    And the endpoint's over-budget latency fails the gate

  Scenario: New endpoint added without a perf declaration
    Given a PR introduces a new `@api.get("/new-thing")` route
    When `make perf` runs
    Then the new endpoint is discovered automatically (via FastAPI
      app introspection) and measured
    And if it's above budget without an exception entry, the gate
      fails the PR

  Scenario: Regression detection across main
    Given the gate's last-measured latency for every endpoint is
      stored in `perf-baseline.json` on main
    When a PR's measurement for any endpoint exceeds `baseline * 2`
      (even if still under 300 ms)
    Then the gate prints a `⚠ regression` warning (does not fail)
    And the PR reviewer can investigate before merging
```

## Capabilities delivered

- **CAP-T235: HTTP latency budget as a first-class ship gate**
  (type: technical, status: active)

  *New `make perf` target measures P50/P95/P99 of every HTTP endpoint
  on a representative fleet state. Fails `make check-full` if any
  endpoint's P95 exceeds 300 ms or P99 exceeds 1 000 ms, unless
  listed in `poc/scripts/perf-exceptions.toml` with a documented
  reason + fix-by date. New endpoints are auto-discovered via
  FastAPI route introspection (no manual registration), so adding
  a route automatically opts it into the gate. A `perf-baseline.json`
  tracks main-branch latencies for regression warnings at 2× the
  baseline (non-blocking warning, blocks nothing — just surfaces
  "this used to be faster"). Measurement is end-to-end: test runner
  → hub HTTP → response. Hub-local (localhost) to isolate from
  network. Representative fleet state is deterministic: 3 scout
  rows, 1 000 open findings, 10 000 audit events with the event-type
  mix of real production.*

Extends CAP-T229 (/audit/integrity routing), CAP-T234 (BC-131 tail
reader — the perf gate would have caught the ~7 s regression at
review time), CAP-T207 (operator CLI surface — the CLI's responsive
feel depends on this gate holding). Does not change endpoint
behavior; purely a measurement + gate.

## Learnings from BC-131 (why this gate is needed)

Documented verbatim from the BC-131 soak + investigation:

1. **`_read_audit_events()` was O(N) over the full JSONL log on
   every call**, and `_load_finding_lookup()` was O(M) over the full
   findings table on every call, and both compounded. Pre-BC-131 the
   /audit endpoint was ~7 s at the current ~3 100 finding scale.
   **Nothing in `make check` or `make check-full` measured endpoint
   latency**, so the regression had been there as long as the audit
   log existed.
2. **Rare-event queries still fall back to full-log scan** even
   post-BC-131 (tail-chunked read with rare matches = reads every
   chunk). `/audit?event=hub_startup&last=10` = 4.9 s because
   hub_startup is one-per-deploy. The tail-chunked approach *does
   not solve* this case; only a sidecar index would. BC-132 needs
   to accept this class as a documented exception until the sidecar
   index ships.
3. **Cold vs warm matters**. First `/audit?last=10` post-deploy was
   2.9 s; subsequent were 28 ms. OS page cache for the audit.log
   file. The perf gate must measure both (10 consecutive samples,
   report cold + P50/P95/P99).
4. **CLI timeout < endpoint latency = silent failure**. My monitor
   script had `--max-time 5`; endpoint took ~7 s. Monitor kept
   emitting SUMMARY lines without errors because its curl calls
   were silently failing then skipping to the next poll. The gate
   must have a HARD timeout that fails loudly (not a soft timeout
   that swallows).
5. **Latency is coupled to table size, which is coupled to fleet
   size**. Findings table grew 6× (500 → 3 137) when the user ran
   `make deploy-docker-all`. The gate's representative fleet state
   must mirror realistic scale (1 000+ findings, 10 000+ audit
   events) or it'll pass today and fail at real deployment.
6. **Audit log grows unboundedly without rotation.** BC-131's
   tail-chunked read is still proportional to the number of chunks
   it needs to scan. Without log rotation, the pathological class
   (rare event + high limit) gets worse monotonically. **BC-132
   motivates BC-13X audit log rotation** as a follow-up.
7. **A perf regression can hide a semantic bug.** The pre-BC-131
   `/audit/integrity` `since` filter was broken (calling
   `datetime.fromisoformat` on an int ts → always raised → always
   included events defensively). Nobody noticed because nobody ran
   a `since`-filtered query fast enough to care. The perf gate's
   side effect of forcing a seeded `since` query in CI would have
   also caught the behavior bug.
8. **A single silent timeout in automation masks many signals.** My
   fleet-watch monitor was dropping every `/audit?event=X&limit=1`
   anomaly poll silently — including some I would have wanted to
   see (BC129_FIRE events, additional heartbeat_integrity_mismatch
   events beyond what /healthz's mm counter surfaces). The gate's
   job isn't just correctness; it's protecting all downstream
   consumers from the same class of failure.

## Open questions

Resolved during drafting (2026-11-17):

1. **Why 300 ms for P95, not 100 ms or 1 s?** 300 ms is the
   human-noticeable threshold for sync UI feel, and it's an order
   of magnitude above the warm-cache floor (~30 ms) so headroom is
   plentiful. P50 at 100 ms is the tight day-to-day target; P95 at
   300 ms is the ceiling that fails the gate; P99 at 1 000 ms
   accounts for cold-cache + rare-event fallback. Can tune later
   based on observed distributions.
2. **How to run the gate without a live hub?** Options: (a) start a
   hub in-process in a pytest fixture, (b) require `BEACONRY_PERF_
   HUB_URL` env var (CI starts a dedicated one), (c) spin up a
   compose stack. **Pick (a)** — in-process keeps the gate fast
   (<30 s total), the FastAPI TestClient is already used in
   integration tests so the plumbing exists. (c) is the fallback
   for live-fleet measurements an operator can run.
3. **What's "representative" fleet state?** Snapshot today's live
   fleet state rounded to clean numbers: 3 scouts, 1 000 findings
   (vs today's 3 137), 10 000 audit events. Seed deterministically
   from a known RNG seed so the same bytes land on every machine.
   Add a `make perf-seed` sub-target that produces the fixture DB
   + audit.log for operator reproducibility.
4. **Should the gate measure non-GET endpoints (POST /scan,
   POST /heartbeat)?** Yes, with representative bodies. BC-132's
   existing /scan body-timing (67 ms today) is well under budget;
   measuring it in the gate ensures a future change to receive_scan
   (e.g., adding a slow SQL insert) is caught.
5. **How to handle the "rare event + high limit" exception class?**
   Three options: (a) raise the P99 ceiling for those specific
   query patterns, (b) ship the sidecar SQLite audit index as
   BC-13X before BC-132 lands, (c) exclude those queries from the
   gate via `perf-exceptions.toml`. **Pick (c)** — document the
   exception with a fix-by date pointing at the sidecar index BC.
   Transparent, not hidden.
6. **Does the gate catch regressions in endpoints that are already
   fast?** Yes — the `perf-baseline.json` tracks main-branch
   latency per endpoint. Any PR whose measurement is 2× the
   baseline for an endpoint prints a non-blocking warning. Prevents
   a slow-creep regression (e.g., adding a SELECT to `/healthz`
   bumping 28 ms → 56 ms isn't a gate-fail but is worth a review
   comment).

## Known-exempt endpoints at ship time of BC-132

These will be in `poc/scripts/perf-exceptions.toml` on the first
ship — the gate measures them, prints `⚠ exempt` markers, but does
not fail:

| Endpoint + query | Current latency | Reason | Fix-by |
|---|---|---|---|
| `/audit?event=hub_startup&last=10` | ~5 s | Rare-event tail fallback (BC-131 lesson #2) | BC-13X sidecar audit index (future) |
| `/audit?event=scan_integrity&last=500` | ~5 s | Same class | Same BC-13X |
| `/audit/integrity?limit=500` | ~5 s | Same class (default limit too high too — tune down to 50?) | Same BC-13X + lower default limit |

All other endpoints are expected to pass on day one (BC-131 just
shipped). The gate captures the current state as the floor and
keeps it from drifting back.

## Links

- Direct predecessor: BC-131 (surfaced the need for a measurement
  gate), BC-130 (whose soak revealed BC-131), BC-126 (audit
  endpoints added), BC-055 (/audit endpoint origin).
- Adjacent chores (will cite BC-132 when they ship):
  - **BC-13X audit log rotation** — prevents BC-131's tail-chunked
    read from degrading monotonically as the log grows.
  - **BC-13X sidecar SQLite audit index** — fixes the rare-event +
    high-limit exception class, lets us retire the `perf-
    exceptions.toml` entries.
- Design: `memory/code-quality.md § Known performance exceptions`
  (new section added by BC-132; documents the tolerance policy),
  `memory/workflow.md § Quality bar` (updates the gate matrix with
  a row for `make perf`).
