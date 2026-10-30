# BC-011 close-gap — compose env propagation

*Branch: `fix/BC-011-env-propagation`*
*Start date: 2026-10-30*

## Situation

Live tap session for BC-011 revealed hub was running without health policies even though `BEACONRY_HEALTH_PROBES` was set in `poc/.env`. Root cause: `poc/docker-compose.yml` uses explicit `environment:` lists that only propagate named vars. `BEACONRY_HEALTH_PROBES` was never added to that list, so the hub container always booted with an empty value, parser returned empty dict, no "policies loaded" log line, no 🔍 Verify button attached.

**Same silent bug has been dormant since BC-009**: `BEACONRY_FIX_ENABLED` is also missing from compose propagation. Operators who tried `BEACONRY_FIX_ENABLED=0` in `.env` to opt out of the fix flow would have been ignored — operator-visible but silent misbehavior.

This is the same class of bug BC-012 (missing Dockerfile `COPY beaconry_health/`), BC-013 (missing `health.py` COPY + relative import), and BC-011 (missing `waterfall.py` COPY) all had: shipped feature works in unit tests, breaks only when a container is built + run with the operator's actual env.

## Problem to solve

Code reads a `BEACONRY_X` env var; compose doesn't propagate it; container silently gets empty-string default; nothing fails until live tap session.

## Goal

Fix the two shipped bugs AND add a `make check` gate that prevents future recurrences.

## Scope

- `poc/docker-compose.yml` — add three missing propagations: `BEACONRY_FIX_ENABLED` (BC-009), `BEACONRY_HEALTH_PROBES` (BC-011), `BEACONRY_MOCK` (dev-mode)
- `poc/scripts/verify_compose_env_propagation.py` — new lint: scan `poc/<svc>/*.py` for `os.environ.get("BEACONRY_X")` reads; cross-check against the matching service's `environment:` block in compose; fail with named error + copy-pasteable fix hint
- `poc/tests/unit/test_compose_env_propagation.py` — 6 tests covering loader, matcher, missing-propagation detection, legitimate-propagation passing, and a live assertion against the real poc/docker-compose.yml
- `Makefile` — new `verify-compose-env-propagation` target hooked into `make check`
- CAP-T051 registered

## Non-goals

- Switching compose from `environment:` list to `env_file: poc/.env` — larger change; the explicit-list approach gives us the opportunity to document each var
- Scout env propagation checks — scout currently reads no `BEACONRY_*` vars, but the lint already checks it preventively
- Other services (future adapters) — the lint iterates `(hub, scout)`; extend when adding a service

## Design decisions

**Explicit list + lint, not env_file bulk propagation.** Rejected: `env_file: .env`. Reason: `env_file` dumps every `.env` entry into the container unconditionally, including Telegram tokens that might not need to be in every service. Explicit list is auditable and documents intent per service.

**Regex-scan for `os.environ.get("BEACONRY_...")`.** Rejected: AST parse. Reason: regex is sufficient for a convention check; AST parse would catch computed env names (`os.environ.get(f"BEACONRY_{...}")`), which we don't have today and would be a code smell if we did.

**Lint over hub + scout.** Rejected: lint hub only. Reason: future services (BC-011's imagined target-probe worker; adapters) need the same protection. Iterating `(hub, scout)` plus future entries is one line of maintenance.

## Breaking changes

None operator-visible. The two shipped-but-dormant env vars now actually work. If anyone had `.env` entries that were being silently ignored, they'll now take effect — which is the correct behavior; the shipped CAP descriptions said they'd work.

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| BEACONRY_HEALTH_PROBES propagated in compose | compose diff | `docker-compose.yml` hub.environment includes it | ✓ |
| BEACONRY_FIX_ENABLED propagated in compose (dormant since BC-009) | compose diff | same | ✓ |
| BEACONRY_MOCK propagated in compose | compose diff | same | ✓ |
| Lint catches a planted missing propagation | unit test | `test_check_service_fails_on_missing_propagation` | ✓ |
| Lint passes clean on legitimate compose | unit test | `test_check_service_passes_when_env_propagated` | ✓ |
| Lint applied to real poc/docker-compose.yml passes | unit test | `test_check_real_poc_passes` | ✓ |
| make check runs verify-compose-env-propagation | Makefile diff | new target in `.PHONY` + check deps | ✓ |
| CAP-T051 registered | verify-traceability | make check traceability green | ✓ |
| Live: hub rebuilt + restarted shows "BC-011 health policies loaded" | live manual | awaits user confirmation on their machine | ❌ |
| Memory drift pattern added if this story deprecates documented behavior | N/A | N/A — close-gap only, no new convention | ✓ |

## Testing commands

```bash
source .venv/bin/activate
make check
# Rebuild hub so the new env propagation takes effect
cd poc && docker compose up -d --build hub
docker logs beaconry-hub 2>&1 | grep "BC-011 health policies"
```
