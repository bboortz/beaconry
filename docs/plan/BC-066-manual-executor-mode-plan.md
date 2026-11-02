# BC-066 — Manual executor as default + reminder loop

*Branch: `feat/BC-066-manual-executor-mode`*
*Start: 2026-11-02*

## Scope

### Hub: executor dispatch via env

- `poc/hub/hub.py` near line 148: replace per-finding dispatch with
  a single `EXECUTOR` instance picked at startup:
  ```python
  EXECUTOR_NAME = os.environ.get("BEACONRY_EXECUTOR", "manual").lower()
  _executors = {"manual": ManualAdapter(), "docker": DockerAdapter()}
  EXECUTOR: ExecutorPlugin = _executors.get(EXECUTOR_NAME, ManualAdapter())
  ```
- Legacy call sites `next((e for e in EXECUTORS if e.can_handle(f)), ManualAdapter())`
  collapse to the single `EXECUTOR`.
- `/healthz` summary gains `"executor": EXECUTOR_NAME` so operators
  can confirm what's running without reading env.

### ManualAdapter behaviour change

Current `ManualAdapter.execute` returns `ExecutionResult(success=True, message="Command shown — manual execution required", command=…)`. BC-066 keeps that return but the hub's `_handle_fix` path now treats the manual case specifically:

- Audit `manual_fix_claimed` with full context (finding_id, cve_id,
  host, command, claim_ts).
