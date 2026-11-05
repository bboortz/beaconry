# Finding lifecycle — authoritative reference

*Last updated: 2026-11-05 (BC-088 initial; BC-089 added snooze expiry
paths). Single source of truth for how a
CVE becomes a finding, is dispatched to the operator, resolved (or
auto-closed, or regressed), and tracked thereafter.*

*Code pointers reference `poc/hub/hub.py` unless noted. All line
numbers are correct at v0.12.0 (BC-087 merge) — regenerate via `grep -n`
if they've drifted.*

---

## 1. State diagram

Finding statuses and transitions. All status values are TEXT columns
on the `findings` table, enforced only by code (no DB CHECK
constraint).

```
                              ┌────────────────────────┐
                              │  scout scan (every 5m) │
                              │  SCAN_INTERVAL=300     │
                              └──────────┬─────────────┘
                                         │
                                         ▼
                      ┌──────────────────────────────────┐
                      │ hub POST /scan                   │
                      │   1. bump_last_seen (BC-034/087) │
                      │   2. reconcile_scan (BC-031/086) │
                      │   3. for each new finding:       │
                      │      process_sbom_entry          │
                      └──────────┬───────────────────────┘
                                 │
                                 ▼
                           ┌──────────┐
                     new   │          │
                 ────────▶ │  OPEN    │ ◀────────────┐
                 notify()  │          │              │  snooze_until expired
                           └─────┬────┘              │  (BC-089, via periodic
                                 │                   │   snooze_expiry_sweep —
                                 │                   │   fires notify_snooze_
                                 │                   │   expired unless
                                 │                   │   BEACONRY_SNOOZE_NOTIFY_
                                 │                   │   ON_EXPIRE=0)
                       buttons   ├───────────┬───────┤
                                 │           │       │
          [🔧 Fix now]           │ [⏰Nh] ×k │  [✅] │
                                 │ (k=len(   │       │
                                 │  SNOOZE_  │       │
                                 │  DEFAULT_ │       │
                                 │  HOURS))  │       │
                                 │           │       │   absent N scans
                                 │           │       │  (BC-086,
                                 │           │       │   THRESHOLD=3)
                                 │           │       │
                                 │           │       │  OPEN also auto-
                                 │           │       │  closes if the
                                 │           │       │  target disappears
                                 │           │       │  without operator
                                 │           │       │  action
                                 │           │       │           │
                                 ▼           ▼       ▼           │
                           ┌─────────┐  ┌────────┐ ┌──────────┐  │
                           │  FIXED  │  │SNOOZED │ │ ACCEPTED │  │
                           └────┬────┘  └────┬───┘ └────┬─────┘  │
                verify            │           │          │        │
                 (next scan)      │           │          │        │
                                 ▼           │          │        │
                           ┌──────────────┐  │          │        │
                           │FIXED_VERIFIED│  │          │        │
                           └──────┬───────┘  │          │        │
                                  │          │          │        │
      ┌──────┐ reappears in scan  │          │          │        │
      │      │ (BC-031)           │          │          │        │
      │ 🔥   │◀───────────────────┘          │          │        │
      │REGR  │                  absent N scans          │        │
      │ESSED │  ────────────────┐    │                  │        │
      │      │                   │   │                  │        │
      └──────┘                   │   │                  │        │
                                 ▼   ▼                  ▼        ▼
                               ┌─────────────────────────────────────┐
                               │            AUTO_FIXED               │
                               │  (silent — audit `finding_auto_     │
                               │   fixed` only, no Telegram)         │
                               │                                     │
                               │  SQL filter in reconcile_scan:      │
                               │  ('open' | 'regressed' | 'snoozed'  │
                               │   | 'accepted') + absent + counter  │
                               │  ≥ BEACONRY_AUTO_FIX_THRESHOLD      │
                               │  → flip to auto_fixed               │
                               └──────────────┬──────────────────────┘
                                              │ reappears (BC-086)
                                              ▼
                                   ┌──────────────────────┐
                                   │ REGRESSED_FROM_      │
                                   │   AUTO_FIX           │
                                   │ 🔥 AUTO-FIX REGRESSED│
                                   │ (⚠️ body wire-up bug,│
                                   │   see §15 inconsist. │
                                   │   #1)                │
                                   └──────────────────────┘

    ┌────────────────────────────────────────────────────────┐
    │  VERIFICATION_FAILED (from any fix dispatch):          │
    │  health waterfall fails or re-scan still shows CVE →   │
    │  status flips, audit `verification_failed`. Terminal   │
    │  until operator acts — reconcile_scan does NOT consider│
    │  this status (§8).                                     │
    └────────────────────────────────────────────────────────┘
```

