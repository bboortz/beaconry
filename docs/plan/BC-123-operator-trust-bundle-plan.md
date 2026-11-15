# BC-123 — Operator trust bundle

*Branch: `feat/BC-123-operator-trust-bundle`*
*Start date: 2026-11-16*
*Story: `docs/stories/BC-123-operator-trust-bundle.md`*

---

## Situation

Yesterday's forensic walk through server2 found 48 CVSS≥9.0 CVEs per
scan silently dropped by `BEACONRY_MAX_FINDINGS_PER_RUN=5`. The cap
value wasn't surfaced by `beaconry status`. The drops left no audit
trail (just `continue` + a one-line log). The CLI had no `--cve`
filter to isolate a specific CVE forensically. BC-122 shipped
integrity hashes but the counter resets on restart.

This story closes the operator-visibility gap: every drop path gets
a dedicated audit event, every policy knob surfaces in `beaconry
status`, the long-overdue `--cve` filter lands, Telegram snooze
buttons show their expiry time, and BC-122's counters persist.

## Problems to solve

1. MIN_CVSS drops in `process_sbom_entry` emit only a log line.
2. SBOM-entry exceptions in `receive_scan` swallow the entire entry.
3. Save-then-notify failures route to the retry queue only *after*
   BC-104's sweep notices — not on the first exception.
4. `beaconry status` doesn't show policy knobs operators configure.
5. `beaconry findings` lacks `--cve` filter.
6. Telegram snooze buttons don't render expiry time.
7. BC-122 counters are in-memory only; restart loses the signal.

## Goals

1. Three audit event types: `finding_filtered`, `sbom_processing_failed`,
   `notify_failed`. Each carries the operator-forensic fields
   (CVE id, host, reason).
2. `/healthz.checks` surfaces `min_cvss`, `max_findings_per_run`,
   `scan_interval_s` (echoed configuration).
3. `beaconry status` renders a POLICIES line consuming those keys.
4. `beaconry findings --cve <id>` + `make findings CVE=<id>` work
   both locally and over HTTP.
5. Telegram snooze buttons carry `(→ HH:MM)` suffix.
6. New SQLite tables persist integrity counter + per-host cache.
7. Restart test proves counter survives.

## Scope

**Files touched:**

- `poc/hub/hub.py`
  - `process_sbom_entry`: emit `finding_filtered` per MIN_CVSS drop.
  - `receive_scan`: emit `sbom_processing_failed` per SBOM exception.
  - `_ingest_candidates`: emit `notify_failed` + queue to pending_notifications.
  - `_scan_integrity` audit event: add `n_filtered_min_cvss`, `n_sbom_errors`.
  - `_hub_healthz_summary`: add `min_cvss`, `max_findings_per_run`, `scan_interval_s`.
  - New `init_integrity_persistence()` + `_persist_mismatch_counter()` +
    `_persist_integrity_state(host, ...)` helpers.
  - Schema migration v11 → v12 (two new tables).
  - Load persisted state on boot.
- `poc/hub/notifiers/telegram.py` + `poc/hub/hub.py`
  - Snooze keyboard formatter accepts `now` + renders `(→ HH:MM)`.
- `poc/scripts/status.py`
  - `main()` adds a POLICIES line after FLEET.
- `poc/scripts/findings.py`
  - `--cve` argparse arg + SQL `cve_id = ?` branch + HTTP query param.
- `poc/hub/hub.py::http_findings`
  - Accept `cve_id` query param.
- `poc/scripts/audit.py`
  - No changes (it already filters by event type generically).
- `poc/tests/unit/test_BC_123_operator_trust_bundle.py` — new.
- `poc/tests/integration/test_BC_123_operator_trust_bundle.py` — new.
- `docs/CAPABILITIES.md` — CAP-T224 + CAP-T225.
- `docs/stories/INDEX.md`.
- `Makefile` — `make findings CVE=X` pass-through.

## Non-goals

- Raising the per-scan cap (operator side; homelab already shipped).
- Changing BC-104 pending_notifications table shape.
- Making the suppressed-finding list render in `beaconry findings`
  (the digest covers that; follow-up BC if we want DB-level visibility).

## Design decisions

- **Per-CVE `finding_filtered` audit events**, not per-scan summary.
  Reasoning: `scan_integrity` already has the aggregate buckets;
  per-CVE event is needed for forensic "where did my CVE go?" grep.
- **Reuse BC-104 pending_notifications** for notify failures. Don't
  invent a second queue.
- **Two new tables for persistence**, not reuse of findings. Keeps
  schema evolution clean; integrity data is cross-cutting.
