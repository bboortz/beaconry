# Environment variables

Authoritative, grep-friendly index of every environment variable beaconry
reads. Any `os.environ.get("X")` or `os.environ["X"]` (or `_int_env("X", …)`,
or `env_var = "X"` on an adapter class) under `poc/` outside tests must have
a row below — `make verify-variables` fails CI otherwise. Likewise, every
row below must correspond to at least one code read; orphans fail the same
gate.

> **Shift-left pattern** — same mechanism as BC-011 (compose env propagation),
> BC-016 (docs drift), BC-019 (evidence pointers). Lint lives in
> `poc/scripts/verify_variables_documented.py`.

See also:
- `poc/.env.example` — minimal operator starter config.
- `poc/docker-compose.yml` — which variables propagate to the hub + scout containers.
- `memory/design-decisions.md` + per-BC story docs for deeper rationale.

**Column meaning:**
- **Default** — code-side default. *(empty)* means the variable is read with no fallback; the function raises or degrades. *Required* calls that out.
- **Type** — `bool (0/1)`, `int`, `int (s)` (seconds), `float`, `string`, `URL`, `path`, `glob list`, `DSL`.
- **Where read** — files inside `poc/` that call `os.environ.get(...)` on this variable (or equivalent).
- **Reason** — why this variable exists, not just what it does — what problem flipping it solves for an operator or developer.

<!-- BC-039 BEGIN VARIABLES TABLE -->

## Required — Telegram credentials

| Variable | Default | Type | Where read | Reason | Origin |
|---|---|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | *(empty)* | string | `poc/hub/hub.py`, `poc/demo.py` | API token from @BotFather. Without it, Telegram notifications no-op; hub still ingests + persists findings so `make findings` works and the operator can wire up Telegram later. | BC-003 |
| `TELEGRAM_CHAT_ID` | *(empty)* | string | `poc/hub/hub.py`, `poc/demo.py` | Chat ID (from @userinfobot) notifications go to. Separate from the token so a bot can serve multiple deployments with different recipients. | BC-003 |

## Scanning behaviour

| Variable | Default | Type | Where read | Reason | Origin |
|---|---|---|---|---|---|
| `MIN_CVSS` | `7.0` | float | `poc/hub/hub.py` | Noise filter — most CVEs below 7.0 are not worth paging an operator. KEV CVEs bypass this per BC-023 since empirical exploitation beats theoretical CVSS. | BC-005 |
| `BEACONRY_MAX_FINDINGS_PER_RUN` | `5` | int | `poc/hub/hub.py` | Per-scan cap on individual notifications. Prevents the 10-image × 15-CVE flood that would otherwise fire 150 Telegram messages per scan. Overflow goes into one digest message. | BC-005 |
| `SCAN_INTERVAL` | `300` | int (s) | `poc/scout/scout.py` | How often scout polls the host inventory. Shorter = faster first-fire latency; longer = less CPU/network. | BC-002 |
| `SCOUT_HEARTBEAT_INTERVAL_S` | `60` | int (s) | `poc/scout/scout.py` | How often scout POSTs a tiny `/heartbeat` (host + version + health, no SBOMs) between full scans. Keeps FLEET's HEALTH / VERSION / LAST SEEN cells fresh so a stale scout is diagnosable in under a minute. Set to `0` to disable (falls back to BC-061 behaviour where `/scan` is the only heartbeat). | BC-063 |
| `SCOUT_INCLUDE` | `nginx*,redis*,postgres*` | glob list | `poc/scout/scout.py` | Allowlist of image tags scout scans. Default is demo-safe; operators who want to scan everything on the host set `*`. Keeps the first-run finding count reasonable. | BC-005 |
| `SCOUT_EXCLUDE` | *(empty)* | glob list | `poc/scout/scout.py` | Blocklist overlaid on `SCOUT_INCLUDE`. Lets operators keep a broad include glob while suppressing `*-test` / `*-dev` noise. | BC-005 |
| `HOST_NAME` | uname().nodename | string | `poc/scout/scout.py` | Label shown in every Telegram notification and stored on each finding's `host` column. Operator wants a human-readable name (`homelab`), not the machine's auto-generated UUID-like hostname. | BC-002 |
| `PACMAN_DB` | `/host/var/lib/pacman` | path | `poc/scout/scout.py` | Where the Arch Linux package DB is mounted inside the scout container. Non-Arch hosts leave it unset — scout logs a warning and skips the pacman source cleanly. | BC-004 |
| `SCOUT_HTTP_PORT` | `8001` | int | `poc/scout/scout.py` | Port scout's FastAPI health server binds inside the container. Externalized so test-e2e and scenarios can run multiple scouts on different ports without collision. | BC-013 |