**Status catalogue (9 values):**

| Status | Set by | Next transitions | Notification shape |
|---|---|---|---|
| `open` | `save_finding` on new ingest | fixed \| snoozed \| accepted \| auto_fixed | first-fire via `notify()` |
| `snoozed` | `_handle_snooze` button | **open** via `snooze_expiry_sweep` at `snooze_until_ts` (BC-089) \| auto_fixed (if target disappears for `AUTO_FIX_THRESHOLD` scans) | silent at snooze; `⏰ Snooze expired` at auto-re-open (unless `BEACONRY_SNOOZE_NOTIFY_ON_EXPIRE=0`) |
| `accepted` | `_handle_ack` button | auto_fixed (if disappears) \| regressed-from-auto-fix (if reappears via auto_fixed path) | silent |
| `fixed` | `_handle_fix` button | fixed_verified \| verification_failed \| regressed | silent after dispatch |
| `fixed_verified` | verify after fix | regressed (if CVE reappears) | silent |
| `auto_fixed` | `reconcile_scan` counter ≥ threshold | regressed_from_auto_fix (if reappears) | **silent** by design |
| `regressed` | `reconcile_scan`: fixed/fixed_verified reappeared | fixed again by operator | 🔥 FIX REGRESSED loud |
| `regressed_from_auto_fix` | `reconcile_scan`: auto_fixed reappeared | same | 🔥 AUTO-FIX REGRESSED **(but see §15.1 — body wire-up bug makes this render as BC-031 body on Telegram today)** |
| `verification_failed` | fix pipeline could not confirm | operator manual intervention | silent at transition (operator sees it in the failed-fix edit message) |

---

## 2. Ingest path — how "open" findings come from grype

```
scout → syft <image> → SBOM.json → POST /scan to hub
                                        │
                                        ▼
                     grype sbom:/tmp/x.json → matches[]
                                        │
                     for each match:    │
                                        ▼
                     process_sbom_entry(f, host):
                        │
                        ├─ MIN_CVSS filter (default 7.0) — dropped
                        │    UNLESS kev.is_known_exploited (BC-023 fix:
                        │    KEV bypass runs BEFORE cvss filter)
                        │
                        ├─ tier = classify_tier(cvss, kev,
                        │              internet_facing)   ← tier 0/1/2/3
                        │
                        ├─ finding_id = sha256(host, cve_id,
                        │               source_ref, package)[:16]
                        │     ← DEDUP: re-scans of the same (host,CVE,
                        │       target,package) tuple produce the same
                        │       id. INSERT OR IGNORE → silent on repeat.
                        │
                        ├─ save_finding → `findings` table row
                        │
                        └─ if row was NEW (ingest, not repeat):
                             ├─ emit `finding_ingested` audit event
                             ├─ per-run cap check
                             │     (BEACONRY_MAX_FINDINGS_PER_RUN=5)
                             │   ├─ under cap → notify()  ← first fire
                             │   └─ over cap  → suppressed → digest
                             └─ exposure/tier stored on row
                                (IMMUTABLE after this; a CVE that
                                 later becomes KEV won't be
                                 re-classified without a re-ingest)
```

**Scout is read-only forever** (per design-decisions.md § Separation of
concerns). It generates SBOMs via syft and posts; it never executes.

---

## 3. Timestamps on the finding row

The `findings` table carries seven time-column columns that drive display
and state. Each is an INTEGER unix timestamp; 0 means "unknown" (or
"no expiry" for `snooze_until_ts`).

| Column | Set when | Consumed by |
|---|---|---|
| `found_at` | First `save_finding` | Heartbeat body "📌 open since Xd" |
| `last_seen_ts` | Every `bump_last_seen` (per POST /scan where the finding appears) | BC-087 "Last seen: X ago" line in notification bodies; heartbeat liveness window gate |
| `last_notified_ts` | Every `notify*()` dispatch | Heartbeat sweep cooldown (`BEACONRY_HEARTBEAT_INTERVAL_S`) |
| `last_fixed_ts` | `_handle_fix` succeeds + `_handle_verify` succeeds | Regression body "fixed Xd ago, now back" |
| `auto_fixed_ts` | BC-086 counter close (`reconcile_scan` flip to `auto_fixed`) | Auto-fix regression body "auto-fixed Xd ago, now back" |
| `absent_count` | Per reconcile cycle where `in_scan=False` | BC-086 auto-fix threshold (`BEACONRY_AUTO_FIX_THRESHOLD=3`) |
| `snooze_until_ts` | `_handle_snooze` tap: `now + hours*3600` (BC-089). Cleared to 0 when sweep flips back to `open`, or if operator manually re-opens. | BC-089 `snooze_expiry_sweep` SQL filter `WHERE snooze_until_ts > 0 AND snooze_until_ts <= now`. `0` means "no expiry" — preserves pre-BC-089 legacy snoozes. |

