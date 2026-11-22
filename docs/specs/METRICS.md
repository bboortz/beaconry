# Beaconry metrics

Two personas, one audit log, different numbers.

- **Key user** (operator): *"is this tool worth it?"*
- **Developer** (us): *"which features earn their keep?"*

## Privacy-first default (BC-075)

**Metrics is off by default.** Scouts don't contribute their timing +
action patterns to the hub's metrics-fuel audit log unless they
explicitly opt in. Flip the switch on each scout you want measured:

```bash
# in the scout's .env or compose environment:
SCOUT_METRICS_ENABLED=1
```

Mechanism: when enabled, the scout attaches
`X-Beaconry-Metrics-Opt-In: 1` to its `/scan` and `/heartbeat` POSTs.
The hub honors the header by writing `scan_received` and
`heartbeat_received` audit events only for opted-in scouts. CVE
matching, Telegram notifications, DB persistence, the FLEET view,
and the per-finding audit (`notify_sent`, `digest_sent`, etc.) keep
working for every scout regardless — the gate is specifically on the
counters metrics aggregates.

**Why default off?** Beaconry's design heads toward multi-tenant /
SaaS (DESIGN.md Phase 4). A scout running against a hub someone else
operates shouldn't leak timing patterns + operator behavior without
explicit consent. Default-off trains the right instincts from day one.

**Side effects when disabled:**
- `beaconry metrics` shows zero `scan_received` / `heartbeat_received`
  counts for opted-out scouts — correct, there's no data to show.
- `make soak`'s pipeline-silence anomaly can't fire for opted-out
  fleets (needs scan counts to assert silence). Operators relying on
  it must opt their scouts in or add a hub-side scheduled synthetic
  signal.