## Fix execution

| Variable | Default | Type | Where read | Reason | Origin |
|---|---|---|---|---|---|
| `BEACONRY_FIX_ENABLED` | `1` | bool (`0`/`1`) | `poc/hub/hub.py` | Safety-conscious demo switch — operator can run a full stack with real notifications but refuse all Fix buttons until they're ready to trust the automation. Fix taps return an informational message instead of executing. | BC-009 |
| `BEACONRY_EXECUTOR` | `manual` | enum (`manual`\|`docker`) | `poc/hub/hub.py` | Which executor adapter handles `[Fix now]` taps. `manual` (default) logs operator intent + reminds in 1h; operator does the fix themselves. `docker` runs `docker pull` on the image. Infrastructure-agnostic default — auto-pull depends on homogeneous stacks that beaconry can't verify. | BC-066 |
| `BEACONRY_MANUAL_REMINDER_INTERVAL_S` | `3600` (1h) | int (s) | `poc/hub/hub.py` | Reminder cadence after operator taps `[Fix now]` under the manual executor. 0 disables reminders entirely (claim still audited). Only applies when `BEACONRY_EXECUTOR=manual`. | BC-066 |
| `BEACONRY_HEALTH_PROBES` | *(empty)* | DSL | `poc/hub/hub.py` | Policy-driven probe resolver: `image_ref=probe_spec[;...]`. Determines what `verify_fix` runs after a successful Fix. Essential for services that don't expose an HTTP endpoint on 80 — operator points probes at the real health check. | BC-011 |

## Reconciliation & regression

| Variable | Default | Type | Where read | Reason | Origin |
|---|---|---|---|---|---|
| `BEACONRY_RECONCILE_ENABLED` | `1` | bool (`0`/`1`) | `poc/hub/hub.py` | Opt-out of the "fix is not forget" long-term diffing. Default on because the whole product thesis depends on it. Privacy/surveillance-conscious operators (e.g. shared hosts) can set `0` so the DB holds only point-in-time state. | BC-031 |
| `BEACONRY_INTERNET_FACING_DETECTION_ENABLED` | `1` | bool (`0`/`1`) | `poc/hub/hub.py` | Opt-out of BC-030 Tier 1 differentiation. Hub ignores scout's port-exposure map and classifies everything as LAN-only when disabled. Useful if an operator considers the port inspection itself intrusive, or runs a reverse-proxy topology where exposure is misleading. | BC-030 |

## Heartbeat reminders