The `bump_last_seen` call **also resets `absent_count=0`** for findings
present in the scan — see `hub.py:662`. This is why a finding that
disappears-then-reappears within fewer than `THRESHOLD` scans never
auto-closes: the counter resets each time it's seen.

---

## 4. Outbound notifications

Five production functions in `hub.py`, each wrapped by
`_dispatch_*` → `notifiers.notify_*` → adapter `send_*`:

| Trigger | Function | Body shape | Keyboard |
|---|---|---|---|
| First ingest of a new finding | `notify()` | Severity emoji + CVSS + host + target + package → fixed-version + description + fix cmd + "Last seen: just now" | [🔧 Fix now] + [⏰ Nh] × k + [✅ Accept risk] (BC-089: k = len(`SNOOZE_DEFAULT_HOURS`)) |
| Stale open finding past heartbeat window | `notify_heartbeat()` | Same body + "📌 open since Xd" + "Last seen: X ago" | Same shape as first-fire |
| Previously-fixed finding reappears (BC-031) | `notify_regression()` | 🔥 FIX REGRESSED header + "last fixed Xd ago" + "Last seen: X ago" | [🔧 Fix now] [✅ Accept risk] (no snooze for regressions) |
| auto_fixed finding reappears (BC-086) | `notify_auto_fix_regression()` | 🔥 AUTO-FIX REGRESSED header + "auto-fixed Xd ago, now back" + "Last seen: X ago" | Same as regression |
| Snoozed finding's expiry hits (BC-089) | `notify_snooze_expired()` | ⏰ SNOOZE EXPIRED header + "back on your radar" + severity + target + package + description | [🔧 Fix now] + [⏰ Nh] × k + [✅ Accept risk] (fresh first-fire shape) |
| Suppressed findings this run | `notify_digest()` | Per-host batch: "📋 N finding(s) suppressed" + table of (CVE / source_ref) | none |

**Last-seen line (BC-087, CAP-T180)** — every notification body now
carries a `Last seen: <just now | Nm ago | Nh ago | Nd ago>` line.
Loaded via `_load_last_seen(finding_id)` (hub.py:644) and threaded
through the adapter chain.

**Markdown v1 vs HTML** — bodies render as HTML (`parse_mode="HTML"`),
not Markdown. Chosen because v1 Markdown has no escape syntax for `*`,
`_`, backtick, `[` — raw CVE descriptions would break parsing.

---

## 5. Operator buttons + handlers

Inline keyboard callback_data format:

```
<action>:<finding_id>[:<extra>][:<cve-shape-segment>]
  action ∈ fix | snooze | ack | verify
  <extra>  = hours (for snooze)
  trailing CVE segment is back-compat for pre-BC-059 messages
```

Dispatch at hub.py:1626 (`on_button_click` → regex match →
`_handle_<action>`).

| Button | callback_data | Handler | What it does |
|---|---|---|---|
| **🔧 Fix now** | `fix:<fid>` | `_handle_fix` (hub.py:1635) | `edit_message_text("⏳ Working…")` → execute EXECUTOR adapter → `edit_message_text("✅ Fix dispatched · reverify at next scan")`. Status → `fixed`. Audit: `finding_fixed`. Keyboard removed by the edit. |
| **⏰ Nh × k** (BC-089) | `snooze:<fid>:<hours>` | `_handle_snooze` | Status → `snoozed`, `snooze_until_ts = now + hours*3600`, audit `finding_snoozed`. Reply names the exact expiry time. Keyboard removed. The `k` buttons come from `BEACONRY_SNOOZE_DEFAULT_HOURS` (default `1,4,24,168` → `1h/4h/1d/1w`). Expiry is honoured by `snooze_expiry_sweep` (see §4 — `notify_snooze_expired`). Legacy pre-BC-089 snoozes (`snooze_until_ts=0`) stay indefinite. |
| **✅ Accept risk** | `ack:<fid>` | `_handle_ack` | Status → `accepted`, audit `finding_accepted`. Finding stays in DB; reconcile tracks presence; no more alerts. |
| (regression only) **Verify** | `verify:<fid>` | `_handle_verify` (hub.py:1810) | Forced re-scan of the target; clean → `fixed_verified`, still present → `verification_failed`. |

