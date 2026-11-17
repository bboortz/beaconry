# BC-132 — HTTP latency budget gate: every endpoint stays under 300 ms

*Branch: `feat/BC-132-http-latency-budget-gate`*
*Start date: 2026-11-17*
*Story: `docs/stories/BC-132-http-latency-budget-gate.md`*

---

## Situation

Three perf-regression incidents in the last 10 days surfaced not
through any gate but through downstream-caller timeouts:

1. **BC-131 (`/audit*` was ~7 s)** — surfaced because the fleet-watch
   monitor had `--max-time 5` and started dropping polls silently.
2. **Deploy-wave bloat** — surfaced because the findings table grew
   6× (`500 → 3 137` rows) and the /fleet endpoint's `LEFT JOIN
   findings` aggregate started slowing down.
3. **Rare-event tail fallback** — BC-131's tail-chunked read still
   degrades proportional to log size for rare-filter queries
   (`/audit?event=hub_startup&last=10 = 4.9 s`). Known but ungated.

Each incident fit the same pattern: **a latency regression exists in
production, nothing in CI measures it, it surfaces only when a
caller-side timeout fails.** The current gate matrix (`make check`,
`make check-full`) measures correctness but not responsiveness. This
leaves beaconry one deploy wave away from the next "operator CLI
hangs for 30 s" incident.

The fix is to make latency a first-class ship gate. A `make perf`
target runs a deterministic workload against every HTTP endpoint and
fails the gate on P95 breach (> 300 ms).

---

## Problems to solve

- **P1 — no measurement at merge time**: there is no automated check
  that `/audit?last=10` is still 28 ms after a PR lands. Any future
  BC can quietly regress it.
- **P2 — observability consumers silently fail**: callers with HTTP
  timeouts set below endpoint latency (monitor scripts, dashboards,
  SIEM, operator CLI defaults) swallow failures without emitting
  signal. A measured budget would have caught BC-131's 7 s
  regression at review time.
- **P3 — rare-event query class is ungated**: BC-131 documented the
  "tail-chunked read with rare filter falls back to full-scan" class
  but there is no artifact that tracks when it crosses from
  "accepted fallback" to "ship blocker". Needs an explicit
  exception table with fix-by dates.
- **P4 — no regression warning for slow-creep**: a PR that adds 50 ms
  to `/healthz` (from 28 ms to 78 ms, both well under 300 ms) is
  invisible today but accumulates across many BCs. Needs a
  baseline-based warning.
- **P5 — fleet state matters for measurement**: pre-BC-131 /audit at
  500 findings was 7 s; at 3 137 findings it was 7 s (same O(N)
  code). But many *other* perf bugs scale sub-linearly with fleet
  size and only appear at scale. Need a representative fixture.

---

## Goals

1. New `make perf` target measures P50/P95/P99 of every HTTP
   endpoint on a deterministic representative fleet state. Exits 0
   if every endpoint meets the budget (P95 ≤ 300 ms, P99 ≤ 1 000 ms),
   1 if any endpoint breaches. (addresses P1, maps to Gherkin
   scenario 1)
2. Deterministic fixture seeder (`make perf-seed`) produces the DB +
   audit.log state used by the gate: 3 scout rows, 1 000 open
   findings across 2 hosts, 10 000 audit events with a representative
   event-type mix. Same seed → same bytes across machines. (addresses
   P5, maps to scenario 2)
3. `poc/scripts/perf-exceptions.toml` lists endpoint patterns that
   are allowed to exceed the budget, each with a human-readable
   `reason` + `fix_by` date. Expired exceptions fail the gate.
   (addresses P3, maps to scenarios 3 + 4)
4. New endpoints are discovered automatically via FastAPI route
   introspection (`api.routes`) — no manual list to keep in sync.
   New endpoint without an exception entry that breaches the budget
   fails its own introducing PR. (maps to scenario 5)
5. `perf-baseline.json` at the repo root tracks main-branch P50
   per endpoint; a `make perf` run that measures ≥ 2× the baseline
   prints a non-blocking warning. CI updates the baseline on
   successful merges to `main`. (addresses P4, maps to scenario 6)
6. `make check-full` calls `make perf` so merge-to-dev is
   blocked on perf regression. (makes the gate real)

---

## Scope

- `poc/scripts/perf_gate.py`:
  - New module. Entrypoint `python -m poc.scripts.perf_gate`.
  - Spins up a hub in-process via `fastapi.testclient.TestClient`
    (pattern mirrors existing integration tests).
  - Seeds the representative fleet state (delegates to
    `perf_seeder.py`).
  - Discovers every `@api.get` / `@api.post` route via
    `api.routes` and auto-generates query params from a
    `perf-endpoint-fixtures.toml` map (endpoint → sample query
    params / sample body).
  - Measures 10 consecutive calls per endpoint with `time.perf_
    counter()`. Separates cold (first call) from warm (next 9).
    Computes P50/P95/P99 over the warm set.
  - Parses `perf-exceptions.toml`; marks matching endpoints
    exempt; evaluates fix-by date against today.
  - Prints a table: endpoint · cold · P50 · P95 · P99 · verdict.
  - Optional `--baseline=perf-baseline.json` for regression warning.
  - Optional `--update-baseline` for CI's main-branch bump.
- `poc/scripts/perf_seeder.py`:
  - Deterministic DB + audit.log fixture generator. Takes a seed
    (default 42). Writes to `$DATA_DIR` or a temp path.
  - 3 scout rows (hetzner-staging, server2, bb-82jq clone).
  - 1 000 findings distributed across 2 hosts, 5 source_refs, 3
    severities, 2 statuses.
  - 10 000 audit events: event-type mix mirrors production
    observed on 2026-11-17 (dominant: finding_filtered 85 %,
    heartbeat_received 10 %, scan_integrity 3 %, scan_received
    1 %, rare: digest_sent, scout_offline, hub_startup).
- `poc/scripts/perf-exceptions.toml`: start with three entries
  per the story's "known-exempt endpoints" section.
- `poc/scripts/perf-endpoint-fixtures.toml`: default query params
  per route (e.g. `/findings` → `host=hetzner-staging&limit=1000`).
- `perf-baseline.json`: committed to repo root; CI updates on
  main merges.
- `Makefile`: new `perf` + `perf-seed` targets; `check-full`
  depends on `perf`.
- `memory/code-quality.md`: new section `§ Known performance
  exceptions` describing the policy.
- `memory/workflow.md § Quality bar / Gate matrix`: adds the
  `make perf` row.
- `docs/CAPABILITIES.md`: CAP-T235.
- `docs/stories/INDEX.md`: BC-132 row.
- `poc/tests/unit/test_BC_132_perf_gate.py`:
  - Unit tests for the stat helpers (P50/P95/P99 given a sample
    list), the exception parser, the baseline comparator.
  - Does NOT run the full gate in unit tests (too slow + duplicate
    of the gate itself); the gate runs in `make check-full`.
- `pyproject.toml`, `VERSION`, `poc/VERSION`, `CHANGELOG.md`:
  version bump to 0.22.16.

No scout changes. No DB schema changes. No audit event shape
changes. No new env vars in production (only in `make perf` for
baseline comparison).

---

## Non-goals

- Memory / CPU profiling. If the gate forces one, that's a separate
  chore.
- Non-HTTP perf (grype subprocess, syft, Telegram, SBOM transfer).
- WAN latency. Measurement is hub-local via TestClient.
- Changing endpoint behavior to hit the budget. BC-132 is the
  measurement + gate; actual fixes land in follow-up BCs that cite
  BC-132.
- Perf at absurd scale (100 000 findings, 1M audit events). The
  fixture is "today's fleet rounded up" — further scaling is a
  separate concern.
- Real-fleet live perf dashboard. Could be added later as a
  separate CAP; BC-132 is CI gate only.

---

## Design decisions

- **300 ms P95 ceiling, 1 000 ms P99 ceiling, 100 ms P50 target**.
  Rationale: 300 ms is the sync-UI noticeability threshold; P50 at
  100 ms is the day-to-day target. P99 at 1 s absorbs cold-cache
  and rare-event tail-fallback. Tunable in a constants file.
- **10 consecutive calls per endpoint**. First is "cold" (reported
  separately); P50/P95/P99 computed over the remaining 9. Enough
  samples to catch variance without ballooning gate runtime.
- **In-process via TestClient, not a real compose stack**. Pros:
  gate runs in ~30 s total (vs ~2 min for compose), no port
  conflicts, deterministic. Cons: misses network stack. Accepted
  because we're measuring hub-local code paths, not the network.
  For operator-run real-fleet measurements, add `BEACONRY_PERF_
  HUB_URL` later (post-BC-132).
- **Deterministic fixture via known RNG seed**. `random.Random(42)`
  for all fixture generation. Same seed → same byte-equivalent DB
  + audit.log. Enables comparing perf numbers across machines.
- **Route auto-discovery** instead of a manual list. `api.routes`
  iteration catches every new endpoint by default. Prevents the
  "I forgot to add my new route to the perf gate" footgun.
- **`perf-exceptions.toml` with mandatory `fix_by` or
  `permanent=True`**. Can't exempt an endpoint without declaring
  why and when. Expired `fix_by` fails the gate.
- **Baseline warning, not failure**. A 2× regression on an
  already-fast endpoint (28 ms → 56 ms) is still fast but
  matters. Warn, don't block — forces reviewer discussion.
- **Representative event-type mix** in the audit fixture. Pre-
  BC-131 bugs hid because rare-event paths weren't exercised.
  The fixture explicitly includes the rare types (digest_sent,
  scout_offline, hub_startup) so their query paths are measured.
- **No in-memory cache layer introduced**. BC-131 did the read
  path right; BC-132 keeps the measurement honest without adding
  implementation-specific optimizations. Any BC that adds caching
  must pass the gate on its own merits.
- **perf-seeder runs init_db() first**. Ensures the schema is at
  the current version (currently 12 post-BC-123). Future schema
  migrations re-run the seeder transparently.

---

## Breaking changes

### 1. API / return shape

- **None.** No HTTP surface change. No endpoint response shape
  change. Pure measurement + reporting in a new `make perf` target.

### 2. Data model (schema + legacy rows)

- **None in production.** The perf fixture writes to its own
  `$DATA_DIR` under the gate's temp directory; the fixture DB
  never touches the operator's real DB. `init_db()` is called on
  the fixture path; it's idempotent (`CREATE TABLE IF NOT EXISTS`
  everywhere).

### 3. Behavioral semantics

- **`make check-full` runtime grows by ~30 s** (the perf gate run).
  Operators running the pre-push hook feel this. Accepted — we
  trade 30 s of pre-push time for catching a latency regression
  that would otherwise ship. CI runtime also grows proportionally.
- **Any existing endpoint that already exceeds the budget fails
  the gate on BC-132's first green landing**. The known-exempt
  list covers the three post-BC-131 known cases
  (`/audit?event=hub_startup&last=10`, `/audit?event=scan_
  integrity&last=500`, `/audit/integrity?limit=500`). If any
  additional endpoint surfaces during testing, it joins the
  exception list before merge.
- **Baseline tracking in `perf-baseline.json`** — a new committed
  file. Operators (if anyone forks) adopt the file; reviewers see
  it in PRs. Low-signal churn that could be noisy in PR diffs;
  mitigate by excluding the file from "lines changed" comments in
  review tooling.

---

## Merge plan

1. All work on `feat/BC-132-http-latency-budget-gate`.
2. `make check` must pass on branch tip; `make perf` must pass
   (new ground-truth); `BEACONRY_SKIP_DOGFOOD=1 make check-full`
   before opening the PR.
3. The first `make perf` run establishes `perf-baseline.json` →
   commit it to the branch.
4. PR to `dev`; Claude opens, user runs `/ultrareview`, Claude
   runs `/code-review`.
5. Merge strategy: squash.
6. Post-merge: Release workflow_dispatch → tag v0.22.16 → forge
   rebuild → `make deploy-beaconry` → verify perf-baseline.json
   is tracked on main.
7. CI's "update baseline on main" step added so main is the
   source-of-truth baseline going forward.

---

## Rollout plan

- No migration. No new env vars in production. No scout change.
  Pure ship-gate addition.
- Pre-push hook users feel +30 s on `make check-full` the first
  push after BC-132 lands. Document in the PR body.
- Perf-baseline.json seed values captured from the live hub
  post-BC-131 (today's measurements). Documented in the PR body
  so reviewers can cross-check.

---

## Shipping evidence

Pre-ship audit (DD-TGD step 6a).

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| `make perf` discovers endpoints via the fixtures TOML | unit | `test_BC_132_perf_gate.py::test_load_fixtures_discovers_declared_endpoints` | ✓ |
| Fixture seeder is deterministic | unit | `test_BC_132_perf_gate.py::test_perf_seeder_deterministic_seed_42` | ✓ |
| P95 computation is correct | unit | `test_BC_132_perf_gate.py::test_stats_p95_from_known_samples` | ✓ |
| Exception with fix_by in the past fails gate | unit | `test_BC_132_perf_gate.py::test_exception_past_fix_by_fails_gate` | ✓ |
| Exception with permanent=true never fails | unit | `test_BC_132_perf_gate.py::test_exception_permanent_never_fails` | ✓ |
| Breaching endpoint without exception fails the verdict | unit | `test_BC_132_perf_gate.py::test_verdict_separates_exempt_from_breach` | ✓ |
| Baseline 2× regression prints warning, does not fail | unit | `test_BC_132_perf_gate.py::test_baseline_regression_warns_not_blocks` | ✓ |
| Sidecar audit_index populated on _audit.append | unit | `test_BC_132_perf_gate.py::test_audit_index_hook_populates_on_append` | ✓ |
| audit_index rebuild bootstraps from pre-existing audit.log | unit | `test_BC_132_perf_gate.py::test_rebuild_audit_index_from_existing_log` | ✓ |
| `make check-full` calls `make perf` | smoke | post-merge `make check-full` output includes a "✓ perf" line | ❌ pending post-merge |
| memory/code-quality.md has new exceptions section | manual | grep for `§ Known performance exceptions` in memory/code-quality.md | ✓ |
| Memory drift pattern added if story deprecates documented behavior | TOML diff / N/A | N/A — additive gate, no documented behavior deprecated | ✓ |

Legend: ✓ covered · ⚠️ partial (notes required) · ❌ open (blocks shipping).

---

## Testing strategy

Unit tests for every pure helper (stats functions, exception
parser, route-discovery adapter, baseline comparator). The gate
itself is tested by running it against a known-small fixture (10
findings, 100 audit events) and asserting the output table + exit
code match a golden snapshot. The full-scale fixture (1 000 /
10 000) runs in `make check-full` only (too slow for unit tests).

Live verification happens post-deploy — on the next PR's `make
check-full` run, the perf gate executes against the merged main
state. Operator-side: run `make perf` locally after a `git pull`
and confirm the table matches the baseline.

---

## Testing commands

```bash
make check
pytest poc/tests/unit/test_BC_132_perf_gate.py -v

# Full-scale gate run (slow — ~30s):
make perf

# Local baseline comparison after the gate's first ship:
make perf  # updates perf-baseline.json via gate
git diff perf-baseline.json  # inspect what changed

# Operator-side real-fleet measurement (optional, not CI-wired):
BEACONRY_PERF_HUB_URL=http://100.77.182.73:8000 make perf
```

Expected output at the bottom of a successful `make perf`:

```
✓ 14 endpoints measured, all under 300 ms P95
  Fastest: /livez (12 ms P50)
  Slowest: /audit/integrity?limit=50 (210 ms P50)
  3 exempt (within fix-by window): /audit?event=hub_startup...
  0 regressions vs baseline
```