| Variable | Default | Type | Where read | Reason | Origin |
|---|---|---|---|---|---|
| `BEACONRY_HEARTBEAT_ENABLED` | `1` | bool (`0`/`1`) | `poc/hub/hub.py` | Opt-out of periodic reminders. Default on because silent-forever findings are the bug this fixes; some operators prefer "quiet unless something changes" and set `0`. | BC-035 |
| `BEACONRY_HEARTBEAT_INTERVAL_S` | `604800` (7 d) | int (s) | `poc/hub/hub.py` | Per-finding cooldown — how long since last reminder before re-poking the same CVE. 7 d default avoids nagging on long-running triage; operators who want a daily nudge set `86400`. | BC-035 |
| `BEACONRY_HEARTBEAT_SWEEP_INTERVAL_S` | `3600` (1 h) | int (s) | `poc/hub/hub.py` | How often the sweep loop walks the DB. Must be ≤ `INTERVAL_S` to never miss a tick. Smaller just means the daily/weekly ping lands closer to the exact anniversary of the previous one. | BC-035 |
| `BEACONRY_HEARTBEAT_LIVENESS_WINDOW_S` | `604800` (7 d) | int (s) | `poc/hub/hub.py` | Findings whose `last_seen_ts` is older than this are considered "gone from inventory" and skipped by the sweep — BC-031 reconcile will auto-close them anyway. Prevents the sweep from reminding operator about ghosts. | BC-035 |

## Digest cadence