**EXECUTOR adapter policy** (hub.py env `EXECUTOR`, default `docker`):

```
DockerAdapter (default)  — poc/hub/executor/docker_adapter.py
  pre-step: docker tag <source_ref> <source_ref>:rollback
  action:   docker pull <source_ref>   (same-track per BC-014)
  operator restarts the container themselves

ManualAdapter           — poc/hub/executor/manual_adapter.py
  logs the exact fix command, marks "manual action required"
  no filesystem or network side effects

(aspirational: AnsibleAdapter, NixAdapter, WebhookAdapter —
 none implemented; see §16.6)
```

---

## 6. Fix pipeline

```
Button tap → _handle_fix → edit_message_text("⏳ Working…")
                │
                ├─ EXECUTOR adapter.execute(finding):
                │     DockerAdapter → tag+pull same-track
                │     ManualAdapter → log cmd, no-op
                │
                ├─ status = "fixed"; audit `finding_fixed`
                │
                ├─ edit_message_text("✅ Fix dispatched ·
                │                     reverify at next scan")
                │
                └─ Verification happens in the NEXT regular scan
                   (scout cadence = 5 min default, not immediate):
                      ├─ finding absent from next scan
                      │       → status "fixed_verified"
                      │         audit `finding_fixed_verified`
                      └─ finding still present
                              → status "verification_failed"
                                audit `verification_failed`
                                OPERATOR MUST ACT (no auto-retry)
```

**Auto-rollback on verification_failed** (design-decisions.md §
Auto-fix trust model):
- Container: `docker tag <source_ref>:rollback <source_ref>` restores
  pre-fix image; operator restarts. The `:rollback` tag is the
  mandatory pre-step above.
- Package: design says "downgrade to previous version". **Not
  implemented** — see §16.2.

---

## 7. Health check waterfall

Per design-decisions.md § Auto-fix trust model. Each service has a
minimum level the operator sets; waterfall runs **in order**, falls
through on the first match:

1. **Docker HEALTHCHECK directive** — if the image defines one, use
   its exit code.
2. **HTTP probe** — auto-discovered from `ss -tlnp`; GET request,
   expect 200.
3. **Systemd stability window** — service stays active for 60s
   post-fix.
4. **Operator-defined custom check** — any shell command, exit 0 =
   pass.

Fails at the required level → auto-rollback (§6). No auto-fix dispatches
for services whose min-level is unset.

---

## 8. Reconcile scope — which statuses can transition

`reconcile_scan` (hub.py:683) pulls from DB:

```sql
WHERE host = ? AND status IN
  ('open', 'fixed', 'fixed_verified', 'regressed',
   'snoozed', 'accepted', 'auto_fixed')
```

- `verification_failed` is **excluded** — it's operator-owned, no
  auto-transition out.
- Per-row decision:
  - `open|regressed|snoozed|accepted + not in scan` → `absent_count++`
    → `auto_fixed` at threshold (silent, audit `finding_auto_fixed`)
  - `fixed|fixed_verified + in scan` → `regressed` (loud, audit
    `regressed`)
  - `auto_fixed + in scan` → `regressed_from_auto_fix` (loud, audit
    `regressed_from_auto_fix`)
  - `fixed|regressed + not in scan` → unchanged (operator decisions
    stay operator-owned)
  - everything else → counter reset via `bump_last_seen`

**Separate path for snoozed → open (BC-089):** the `snooze_expiry_
sweep` task (independent of reconcile, runs every
`BEACONRY_SNOOZE_SWEEP_INTERVAL_S`) does `SELECT ... WHERE
status='snoozed' AND snooze_until_ts > 0 AND snooze_until_ts <= now`,
flips to `open`, clears `snooze_until_ts`. This is the "snoozed → open
after Nh" path — reconcile does not handle it. Reconcile's
`snoozed + absent → auto_fixed` path still fires too; the two
mechanisms can race on a snooze whose target disappears during the
snooze window (whichever runs first wins; both are audited
distinctly).

**Reconcile fires on every POST /scan**, even if `scanned_ids` is empty
— `bump_last_seen` short-circuits (hub.py:671) but `reconcile_scan`
does NOT. An empty scan from a scout (e.g. scout crash mid-collect)
would tick every finding's `absent_count`. See §13 unclearity #3.

Guarded by `BEACONRY_RECONCILE_ENABLED=1` (default on); setting to 0
returns three empty lists immediately.

---

## 9. Isolation / scope guarantees

- **Per-host**: same CVE on `hostA` and `hostB` → two distinct rows
  (host is in the sha256 hash). A snooze on `hostA` does NOT silence
  `hostB`.