- **Snooze expiry in UTC**, computed by the hub at keyboard-build
  time. Not stored — the keyboard is regenerated per-notification.
- **Status POLICIES line reads /healthz**, not env directly. Single
  source of truth: operator sees the running config, not the
  config they intended.

## Breaking changes

### 1. API / return shape

- `/healthz.checks` gains three keys. `.get()` consumers tolerate.
- `/findings` endpoint gains a `cve_id` query param. Omitted ⇒ no filter.
- `/scan` response shape unchanged.

### 2. Data model (schema + legacy rows)

- **Schema v11 → v12.** Two new tables:
  - `integrity_state (host TEXT PRIMARY KEY, iteration_hash TEXT,
    transfer_hash TEXT, last_updated_ts INTEGER)`
  - `integrity_counters (key TEXT PRIMARY KEY, value INTEGER)`
- Migration is idempotent `CREATE TABLE IF NOT EXISTS`. No backfill
  needed — counter starts at 0 on first boot post-migration;
  per-host cache stays empty until the next scan.

### 3. Behavioral semantics

- **Audit log volume up.** Each dropped CVE now gets one JSON line.
  On server2 that's ~50 extra lines per scan. Not a problem (audit
  is append-only + capped via log rotation).
- **Notification retry queue sees more entries.** BC-104's sweep now
  handles both "post-persist notify failed" AND the new "first-attempt
  notify failed" case. Same code path; just a different entry point.
- **Snooze expiry in button labels.** Operator who expected "⏰ 1h"
  without a time now sees "⏰ 1h (→ 00:47)". Cosmetic; no action change.
- **`beaconry status` output has an extra line.** Scripts parsing
  the output must tolerate extra lines (verified — nothing in-tree
  does strict line-count assertions).

## Merge plan

1. All work on `feat/BC-123-operator-trust-bundle`
2. `make check` passes on branch tip
3. PR to `dev`; Claude opens; user `/ultrareview` optional; auto-merge if green
4. Squash merge
5. Post-merge: BC-123 story status → `shipped`, release workflow bumps to next patch

## Rollout plan

Additive. Schema migration is a one-shot CREATE TABLE at boot.
Legacy /healthz consumers tolerate extra keys. Legacy findings CLI
tolerates the new column. No operator action required.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Scenario 1: MIN_CVSS drop audited | unit (source check) | `test_BC_123_operator_trust_bundle.py::test_min_cvss_drop_emits_finding_filtered_shape` | ✓ |
| Scenario 2: SBOM processing failure audited | unit (source check) | `test_sbom_processing_failed_audit_source_check` | ✓ |
| Scenario 3: Notify failure emits notify_failed audit | unit (source check) | `test_notify_failed_audit_source_check` | ✓ |
| Scenario 4: `beaconry status` POLICIES line | unit | `test_status_renders_policies_line` + `test_status_policies_line_silent_on_legacy_hub` | ✓ |
| Scenario 5: `beaconry findings --cve` filter | unit | `test_findings_parse_args_accepts_cve_flag` + `test_findings_load_findings_cve_filter` + `test_http_findings_endpoint_accepts_cve_id` | ✓ |
| Scenario 6: Snooze buttons show expiry | unit | `test_snooze_label_with_expiry_renders_utc_hour_minute` + `test_snooze_label_with_expiry_week_rolls_over` | ✓ |
| Scenario 7: Integrity counter persists across restart | unit | `test_integrity_counter_survives_restart` + `test_integrity_state_survives_restart` + `test_load_integrity_persistence_degrades_on_missing_tables` | ✓ |
| Goal: schema v11 → v12 migration idempotent | unit | `test_schema_version_is_12_on_boot` + `test_integrity_tables_created_idempotently` | ✓ |
| Goal: /healthz.checks exposes policy knobs | unit | `test_healthz_exposes_policy_knobs` + `test_policy_knobs_excluded_from_degraded_check` | ✓ |
| Memory drift pattern added if this story deprecates documented behavior | N/A | pure additive | ✓ |

Legend: ✓ covered · ⚠️ partial · ❌ open (blocks shipping).

## Testing strategy

- Unit tests for every audit-event emission + schema migration + CLI
  argument plumbing + keyboard formatter.
- One integration test spins TestClient hub + calls /scan twice +
  verifies the counter persisted across a simulated restart (reload
  module after writing to the DB).

## Testing commands

```bash
make check
pytest poc/tests/unit/test_BC_123_operator_trust_bundle.py -v
pytest poc/tests/integration/test_BC_123_operator_trust_bundle.py -v
```