| Variable | Default | Type | Where read | Reason | Origin |
|---|---|---|---|---|---|
| `BEACONRY_DIGEST_INTERVAL_S` | `86400` (24 h) | int (s) | `poc/hub/hub.py` | Per-host digest cooldown. Default 24 h fixes the 288-digests-per-day flood on inventories with cap overflow. Content-hash fallback still bypasses the throttle when the suppressed set actually changes (so operator snoozes land immediately). `0` disables throttling (pre-BC-036 "every scan" behavior). | BC-036 |
| `BEACONRY_DIGEST_ON_CONTENT_CHANGE` | `1` | bool (`0`/`1`) | `poc/hub/hub.py` | Controls whether a suppressed-set hash change bypasses the digest throttle. `1` (default) preserves BC-036 behaviour (fire whenever the cap tail rotates). `0` makes `BEACONRY_DIGEST_INTERVAL_S` a strict floor — only `first_fire` and `interval_elapsed` can trigger a send. Noisy homelabs where new CVEs regularly rotate through the capped tail turn this off to get the intended 24h cadence. | BC-065 |
| `BEACONRY_FLEET_ACTIVE_WINDOW_S` | `600` (10 min) | int (s) | `poc/hub/hub.py` | A scout counts as `active` in `/fleet` STATUS if it posted within this window. Default is 2× SCAN_INTERVAL (300 s). Operators who use a longer SCAN_INTERVAL should bump this proportionally so active scouts don't flap to `stale`. | BC-061 |
| `BEACONRY_FLEET_DEAD_AFTER_S` | `86400` (24 h) | int (s) | `poc/hub/hub.py` | Scouts whose last POST is older than this count as `dead` in `/fleet` STATUS. Between `BEACONRY_FLEET_ACTIVE_WINDOW_S` and this threshold the scout is `stale`. 24 h default means a decommissioned scout turns red after a day. | BC-061 |
| `BEACONRY_STRICT_API` | `0` | bool (`0`/`1`) | `poc/hub/hub.py` | When set to `1`, the hub short-circuits any request whose `X-Beaconry-API-Expect` header disagrees with its `API_VERSION` with `412 Precondition Failed`. Default soft mode logs a WARNING and lets the request through — supports rolling upgrades where client + hub briefly disagree. Operators who want zero drift in production turn it on. | BC-062 |
| `BEACONRY_SKIP_VERSION_BUMP` | *(unset)* | bool (`0`/`1`) | `poc/scripts/verify_version_bumped.py` | **Dev-only escape hatch.** Set to `1` to bypass the pre-push gate that requires `VERSION` to move relative to `origin/dev`. Use only in emergencies (hotfix shipped on top of an already-pushed branch, cross-branch merge mechanics). Normal flow is `make bump-patch` + commit before `git push`. | BC-062 |
| `BEACONRY_SKIP_CHANGELOG` | *(unset)* | bool (`0`/`1`) | `poc/scripts/bump_version.py`, `poc/scripts/verify_changelog.py` | **Dev-only escape hatch.** Set to `1` to (a) skip CHANGELOG regeneration during `make bump-*` and (b) bypass the pre-push gate that requires a `## [v<VERSION>]` header in `CHANGELOG.md`. Use only in emergencies. Normal flow is `make bump-*` auto-updates CHANGELOG, operator commits both before pushing. | BC-069 |
| `SOAK_WINDOW_S` | `86400` (24h) | int (s) | `poc/scripts/soak.py` | Operator knob for `make soak`. Controls how far back the audit-log health check scans. BC-074 raised the default from 1h → 24h to match the natural staging soak length (deploy day N → `make soak` day N+1 → promote). Shorter window = tighter signal after a fresh deploy; longer window = catches slower-emerging regressions. Set to `0` to disable (treats everything as outside the window; always exits 0). | BC-069, BC-074 |
| `SCOUT_METRICS_ENABLED` | `0` (**off by default**) | bool (`0`/`1`) | `poc/scout/scout.py` | **Privacy-first opt-in.** When `1`, scout adds `X-Beaconry-Metrics-Opt-In: 1` header to `/scan` + `/heartbeat` POSTs; hub honors the header by writing the `scan_received` + `heartbeat_received` audit events used by `beaconry metrics` + `make soak`. CVE matching, notifications, DB persistence, FLEET view all keep working regardless — only metrics-fuel audit records are gated. Default off because beaconry heads toward multi-tenant / SaaS (DESIGN.md Phase 4) where scouts shouldn't leak timing patterns to hubs they don't own. Operator who wants metrics: set to `1` on each scout + restart. | BC-075 |
| `BEACONRY_SKIP_FEATURE_COMPLETE` | *(unset)* | bool (`0`/`1`) | `poc/scripts/verify_feature_complete.py` | **Dev-only escape hatch.** Set to `1` to bypass the lint that requires every `status: shipped` story's paired plan to have no open ❌ / ⚠️ shipping-evidence rows. Normal flow is to flip rows to ✓ before marking a story shipped — this bypass exists for the "I need to push a hotfix now, I'll close the gaps in a follow-up" case. | BC-074 |
| `BEACONRY_SKIP_DOGFOOD` | *(unset)* | bool (`0`/`1`) | `poc/scripts/dogfood_scan.py` | **Dev-only escape hatch.** Set to `1` to bypass the dogfood-grype gate that re-scans `beaconry-hub:latest` + `beaconry-scout:latest` after rebuild and fails on any High/Critical finding with a published fix. Normal flow is to let the gate fire and bump the pinned dep that regressed (see BC-081 for the originating incident). | BC-082 |
| `BEACONRY_SKIP_BASE_IMAGES` | *(unset)* | bool (`0`/`1`) | `poc/scripts/verify_base_images.py` | **Dev-only escape hatch.** Set to `1` to bypass the `verify-base-images` preflight — the anonymous-pull probe against every `FROM` ref in Dockerfiles. Normal flow is to let the gate fire if a base image goes private or a tag is retagged. Use only in air-gapped CI environments where no outbound network to ghcr.io / gcr.io / docker.io is available. | BC-083 |
| `BEACONRY_AUTO_FIX_THRESHOLD` | `3` | int (≥1) | `poc/hub/hub.py` | Number of consecutive scout cycles a finding must be absent before the counter-based auto-fix (BC-086) flips its status to `auto_fixed`. Default 3 is conservative — one glitchy scan or scout restart does not auto-close long-lived findings. `1` reproduces the pre-BC-086 immediate-close cadence (reconcile fires on first absence). Applies to `open`, `regressed`, `snoozed`, `accepted` statuses; operator-decided `fixed` / `fixed_verified` are never touched by auto-fix. | BC-086 |
| `BEACONRY_SNOOZE_DEFAULT_HOURS` | `1,4,24,168` | comma-separated ints | `poc/hub/hub.py` | Hours offered as `[⏰ Nh]` inline-keyboard buttons on every snooze-eligible notification (BC-089). Operator taps one → finding snoozes for that many hours, auto-re-opens at expiry via the sweep below. Set to a single value (e.g. `4`) to collapse the keyboard to one snooze button. Rendering: `1→1h`, `24→1d`, `168→1w`; non-multiples render as raw hours (e.g. `72→72h`). Non-integer tokens silently dropped; empty env falls back to the default. | BC-089 |
| `BEACONRY_SNOOZE_SWEEP_INTERVAL_S` | `60` | int (≥5) | `poc/hub/hub.py` | Cadence of the snooze-expiry sweep (BC-089). Drift ceiling: a snooze whose `snooze_until_ts` passes at `T` re-opens somewhere in `[T, T + SWEEP_INTERVAL_S)`. Default 60s is tight enough for human-granular snoozes (1h+). Clamped to ≥5 to prevent accidental DoS on sqlite. | BC-089 |
| `BEACONRY_SNOOZE_NOTIFY_ON_EXPIRE` | `1` | bool (`0`/`1`) | `poc/hub/hub.py` | `1` = fire a distinct `⏰ Snooze expired` Telegram notification when the sweep re-opens a finding (BC-089). `0` = silent re-open (status flips, audit event fires, no Telegram). Audit log is authoritative either way — `make audit EVENT=snooze_expired` surfaces every expiry. | BC-089 |
| `BEACONRY_AUTO_FIX_REGRESSION_NOTIFY_ENABLED` | `1` | bool (`0`/`1`) | `poc/hub/hub.py` | BC-090 — `1` = fire the distinct `🔥 AUTO-FIX REGRESSED` Telegram body when an `auto_fixed` finding reappears in a scan. `0` = audit-only (status flip + `regressed_from_auto_fix` event fire, but no Telegram ping; a `notify_suppressed` audit event records the deliberate silence). Separate axis from per-adapter `BEACONRY_NOTIFIER_<NAME>_ENABLED` (silences a channel entirely) and from `BEACONRY_SNOOZE_NOTIFY_ON_EXPIRE` (per-snooze-kind). BC-090 also fixes the dispatch wiring — pre-BC-090 the real Telegram path rendered the generic BC-031 body; BC-090 routes to `format_auto_fix_regression` (BC-086 CAP-T178 intended body). | BC-090 |
| `BEACONRY_LOG_LEVEL` | `INFO` | enum (`DEBUG`\|`INFO`\|`WARNING`\|`ERROR`\|`CRITICAL`) | `poc/hub/hub.py`, `poc/scout/scout.py` | Root logger level for both services, read at boot. `DEBUG` re-enables BC-086's per-finding reconcile trace + `bump_last_seen` diagnostic that was shipped at `log.info` during BC-086 verification and downgraded to `log.debug` in BC-087 (~20 lines/scan/host at INFO, too noisy for production). Invalid values silently fall back to INFO with a one-time warning. Same env var covers both processes to keep the dial simple — per-module levels deferred until an operator asks. | BC-087 |
| `DOCKER_GID` | `999` (Debian/Ubuntu) | int (gid) | `poc/docker-compose.yml` (compose `group_add`); `poc/scripts/dogfood_scan.py` (grype rescan via docker socket) | The gid of the host's `/var/run/docker.sock`. BC-078 moved this from a Dockerfile build-arg to compose `group_add` so operators can change it in `.env` without rebuilding. Default 999 matches Debian/Ubuntu; set to the output of `getent group docker \| cut -d: -f3` on Fedora (995), Arch (962/989), Alpine (412). BC-082 dogfood-scan also reads it to pass `--group-add` to the grype rescan container. | BC-078, BC-082 |