- **Per-target**: same CVE on `nginx:1.24` and `nginx:1.25` on one
  host → two distinct rows (source_ref is in the hash).
- **Per-package**: same CVE affecting `libssl1.1` and `libssl3` in the
  same image → two distinct rows (package is in the hash).
- **Reconcile scope is per-host**: a scan from `hostA` only
  touches `hostA`'s rows.
- **Never deleted**: no DB row is ever purged. "Fix" is a state
  transition, not a delete. The DB itself is the audit trail. This is
  what *"fix is not forget"* means — a regressed CVE (any path) is
  ALWAYS a loud alert, not silent re-ingestion.

---

## 10. Timers + cadences

| Knob | Default | Gates |
|---|---|---|
| `SCAN_INTERVAL` | **300 s (5 min)** | scout re-scans inventory + POSTs to hub |
| `SCOUT_HEARTBEAT_INTERVAL_S` | **60 s (1 min)** | scout keepalive → hub fleet view (BC-063) |
| `BEACONRY_AUTO_FIX_THRESHOLD` | **3 scans** (~15 min at default SCAN_INTERVAL) | consecutive absent before `open/snoozed/accepted/regressed → auto_fixed` |
| `BEACONRY_SNOOZE_SWEEP_INTERVAL_S` | **60 s (1 min)**, clamped ≥5 | BC-089 snooze-expiry sweep cadence; drift ceiling ≈ this value |
| `BEACONRY_SNOOZE_DEFAULT_HOURS` | **`1,4,24,168`** | BC-089 — hours offered as `[⏰ Nh]` inline keyboard buttons; parsed list |
| `BEACONRY_SNOOZE_NOTIFY_ON_EXPIRE` | **1** | BC-089 — `0` makes expiry re-open silent (audit still fires) |
| `BEACONRY_HEARTBEAT_SWEEP_INTERVAL_S` | **3600 s (1 h)** | how often hub walks the DB looking for stale open findings (BC-035) |
| `BEACONRY_HEARTBEAT_INTERVAL_S` | **604800 s (7 d)** | per-finding cooldown — same finding won't ping twice within this window |
| `BEACONRY_HEARTBEAT_LIVENESS_WINDOW_S` | **604800 s (7 d)** | findings whose `last_seen_ts` is older than this don't get heartbeats (the target is gone) |
| `BEACONRY_DIGEST_INTERVAL_S` | **86400 s (24 h)** | per-host digest cooldown (BC-036); content-hash bypass fires if suppressed set changed (BC-065) |
| `BEACONRY_MAX_FINDINGS_PER_RUN` | **5** | per-run cap on first-fire notifications; overflow → digest |
| KEV refresh | **3600 s (1 h)** | CISA KEV feed re-pull (BC-020) |
| `BEACONRY_LOG_LEVEL` | **INFO** | root log level for hub + scout (BC-087, CAP-T181) |
| Tier 0 (KEV) target | **15 min** | from CVE ingest to Telegram button (design) |
| Tier 1 (CVSS≥9 + internet-facing) target | **15 min** | same |
| Tier 2 (CVSS 7–8 or LAN-only) target | **1 h** | confirm button |
| Tier 3 (CVSS<7) target | **24 h** | nightly batch digest |

Every var is also documented in `docs/VARIABLES.md` with "where read"
pointers.

---

## 11. Audit events

Written to `/data/audit.log` as JSONL. Readable via `make audit
[EVENT=<name>]`.

| Event | Fired when |
|---|---|
| `finding_ingested` | New row created (not re-ingest) |
| `notify_sent` | Any `notify*()` dispatched (kind=`finding` \| `regression` \| `heartbeat` \| `auto_fix_regression` \| `snooze_expired` \| `digest`) |
| `notify_sent_failed` | A notification adapter raised; one failure doesn't block siblings (BC-035) |
| `finding_fixed` | `_handle_fix` succeeded (executor ran) |
| `finding_fixed_verified` | Re-scan confirmed the fix |
| `verification_failed` | Health waterfall or re-scan still shows CVE |
| `finding_snoozed` | Any `[⏰ Nh]` button; `snooze_until_ts = now + hours*3600` (BC-089) |
| `snooze_expired` | BC-089: sweep flipped `snoozed → open` at expiry |
| `finding_accepted` | [✅ Accept risk] |
| `finding_auto_fixed` | BC-086: counter hit threshold (silent — no Telegram) |
| `regressed` | BC-031: `fixed`/`fixed_verified` finding reappeared |
| `regressed_from_auto_fix` | BC-086: `auto_fixed` finding reappeared |
| `kev_cache_hit` | KEV lookup resolved from local cache |
| `heartbeat_reminder_sent` | Heartbeat sweep fired a reminder |
| `digest_throttled` | Digest skipped due to cooldown window |

