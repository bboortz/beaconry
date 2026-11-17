# BC-131 — Audit-read performance: tail-chunked audit.log + batched finding enrichment

*Branch: `feat/BC-131-audit-read-perf`*
*Start date: 2026-11-17*
*Story: `docs/stories/BC-131-audit-read-perf.md`*

---

## Situation

Measured during the BC-130 soak:

- `/audit?last=N` returns in ~**7 s** regardless of `N`.
- `/audit/integrity?limit=N` returns in ~**7 s** regardless of `N`.
- Other endpoints (`/healthz`, `/livez`, `/readyz`, `/startupz`,
  `/fleet`, `/scouts`, `/findings`) all respond in 28-170 ms.
- Production path (scout `POST /scan`) is 67 ms — unaffected.

Root cause (two compounding O(N) operations per request):

1. `_read_audit_events()` reads the entire `audit.log` into memory,
   parses every line, reverses the list, returns it. `limit` /
   `last` is applied as a slice AFTER.
2. `_load_finding_lookup()` scans the entire `findings` table (3137
   rows today, growing) and builds an in-memory map on every call,
   just to enrich events that carry `finding_id`. Many event types
   have no `finding_id` and the lookup is wasted work.

Why it spiked today: findings table grew 6× (500 → 3137) from the
homelab deploy wave, and audit.log has been growing for weeks without
rotation. Combined, the two scans crossed the monitor-script
`--max-time 5` threshold, which is what surfaced the problem.

---

## Problems to solve

- **P1 — unbounded read latency**: `_read_audit_events()` cost is
  O(audit.log size). Grows forever without rotation.
- **P2 — wasted enrichment work**: full-table findings scan on every
  `/audit` call, for events that often don't need enrichment.
- **P3 — silent observability failure**: monitor scripts and
  dashboards polling `/audit*` with sub-10 s HTTP timeouts have been
  failing silently during this window. Downstream alerts could be
  missed.

---

## Goals

1. New `_tail_audit_events(matcher, limit, chunk_size=65536)` helper
   reads `audit.log` backward from EOF in 64 KB chunks, parses each
   chunk newest-first, early-exits when `matcher` yields `limit`
   matches. (maps to Gherkin scenarios 1, 2, 3, 6, 7)
2. New `_enrich_findings_batch(events)` helper collects all
   `finding_id` values from the matched events, issues ONE `SELECT …
   FROM findings WHERE id IN (?, …)` for them, applies `setdefault`.
   Zero DB calls when no event carries `finding_id`. (maps to
   Gherkin scenarios 4, 5)
3. `http_audit` + `http_audit_integrity` replace their body with:
   build matcher from query params → call `_tail_audit_events` →
   `_enrich_findings_batch` → return JSON. (both call sites share
   the new primitives)
4. `/audit?last=10` returns in <100 ms on the current 3-host /
   3137-finding fleet. `/audit/integrity?limit=500` returns in
   <200 ms. (maps to the "7 s → <1 s" expected outcome)
5. Behavior parity: every query that worked pre-BC-131 returns the
   same event set in the same order (newest-first).

---

## Scope

