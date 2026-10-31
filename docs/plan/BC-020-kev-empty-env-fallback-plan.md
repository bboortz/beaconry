# BC-020 close-gap — KEV empty-env fallback

*Branch: `fix/BC-020-kev-empty-env-fallback`*
*Date: 2026-10-31*
*Parent: `docs/plan/BC-020-kev-ingest-plan.md`*

---

## Situation

Live diagnostic on the rebuilt `beaconry-hub` after BC-020 merge showed:

```
[hub] WARNING KEV fetch failed: unknown url type: '' — falling back to cache
/healthz "kev": "unfetched", "kev_count": 0
```

Root cause: `poc/hub/kev.py` reads `FEED_URL = os.environ.get("BEACONRY_KEV_FEED_URL", DEFAULT_FEED_URL)`. `docker-compose.yml` sets the env via `BEACONRY_KEV_FEED_URL=${BEACONRY_KEV_FEED_URL:-}` — the shell `${VAR:-}` expansion produces an **empty string** when VAR is unset (not an unset variable). `os.environ.get(VAR, default)` returns `""` when VAR is `""`, not `default`. urllib opens `""` → `unknown url type: ''`.

Impact: every production hub boot with no explicit override has `FEED_URL=""`, fetch loop fails every hour, `/healthz` reports `kev: unfetched` forever, every finding falls through to CVSS tiering. Tier 0 is unreachable in a default compose deployment.

The unit test `test_telegram_base_url_env_overrides_default` already covered the symmetric case (explicit non-empty override) — but no test covered the empty-string case.

## Fix

One-line change in `kev.py`:

```python
FEED_URL = os.environ.get("BEACONRY_KEV_FEED_URL") or DEFAULT_FEED_URL
```

`or` treats empty-string as falsy → falls through to default. Comment left in-source explaining the compose idiom that caused this.

## Scope

- **Modified:** `poc/hub/kev.py` — one line + a WHY comment.
- **New:** `poc/tests/unit/test_kev.py::test_empty_env_feed_url_falls_back_to_default` — regression guard.

## Breaking changes

None.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Empty-env `BEACONRY_KEV_FEED_URL=""` falls back to DEFAULT_FEED_URL | unit test | `test_kev.py::test_empty_env_feed_url_falls_back_to_default` | ✓ |
| Live hub rebuild + /healthz shows `kev: ok` + `kev_count > 0` | live manual | 2026-10-31 — see rollout plan | ✓ |
| `make check` green | multi-gate | `make check` | ✓ |

## Rollout plan

Rebuild + redeploy the hub container to pick up the fix:

```bash
cd poc && docker compose up -d --build hub
sleep 30  # KEV fetch runs in background; CISA responds ~2s
curl -s http://localhost:8000/healthz | jq '.checks | {kev, kev_count}'
# Expected: {"kev": "ok", "kev_count": ~1300}
```