---

## 12. Precedence at notify/transition time

```
Within reconcile_scan ordering (per host, per scan):
  1. regressed_from_auto_fix  (BC-086, loud, distinct body — see §15.1)
  2. regressed                (BC-031, loud)
  3. auto_fixed               (silent audit only)

Within the ingest/sweep loop (per scan, per host):
  4. verification_failed      (loud, from fix pipeline — direct, no
                               reconcile involvement)
  5. new finding ingested     → notify()                    ← first fire
  6. still open past
     HEARTBEAT_INTERVAL        → notify_heartbeat() at sweep
  7. snoozed + snooze_until   → snooze_expiry_sweep flips → open +
     past                        notify_snooze_expired (BC-089)
  8. over per-run cap          → digest at BC-036 interval or
                                 BC-065 content-change bypass
  9. snoozed (not yet expired) → no action
  10. accepted | fixed_verified| no action (unless reconcile flips them)
      | fixed
```

---

## 13. Unclearities

Questions that can't be answered from code + design-decisions.md alone.
Candidates for a future BC to resolve.

1. **Snoozed findings and reconcile vs BC-089 sweep race**: a `snoozed`
   finding whose target disappears during the snooze window has two
   paths back out: (a) BC-089 `snooze_expiry_sweep` flips it to `open`
   at `snooze_until_ts`, or (b) BC-086 reconcile ticks `absent_count`
   and silently flips to `auto_fixed` at threshold. Whichever runs
   first wins; both are audited distinctly. Unclear whether this race
   is intended or should have a tiebreak (e.g. "if target absent at
   expiry, prefer silent auto_fixed"). Today the operator sees whichever
   event fired — behaviour is well-defined per-run but not deterministic
   across instances. Code pointer: `hub.py:snooze_expiry_sweep` vs
   `hub.py:reconcile_scan` both run on separate async loops.

2. **Idempotency of button taps**: operator double-taps [🔧 Fix now].
   The handler edits the message, but the DB UPDATE and audit event
   happen twice. Unclear what the second UPDATE does — likely a no-op
   if status is already `fixed`, but the audit log will have duplicate
   `finding_fixed` events. No explicit guard.

3. **Empty scanned_ids reconcile behaviour** (§8): if a scout POSTs a
   scan with zero findings (crashed collector, host rebooted into a
   different image set), `reconcile_scan` treats every existing finding
   as absent. Unclear whether this is intended (host reporting empty =
   everything cleared) or a bug (host failed to collect = should not
   tick counters). No guard, no audit discrimination.

4. **Verification cadence vs target scan cadence**: verification happens
   on "the next regular scan" (§6) — but if the scout for a particular
   host is on a longer SCAN_INTERVAL, the verification window is
   proportionally longer. Fast-tier (Tier 0/1) 15-min targets rely on
   SCAN_INTERVAL=300. If an operator cranks `SCAN_INTERVAL=1800` for
   bandwidth reasons, Tier 0 misses its target. No warning.

5. **Reconcile + regression on auto_fixed → regressed_from_auto_fix
   vs ingest flow**: a finding in `auto_fixed` that reappears goes
   through `reconcile_scan`. But the **new scanned_ids** also go
   through `process_sbom_entry` which checks `save_finding INSERT OR
   IGNORE` — the row already exists, so ingest is silent, and the
   regression path handles the notification. What if reconcile_scan
   runs BEFORE the row's new-state is observable? Ordering appears
   correct in `hub.py:_ingest_candidates` but isn't explicitly
   documented.

6. **Tier recalculation on state change**: `tier` is set at ingest time
   and never re-evaluated (§2 "IMMUTABLE after this"). A CVE that
   gets KEV-listed a week post-ingest keeps its old Tier 2/3 — no
   backfill. Unclear if this is a deliberate design choice (ingest is
   authoritative) or a known caveat.

---

## 14. Necessary improvements

Non-bug enhancements that the lifecycle would benefit from. Each is
a candidate future BC.

1. **Per-module log level** (BC-087 left this deferred):
   `BEACONRY_LOG_LEVEL` is a single global dial. Operator can't say
   "DEBUG on reconcile but INFO everywhere else". Current workaround:
   grep `log.debug` output after setting global DEBUG.

2. **Daily full rescan** (design-decisions.md § Inventory cadence)
   mentions "Daily full rescan: 02:00 host-local time — catches pip
   installs, port changes, drift". Not implemented — scout only has
   the `SCAN_INTERVAL` loop. If a package installs between regular
   scans and is uninstalled before the next one, the CVE is never
   visible. See §16.3.

3. **Real-time push** (design-decisions.md same section) describes
   "pacman post-transaction hook + docker events listener → scout
   queues delta". Not implemented. Same risk as #2 at smaller scale.

4. **Snooze with real expiry** (see §13 bug 1 — implementation
   needed, not a bug per se since the feature was never fully built).
   Proposed: `snooze_until_ts` column, sweep, re-fire notification.
   Noted in IDEAS.md.

5. **Canary ring** (design-decisions.md § Multi-host canary ring):
   Ring 0 workstation → Ring 1 staging → Ring 2 production, with
   health-check gate between rings. Design is detailed; no code
   wires a ring attribute, let alone the ordering.

6. **Verification without full re-scan**: today verification waits
   for the next cadenced scan (up to 5 min). An on-demand verify
   endpoint (POST /verify-finding/<id> → triggers a targeted re-grype
   of just that source_ref) would shrink fix→verify to seconds.

7. **MIN_CVSS re-evaluation on KEV update**: when the daily KEV feed
   adds a CVE that's already in the DB as `open` but below MIN_CVSS,
   there's no trigger to re-evaluate. The finding was dropped at
   ingest. This is the §13 unclearity #6 but formalised as a desired
   fix.

---

## 15. Contradictions / inconsistencies

Where the design doc says one thing, the code does another; or where
code paths disagree with each other.

1. **`notify_auto_fix_regression` body wire-up** — **high priority
   bug, code-grep confirmed:**

   - `notify_auto_fix_regression` (hub.py:1567) only uses
     `format_auto_fix_regression` on the MOCK/no-adapter path
     (hub.py:1580).
   - The real production path at hub.py:1589 does
     `await _dispatch_regression(finding, auto_fixed_ts, last_seen_ts)`.
   - `_dispatch_regression` → `notifiers.notify_regression` →
     `TelegramAdapter.send_regression` (notifiers/telegram.py:68),
     which calls `self._fmt["regression"](finding, last_fixed_ts, ...)`
     — i.e. `format_regression`, **not** `format_auto_fix_regression`.
   - Comment at hub.py:1586 claims "the text body is already shaped by
     `format_auto_fix_regression` upstream" — **false for the Telegram
     path**. True only for the MOCK path.

   **Operator-visible effect:** auto-fix regressions on Telegram look
   identical to BC-031 regressions. The distinct "🔥 AUTO-FIX
   REGRESSED" body (the whole point of BC-086's CAP-T178) never
   reaches a real operator.

   Fix candidate: either (a) rename adapter method
   `send_auto_fix_regression` with its own formatter key, or (b) pass
   a `formatter_key: str` through the adapter dispatch.

2. **Scout "read-only forever" vs scout initiating fix**: design-
   decisions.md § Separation of concerns says "Scout is read-only
   forever. It generates SBOMs via syft, posts to hub. It never
   executes fixes." Code matches — scout has no executor dependency.
   No contradiction, but worth confirming since the design doc
   capitalises it and some operators ask "why can't my local scout
   fix my local docker?". The answer is architectural, not technical.

3. **Finding ID stability vs reconcile symmetry**: `finding_id`
   includes `host`, so the same CVE on two hosts is two rows (good).
   But reconcile is scoped to a single host's scan payload
   (`host`-parameterised SQL). If a scout for `hostA` reports and
   reconcile fires, findings on `hostB` are untouched. If scout for
   `hostA` ALSO reports a finding that happens to be on `hostB`
   (shouldn't happen — scout only sees its own host), it would create
   a row with `host=hostA` for hostB's CVE. Not a current bug (scout
   topology prevents it), but a fragile invariant.

---

## 16. Missing implementation

Behaviour described in design-decisions.md (locked 2026-10-29) that
has no code backing. Each is a future BC candidate.

1. **Canary ring** (§14.5 above): Ring 0 workstation → Ring 1 staging
   → Ring 2 production. Design is complete; code has zero
   implementation. No `role` attribute on scout config, no ring
   ordering in `_handle_fix`.

2. **Package auto-rollback via ZFS snapshot**: design-decisions.md
   § Auto-fix trust model says "ZFS snapshot (packages) OR `:rollback`
   tag (containers) taken first". Container path exists (DockerAdapter
   pre-tag). ZFS path: no code. No pre-check that ZFS is even
   present. If a future adapter does package fixes, there's no
   safety net.