- `poc/hub/hub.py`:
  - New `_tail_audit_events(matcher, limit, chunk_size=65536) -> list[dict]`
    (near the existing `_read_audit_events`; keep the old function
    for the `_load_integrity_persistence` path and any test that
    still uses it — it's cheap to keep as a utility).
  - New `_enrich_findings_batch(events: list[dict]) -> None`
    (mutates events in place, adding cve_id/host/cve_alias from a
    single SELECT keyed by the set of finding_ids present).
  - `http_audit` rewrites to call `_tail_audit_events` with a
    matcher closure that embeds the query-param filters (event
    type set, since, until, cve, host, finding); runs
    `_enrich_findings_batch`; returns.
  - `http_audit_integrity` rewrites to call `_tail_audit_events`
    with a matcher that filters to `event="scan_integrity"` + host
    + since. No enrichment needed (scan_integrity carries no
    finding_id).
  - `_read_audit_events` kept for any caller that genuinely needs
    the whole log (currently none in production code; keep for
    backward compatibility of future use).
- `poc/tests/unit/test_BC_131_audit_read_perf.py`: new file.
- `docs/CAPABILITIES.md`: CAP-T234.
- `docs/stories/INDEX.md`: BC-131 row.
- `pyproject.toml`, `VERSION`, `poc/VERSION`, `CHANGELOG.md`:
  version bump to 0.22.15.

No scout changes. No DB schema changes. No new env vars. No wire
protocol changes.

---

## Non-goals

- Audit log rotation.
- Sidecar SQLite index for audit (a future story — would make even
  `since=30d` queries fast; the tail-chunked read is already
  sufficient for operator-facing CLI latency).
- Pagination / streaming responses.
- In-memory ring-buffer cache on the append path.
- Changes to the audit event schema or filter grammar.

---

## Design decisions

- **Tail read from EOF, 64 KB chunks** — typical event is 250-500
  bytes; one chunk holds ~130-250 events. Operator queries
  (`--last 10`, `--last 50`, `verify-integrity --since 24h`) resolve
  in the first chunk. Fallback for queries with rare matches reads
  older chunks until BOF.
- **Carry-forward "leftover" buffer** — the first "line" of any
  chunk might be truncated mid-event from a prior line. The
  leftover is prepended to the next (earlier) chunk's bytes so the
  truncated line reassembles cleanly. At BOF, the current chunk is
  guaranteed complete and no leftover carries.
- **Within-chunk order**: split `chunk + leftover` by `\n`, treat
  `lines[0]` as potentially incomplete (becomes new leftover),
  iterate `lines[1:]` in REVERSE (newest within the chunk is at the
  end). At BOF, `lines[0]` is complete too and gets iterated.
- **Matcher is a closure, not a dict of filters** — the matcher
  encapsulates since/until/event/cve/host/finding logic at the call
  site; the tail reader is pure "read backward, apply matcher,
  break at limit". Easier to test, easier to extend.
- **Batched enrichment**: `SELECT … WHERE id IN (?, ?, …)` with the
  distinct `finding_id` set from the matched events. Zero DB calls
  when no event has a finding_id. One call otherwise. No per-event
  roundtrip.
- **`_enrich_findings_batch` is in-place mutation** — same shape as
  the previous `http_audit` enrichment loop, just batched. Keeps the
  public response shape identical.
- **Keep `_read_audit_events` as a utility**: some future caller
  (one-shot analytical tool, re-index script) may genuinely want
  the whole log. It's a 10-line function; deletion isn't worth the
  grep-for-all-callers risk.

---

## Breaking changes

### 1. API / return shape

- **None.** `/audit` and `/audit/integrity` return the same JSON
  list, same event order (newest-first), same optional fields (`cve_id`
  / `host` / `cve_alias` enriched from findings when `finding_id`
  present). All query parameters preserved (`last`, `since`,
  `until`, `event`, `cve`, `host`, `finding`).
- No `/scan`, `/heartbeat`, `/healthz`, `/fleet`, `/findings`
  changes.

### 2. Data model (schema + legacy rows)

- **None.** audit.log file format unchanged (JSONL, one event per
  line, append-only). findings SQLite table unchanged. No migrations.

### 3. Behavioral semantics

- **Faster read, same response.** Operator-visible: CLI commands
  (`beaconry audit`, `beaconry verify-integrity`) that previously
  took ~7 s now take <1 s. No change in output content.
- **Pathological case**: a filter that matches nothing in a
  massive audit.log will still read to BOF. Cost: linear in
  log-size bytes, same as pre-BC-131, but with ~2× faster constants
  (chunked `read()` vs. `read_text().splitlines()` + `json.loads`
  on the full file).
- **Monitor scripts with low HTTP timeouts** (my 5 s `--max-time`)
  stop silently failing. They may start to deliver signals that
  were being dropped. Operator-visible: potentially more
  observability events in logs / notifications, because previously
  they were lost. This is a correction, not a regression.

---

## Merge plan

1. All work on `feat/BC-131-audit-read-perf`.
2. `make check` must pass on branch tip; `BEACONRY_SKIP_DOGFOOD=1
   make check-full` before opening the PR (python 3.14.11 base-image
   CVE still unresolved).
3. PR to `dev`; Claude opens, user runs `/ultrareview`, Claude runs
   `/code-review`.
4. Merge strategy: squash.
5. Post-merge: Release workflow_dispatch → tag v0.22.15 → forge
   rebuild → `make deploy-beaconry` → verify `/audit?last=10` latency
   drops below 1 s on the live hub.

---

## Rollout plan

- No migration, no backfill, no new env vars.
- Hub-only change. Scouts need no update.
- Observable improvement: first operator who runs `beaconry audit`
  after the deploy sees ~100 ms latency instead of ~7 s.

---

## Shipping evidence

Pre-ship audit (DD-TGD step 6a).

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Tail read short-circuits on common recent-event query | unit | `test_BC_131_audit_read_perf.py::test_tail_read_short_circuits_on_recent_matches` | ✓ |
| Falls back to full-file scan when filter is rare | unit | `test_tail_read_full_scan_when_rare_match` | ✓ |
| Events spanning chunk boundaries parse correctly | unit | `test_tail_read_chunk_boundary_leftover` | ✓ |
| Events without finding_id incur no DB lookup | unit | `test_enrich_findings_batch_zero_db_calls_when_no_finding_id` | ✓ |
| Events with finding_id batch into one SELECT | unit | `test_enrich_findings_batch_one_select_for_many_events` | ✓ |
| Corrupted lines don't abort the read | unit | `test_tail_read_skips_malformed_json` | ✓ |
| Empty audit.log is handled | unit | `test_tail_read_empty_log_returns_empty_list` | ✓ |
| Latency ≤ 1 s on production fleet | live | post-deploy `curl -w '%{time_total}' /audit?last=10` ≤ 1.0 s | ⏳ pending post-deploy |
| Memory drift pattern added if story deprecates documented behavior | TOML diff / N/A | N/A — additive perf change, no documented behavior deprecated | ✓ |

Legend: ✓ covered · ⚠️ partial (notes required) · ❌ open (blocks shipping).

---

## Testing strategy

Unit tests build synthetic audit.log files with known event mixes
(including a chunk-boundary stress case where a 70 KB event
straddles the 64 KB chunk edge). Tests verify:

- Correct events returned in correct order.
- Byte-count of file read is bounded (we instrument by reading into
  a BytesIO and tracking position).
- `_enrich_findings_batch` makes zero / one / N DB calls as
  specified.

Live verification: post-deploy, measure `/audit?last=10` with
`curl -w '%{time_total}'`. Target: <100 ms.

---

## Testing commands

```bash
make check
pytest poc/tests/unit/test_BC_131_audit_read_perf.py -v

# Post-deploy live verification:
for i in 1 2 3; do
  curl -s -w '/audit?last=10: %{time_total}s\n' \
    -o /dev/null http://100.77.182.73:8000/audit?last=10
done
# Expected: <0.1s per call

for i in 1 2 3; do
  curl -s -w '/audit/integrity?limit=500: %{time_total}s\n' \
    -o /dev/null 'http://100.77.182.73:8000/audit/integrity?limit=500'
done
# Expected: <0.2s per call
```
