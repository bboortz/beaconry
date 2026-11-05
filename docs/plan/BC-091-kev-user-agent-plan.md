# BC-091 — KEV fetch uses identifiable User-Agent

*Branch: `feat/BC-091-kev-user-agent`*
*Start: 2026-11-05*
*Story: `docs/stories/BC-091-kev-user-agent.md`*

---

## Situation

CISA KEV endpoint 403s hourly on hetzner-staging. `_http_fetch`
currently sends `User-Agent: beaconry/0.1 (+kev)` — hardcoded
pre-0.1 version string from early scaffolding, no repo reference,
no contact. CISA's "Policy" page requests identifiable UAs with
contact URLs; anonymous bot-ish fetches are throttled or blocked.

## Problems to solve

- Hetzner hub's `/healthz` shows `"kev": "unfetched"` — operator
  loses confidence that Tier 0 classification works.
- Stale cache past 25h means KEV lookups degrade silently to
  CVSS-only — a Tier 0 KEV CVE could go undetected.
- No operator escape hatch for proxied / air-gapped setups.

## Goals

1. Default UA names real version + repo — Scenarios 1, 4.
2. Operator override via `BEACONRY_KEV_USER_AGENT` — Scenario 2.
3. Empty env falls back to default — Scenario 3.

## Scope

- `poc/hub/kev.py`:
  - Import `SOFTWARE_VERSION` from `beaconry_health` (dual path:
    container `/app`, host `poc.beaconry_health`).
  - `DEFAULT_KEV_USER_AGENT = f"beaconry/{SOFTWARE_VERSION}
    (+https://github.com/bboortz/beaconry)"`
  - `KEV_USER_AGENT = os.environ.get("BEACONRY_KEV_USER_AGENT") or
    DEFAULT_KEV_USER_AGENT`
  - `_http_fetch` reads module-level `KEV_USER_AGENT`.
- `docs/VARIABLES.md`, `poc/.env.example`, `poc/docker-compose.yml`:
  one new row.
- `docs/CAPABILITIES.md`: CAP-T187.
- `docs/stories/INDEX.md`: BC-091 row.
- `CHANGELOG.md`: v0.14.1 entry.
- Tests: `poc/tests/unit/test_BC_091_kev_user_agent.py` — 4
  scenarios + reload safety.
- Version 0.14.0 → **0.14.1** (patch — bug fix for CISA 403;
  the env var is a minor override knob, not a user-visible
  feature, so patch is appropriate).

## Non-goals

- Changing cache-fallback semantics.
- Adding retry/backoff on 403.
- Supporting multiple KEV feed mirrors.

## Design decisions

- **Dynamic version vs frozen string**: use `SOFTWARE_VERSION` so
  UA always tracks the release. CISA is more likely to allow a
  UA that's visibly maintained. Rejected: `beaconry (+repo)` with
  no version — loses forensic utility when a specific version has
  a KEV fetch bug.
- **Env var vs config file**: env. Matches every other knob in
  `docs/VARIABLES.md`. One dial.
- **Module-level constant vs per-call env read**: module-level
  (same pattern as `FEED_URL`). Operator restarts hub to change.
  Tradeoff: no hot-reload, but no risk of racy per-fetch reads
  either. Matches the rest of the hub's env handling.

## Breaking changes

### API / return shape

None — no caller interface changes. `_http_fetch(url: str) ->
bytes` signature unchanged.

### Data model

None — no schema changes.

### Behavioral semantics

- CISA accepts the identifiable UA → `_cve_ids` populates →
  Tier 0 classification works. **This is the fix.**
- If CISA was already accepting the old UA (fresh install, dev
  box, air-gapped mirror), nothing changes — the default UA is
  valid HTTP and CISA has no reason to reject it.

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| Default UA names version + repo | unit | `test_BC_091_kev_user_agent.py::test_default_user_agent_names_version_and_repo` | ✓ |
| Operator can override | unit | `test_BC_091_kev_user_agent.py::test_env_override` | ✓ |
| Empty env falls back | unit | `test_BC_091_kev_user_agent.py::test_empty_env_falls_back_to_default` | ✓ |
| UA version tracks SOFTWARE_VERSION | unit | `test_BC_091_kev_user_agent.py::test_ua_version_tracks_software_version` | ✓ |
| `_http_fetch` sends the configured UA | unit | `test_BC_091_kev_user_agent.py::test_http_fetch_sends_configured_ua` | ✓ |
| VARIABLES + env + compose sync | lint | `make verify-variables` + `verify-compose-env-propagation` | ✓ |
| CAP-T187 registered | lint | `make verify-traceability` | ✓ |
| Fast tier green | gate log | `make check` | ✓ |

## Rollout

On upgrade:
- Hub boots with the new UA by default.
- Next hourly KEV refresh succeeds (expected).
- `/healthz` moves `"kev": "unfetched"` → `"kev": "ok"`.
- No fleet-wide migration; each hub rolls own.
