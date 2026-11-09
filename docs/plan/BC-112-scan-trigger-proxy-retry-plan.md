# BC-112 — Scan trigger proxy retry

*Branch: `feat/BC-112-scan-trigger-proxy-retry`*
*Start date: 2026-11-09*
*Story: `docs/stories/BC-112-scan-trigger-proxy-retry.md`*

---

## Situation

`poc/hub/hub.py::http_scan_trigger` POSTs to `scout:8001` exactly
once. On `URLError` (DNS, connection refused, timeout) it raises
`HTTPException(503)`. Operator sees 503 on first failure and has
to manually retry.

---

## Problems to solve

- Transient compose-DNS blip or scout-busy socket refusal immediately
  fails the operator's trigger.
- HTTP 4xx/5xx from scout must be preserved — those are genuine
  responses (409 "already running", 401 "bad token", etc.)

---

## Goals

1. Retry on transport failures (DNS / connection refused / timeout)
   up to N times (maps to Scenario "POST fails once then succeeds").
2. Backoff doubles each attempt (maps to Scenario "Backoff doubles").
3. 4xx/5xx from scout returns immediately (maps to Scenario
   "4xx/5xx NOT retried").

---

## Scope

- `poc/hub/hub.py`:
  - New env vars: `BEACONRY_SCAN_TRIGGER_MAX_ATTEMPTS` (default 3),
    `BEACONRY_SCAN_TRIGGER_BASE_S` (default 1).
  - New `_scan_trigger_once(scout_url)` helper — single-attempt
    POST returning `(status_code, body, transport_error)`.
  - `http_scan_trigger` rewritten to loop over `_scan_trigger_once`
    with backoff on transport errors only.
- `poc/.env.example`, `poc/docker-compose.yml`, `docs/VARIABLES.md`:
  two new env rows.
- `docs/CAPABILITIES.md`: CAP-T214.
- Unit tests: `poc/tests/unit/test_BC_112_scan_trigger_proxy_retry.py`.
- `CHANGELOG.md` + `VERSION` → `0.20.5` (patch — resilience fix).

---

## Non-goals

- Retry on 5xx body responses (hub shouldn't second-guess scout).
- Per-request rate limiting.

---

## Design decisions

- **Transport-error-only retry**: `URLError` / `ConnectionError` /
  `TimeoutError` / `OSError` are transient; `HTTPError` (4xx/5xx)
  is a real scout response. Discrimination happens in
  `_scan_trigger_once` via distinct return values.
- **Short backoff 1s/2s/4s**: operator's HTTP request is blocked
  while we retry. ~15s worst-case total (plus 3 × 5s socket
  timeout = ~30s absolute cap) is acceptable UX for a `make scan`.
- **`retry_attempts` in response JSON**: operator sees `retry
  succeeded on attempt 2` in CLI output.

---

## Breaking changes

### 1. API / return shape

- Additive key `retry_attempts: N` on success responses when
  `attempt > 1`. CLI tools tolerating extra keys see no change.
- 503 message changes from `"scout unreachable: X"` to
  `"scout unreachable after N attempt(s): X"`. CLI tooling that
  parses this string would break; none in-tree does.

### 2. Data model (schema + legacy rows)

None.

### 3. Behavioral semantics

- Pre-BC-112: single POST; transport failure → 503.
- Post-BC-112: N POSTs; transient failure clears on retry.
- 4xx/5xx from scout: same behavior as pre-BC-112 (returned
  immediately, no retry).

---

## Merge plan

1. All work on `feat/BC-112-scan-trigger-proxy-retry`.
2. `make check` passes.
3. PR to `dev`, squash merge.
4. Post-merge: INDEX + story → shipped.

---

## Rollout plan

Additive. Zero operator action.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Scenario "POST fails once then succeeds" | unit | `poc/tests/unit/test_BC_112_scan_trigger_proxy_retry.py::test_http_scan_trigger_retries_on_transport_error` | ✓ |
| Scenario "Backoff doubles each attempt" | unit | `test_backoff_doubles_each_attempt` | ✓ |
| Scenario "4xx/5xx is NOT retried" | unit | `test_http_scan_trigger_does_not_retry_on_4xx` | ✓ |
| First-attempt success | unit | `test_http_scan_trigger_succeeds_on_first_attempt` | ✓ |
| All-fail → 503 | unit | `test_http_scan_trigger_raises_503_after_max_transport_failures` | ✓ |
| Env parsing | unit | `test_env_defaults_sensible`, `test_env_invalid_falls_back` | ✓ |
| Memory drift pattern added | N/A | N/A — no documented behavior deprecated | ✓ |

---

## Testing strategy

Unit tests monkeypatch `_scan_trigger_once` to control result
sequence + `asyncio.sleep` to assert backoff schedule without real
time elapsing.

## Testing commands

```bash
make check
pytest poc/tests/unit/test_BC_112_scan_trigger_proxy_retry.py -v
```