## Notification adapters

| Variable | Default | Type | Where read | Reason | Origin |
|---|---|---|---|---|---|
| `BEACONRY_NOTIFIER_TELEGRAM_ENABLED` | `1` (default-on when unset) | bool (`0`/`1`) | `poc/hub/notifiers/telegram.py` (class attr), `poc/hub/notifiers/base.py` (`os.environ.get(self.env_var)`) | Per-channel silence toggle. Operators migrating to a new notifier (email/Reddit/SMS once those ship) can disable Telegram without touching anything else. Pattern extends: each future adapter gets its own `BEACONRY_NOTIFIER_<NAME>_ENABLED`. | BC-035 |

## CVE feeds & external data

| Variable | Default | Type | Where read | Reason | Origin |
|---|---|---|---|---|---|
| `BEACONRY_KEV_FEED_URL` | CISA upstream | URL | `poc/hub/kev.py` | Override the CISA KEV feed source. Air-gapped setups mirror the JSON internally; test suites point at a fixture file. Default is the public CISA endpoint which disaster-recovers from stale disk cache. | BC-020 |
| `BEACONRY_KEV_USER_AGENT` | *(empty, auto-generated)* | UA string | `poc/hub/kev.py` | Override the `User-Agent` HTTP header sent to the KEV feed (BC-091). Empty / unset → auto-composed `beaconry/<SOFTWARE_VERSION> (+https://github.com/bboortz/beaconry)` — identifiable per CISA policy. Operator override needed for air-gapped or proxied deployments where a specific UA must traverse a gateway. Context: CISA 403'd beaconry's pre-BC-091 hardcoded UA `beaconry/0.1 (+kev)` on 2026-11-05 on hetzner-staging — hourly fetches failed for ~4 h before BC-091 landed. | BC-091 |
| `SCOUT_HOST_ROLE` | `workstation` | enum (`workstation`\|`staging`\|`production`) | `poc/scout/scout.py` | BC-092 — host's position in the canary ring. Scout advertises on `/heartbeat` + `/scan`; hub persists to `scouts.role`. `production` is gate-eligible: the hub's `_handle_fix` blocks a production fix unless the same CVE is in `fixed_verified` on at least one `staging` scout (unless `BEACONRY_CANARY_RING_ENABLED=0` or `BEACONRY_CANARY_PRODUCTION_FORCE_ALLOW=1`). Invalid values clamp to `workstation` with a warning. Legacy scouts that don't send the field also default to `workstation`. | BC-092 |
| `BEACONRY_CANARY_RING_ENABLED` | `1` | bool (`0`/`1`) | `poc/hub/hub.py` | BC-092 — global master toggle for the canary ring gate on production fixes. `0` disables the gate entirely (all fixes proceed regardless of role). Operators with only one host (no staging) typically keep this at `1` and either flag no host as production or set the per-fix force-allow below. | BC-092 |
| `BEACONRY_CANARY_PRODUCTION_FORCE_ALLOW` | `0` | bool (`0`/`1`) | `poc/hub/hub.py` | BC-092 — emergency escape. `1` lets a production fix proceed without a staging precedent; audit records `canary_gate_force_allowed` (vs the normal `canary_gate_passed` / `canary_gate_blocked`). Operator should flip back to `0` after the emergency; the flag is read at each tap, so a fleet-wide hub restart propagates the new value. | BC-092 |
| `BEACONRY_DAILY_RESCAN_HOUR` | `2` | int (0-23 or `-1`) | `poc/scout/scout.py` | BC-093 — wall-clock hour (host-local) when scout runs a full daily rescan independent of `SCAN_INTERVAL`. Catches slow drift (pip installs, port changes) between regular ticks. `-1` disables the daily loop entirely. Invalid values clamp to `2` with a warning. | BC-093 |
| `BEACONRY_DOCKER_EVENTS_ENABLED` | `1` | bool (`0`/`1`) | `poc/scout/scout.py` | BC-093 — subscribe to the docker events stream and trigger (debounced) scans on image `pull` / container `create` + `start`. `0` disables the listener (scout falls back to SCAN_INTERVAL + manual triggers). Needs read access to `/var/run/docker.sock` (default compose mount). | BC-093 |
| `BEACONRY_DOCKER_EVENTS_DEBOUNCE_S` | `10` | int (≥1) | `poc/scout/scout.py` | BC-093 — debounce window for docker events. A compose up pulling N images fires exactly one trailing scan `DEBOUNCE_S` seconds after the last event. Lower = more responsive, more hub traffic; higher = fewer scans, more delay. | BC-093 |

