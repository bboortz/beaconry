# BC-042 — `make audit` CLI

*Branch: `feat/BC-042-make-audit-cli`*
*Start date: 2026-11-01*
*Story: `docs/stories/BC-042-make-audit-cli.md`*

---

## Situation

Hub writes 7 audit event types to `/data/audit.log` (BC-029). No operator-grade
view exists. Session debugging repeatedly needed audit data via
`docker exec … cat /data/audit.log` + eye-grep. One structural gap: scan
reception is NOT audited today, so "did scout even POST this cycle?" can't
be answered without hub docker logs.

---

## Problems to solve

- No operator CLI for the audit log.
- `finding_id` → `cve_id` + `host` requires manual DB cross-reference.
- No `scan_received` event — scout silence is indistinguishable from "scout
  posted 0 findings" from the audit perspective.
- Time filtering (`last hour`, `since 09:00`) requires shell + awk gymnastics.

---

## Goals

1. `poc/scripts/audit.py` renders `audit.log` as an aligned table, newest-first.
2. Filters: `LAST=N`, `SINCE=1h|30m|1d`, `EVENT=a,b`, `CVE=X`, `HOST=X`,
   `FINDING=X`, `COUNT=1`, `FOLLOW=1`.
3. CVE filter resolves `finding_id` → `cve_id` via the DB so operators can
   query by CVE even for `status_change` events that store only `finding_id`.
4. New hub audit event: `scan_received` with `host`, `sbom_count`, `candidate_count`,
   `exposed_count`. Closes the "did scout even post?" blind spot.
5. Compose-aware routing: `docker exec beaconry-hub` when the live stack is
   up (same pattern as `findings.py` + `status.py`).
6. `make audit` Makefile target.

---

## Scope

- NEW `poc/scripts/audit.py` — CLI, parser, filters, pretty-table render.
- `poc/hub/hub.py` — add `_audit.append("scan_received", ...)` at the top
  of `receive_scan`.
- `Makefile` — new `audit` target + help entry.
- `poc/tests/unit/test_audit_cli.py` — parsers, filter composition, time
  parsing, CVE cross-reference resolution.
- `poc/tests/integration/test_BC_042_make_audit_cli.py` — end-to-end: craft
  an audit.log + DB, run the CLI, assert rendered output.
- `docs/CAPABILITIES.md` — register CAP-T089, CAP-T090, CAP-B026.
- `docs/VARIABLES.md` + `.env.example` + lint — no new vars (CLI reads via
  `BEACONRY_HUB_CONTAINER` which already exists).

---

## Non-goals

- Audit log rotation, truncation, compression. Operator ops concern.
- Streaming audit to Prometheus / Loki. Later story.
- Writing to the audit log from the CLI. Append-only semantic preserved.
- Multi-hub / remote fetch. Local docker exec only.
- Rich colour output. Keep it grep + less friendly.

---

## Design decisions

### Filter semantics

- All filters AND together.
- `EVENT=a,b` is OR within the filter (multiple event types allowed).
- `SINCE` accepts `Nd`, `Nh`, `Nm`, `Ns` (`SINCE=90m`). Also an ISO-like
  `YYYY-MM-DDTHH:MM` for exact windows. Rejects garbage with a clear error.
- `CVE=X` matches two paths:
  1. Events that carry `cve_id` directly (`finding_ingested`, `regressed`, …).
  2. Events that carry `finding_id` only (`status_change`) — resolved via
     the DB. If DB unreachable, the lookup silently returns no match.

### `scan_received` event

Added at the top of `receive_scan` before any `process_sbom_entry` call.
Shape:
```python
_audit.append(
    "scan_received",
    host=host,
    sbom_count=len(sboms),
    candidate_count=0,       # pre-ingestion unknown; left as 0 and updated below? No — split.
    exposed_count=sum(1 for v in port_exposure.values() if v),
)
```
Decision: candidate count is **not** on the receive-side event because we
don't know it until after grype matches. Keep this event scan-reception-only;
rely on existing `finding_ingested` for post-match stats. Simpler.

### `COUNT=1` summary mode

Group events by type, emit:
```
EVENT                      COUNT  FIRST SEEN       LAST SEEN
---------------------      -----  ---------------  ---------------
finding_ingested            13   16:21:00 20m ago  16:48:10 10m ago
digest_sent                  2    16:21:49 20m ago  16:48:12 10m ago
```

### `FOLLOW=1`

Mirror `tail -f` on the file via `docker exec beaconry-hub tail -f /data/audit.log`
piped through the Python renderer. Rejects other filters because the stream
isn't buffered for cross-event cross-reference. Keep V1 simple.

### Pretty-table columns

Fixed-width:
```
TIME        AGE          EVENT                       CVE                   HOST       EXTRA
----------  -----------  --------------------------  --------------------  ---------  ----------------------------
16:48:10    10m ago      finding_ingested            CVE-2026-14669        bb-82jq    tier=1 cvss=8.8 kev=0 if=1
16:49:00     9m ago      status_change               CVE-2026-14669        bb-82jq    status=snoozed
```

