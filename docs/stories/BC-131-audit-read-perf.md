---
id: BC-131
title: Audit-read performance — tail-chunked audit.log + batched finding enrichment
status: draft
created: 2026-11-17
branch: feat/BC-131-audit-read-perf
plan: docs/plan/BC-131-audit-read-perf-plan.md
---

# BC-131: Audit-read performance — tail-chunked audit.log + batched finding enrichment

## Narrative

Operator runs `beaconry audit --last 10` and waits ~7 seconds. Same for
`beaconry verify-integrity`. Meanwhile scout → hub `POST /scan` is
67 ms, `/healthz` is 28 ms, `/fleet` is 40 ms — the production path is
healthy, only the observability surface is slow.

Root cause located during the BC-130 soak investigation:
`_read_audit_events()` (hub.py:3599) does `audit_path.read_text().splitlines()`
on EVERY call to `/audit` and `/audit/integrity`. It reads the entire
JSONL file into memory, parses every line, reverses the list, returns
it. The `limit` / `last` parameter is applied AFTER as a slice, so
`limit=10` costs the same as `limit=10000`.

Second compounding cost: `http_audit` then calls
`_load_finding_lookup()` which does another full-table scan on the
findings SQLite table (3137 rows and growing) on every request, to
enrich events that carry `finding_id`. Many events (`hub_startup`,
`scan_integrity`, `heartbeat_received`, `heartbeat_integrity_mismatch`,
`transfer_hash_mismatch`, `digest_sent`, `scout_offline`) have NO
`finding_id` at all, so the lookup is wasted work for them.

Together: every `/audit*` request is O(audit.log size) + O(findings table
size). Not visible in the POC-scale fleet yet (3 hosts) but will bite
any operator with longer runtime or larger fleets. Already biting
operator inspection at 7 s per query.

## Trigger

- `beaconry audit --last N` or `beaconry verify-integrity` from the
  operator CLI at any time.
- Automated consumers (background monitors, dashboards) polling
  `/audit?event=X&limit=1` for anomaly detection — my own BC-129
  fleet watch has been silently failing these calls for hours because
  its `--max-time 5` is below the current ~7 s floor.
- Will get worse without rotation / bounded scan: audit.log grows
  every scan + finding_filtered event; findings table grows with
  fleet × unique-CVE count.

## Expected outcome

- `/audit?last=10` returns **<100 ms** in the common case (recent
  events dominate the matches) and never exceeds ~1 s even when the
  filter forces a full-log scan.
- `/audit/integrity?limit=500` returns **<200 ms**.
- Monitor scripts with `--max-time 5` stop silently failing.
- Finding-enrichment (cve_id / host / cve_alias injection for events
  carrying a `finding_id`) is preserved — just lazy + batched instead
  of a full-table scan.
- `beaconry audit` and `beaconry verify-integrity` operator-visible
  latency drops from 7 s to <1 s on the current 3-host fleet.
- Audit log file remains the durable truth (JSONL on disk), unchanged
  in shape or write path. No migration.

## Out of scope

- Audit log rotation (daily / size-based). Related but a separate
  chore — this story is purely about read performance.
- Sidecar SQLite index for audit events. Longer-term fix that would
  make even full-range queries (`since=30d`) fast; parks behind BC-131
  because BC-131's tail-chunked read is sufficient for the operator-
  inspection latency ceiling.
- Changes to the audit event schema or filter grammar.
- In-memory ring-buffer cache on the append path. Could layer later;
  not needed to meet the <100 ms goal.
- Changes to how `http_findings` works (already fast at 74-170 ms).

## Acceptance criteria (Gherkin)

