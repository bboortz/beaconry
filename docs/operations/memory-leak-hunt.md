# Memory-leak investigation playbook

**When to run this**: `beaconry status` DETAILS on the hub row shows
`rss` climbing over time (BC-142), or `/healthz checks.rss_mb` is
trending up, or `make perf` fails on the Memory-check row (BC-144).

**What this covers**: how to enable `tracemalloc` on a live hub,
capture before/after snapshots, diff, and identify the source file +
line responsible for the growth. Cross-references the top candidate
leak classes from the 2026-11-17 incident so you can match a diff
line to a known suspect.

---

## Quick reference

| Step | Command |
|---|---|
| 1. Enable tracemalloc | Set `BEACONRY_TRACEMALLOC=1` in `.env` + redeploy, OR exec: `BEACONRY_TRACEMALLOC=1 docker compose up -d --force-recreate hub` |
| 2. Confirm it's on | `curl -s $HUB/healthz \| jq .checks.tracemalloc_enabled` → `true` |
| 3. Snapshot T0 | `curl -X POST $HUB/_tracemalloc/snapshot?label=t0` (CLI-token auth) |
| 4. Wait one scan cycle | ~5 min default; verify via `beaconry audit --event scan_integrity --last 1` |
| 5. Diff | `curl $HUB/_tracemalloc/diff?label=t0 \| jq` |
| 6. Fix the top grower | map file:lineno to the live BC list below |
| 7. Disable tracemalloc | Unset `BEACONRY_TRACEMALLOC` + redeploy — don't leave it on; ~10-20% CPU overhead |

---

## Step 1 — enable tracemalloc

```bash
# On the host (hetzner-staging), edit /opt/beaconry/.env (or wherever
# the systemd beaconry-hub.service reads env from):
BEACONRY_TRACEMALLOC=1

# Restart:
sudo systemctl restart beaconry-hub
```

Overhead: ~10-20% CPU on the hub, ~5-10% baseline RAM (tracemalloc
tracks every allocation; traces cost memory too). Worth it ONLY
during an investigation window.

## Step 2 — confirm

```bash
curl -s "$HUB/healthz" | jq .checks.tracemalloc_enabled
# → true
```

If false: either the env var didn't take effect (re-check how
systemd loads env), or the hub needs a restart.

## Step 3 — snapshot t0

```bash
TOKEN=$(yq .hub_token ~/.config/beaconry/cli.yml)
curl -X POST -H "Authorization: Bearer $TOKEN" \
    "$HUB/_tracemalloc/snapshot?label=t0"
```

This freezes the current allocation set under the label `t0`.

## Step 4 — wait one scan cycle

Scouts scan every 5 min (SCAN_INTERVAL=300). Wait ~5-10 min to let
the leak accumulate.

```bash
# verify scans happened:
beaconry audit --event scan_integrity --last 3
```

## Step 5 — diff

```bash
curl -s -H "Authorization: Bearer $TOKEN" \
    "$HUB/_tracemalloc/diff?label=t0&limit=20" | jq .top_growers
```

Output example:
```json
{
  "top_growers": [
    {
      "file": "/app/hub/hub.py",
      "lineno": 2145,
      "size_diff_mb": 42.3,
      "count_diff": 1203,
      "size_mb": 85.4,
      "count": 2341
    },
    ...
  ]
}
```

**`size_diff_mb` > 10 MB per 5-min cycle is the signal.** Multiple
entries from the same file are usually the same underlying leak
surfacing via different allocators.

## Step 6 — map to a known suspect

Cross-reference the top grower's `file:lineno` to the 2026-11-17
candidate list:

### Suspect A — BC-104 notify retry queue unbounded (HIGH)
- **Signature**: growth in `poc/hub/hub.py` around the
  `_pending_notifications` / `notify_retry_loop` functions.
- **Symptom**: `beaconry audit --event notify_failed --last 1` shows
  recent entries with the same `finding_id` repeating.
- **Fix**: bound the retry queue size to ~1000 with drop-oldest
  semantics. Needs a BC story.

### Suspect B — asyncio fire-and-forget regression tasks (HIGH)
- **Signature**: growth in `asyncio` internals (`tasks.py`) or
  `_dispatch_regression_notifications`.
- **Symptom**: `docker exec hub ps auxf` shows many python threads;
  `curl -s $HUB/_tracemalloc/top | jq .top` lists
  `_dispatch_regression_notifications` high.
- **Fix**: wrap with `asyncio.wait_for(timeout=30)` so hung Telegram
  calls can't hold references forever.

### Suspect C — KEV refresh urllib response leak (MEDIUM)
- **Signature**: growth in `urllib/request.py` or `kev.py`'s
  `_http_fetch`. Especially likely when `/healthz checks.kev` is
  `unfetched` (retry loop active).
- **Fix**: explicit `response.close()` + `with contextlib.closing(...)`
  in the fetch helper. Needs a BC story.

### Suspect D — python-telegram-bot update queue (MEDIUM)
- **Signature**: growth in `telegram/ext/_updater.py` or
  `_application.py`.
- **Symptom**: callbacks may be raising exceptions (check
  `docker logs hub | grep ERROR`).
- **Fix**: add exception handler on `on_button_click`; ensure every
  `callback_query.answer()` is called even on early return.

### Suspect E — SBOM payload retention in /scan handler (LOW)
- **Signature**: growth in `fastapi/` or around `_ingest_candidates`.
- **Symptom**: hub RSS tracks scout scan cadence (climbs on scan,
  flat between).
- **Fix**: explicitly `del payload` after extracting the fields we
  need; convert to streaming parse if that doesn't suffice.

### Unknown — new leak class
If the top grower doesn't match any of A-E: capture the full
traceback with
`curl $HUB/_tracemalloc/top?limit=200 | jq` and open a BC story
with the top entry's file:lineno + stack as evidence.

## Step 7 — disable tracemalloc

Don't forget this. Each tracemalloc frame costs ~50 bytes resident
plus the per-allocation overhead; a long-running investigation can
itself trigger a BC-141 container-OOM via the diagnostic tool.

```bash
# Remove BEACONRY_TRACEMALLOC=1 from .env
sudo systemctl restart beaconry-hub
# Confirm:
curl -s "$HUB/healthz" | jq .checks.tracemalloc_enabled  # → false
```

---

## Related

- **BC-141** — container memory caps. During the investigation, if
  the hub OOMs, docker kills just the container (host stays up).
- **BC-142** — `/healthz` RSS metric — the signal that triggers this
  playbook.
- **BC-144** — `make perf` memory-growth assertion. Once you've
  fixed the leak, BC-144 keeps future BCs from reintroducing it.