- `TIME` HH:MM:SS (not date; date in help banner).
- `AGE` relative, operator-friendly ("3h ago", "2d ago").
- `EVENT` wide enough for the longest event name (`heartbeat_reminder_sent` = 24 chars).
- `CVE` 20 chars (same as findings CLI).
- `HOST` 9 chars.
- `EXTRA` rest-of-line k=v pairs; truncated to 50 chars with "…".

### Compose-aware routing

Reuse `findings.py`'s `_running_compose_hub` pattern. If beaconry-hub is up,
docker-exec a one-liner that cat's audit.log through our Python parser.
Otherwise read `${DATA_DIR}/audit.log` locally.

---

## Breaking changes

### 1. API / return shape

**None.** New CLI, new Makefile target. The `scan_received` event is additive
(no existing consumer).

### 2. Data model

**None.** audit.log is append-only JSONL; new event type is just one more line.

### 3. Behavioral semantics

- Operators get a new tool. No existing surface changes.
- Hub emits one additional audit event per `/scan` POST. ~100 bytes per event
  × 288 scans/day (default SCAN_INTERVAL=300) = ~29 KB/day. Negligible disk.

---

## Merge plan

1. All work on `feat/BC-042-make-audit-cli`.
2. `make check` + `make check-full` pass.
3. PR to `dev`; Claude opens, user runs `/ultrareview`, Claude runs `/code-review`.
4. Merge strategy: fast-forward.
5. Post-merge: story → `shipped`, INDEX update, `make live-rebuild`, verify
   `make audit SINCE=1m` returns the new `scan_received` event.

---

## Rollout plan

Additive. Operators get a new command (`make audit`). Hub emits one new
event per scan on upgrade — immediately queryable via the new CLI.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Default run returns last 50 events as timeline | integration | `test_BC_042_make_audit_cli.py::test_default_run_returns_last_50` | ✓ |
| `SINCE=1h` filter | integration | `test_BC_042_make_audit_cli.py::test_since_filter_restricts_to_window` | ✓ |
| Multi-event filter via comma | integration | `test_BC_042_make_audit_cli.py::test_multi_event_filter_or_semantics` | ✓ |
| CVE filter resolves finding_id → cve_id | integration | `test_BC_042_make_audit_cli.py::test_cve_filter_resolves_via_db` | ✓ |
| COUNT mode summarises by event type | integration | `test_BC_042_make_audit_cli.py::test_count_mode_groups_by_event` | ✓ |
| `scan_received` emitted per `/scan` POST | integration | `test_BC_042_make_audit_cli.py::test_scan_received_emitted` | ✓ |
| Scan with zero candidates still produces `scan_received` | integration | `test_BC_042_make_audit_cli.py::test_scan_received_fires_even_with_zero_candidates` | ✓ |
| `scan_received` carries exposed_count from BC-030 map | integration | `test_BC_042_make_audit_cli.py::test_scan_received_carries_exposed_count` | ✓ |
| HOST filter (unit) | unit | `test_audit_cli.py::test_filter_by_host` | ✓ |
| HOST filter (integration via CLI) | integration | `test_BC_042_make_audit_cli.py::test_host_filter` | ✓ |
| FINDING filter (unit) | unit | `test_audit_cli.py::test_filter_by_finding_id` | ✓ |
| FINDING filter (integration via CLI) | integration | `test_BC_042_make_audit_cli.py::test_finding_filter` | ✓ |
| Empty audit log → "No events" message | integration | `test_BC_042_make_audit_cli.py::test_empty_audit_log` | ✓ |
| Bad SINCE value → exit 2 + clear error | integration | `test_BC_042_make_audit_cli.py::test_bad_since_fails_with_clear_error` | ✓ |
| Time parser accepts human spans | unit | `test_audit_cli.py::test_parse_duration_accepts_common_shapes` | ✓ |
| Time parser rejects garbage | unit | `test_audit_cli.py::test_parse_duration_rejects_garbage` | ✓ |
| Pretty-table alignment stable | unit | `test_audit_cli.py::test_format_row_width` | ✓ |
| `make audit` wired via Makefile | multi-gate | `make help` lists it + `make audit --help` works | ✓ |
| CAP-T089 + CAP-T090 + CAP-B026 registered | verify-traceability | `docs/CAPABILITIES.md` diff | ✓ |
| make check green | multi-gate | `make check` | ✓ |

Legend: ✓ covered · ⚠️ partial · ❌ open (blocks shipping).

---

## Testing strategy

Unit tests cover parsers and filters in isolation. Integration tests build
an audit.log + SQLite DB in a tmp dir and run the CLI's `main()` with crafted
argv; capture stdout and assert on structure.

---

## Testing commands

```bash
make check
make audit                          # last 50
make audit SINCE=1h EVENT=digest_sent
make audit COUNT=1 SINCE=24h
make audit FOLLOW=1                 # live tail
```
