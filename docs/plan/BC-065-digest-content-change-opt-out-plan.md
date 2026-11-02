# BC-065 — Digest content-change throttle opt-out

*Branch: `fix/BC-065-digest-content-change-opt-out`*
*Start: 2026-11-02*

## Symptom

Operator reports the suppressed-findings digest firing on nearly
every scan in the homelab deployment. `BEACONRY_DIGEST_INTERVAL_S`
defaults to 86400s (24h) — the operator expected at most one digest
per host per day. In practice they see several per hour.

## Root cause

`should_send_digest()` in `poc/hub/hub.py` (lines 2150–2183) runs
these checks in order:

1. `empty` → never send
2. `DIGEST_INTERVAL_S == 0` → always send
3. No prior send → `first_fire`
4. **`last_hash != content_hash` → `content_changed`** ← fires here
5. `now - last_sent_ts >= DIGEST_INTERVAL_S` → `interval_elapsed`

The `content_changed` branch at step 4 **short-circuits before the
interval check**. In a stack where new CVEs regularly rotate through
the capped tail (post-BC-005 dedup + MAX_FINDINGS_PER_RUN=5), the hash
changes every scan and the throttle at step 5 is never reached.

Policy originally shipped in BC-036 (digest content hash) assumed set
turnover was rare. In an actively ingesting homelab it isn't.

## Fix

Config-driven, not a behaviour removal. Operators who want the
current behaviour keep it; operators who want strict 24h throttling
opt out.

### New env var

`BEACONRY_DIGEST_ON_CONTENT_CHANGE`:
- unset or `1` → current behaviour (content change bypasses throttle)
- `0` → content change ignored; only `first_fire` / `interval_elapsed`
  can trigger a send

Default `1` preserves backwards compat for existing deployments. The
homelab's noisy stack will set it to `0` via Ansible.

### Code change

```python
# ~line 130, near DIGEST_INTERVAL_S
DIGEST_ON_CONTENT_CHANGE = os.environ.get("BEACONRY_DIGEST_ON_CONTENT_CHANGE", "1") != "0"
```

```python
# ~line 2179, inside should_send_digest()
if DIGEST_ON_CONTENT_CHANGE and last_hash != content_hash:
    return True, content_hash, "content_changed"
```

### Test

Extend `test_set_change_bypasses_interval` in
`poc/tests/unit/test_digest_cadence.py` to cover both states.
Parameterise or add a mirror test — the opted-out case must return
`throttled` (same reason as the "unchanged within interval" path), not
some new reason, so the audit log stays coherent.

### Documentation

- `docs/VARIABLES.md`: row documenting the new env var + rationale.
- `poc/.env.example`: operator-visible default comment.
- `poc/docker-compose.yml`: propagate from host env with
  `${BEACONRY_DIGEST_ON_CONTENT_CHANGE:-}` (same pattern as other
  digest knobs).

### Version

`0.2.1 → 0.2.2`. Patch — additive env var, default preserves behaviour.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Content change still fires when enabled (default) | unit | `test_digest_cadence.py::test_set_change_bypasses_interval_when_enabled` | ✓ |
| Content change throttled when disabled | unit | `test_digest_cadence.py::test_set_change_respects_interval_when_disabled` | ✓ |
| Interval-elapsed still fires regardless of flag | unit | `test_digest_cadence.py::test_interval_elapsed_fires_when_content_change_disabled` | ✓ |
| First-fire still fires regardless of flag | unit | `test_digest_cadence.py::test_first_fire_ignores_content_change_flag` | ✓ |
| VARIABLES.md row | verify-variables | `docs/VARIABLES.md` | ✓ |
| docker-compose propagates env | verify-compose-env-propagation | `poc/docker-compose.yml` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |

## Breaking changes

None. Default `1` = current behaviour.

## Rollout

- `make bump-patch` → 0.2.2.
- Merge to dev.
- Homelab: Ansible role sets `BEACONRY_DIGEST_ON_CONTENT_CHANGE=0` for
  this specific operator. Separate PR in `/home/benni/git/homelab`.
