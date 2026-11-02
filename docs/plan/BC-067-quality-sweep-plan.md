# BC-067 — Quality sweep: 5 severity-1 bugs

*Branch: `fix/BC-067-quality-sweep`*
*Start: 2026-11-02*

Autonomous audit sweep. Three parallel investigators found 12 issues
across concurrency/data-integrity, pipeline correctness, and security.
Cross-checked, prioritised, picking the five that are either security
or silent-correctness failures. No UX changes — all fixes are internal
behaviour / safety nets.

## Bugs fixed

### 1. Shell injection in DockerAdapter (CRITICAL — security)

`poc/hub/executor/docker_adapter.py:56-71` runs:

```python
rollback_cmd = f"docker tag {finding.source_ref} {base_name}:rollback"
pull_cmd     = f"docker pull {pull_target}"
# ...
proc = await asyncio.create_subprocess_shell(rollback_cmd, ...)
```

`source_ref` originates in grype's parse of the SBOM. A compromised
scout, a crafted container label, or a hub-DB tamper that writes an
image tag like `nginx; rm -rf /data; #` becomes arbitrary shell on
`[Fix now]`.

**Fix:** switch to `asyncio.create_subprocess_exec` with argv list.
No shell interpolation; `source_ref` is a positional argv — can't
inject metacharacters. No UX change.

### 2. `/test/*` endpoints unauthenticated (CRITICAL — security)

`poc/hub/hub.py` registers four routes under `BEACONRY_TEST_MODE=1`
with **no `Depends()` guards**: `/test/wipe`, `/test/scan`,
`/test/sweep`, `/test/set_status`. If an operator forgets to unset
the flag before shipping to a LAN-reachable hub, anyone on the
network can:

- `POST /test/wipe` → drop DB + audit log
- `POST /test/scan` → inject fake findings
- `POST /test/set_status` → mark critical CVEs accepted

**Fix:** attach `Depends(require_cli_token)` to every `/test/*` route.
Scenario harness already uses the CLI token so no behaviour change
for intended use. Operators who left `BEACONRY_TEST_MODE=1` on
accidentally are now protected by their existing CLI auth.

### 3. `vuln["id"]` KeyError silently drops findings (SEVERE — correctness)

`poc/hub/hub.py:764` reads `cve_id = vuln["id"]`. If grype returns a
match with an unexpected shape (missing id, DB corruption, legacy
non-standard advisory), KeyError raises → caught by the broad
`except Exception` at `_ingest_candidates` → finding silently lost,
only one log line.

The POC's one job is "surface CVEs". A silent drop is the worst
possible failure mode.

**Fix:** `cve_id = vuln.get("id")`; `if not cve_id: log.warning(...);
continue`. Audit-log an `invalid_grype_match` event with the artifact
name so operators can see when this happens.

### 4. Reconcile mass-closes on partial inventory (SEVERE — correctness)

`reconcile_scan(host, scanned_ids)` closes every open finding on
`host` whose id isn't in `scanned_ids`. If scout POSTs a partial
inventory (one of ten images failed to syft, docker socket
hiccupped mid-collection), `scanned_ids` is a strict subset of
"what the host actually has". Reconcile silently closes the missing
ones.

Next scan re-discovers them as fresh "regressed" findings and the
operator gets flooded with alarms for CVEs that never actually
went away.

**Fix:** receive_scan already knows how many SBOMs were posted
(`len(sboms)`). Track a per-host rolling SBOM-count baseline in a
new column `scouts.healthy_sbom_count` (updated on every "normal
looking" scan — meaning count is within 50%+ of baseline). If the
current scan's count is < 50% of baseline, SKIP reconcile and emit
`reconcile_skipped_partial_inventory` audit event. Still save new
findings; just don't auto-close missing ones.

Simplest no-schema-change version: track baseline in-memory per
host for the lifetime of the hub process (resets on hub restart).
Good enough — a partial scan right after hub restart is survivable;
the real alarm-storm case is "hub up for days, scout glitches
briefly".

### 5. Double-send on same heartbeat tick (MODERATE — UX noise)

`_heartbeat_loop_forever` calls `heartbeat_sweep()` then
`manual_claim_sweep()` on each tick. A finding that's overdue on
BOTH timers triggers both notifications. Operator gets two Telegram
messages 10ms apart for the same CVE.

**Fix:** `manual_claim_sweep` already bumps both `last_claim_ts` AND
`last_notified_ts`. The second condition — `last_notified_ts <
cutoff` — is checked by `heartbeat_sweep` first on each tick. Swap
sweep order: run `manual_claim_sweep` BEFORE `heartbeat_sweep`.
Manual sweep bumps `last_notified_ts` → heartbeat_sweep's WHERE
clause now sees it fresh and skips the same finding. Zero schema
change, zero UX regression — the manual reminder renders the same
content as a heartbeat (both route through `notify_heartbeat`).

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| Shell metacharacters in source_ref don't execute | integration | `test_BC_067_quality_sweep.py::test_docker_adapter_rejects_shell_injection` | ✓ |
| /test/wipe requires CLI token | integration | `test_BC_067_quality_sweep.py::test_test_wipe_rejects_unauthenticated` | ✓ |
| /test/scan requires CLI token | integration | `test_BC_067_quality_sweep.py::test_test_scan_rejects_unauthenticated` | ✓ |
| /test/sweep requires CLI token | integration | `test_BC_067_quality_sweep.py::test_test_sweep_rejects_unauthenticated` | ✓ |
| /test/set_status requires CLI token | integration | `test_BC_067_quality_sweep.py::test_test_set_status_rejects_unauthenticated` | ✓ |
| /test/* accepts valid CLI token (scenario harness regression) | integration | `test_BC_067_quality_sweep.py::test_test_endpoints_accept_valid_cli_token` | ✓ |
| /test/* rejects wrong CLI token | integration | `test_BC_067_quality_sweep.py::test_test_endpoints_reject_wrong_token` | ✓ |
| Malformed grype match (missing id) → audit event, no crash | integration | `test_BC_067_quality_sweep.py::test_malformed_grype_match_audited_not_crashed` | ✓ |
| Partial-inventory scan does NOT mass-close findings | integration | `test_BC_067_quality_sweep.py::test_partial_inventory_skips_reconcile` | ✓ |
| Full-inventory scan DOES reconcile (regression guard) | integration | `test_BC_067_quality_sweep.py::test_full_inventory_still_reconciles` | ✓ |
| First scan with no baseline treated as full | integration | `test_BC_067_quality_sweep.py::test_first_scan_is_not_partial` | ✓ |
| Baseline only moves up (one-off small scan doesn't lower threshold) | integration | `test_BC_067_quality_sweep.py::test_baseline_only_moves_up` | ✓ |
| Partial-inventory skip emits audit event | integration | `test_BC_067_quality_sweep.py::test_partial_inventory_emits_audit_event` | ✓ |
| Overdue-on-both-timers finding gets ONE reminder per tick | integration | `test_BC_067_quality_sweep.py::test_double_overdue_single_reminder` | ✓ |
| Reversed sweep order double-fires (regression documentation) | integration | `test_BC_067_quality_sweep.py::test_old_order_would_have_double_fired` | ✓ |
| CAPs registered | verify-traceability | `docs/CAPABILITIES.md` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |

## Breaking changes

None. All fixes are internal; the HTTP contract, Telegram UX, env
vars, and schema are unchanged. Version bump 0.3.0 → 0.3.1 (patch).

## Rollout

- `make bump-patch` → 0.3.1
- Merge to dev
- Homelab live-rebuild — no config changes required