```gherkin
Feature: /audit* endpoint latency is bounded by the result set, not the log size

  Scenario: Tail read short-circuits when enough matches are found
    Given an audit.log with 10 000 scan_integrity events over 7 days
    When operator requests `/audit?event=scan_integrity&last=10`
    Then the response contains the 10 newest scan_integrity events
    And the response returns in under 200 ms
    And at most a small suffix of the file has been read (not the whole log)

  Scenario: Tail read falls back to full-file scan when filter matches rare events
    Given an audit.log with 10 000 events and 2 transfer_hash_mismatch events
    When operator requests `/audit?event=transfer_hash_mismatch&last=10`
    Then the response contains the 2 events
    And the function reads up to the start of the file (no infinite loop,
      no partial-line parse error)

  Scenario: Events spanning chunk boundaries parse correctly
    Given an audit.log whose last event's JSON line straddles the 64 KB
      boundary from EOF
    When the tail reader processes the chunk
    Then the event is parsed correctly from the carry-forward "leftover"
      buffer, no event is lost or double-counted

  Scenario: Events without finding_id incur no DB lookup
    Given an audit response containing only hub_startup and
      heartbeat_integrity_mismatch events
    When the enrichment pass runs
    Then no SQLite SELECT is executed on the findings table

  Scenario: Events with finding_id batch into one SELECT
    Given an audit response containing N events, M of which carry a
      finding_id (M <= N)
    When the enrichment pass runs
    Then exactly one SELECT `... WHERE id IN (?,?,…?)` executes with
      M distinct ids
    And each matching event picks up cve_id / host / cve_alias from
      that single query result

  Scenario: Corrupted lines don't abort the read
    Given an audit.log with a malformed JSON line between valid events
    When the tail reader encounters it
    Then the malformed line is skipped silently
    And valid events on either side are returned normally

  Scenario: Empty audit.log is handled
    Given audit.log is missing or empty
    When `/audit?last=10` is requested
    Then the response is `[]` with 200 status
```

## Capabilities delivered

- **CAP-T234: Tail-chunked audit.log read + batched finding enrichment**
  (type: technical, status: active)

  *New `_tail_audit_events(matcher, limit, chunk_size)` helper reads
  the audit.log in backward 64 KB chunks starting from EOF; parses each
  chunk's lines newest-first; carries the first (possibly truncated)
  line as "leftover" to the next (earlier) chunk so chunk-boundary
  events parse cleanly; early-exits once `matcher` has produced `limit`
  matches. `http_audit` + `http_audit_integrity` consume it directly.
  `_enrich_findings_batch(events)` replaces `_load_finding_lookup` —
  collects all `finding_id` values from the matched events into a set,
  issues one `SELECT cve_id, host, cve_alias FROM findings WHERE id
  IN (…)` for the whole set, applies results via `.setdefault(...)`.
  Events without `finding_id` skip the DB entirely. Operator CLI
  latency drops from ~7 s to <100 ms on recent-event queries.*

Extends CAP-T229 (/audit/integrity endpoint), CAP-T207 (operator CLI
surface), CAP-T223 (integrity accounting). Does not change event
semantics or filter grammar.

## Open questions

Resolved during drafting (2026-11-17):

1. Should we also add in-memory ring-buffer caching of recent events?
   **Not now.** The tail-chunked read is simple + sufficient. Caching
   adds a second code path to keep consistent with the durable log and
   isn't needed to meet the latency goal.
2. 64 KB chunk size — right choice? **Yes.** Typical audit event is
   ~250-500 bytes JSON; 64 KB holds ~130-250 events per chunk. Most
   operator queries (`--last 10`, `--last 50`) resolve in the first
   chunk.
3. When filter matches no recent events and we reach BOF, do we
   return an error or empty list? **Empty list, 200 status.** Matches
   the pre-BC-131 behavior for queries that find nothing in the full
   log.
4. Should `_tail_audit_events` be async? **No.** sqlite3 is sync; the
   file read is sync; making the function async wouldn't help because
   FastAPI runs sync endpoints on the thread pool. Keeping it sync
   preserves readability.
5. Does the existing `since: str` parameter on `/audit/integrity`
   still work? **Yes** — the matcher-closure captures `since_ts` and
   rejects events older, same as pre-BC-131. The new code reads
   backward; once we hit an event older than `since_ts`, we can break
   even without matching the `limit` (future micro-optimization,
   noted in the plan but not shipped in this branch).

## Links

- Direct predecessor: BC-126 (added `/audit/integrity`), BC-055
  (added `/audit` tail), BC-130 (whose soak investigation surfaced
  the latency).
- Adjacent chores: audit log rotation (not filed), sidecar SQLite
  index for audit events (future v1+).
- Design: `memory/design-decisions.md § Separation of concerns`
  (durability stays with JSONL on disk; perf optimizations are
  readers over the same file).
