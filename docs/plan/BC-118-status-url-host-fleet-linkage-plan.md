# BC-118 — `beaconry status` URL + host + fleet linkage

*Branch: `feat/BC-118-status-url-host-fleet-linkage`*
*Start date: 2026-11-15*
*Story: `docs/stories/BC-118-status-url-host-fleet-linkage.md`*

---

## Situation

`beaconry status` top block shows `beaconry-hub running` + `beaconry-scout down`
with no indication of which URL each row probed. On a host like bb-82jq
(systemd scout on :8001, CLI config points at remote hetzner hub, no
dev-compose) operator sees `scout: down` + `container not up` without
knowing it's probing `localhost:18001` — expected absent on this host.

---

## Problems to solve

- URL opacity: rows don't surface the endpoint they probed.
- Hostname context: operator can't tell which machine the CLI ran on.
- False-down UX: `down` reads as "something broke" when actually
  dev-compose isn't configured on this host.
- Fleet linkage: operator can't tell which FLEET row represents the
  current machine.

---

## Goals

Maps 1:1 to Gherkin scenarios:

1. Systemd-only host → URL shown + "dev-compose not configured" detail.
2. Dev-compose-up host → URL shown + normal `running` detail.
3. Host with no fleet presence → no `(you)` marker.
4. Hub URL displays even when hub is down.

---

## Scope

- `poc/scripts/status.py`:
  - `import socket`, `subprocess`, `pathlib.Path`.
  - `ServiceStatus` gains `probed_url: str = ""`.
  - `_repo_root()` + `_dev_compose_configured()` +
    `_dev_scout_container_exists()` + `_scout_down_detail(url)` helpers.
  - `gather_hub_status` + `gather_scout_status` populate `probed_url`
    on both success + down branches.
  - `_format_table` inserts `URL` column between `NAME` and `STATE`.
  - `format_fleet` gains `local_hostname: str | None = None` kwarg;
    matching row renders as `<host> (you)`.
  - `main()` prints `Running on: <socket.gethostname()>` above the top
    table; passes `local_hostname` into `format_fleet`.
- Unit tests: `poc/tests/unit/test_BC_118_status_url_host_fleet.py`.
- `docs/CAPABILITIES.md`: CAP-T220.
- `CHANGELOG.md` + `VERSION` → `0.22.1` (patch — CLI UX improvement).

---

## Non-goals

- Programmatic fleet<->local linkage (the scout URL in the top block
  stays as the dev-compose probe URL, independent of FLEET's rows).
- Topology preamble line beyond `Running on:`.
- Collapsing the top block into FLEET.

---

## Design decisions

- **URL column position**: between NAME and STATE so the operator
  reads "WHAT (name) at WHERE (url) is in WHICH STATE".
- **`socket.gethostname()` over `$HOSTNAME`**: Python stdlib,
  OS-portable, matches scout's default `HOST_NAME` env semantics
  (scout.py uses `os.uname().nodename` which is the same value on
  Linux). Letting operator override via env is scope creep for v1.
- **Repo-walk for `_repo_root`**: walks from `__file__` parent
  directories looking for `poc/docker-compose.yml`. Test harness
  can monkeypatch for isolation.
- **Docker-subprocess for container existence check**: 2s timeout;
  any OSError/SubprocessError → return False. Matches operator
  mental model ("if docker's broken, assume nothing's running").

---

## Breaking changes

### 1. API / return shape

- `ServiceStatus` has one new field with a default (`probed_url=""`),
  so existing constructors keep working.
- Top table gains a new column. CLI tools parsing the output
  positionally break; none in-tree do. Scripts that pipe through
  `awk '$N==...'` need to shift by 1. Workaround: use `-o json`
  (future BC; today `status.py` only emits text).

### 2. Data model

None.

### 3. Behavioral semantics

- Pre-BC-118: `beaconry-scout: down · container not up` on every host.
- Post-BC-118 on dev-compose-less host: `beaconry-scout: down ·
  down (dev-compose not configured on this host)`.
- Pre-BC-118 FLEET: no row marker. Post-BC-118: matching row marked
  `(you)`.

---

## Merge plan

1. All work on `feat/BC-118-status-url-host-fleet-linkage`.
2. `make check` passes.
3. PR to `dev`, squash merge.
4. Post-merge: INDEX + story → shipped.
5. Operator verifies on bb-82jq + hetzner-staging per acceptance scenarios.

---

## Rollout plan

Additive. No operator action unless they want to parse status output
positionally (they'd need to re-adjust column indices).

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Scenario "Systemd-only host" | unit | `poc/tests/unit/test_BC_118_status_url_host_fleet.py::test_scout_down_detail_when_dev_compose_not_configured` + `test_gather_scout_status_on_down_uses_contextual_detail` | ✓ |
| Scenario "Dev-compose-up host" | unit | `test_gather_scout_status_sets_probed_url_on_success` | ✓ |
| Scenario "No fleet presence" | unit | `test_format_fleet_no_marker_when_hostname_missing_from_fleet` | ✓ |
| Scenario "Hub URL displays even when hub is down" | unit | `test_gather_hub_status_sets_probed_url_on_down` | ✓ |
| URL column rendered in header | unit | `test_format_table_includes_url_column_header` | ✓ |
| `(you)` marker when hostname matches | unit | `test_format_fleet_marks_matching_host_with_you` | ✓ |
| `Running on` header printed first | unit | `test_main_running_on_header_precedes_table` | ✓ |
| Dev-compose detection: compose-not-up branch | unit | `test_scout_down_detail_when_compose_stack_not_up` | ✓ |
| Dev-compose detection: container-present-but-unreachable | unit | `test_scout_down_detail_when_container_present_but_unreachable` | ✓ |
| Memory drift pattern added | N/A | N/A — no documented behavior deprecated | ✓ |

---

## Testing strategy

- Unit tests isolate pure helpers (`_scout_down_detail`, `_format_table`,
  `format_fleet` with hostname), monkeypatch `fetch_healthz` /
  `_dev_compose_configured` / `_dev_scout_container_exists` /
  `socket.gethostname` for deterministic runs.
- Operator live-verifies on bb-82jq (systemd-only, expected new "dev-
  compose not configured" message) + hetzner-staging (dev-compose up,
  expected `running`).

## Testing commands

```bash
make check
pytest poc/tests/unit/test_BC_118_status_url_host_fleet.py -v
beaconry status  # live on target host
```
