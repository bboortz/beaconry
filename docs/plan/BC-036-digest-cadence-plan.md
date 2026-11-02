# BC-036 — Decouple digest cadence from scan cadence

*Branch: `feat/BC-036-digest-cadence`*
*Start date: 2026-10-31*
*Story: `docs/stories/BC-036-digest-cadence.md`*

---

## Situation

Hub fires `send_digest(suppressed)` every scan that has non-empty suppressed findings. With `SCAN_INTERVAL=300s`, operators with cap-overflow inventories get the same `📋 53 additional finding(s) suppressed this run` message every 5 min — 288× per day. No way to throttle without also slowing the first-fire path for new CVEs.

Heartbeat (BC-035) already shipped the "cadence decoupled from scan" pattern; this story applies it to the digest.

---

## Problems to solve

- Digest cadence is tied 1:1 to scan cadence.
- No way to distinguish "same 53 CVEs again" from "the suppressed set actually changed".
- Operators either accept the flood or raise SCAN_INTERVAL (hurting correctness latency on first-fire).

---

## Goals

1. Add `BEACONRY_DIGEST_INTERVAL_S` (default `86400` = 24h). Hub skips digest sends inside the window unless content changed.
2. Content-hash fallback: if the set of suppressed CVE ids *actually changes*, send sooner. Prevents stale digests from blocking fresh alerts.
3. Preserve BC-022 empty-set short-circuit (no digest if no suppressed).
4. Preserve operator opt-out — `BEACONRY_DIGEST_INTERVAL_S=0` restores pre-BC-036 behavior.
5. First-fire notifications unaffected — digest throttle is strictly digest-local.

---

## Scope

- `poc/hub/hub.py`:
  - New env var `DIGEST_INTERVAL_S`.
  - New `digest_state` table (schema below) + idempotent migration.
  - New `_digest_hash(suppressed) -> str` pure helper.
  - New `should_send_digest(host, suppressed, now) -> tuple[bool, str, str]` policy function returning (should_send, hash, reason).
  - `receive_scan`: wrap the `send_digest` call in the gate; update digest_state on send; emit audit events for both send and throttle.
- `poc/docker-compose.yml` — propagate `BEACONRY_DIGEST_INTERVAL_S`.
- `poc/.env.example` — document the new var.
- `poc/tests/unit/test_digest_cadence.py` — all 7 Gherkin scenarios as unit tests over the policy + DB plumbing.
- `docs/CAPABILITIES.md` — register CAP-T080.
- `docs/stories/INDEX.md` — BC-036 row.

### Schema addition

```sql
CREATE TABLE IF NOT EXISTS digest_state (
    host         TEXT PRIMARY KEY,
    last_sent_ts INTEGER NOT NULL,
    last_hash    TEXT NOT NULL
);
```

Separate table (not a column on `findings`) because digest cadence is per-host state, not per-finding.

---

## Non-goals

- Per-adapter cadence (email digest cadence ≠ Telegram digest cadence). Gating lives at the hub-wide level; all adapters see the throttled signal.
- Interactive "snooze digest" button. Env toggle is the V1 dial.
- Multi-host aggregation. Each host tracks independently.
- Throttling first-fire or regression notifications. Both are correctness-latency-sensitive.

---

## Design decisions

- **Content hash is a sorted join of finding ids**: `sha256(":".join(sorted(f.id for f in suppressed)))[:16]`. Stable across scans with the same set, flips on any add/remove. CVE id alone would miss "same CVE, different host" shifts; `f.id` already encodes (host, cve, source_ref, package).
- **Interval gate OR content change — not AND**: either path triggers a send. The alternative ("only send if BOTH interval elapsed AND content changed") would mean a stable set gets zero digests forever, which breaks the "periodic nudge" value of the digest.
- **`DIGEST_INTERVAL_S=0` means "never throttle"**: parallels `BEACONRY_HEARTBEAT_ENABLED=0`'s dial semantics, but inverted (0 is OFF for enable flags, 0 is OFF-the-throttle here). Documented clearly in `.env.example`.
- **Per-host state in SQLite**: survives hub restart; a /data file would too but SQLite is already the standard. Rejected: in-memory dict — would re-send on every restart.

---

## Breaking changes

### 1. API / return shape

**None.** `send_digest` signature unchanged. `receive_scan` return shape unchanged. The policy gate wraps the call site inside `receive_scan` — no external callers.