- Fleet health (`make status`'s FLEET section + `/fleet` endpoint)
  is NOT privacy-protected — the `scouts` registry table bumps on
  every POST regardless of header. Opted-out scouts still appear
  with correct heartbeat/health. Only the audit-log histogram is
  gated.

## How to run

```bash
beaconry metrics            # both lenses, last 7d
beaconry metrics usability  # key-user only
beaconry metrics features   # developer only
beaconry metrics all --since 24h --json   # machine-readable
```

Also works as `make metrics ARGS="usability --since 7d"`.

### Local vs remote (BC-076)

`beaconry metrics` auto-routes the same way `beaconry status` and
`beaconry findings` do, using `~/.config/beaconry/cli.yml`:

- `hub_url` non-localhost → **remote** (`GET /audit` on the hub, bearer auth)
- `hub_url` localhost or missing → **local** (`$DATA_DIR/audit.log`)

Operator can override:

```bash
beaconry metrics --remote    # force HTTP to the configured hub
beaconry metrics --local     # force local audit.log (useful for exports)
```

Output includes the source: `Window: last 7.0d, 1234 events from
remote (/audit on http://100.1.2.3:8000)`.

Remote mode fetches up to 10000 events per call (the `/audit`
endpoint's cap). If the response hits the cap, metrics prints a
truncation warning — shrink `--since` or run on the hub host.

## Usability lens — "is this tool worth it?"

| Metric | What it means | Healthy range |
|---|---|---|
| **Alerts delivered** | `notify_sent` events | Depends on your CVE flow; typically 5–50/week |
| **Delivery success rate** | `notify_sent / (notify_sent + notify_sent_failed)` | ≥ 99%; below 95% = notifier breaking |
| **Unique findings alerted** | distinct `finding_id`s with ≥1 `notify_sent` | — |
| **Action rate** | % findings where operator triggered any status transition | > 80% — "I act on most alerts" |
| **Fix rate** | % actioned findings that landed on `fixed`/`fixed_verified` | > 50% — "most of what I act on, I fix" |
| **Snooze/accept rate** | % actioned findings deferred/dismissed | 10–30% is fine; > 40% = alerts are too noisy |
| **Ignored rate** | % findings with first `notify_sent` > 24h ago and no action | < 10% — above means the alert isn't actionable or there's too much noise |
| **Time-to-action p50/p75/p95** | median/75th/95th percentile of time from first notify_sent to first action | Depends on urgency tier; Tier 0 should p75 < 1h |
| **Regression count** | `regressed` events (fix regression — CVE came back) | 0 is ideal; non-zero signals "fix-is-not-forget" broke |
| **Reminder count** | `manual_fix_reminder_sent` events | ≤ 1 per claim is good |
| **Reminder effectiveness** | % of reminded findings that eventually hit `fixed_verified` | > 60% — nag works for most people |

### Interpreting

- **Low action rate + low ignored rate** = healthy but quiet. Few CVEs; those that come get fixed quickly.
- **High ignored rate** = alerts aren't actionable OR operator is overwhelmed. Tune `MIN_CVSS` higher, or raise `BEACONRY_DIGEST_INTERVAL_S`.
- **High snooze/accept** = fatigue signal; most alerts aren't worth fixing. Something in the pipeline is miscalibrated (exposure scoring, tier classification).
- **Non-zero regressions** = your fixes aren't sticking. Likely a container-rebuild or tag-track problem. Investigate.
- **Low reminder effectiveness** = operator ignores nags → the manual-fix workflow isn't working for you; consider `BEACONRY_EXECUTOR=docker` or a shorter reminder interval.

## Features lens — "which features earn their keep?"

| Metric | What it means | Healthy range |
|---|---|---|
| **Digest fire ratio** | `digest_sent / (digest_sent + digest_throttled)` | 0.1–0.3; too high means the throttle isn't working |
| **Distinct hosts (7d)** | hosts that have `scan_received` or `heartbeat_received` recently | matches your fleet size |
| **Heartbeat events** | total `heartbeat_received` events in window | SCOUT_HEARTBEAT_INTERVAL_S cadence × distinct hosts |
| **Worst heartbeat gap** | longest gap between consecutive heartbeats for any host | ≤ 2× SCOUT_HEARTBEAT_INTERVAL_S — above = scout dropped |
| **Scan cadence (median)** | median delta between consecutive scan_received events | matches SCAN_INTERVAL (default 300s) |
| **Manual-fix claimed** | `manual_fix_claimed` events (operator tapped `[Fix now]` in manual mode) | should correlate with alerts in manual mode |
| **Manual-fix uptake** | `manual_fix_claimed / notify_sent` | > 30% — operators use the button |
| **Partial-inventory saves** | `reconcile_skipped_partial_inventory` events (BC-067 guard firings) | 0 is ideal; non-zero = scout glitches you'd want to investigate |
| **Invalid grype matches** | `invalid_grype_match` events (BC-067 missing CVE id) | 0 — any non-zero means data loss, investigate |

### Interpreting

- **Low manual-fix uptake** = operators prefer `make set-status` CLI over the Telegram button. That's fine but suggests the button could ship.
- **Non-zero invalid grype** = grype DB has data issues OR beaconry's parser is wrong. Each one lost a CVE from the pipeline.
- **High worst-heartbeat-gap** = a scout is intermittently unreachable. Not a hub bug; likely network or scout host issue.
- **Scan cadence much higher than SCAN_INTERVAL** = scouts are missing scheduled scans. Resource contention? Lock contention? Investigate.

## Soak thresholds — "would a developer deny the merge?"

`make soak` scans the last 24h and flags any of these as merge-blocking:

| Signal | Threshold | What it says |
|---|---|---|
| `regressed` event | ≥ 1 | fix-is-not-forget broke |
| `invalid_grype_match` | ≥ 1 | silent CVE loss |
| `notify_sent_failed` | each event (hard error) | notifier failure |
| Notifier failure ratio | > 10% (sample floor ≥ 10) | notifier is dead |
| Partial-inventory saves | > 20% of scans (floor ≥ 5 scans) | scout flaky |
| Reminder storm | ≥ 3 reminders for same finding, no fix | operator ignoring nags → broken workflow |
| Pipeline silence | zero `scan_received` in window (window ≥ 4× SCAN_INTERVAL) | pipeline dead |

Soak exits 1 if any fire (same as hard errors). Section headers separate hard-error output from threshold output.

## Running on the live stack

```bash
# On hetzner-staging (where the hub lives):
DATA_DIR=/var/lib/beaconry make metrics
# or
beaconry metrics all --since 7d
```

For weekly health review:

```bash
# Dump both lenses + soak to a dated file
mkdir -p /var/log/beaconry-weekly
beaconry metrics all --since 7d > /var/log/beaconry-weekly/$(date +%F).txt
beaconry soak >> /var/log/beaconry-weekly/$(date +%F).txt
```

Review on Sundays. Trends over N weeks tell the real story.
