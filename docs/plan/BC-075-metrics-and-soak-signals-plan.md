# BC-075 — Beaconry metrics + threshold-based soak signals

*Branch: `feat/BC-075-metrics-and-soak-signals`*
*Start: 2026-11-02*

## Why

Phase 1 success criterion per DESIGN.md is *"used daily on the
author's own servers"*. The infrastructure is in place; the
measurement loop isn't. Two personas need different numbers from
the same data:

- **Key user** ("is this tool worth it?"): action rate, time-to-fix,
  false-positive signals, ignored-alerts rate.
- **Developer** ("which features earn their keep?"): digest usage,
  heartbeat health, reconcile saves, invalid-grype losses.

Current `make soak` flags hard error events in a 24h window.
Doesn't catch degradation patterns ("scout silent too long",
"reminder storm with no follow-up", "regressions spiking"). Needs
threshold-aware signals.

## Scope

### 1. `beaconry metrics` subcommand + `make metrics`

`poc/scripts/metrics.py` reads audit log + findings DB; produces
three report shapes selectable by positional:

```bash
beaconry metrics usability    # key-user lens
beaconry metrics features     # developer lens
beaconry metrics all          # both (default)
```

#### Usability metrics (key user)

| Metric | How computed | What it tells |
|---|---|---|
| Alerts delivered | count(`notify_sent`) + count(`notify_sent_failed`) | Pipeline reach |
| Delivery success | `notify_sent / (notify_sent + notify_sent_failed)` | Notifier health |
| Action rate | distinct finding_ids where status moved from `open` → anything, in the finding's lifetime | % alerts the operator engaged with |
| Fix rate | % action events that landed on `fixed` or `fixed_verified` | How many turn into fixes vs. snooze/accept |
| Snooze+accept rate | % that landed on `snoozed` or `accepted` | How much is "not now" vs. "done" |
| Ignored rate | % findings whose first notify_sent is > 24h old and status still `open` | Alerts the operator never touched |
| Time-to-first-action | p50/p75/p95 of (first_status_change_ts − first_notify_sent_ts) | Operator responsiveness |
| Regression rate | count(`regressed`) / count(`notify_sent`) | Fix-is-not-forget: are closed CVEs coming back? |
| Reminder effectiveness | % `manual_fix_reminder_sent` findings that eventually hit `fixed_verified` | Does the nag actually work? |

#### Features metrics (developer)

| Metric | How computed | What it tells |
|---|---|---|
| Digest fires vs throttled | count(`digest_sent`) / count(`digest_throttled`) | BC-036+BC-065 cadence health |
| Hosts seen in last 7d | distinct hosts in `scan_received` or `heartbeat_received` | Fleet usage |
| Heartbeat gaps | per host, longest gap between heartbeat_received events | BC-063 scout reliability |
| Manual-fix engagement | count(`manual_fix_claimed`) / count(`notify_sent`) | BC-066 default-manual uptake |
| Partial-inventory saves | count(`reconcile_skipped_partial_inventory`) | BC-067 guard firing |
| Invalid grype matches | count(`invalid_grype_match`) | BC-067 malformed-data rate |
| Scan cadence | median(delta between scan_received events) | Scout interval vs config |

Output is a plain-text table with sections per lens. JSON mode
(`--json`) for scripting / dashboard feeding.

### 2. Threshold-based soak signals (enhanced `make soak`)

Current soak lists error events in-window and exits 1 if any.
Add a second class: **anomaly thresholds** that would make a
developer deny a release.

| Signal | Threshold | Why reject |
|---|---|---|
| `regressed` in window | ≥ 1 | fix-is-not-forget regression → something broke |
| `invalid_grype_match` in window | ≥ 1 | data loss (CVE silently dropped) |
| `notify_sent_failed` ratio | > 10% of attempts | notifier is down |
| `reconcile_skipped_partial_inventory` ratio | > 20% of scans | scout is flaky |
| Zero `scan_received` for > 2× SCAN_INTERVAL | always | pipeline silent |
| Heartbeat gap for a known scout | > 2× SCOUT_HEARTBEAT_INTERVAL_S | scout unreachable |
| Reminder-without-follow-up | ≥ 3 `manual_fix_reminder_sent` for same finding without `fixed_verified` | operator ignoring the nag |

Exit codes stay the same: 0 clean, 1 anomalies, 2 unreadable.
Section header distinguishes "hard errors" from "thresholds".

### 3. `beaconry metrics` as a CLI subcommand

Register in `poc/scripts/cli.py` dispatcher's `_SUBCOMMANDS` so
`beaconry metrics` works out of the box after `pip install -e .`.
`make metrics` for the make-flow users.

### 4. docs/METRICS.md