3. **Daily full rescan at 02:00 host-local** (§14.2): design-
   decisions.md § Inventory cadence. No scheduler, no cron-equivalent.
   Scout runs `SCAN_INTERVAL` loop only.

4. **Real-time push via pacman hook + docker events** (§14.3): same
   section of design doc. Nothing in scout code listens to docker
   events; no pacman hook script shipped. Operator who installs
   beaconry on an Arch host doesn't get a `pacman -S` → instant scan
   trigger.

5. **Health check waterfall levels 3–4**: §7 describes four levels.
   `poc/hub/waterfall.py` exists (CAP-B023 related) but its level-3
   (systemd stability) and level-4 (operator-defined) paths are
   partially stubbed. Needs audit — not obvious which level is
   actually consulted on a real `_handle_fix` dispatch.

6. **Executor adapters beyond docker/manual**: `AnsibleAdapter`,
   `NixAdapter`, `WebhookAdapter` mentioned in design-decisions.md §
   Separation of concerns. None exist. Operators on Ansible-managed
   fleets must fall back to ManualAdapter.

7. ~~Snoozed → open on expiry~~ — **Shipped in BC-089** (CAP-T183).
   `snooze_until_ts` column + `snooze_expiry_sweep` + distinct
   `notify_snooze_expired` body. Multi-button keyboard via
   `BEACONRY_SNOOZE_DEFAULT_HOURS` (CAP-T184).

