# BC-125 — EXECUTOR env default hygiene

*Branch: `feat/BC-125-executor-env-default-hygiene`*
*Start date: 2026-11-16*
*Story: `docs/stories/BC-125-executor-env-default-hygiene.md`*

---

## Situation

Operator saw `EXECUTOR=` empty in the BC-123 POLICIES line after
v0.22.8 deployed. Hub IS running manual (Python-level
`_executor_registry.get(..., ManualAdapter())` fallback) — but the
displayed string is blank because:

1. `poc/docker-compose.yml`:
   `- BEACONRY_EXECUTOR=${BEACONRY_EXECUTOR:-}` — the `:-` sets an
   empty string when the env is unset upstream.
2. `poc/hub/hub.py:500`:
   `os.environ.get("BEACONRY_EXECUTOR", "manual").lower()` — returns
   `""` when the key is present with an empty value (the Python
   `.get(key, default)` default only kicks in when the key is
   absent, not when the value is empty).

## Problems to solve

- Operator sees ambiguous UI: `EXECUTOR=` blank, no hint what's running.
- Future env sources (homelab, k8s manifests, test harnesses) can
  reintroduce the empty-string case unless hub.py is defensive.

## Goals

1. Compose default is `manual` so every deploy gets a populated env.
2. Hub normalizes empty string → `manual` (defensive guard).
3. Regression tests pin both behaviors.

## Scope

- `poc/docker-compose.yml` — change `${BEACONRY_EXECUTOR:-}` to `${BEACONRY_EXECUTOR:-manual}`.
- `poc/hub/hub.py:500` — swap `.get("X", "manual")` for
  `(.get("X") or "manual")`.
- `poc/tests/unit/test_BC_125_executor_env_default_hygiene.py` — new.
- `docs/CAPABILITIES.md` — CAP-T228.
- INDEX + story status at ship time.

## Non-goals

- Changing the executor registry shape.
- Adding new executors.
- Modifying homelab's `hub.env.j2`.

## Design decisions

- **Both fixes, not either/or.** Compose default is universal
  (survives bypassing hub.py); Python guard is defense-in-depth.
- **Keep the ManualAdapter fallback** in `_executor_registry.get()`.
  An operator setting `BEACONRY_EXECUTOR=garbage` still runs manual
  but sees `EXECUTOR=garbage` in the POLICIES line — honest about
  what they set, so they can fix their config.
- **Don't raise on invalid values.** Soft fallback is kinder than
  hub-won't-boot.

## Breaking changes

### 1. API / return shape
None.

### 2. Data model
None.

### 3. Behavioral semantics

- Pre-BC-125 hubs with unset `BEACONRY_EXECUTOR` were running manual
  but reporting `EXECUTOR=` (empty). Post-BC-125 they report
  `EXECUTOR=manual`. Pure UX fix; no runtime behavior change.
- Operators who explicitly set `BEACONRY_EXECUTOR=docker` see no change.

## Merge plan

1. `make check` passes on branch tip.
2. PR to `dev`; auto-merge if CI green.
3. Post-merge: BC-125 shipped, bump to v0.22.9, forge rebuild.

## Rollout plan

Additive. No operator action required. On next hub deploy:

- `EXECUTOR=manual` appears in `beaconry status` POLICIES line.
- `/healthz.checks.executor` returns `"manual"` instead of `""`.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Unset env → manual | unit | `test_BC_125_executor_env_default_hygiene.py::test_unset_env_defaults_to_manual` | ✓ |
| Empty-string env → manual (compose `:-` case) | unit | `test_empty_string_env_defaults_to_manual` | ✓ |
| Whitespace-only env → manual | unit | `test_whitespace_env_defaults_to_manual` | ✓ |
| Explicit docker → docker | unit | `test_explicit_docker_env_wires_docker` | ✓ |
| Case-insensitive normalization | unit | `test_case_insensitive_normalization` | ✓ |
| /healthz.checks.executor normalized | unit | `test_healthz_executor_key_normalized` | ✓ |
| compose default is manual | unit | `test_compose_yaml_defaults_executor_to_manual` | ✓ |
| Invalid value → ManualAdapter fallback but EXECUTOR_NAME preserved | unit | `test_invalid_value_falls_through_to_manual_adapter` | ✓ |
| Memory drift pattern added if this story deprecates documented behavior | N/A | pure additive bug fix | ✓ |

## Testing strategy

Unit-only — five scenarios cover every branch (unset / empty /
docker / compose config / invalid). `importlib.reload(hub)` under
different `monkeypatch.setenv` combinations is the standard
pattern (BC-113 / BC-119 both use it).

## Testing commands

```bash
make check
pytest poc/tests/unit/test_BC_125_executor_env_default_hygiene.py -v
```