- `UPDATE findings SET last_claim_ts = ? WHERE id = ?`.
- Edit Telegram message with the `📝 Logged` body + reminder note.
- Return — do NOT call `verify_fix` (that's a scan path, not a tap
  path; operator hasn't done the work yet).

### Schema v4

- `EXPECTED_SCHEMA_VERSION = 4`.
- `init_db` ALTERs `findings` to add `last_claim_ts INTEGER` under the
  existing BC-063 pattern (`PRAGMA table_info(findings)` → conditional
  ADD COLUMN).

### Reminder sweep

- New function `manual_claim_sweep(now_ts=None) -> int` next to
  `heartbeat_sweep` (same signature + style).
- Reads open findings with `last_claim_ts > 0` and
  `now - last_claim_ts > MANUAL_REMINDER_INTERVAL_S`.
- For each: send `format_notification(finding, reason="manual_reminder")`
  via the existing notification adapter (reuses `multi_host` suppression
  + stable callback_data), audit `manual_fix_reminder_sent`, then
  `UPDATE findings SET last_claim_ts = ?` to the fresh `now`.
- Called from the existing BC-035 async loop (`_heartbeat_loop_forever`)
  — no new asyncio task.
- Env: `BEACONRY_MANUAL_REMINDER_INTERVAL_S` (default `3600`, `0` disables).

### notification `reason="manual_reminder"` rendering

`format_notification` gains a leading line when `reason == "manual_reminder"`:
> ⏰ **Still open** — you said you'd fix this {age} ago.

The body (CVE/host/command) stays identical so the operator sees the
same actionable info.

### Env + docs

- `docs/VARIABLES.md`: rows for `BEACONRY_EXECUTOR` and
  `BEACONRY_MANUAL_REMINDER_INTERVAL_S`.
- `poc/.env.example`: both lines, both with defaults.
- `poc/docker-compose.yml`: propagate both.

### Version

`0.2.2 → 0.3.0` (minor — default behaviour for `[Fix now]` changes;
response additive, no API break).

## Design decisions

### Why global env, not per-finding dispatch

The per-finding dispatch (`can_handle`) made sense when adapters
were orthogonal (docker handles docker; manual handles everything
else). With BC-066's workflow, the manual path is a *policy choice*
("I never want beaconry to execute, period") — a policy belongs on
the operator, not on the finding type.

### Why not call verify_fix on claim

`verify_fix` runs a re-scan of the affected source. In the manual
flow the operator hasn't done anything yet — re-scanning would just
return the same CVE and look like "fix failed" to the UI. The
reconcile pass on the *next scan* (BC-031) is the right verification
mechanism; it tracks "CVE no longer in inventory" naturally.

### Why last_claim_ts on findings, not a separate table

The relationship is 1:1 — each finding has at most one open claim.
Adding a nullable column is cheaper than a join. Multiple historical
claims aren't needed; the audit log carries that history.

### Why not auto-reset last_claim_ts when the finding transitions out of open

The reconcile path on next-scan will close the finding (status =
`fixed_verified`), and the sweep's `status != open` filter skips it.
Resetting `last_claim_ts` on close would just be redundant work;
non-open rows never fire reminders.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Default executor is manual | integration | `test_BC_066_manual_executor.py::test_default_executor_is_manual` | ✓ |
| BEACONRY_EXECUTOR=docker switches | integration | `test_BC_066_manual_executor.py::test_env_switches_to_docker_executor` | ✓ |
| /healthz reports executor name (manual) | integration | `test_BC_066_manual_executor.py::test_healthz_reports_executor_name` | ✓ |
| /healthz reports executor name (docker) | integration | `test_BC_066_manual_executor.py::test_healthz_reports_docker_executor` | ✓ |
| /healthz executor label does not drive degraded | integration | `test_BC_066_manual_executor.py::test_healthz_executor_not_counted_as_check` | ✓ |
| Unknown executor name falls back to manual | integration | `test_BC_066_manual_executor.py::test_unknown_executor_falls_back_to_manual` | ✓ |
| Fresh v4 DB has last_claim_ts column | integration | `test_BC_066_manual_executor.py::test_fresh_db_v4_has_last_claim_ts_column` | ✓ |
| Schema v3 → v4 migration adds last_claim_ts | integration | `test_BC_066_manual_executor.py::test_schema_v3_to_v4_migration` | ✓ |
| _record_manual_claim writes timestamp | integration | `test_BC_066_manual_executor.py::test_record_manual_claim_writes_timestamp` | ✓ |
| _record_manual_claim tolerant of missing column | integration | `test_BC_066_manual_executor.py::test_record_manual_claim_tolerant_of_missing_column` | ✓ |
| Reminder sweep fires on overdue claim + audits + bumps claim_ts | integration | `test_BC_066_manual_executor.py::test_sweep_fires_on_overdue_claim` | ✓ |
| Reminder sweep skips fresh claim | integration | `test_BC_066_manual_executor.py::test_sweep_skips_fresh_claim` | ✓ |
| Reminder sweep skips non-open finding | integration | `test_BC_066_manual_executor.py::test_sweep_skips_closed_finding` | ✓ |
| Reminder sweep skips rows without a claim | integration | `test_BC_066_manual_executor.py::test_sweep_skips_rows_without_claim` | ✓ |
| Reminder sweep bumps last_notified_ts (dedup w/ BC-035) | integration | `test_BC_066_manual_executor.py::test_sweep_also_bumps_last_notified_ts` | ✓ |
| MANUAL_REMINDER_INTERVAL_S=0 disables sweep | integration | `test_BC_066_manual_executor.py::test_interval_zero_disables_reminders` | ✓ |
| EXPECTED_SCHEMA_VERSION == 4 | integration | `test_BC_066_manual_executor.py::test_expected_schema_version_is_4` | ✓ |
| CAPs registered | verify-traceability | `docs/CAPABILITIES.md` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |

## Breaking changes

- `[Fix now]` default behaviour changes: pre-BC-066 → docker pull +
  verify; post-BC-066 → audit + show command + schedule reminder.
  Not a HTTP/data break, but a visible operator behaviour change.
  Operators who want the old behaviour set `BEACONRY_EXECUTOR=docker`.

## Rollout

- `make bump-minor` → 0.3.0.
- Merge to dev.
- Homelab: ships default-on manual. Operator's existing `demo` mode
  continues to use whatever `BEACONRY_EXECUTOR` is set.