---

## 17. Known bugs

Reproducible defects with operator-visible effect. Code-grep or
monitor-verified.

1. ~~Snooze 4h button doesn't actually snooze for 4h~~ — **Fixed in
   BC-089** (CAP-T183). `snooze_until_ts` column stores the expiry;
   `snooze_expiry_sweep` re-opens at that time and fires
   `notify_snooze_expired` (unless `BEACONRY_SNOOZE_NOTIFY_ON_EXPIRE=0`).
   Pre-BC-089 snoozes (`snooze_until_ts=0`) stay indefinite —
   legacy state preserved.

2. **Auto-fix regression body renders as BC-031 regression body on
   Telegram** — §15.1 above. Code-grep confirmed. The distinct
   "🔥 AUTO-FIX REGRESSED" body is dead code on the real Telegram
   path. Tracked for BC-090.

3. **KEV refresh hourly 403 on hetzner-staging**: CISA KEV endpoint
   returns 403 Forbidden on every hourly refresh attempt from the
   hetzner hub (monitor stream 2026-11-05 07:55 onward, hourly to
   10:55+). `/healthz` shows `"kev": "unfetched", "kev_count": 0`.
   Cache fallback works short-term, but if CISA stays locked out
   past the cache TTL, Tier 0 KEV classification degrades to
   CVSS-only. Suspected cause: CISA started enforcing a User-Agent;
   fix is `User-Agent: beaconry/<version> (+repo)` on the GET in
   `poc/hub/kev.py`. Tracked for BC-091.

4. **BC-086's initial-scan counter stall** (BC-086 ship-verification
   anomaly): 11 findings observed stuck at `absent_count=2` after 3
   post-upgrade scans (expected 3 → `auto_fixed`). Manual probe
   flipped them. Hypothesis: first-post-upgrade-scan race. Not
   reproducible after shipping the BC-086 close-gaps diagnostic
   logging. Shipped as a risk-accepted known unknown; subsequent
   natural progression on bb-82jq worked 3-for-3. Flagged here so
   next investigator has the pointer.

---

## How to use this document

- **New contributor**: read sections 1 through 12 top-to-bottom.
- **Operator debugging a weird state**: jump to §8 (reconcile) or §4
  (notifications) depending on symptom.
- **Proposing a new BC**: cross-reference §13–§17. If your proposal
  is already listed, link to it in the plan doc's "Links" section.
- **Changing a lifecycle behaviour**: update this doc as part of your
  BC's Definition of Done. No `verify-lifecycle-drift` lint today
  — the honour system applies.

## Related references

- `memory/design-decisions.md` — locked 2026-10-29. The design source
  of truth; this doc is the code source of truth. §15–§17 cross-
  reference them.
- `docs/VARIABLES.md` — every env var in §10 has a full row there.
- `docs/CAPABILITIES.md` — CAP-T176 through T181 are the most
  recent lifecycle-adjacent caps. This doc is CAP-T182.
- `docs/plan/IDEAS.md` — pre-BC backlog; BC-088 formalises the subset
  of IDEAS that's lifecycle-shaped.

---

*Maintenance: if you add a new finding status, a new notification
function, a new button, or a new timer env var, update sections 1,
4, 5, 10 respectively. If you fix one of the issues in §13–§17,
remove the entry and open a BC that cites this doc in its plan
"Links" section.*