One page that documents what each metric is, how to interpret it,
and when to raise an alarm. Living doc; developer updates when a
new audit event lands.

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| parse_window accepts valid forms | integration | `test_BC_075_metrics.py::test_parse_window_valid_forms` | ✓ |
| parse_window rejects garbage | integration | `test_BC_075_metrics.py::test_parse_window_rejects_garbage` | ✓ |
| Usability counts actions from status_change | integration | `test_BC_075_metrics.py::test_usability_report_counts_actions` | ✓ |
| Usability tracks ignored rate | integration | `test_BC_075_metrics.py::test_usability_tracks_ignored_rate` | ✓ |
| Time-to-first-action p50/p75/p95 | integration | `test_BC_075_metrics.py::test_time_to_action_percentiles` | ✓ |
| Features counts digest fire ratio | integration | `test_BC_075_metrics.py::test_features_report_counts_digest` | ✓ |
| Features tracks heartbeat gaps per host | integration | `test_BC_075_metrics.py::test_features_tracks_heartbeat_gaps` | ✓ |
| `beaconry metrics` default (all) runs both lenses | integration | `test_BC_075_metrics.py::test_metrics_all_lens_runs_both` | ✓ |
| JSON mode outputs parseable JSON | integration | `test_BC_075_metrics.py::test_metrics_json_mode` | ✓ |
| Missing audit log → exit 2 | integration | `test_BC_075_metrics.py::test_metrics_missing_audit_log_returns_two` | ✓ |
| Bad --since → exit 2 | integration | `test_BC_075_metrics.py::test_metrics_rejects_bad_since` | ✓ |
| Soak flags `regressed` as hard error | integration | `test_BC_075_metrics.py::test_soak_flags_regression_as_hard_error` | ✓ |
| Soak flags notify-failure ratio > 10% | integration | `test_BC_075_metrics.py::test_soak_flags_notify_failure_ratio` | ✓ |
| Soak doesn't flag notify-failure on tiny samples | integration | `test_BC_075_metrics.py::test_soak_does_not_flag_notify_failure_below_sample_floor` | ✓ |
| Soak flags partial-inventory ratio > 20% | integration | `test_BC_075_metrics.py::test_soak_flags_partial_inventory_ratio` | ✓ |
| Soak flags reminder storm (3+ no fix) | integration | `test_BC_075_metrics.py::test_soak_flags_reminder_storm` | ✓ |
| Soak doesn't flag reminders when fixed | integration | `test_BC_075_metrics.py::test_soak_does_not_flag_reminders_when_fixed` | ✓ |
| Soak flags pipeline silence (24h+ zero scans) | integration | `test_BC_075_metrics.py::test_soak_flags_pipeline_silence` | ✓ |
| Soak skips silence check in short windows | integration | `test_BC_075_metrics.py::test_soak_pipeline_silence_skipped_in_short_window` | ✓ |
| Soak passes on healthy audit | integration | `test_BC_075_metrics.py::test_soak_passes_on_healthy_audit` | ✓ |
| `beaconry metrics` dispatcher subcommand | integration | `test_BC_075_metrics.py::test_cli_dispatcher_has_metrics_subcommand` | ✓ |
| `beaconry metrics --help` renders dispatcher-owned usage | integration | `test_BC_075_metrics.py::test_cli_dispatcher_metrics_help` | ✓ |
| CAP-T157: /scan without opt-in header skips metrics audit | integration | `test_BC_075_metrics.py::test_scan_without_opt_in_header_skips_metrics_audit` | ✓ |
| CAP-T157: /scan with opt-in header writes metrics audit | integration | `test_BC_075_metrics.py::test_scan_with_opt_in_header_writes_metrics_audit` | ✓ |
| CAP-T157: only exact `1` opts in (not true/yes/0) | integration | `test_BC_075_metrics.py::test_scan_opt_in_header_other_values_dont_write_audit` | ✓ |
| CAP-T157: /heartbeat without opt-in skips audit | integration | `test_BC_075_metrics.py::test_heartbeat_without_opt_in_skips_audit` | ✓ |
| CAP-T157: /heartbeat with opt-in writes audit | integration | `test_BC_075_metrics.py::test_heartbeat_with_opt_in_writes_audit` | ✓ |
| CAP-T157: opt-out scout still appears in /fleet | integration | `test_BC_075_metrics.py::test_opt_out_scout_still_registered_in_fleet` | ✓ |
| CAPs registered | verify-traceability | `docs/CAPABILITIES.md` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |

## Privacy (added mid-scope per operator feedback)

Metrics is **off by default**. The scout must explicitly opt in:

```
SCOUT_METRICS_ENABLED=1
```

Mechanism: when `SCOUT_METRICS_ENABLED=1`, scout attaches
`X-Beaconry-Metrics-Opt-In: 1` to its `/scan` and `/heartbeat` POSTs.
Hub honors the header: writes `scan_received` + `heartbeat_received`
audit events ONLY when the header is `1`. CVE matching,
notifications, fleet view, DB persistence all keep working for every
scout regardless — only the metrics-fuel audit entries are gated.

Rationale: beaconry is heading toward multi-tenant / SaaS (Phase 4
in DESIGN.md). Operators running scouts against a hub they don't
own shouldn't have their timing patterns + action behavior
logged without explicit consent. Default-off trains the right
instincts from day one.

Visible side effects when metrics disabled:
- `beaconry metrics` shows 0 scan_received / heartbeat_received
  counts for opted-out scouts (correct — no data to show).
- `make soak`'s pipeline-silence anomaly won't fire for opted-out
  fleets because it needs to be able to count scans. Documented in
  METRICS.md.
- Hub's `/fleet` still shows the opted-out scouts via BC-061's
  `scouts` registry table (which bumps on every POST regardless of
  header) — fleet health is NOT privacy-protected.

Operator who wants metrics: set `SCOUT_METRICS_ENABLED=1` on each
scout + restart.

## Breaking changes

None. `make soak` still exits 0/1/2 with the same semantics;
anomaly signals land in the exit-1 bucket alongside hard errors.
`beaconry metrics` is a new subcommand (additive). Metrics
default-off means POC operators see no data without opt-in —
intentional privacy-first stance.

## Version

0.4.0 → 0.5.0 (**minor** — new `beaconry metrics` subcommand is
net-new user-visible functionality; `make soak` output gains new
anomaly sections, visible behaviour change. SemVer calls that a
minor bump, not a patch.).

## Rollout

- `make bump-minor` → 0.5.0
- Merge via Release workflow (click the button after CI green)
- Operator runs `beaconry metrics all` on hetzner-staging to seed
  the first measurement baseline.
- Then weekly: `beaconry metrics` + `make soak` → interpret → decide
  if beaconry is still earning its keep.