## Paths & storage

| Variable | Default | Type | Where read | Reason | Origin |
|---|---|---|---|---|---|
| `DATA_DIR` | `/data` | path | `poc/hub/hub.py`, `poc/scout/scout.py`, `poc/hub/kev.py`, `poc/hub/audit.py` | Where SQLite DB + audit log + sentinel files live. Shared between hub + scout via a named Docker volume so the operator can wipe or migrate the whole state atomically. | BC-001 |
| `GRYPE_DB_CACHE_DIR` | *(grype default)* | path | `poc/hub/hub.py` | Where grype stores its ~200MB vulnerability DB. Pointed at a named volume so it survives container recreation; otherwise every `docker compose up` triggers a 200MB download. | BC-005 |

## Hub ↔ scout wiring

| Variable | Default | Type | Where read | Reason | Origin |
|---|---|---|---|---|---|
| `HUB_URL` | `http://hub:8000` | URL | `poc/scout/scout.py`, `poc/scripts/cli_config.py` | Where scout POSTs SBOMs AND where the operator CLI reaches the hub. Externalized so a scout on `laptop` can hit a hub on `hetzner-staging` over Tailscale. | BC-002, BC-055 |
| `HUB_TOKEN` | *(empty)* | string | `poc/scout/scout.py`, `poc/scripts/cli_config.py` | Client-side bearer the scout OR the operator CLI sends. On the scout host this must match the hub's `HUB_SCOUT_TOKEN`; on the laptop/CLI host it must match `HUB_CLI_TOKEN`. Scout and CLI live on different hosts and never share this value — each sees its own secret only. | BC-055 |
| `HUB_SCOUT_TOKEN` | *(empty)* | string | `poc/hub/hub.py` | Hub-side bearer gating `/scan` (scout → hub ingest). Independent from `HUB_CLI_TOKEN`: a scout-side leak doesn't compromise the operator surface. Empty = scout auth disabled. | BC-055 |
| `HUB_CLI_TOKEN` | *(empty)* | string | `poc/hub/hub.py` | Hub-side bearer gating `/findings`, `/audit`, `/fleet`, `/scan/trigger`, `PATCH /findings/{id}` (CLI → hub management). Independent from `HUB_SCOUT_TOKEN`. Empty = CLI auth disabled. | BC-055 |
| `HUB_PUBLISH_ADDR` | `127.0.0.1` | string | `poc/hub/hub.py`, `poc/docker-compose.yml` | Host-side interface the hub port is published on. Default `127.0.0.1` keeps the hub reachable only from the host. Set to `0.0.0.0` or a specific LAN/Tailscale IP for multi-host deployments. Must pair with `HUB_AUTH_TOKEN` or the hub logs a startup `WARNING` about unauthenticated exposure. | BC-055 |
| `BEACONRY_CLI_CONFIG` | *(xdg path)* | path | `poc/scripts/cli_config.py` | Path to the operator-CLI YAML. Empty = `$XDG_CONFIG_HOME/beaconry/cli.yml` (fallback `~/.config/beaconry/cli.yml`). YAML carries `hub_url` + `hub_token`. Env (`HUB_URL`/`HUB_TOKEN`) overrides YAML. | BC-055 |
| `SCOUT_TRIGGER_URL` | `http://scout:8001/scan/trigger` | URL | `poc/hub/hub.py` | Where `POST /scan/trigger` on the hub proxies to. Default is the scout's Docker-DNS name on the compose network. Override only in test harnesses that run the hub without a scout alongside. | BC-055 |