### 2. Data model (schema + legacy rows)

New table `digest_state`. Idempotent `CREATE TABLE IF NOT EXISTS` in `init_db` — no pre-existing rows (new table), no backfill needed. First post-BC-036 scan on each host creates its row.

### 3. Behavioral semantics

- **Operators on default config**: digest cadence drops from "every scan" (288/day at SCAN_INTERVAL=300) to "once per 24h unless the set changes". **This is the whole point.** Operators who liked the old behavior opt back in with `BEACONRY_DIGEST_INTERVAL_S=0`.
- **Operators who already run SCAN_INTERVAL=86400 or higher**: no change — they were already getting ≤1 digest/day; the new throttle sits above their effective rate.
- **Edge case — operator snoozes a finding mid-interval**: snoozed findings drop from the suppressed set → content hash flips → next scan fires a fresh digest. The operator-action is implicitly acknowledged, not swallowed by the throttle.
- **No implicit disable**: default is throttle ON. Opt-out requires an explicit env change.

**If any breaking change is discovered during implementation or testing**: fix in-branch per [[breaking-changes-in-planning]]. The 24h default is a judgement call; if operator testing reveals it's wrong, flip to a smaller default (e.g. 6h) rather than add complexity.

---

## Merge plan

1. All work on `feat/BC-036-digest-cadence`.
2. `make check` + `make check-full` must pass on branch tip.
3. PR to `dev`; Claude opens, user runs `/ultrareview`, Claude runs `/code-review`.
4. Merge strategy: fast-forward.
5. Post-merge: story → `shipped`, update INDEX, `make live-rebuild`.

---

## Rollout plan

Additive. On upgrade, operators get a quieter digest. If they *want* the old flood for a reason I haven't thought of, `BEACONRY_DIGEST_INTERVAL_S=0` in `.env` + restart. First post-upgrade scan creates the per-host `digest_state` row.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| First digest in a window fires | unit | `test_digest_cadence.py::test_first_digest_in_window_fires` | ✓ |
| Repeat within interval, same set → no digest | unit | `test_digest_cadence.py::test_repeat_within_interval_same_set_skipped` | ✓ |
| Set changes within interval → digest fires | unit | `test_digest_cadence.py::test_set_change_bypasses_interval_when_enabled` | ✓ |
| Interval elapsed, same set → digest fires | unit | `test_digest_cadence.py::test_interval_elapsed_fires_even_unchanged` | ✓ |
| INTERVAL_S=0 → no throttling | unit | `test_digest_cadence.py::test_interval_zero_never_throttles` | ✓ |
| Empty suppressed → never fires (BC-022 regression guard) | unit | `test_digest_cadence.py::test_empty_set_never_fires` | ✓ |
| Audit log carries both `digest_sent` + `digest_throttled` events | unit | `test_digest_cadence.py::test_audit_events_cover_both_paths` | ✓ |
| `_digest_hash` is stable + order-independent + sensitive to set changes | unit | `test_digest_cadence.py::test_digest_hash_properties` | ✓ |
| `digest_state` table + idempotent migration | unit | `test_digest_cadence.py::test_digest_state_table_migration` | ✓ |
| CAP-T080 registered + bidirectional | verify-traceability | `docs/CAPABILITIES.md` diff | ✓ |
| Compose env propagation | lint | `make verify-compose-env-propagation` | ✓ |
| `.env.example` documents `BEACONRY_DIGEST_INTERVAL_S` | manual | `poc/.env.example` entry | ✓ |
| make check green | multi-gate | `make check` | ✓ |
| Memory drift pattern added if applicable | N/A | no documented behavior deprecated | ✓ |

Legend: ✓ covered · ⚠️ partial · ❌ open (blocks shipping).

---

## Testing strategy

Unit-only — the policy function (`should_send_digest`) is pure given (host, suppressed, now, env). The DB write path is tested with the standard `tmp_path + monkeypatch.setattr(hub, "DB_PATH", ...)` fixture pattern used in test_heartbeat_sweep.py.

Integration coverage falls out of existing BC-022 digest tests (`test_hub_helpers.py::_capture_digest`) — they'll continue asserting the digest body renders correctly, and now additionally that the throttle doesn't accidentally suppress them (fixture-fresh DB → first digest always fires).

---

## Testing commands

```bash
make check
pytest poc/tests/unit/test_digest_cadence.py -v
```