## Developer / dev-mode dials

| Variable | Default | Type | Where read | Reason | Origin |
|---|---|---|---|---|---|
| `BEACONRY_MOCK` | *(empty)* | bool (`0`/`1`) | `poc/hub/hub.py`, `poc/demo.py` | Master dev-mode switch — `1` skips real Telegram sends AND skips the 200MB grype DB download, so `make demo BEACONRY_MOCK=1` boots in seconds. Pre-BC-018 was a monolith; split for finer control. | BC-002 |
| `BEACONRY_SKIP_GRYPE_DB` | derived from `BEACONRY_MOCK` | bool (`0`/`1`) | `poc/hub/hub.py` | Fine-grained grype DB skip — lets operator run REAL Telegram against mocked grype (e2e stack hits `telegram-mock` as if it were real, no need for grype's 200MB download). | BC-018 |
| `BEACONRY_TELEGRAM_BASE_URL` | *(empty, api.telegram.org)* | URL | `poc/hub/hub.py` | Override python-telegram-bot's API base. Points at `telegram-mock` in BC-017's e2e stack; also usable for operators testing against a Telegram API proxy. | BC-018 |
| `BEACONRY_TEST_MODE` | *(empty)* | bool (`0`/`1`) | `poc/hub/hub.py` | **DO NOT SET IN PRODUCTION.** Enables `/test/wipe`, `/test/scan`, `/test/sweep`, `/test/set_status` — the BC-038 scenario-harness endpoints that can delete findings and inject arbitrary rows. Guarded by route registration (not per-request check) so the routes don't exist when the flag is off. | BC-038 |

## CLI script config

| Variable | Default | Type | Where read | Reason | Origin |
|---|---|---|---|---|---|
| `BEACONRY_HUB_CONTAINER` | `beaconry-hub` | string | `poc/scripts/findings.py`, `poc/scripts/status.py` | Container name the `make findings` + `make status` CLIs docker-exec into. Operators who rename their compose service (e.g. multi-tenant hosts) can retarget without editing scripts. | BC-013 |
| `BEACONRY_FINDINGS_LOCAL` | *(empty)* | bool (`0`/`1`) | `poc/scripts/findings.py` | `1` forces `make findings` to use the local `.beaconry-cache/beaconry.db` even if the compose hub is up. Needed when debugging an older dev-mode DB without stopping the live stack. | BC-007 |

## Scenario harness

| Variable | Default | Type | Where read | Reason | Origin |
|---|---|---|---|---|---|
| `BEACONRY_SCENARIO_HUB_URL` | `http://127.0.0.1:8004` | URL | `poc/scenarios/__main__.py` | Base URL the scenario runner POSTs to. Overridable so a developer can target an already-running test-mode hub (e.g. hand-debugged with breakpoints) instead of the compose project the shell wrapper boots. | BC-038 |
| `BEACONRY_SCENARIO_DATA_DIR` | `/tmp/beaconry-scenarios` | path | `poc/scenarios/__main__.py` | Shared `DATA_DIR` mounted into the scenario hub container; the runner reads SQLite + audit directly from the host side. Overridable for parallel runs on CI runners. | BC-038 |

## Live-smoke CLI harness

| Variable | Default | Type | Where read | Reason | Origin |
|---|---|---|---|---|---|
| `BEACONRY_SKIP_CLI_SMOKE` | *(empty)* | bool (`0`/`1`) | `poc/scripts/cli_smoke.py` | Opt out of the live-stack CLI smoke harness. Needed on CI runners without a privileged Docker daemon, dev laptops without the stack booted, or when the BC-038 scenario stack owns the hub container (double-boot avoidance). Any non-empty value → the gate exits 0 with "skipped per env". | BC-044 |

<!-- BC-039 END VARIABLES TABLE -->
