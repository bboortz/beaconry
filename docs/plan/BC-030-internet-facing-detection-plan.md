# BC-030 — internet_facing detection → Tier 1 fires

*Branch: `feat/BC-030-internet-facing-detection`*
*Start date: 2026-10-31*
*Story: `docs/stories/BC-030-internet-facing-detection.md`*

---

## Situation

`memory/design-decisions.md § Urgency tiers` promises Tier 1 = CVSS ≥ 9.0 + internet-facing. BC-020 wired `classify_tier` but hard-coded `internet_facing=False` in `process_sbom_entry` (`poc/hub/hub.py:506`), so Tier 1 is unreachable — a CVSS 9.8 CVE on a container publishing `0.0.0.0:80` tiers identically to one on a LAN-only container, silently defeating the differentiation.

Scout already scans *images* via `docker.images.list()`; nothing today looks at *containers* (where port publication actually lives). Hub stores a flat `findings` table — Finding dataclass has no `internet_facing` field.

---

## Problems to solve

- Scout has no view of per-container port publication (image → container is many-to-many; port publication is a container-level attribute).
- Hub Finding dataclass + DB schema don't carry the exposure dimension.
- `process_sbom_entry` fakes `internet_facing=False`; `classify_tier` never gets the real value.
- `make findings` + Telegram notifications have no exposure column — operator can't visually tell exposed from LAN-only.

---

## Goals

1. Scout collects a per-image exposure map (`{image_tag: is_internet_facing}`) by inspecting running containers' port maps, posts alongside SBOMs.
2. Hub joins the exposure map to findings at ingest time; `classify_tier` receives real value → Tier 1 reachable.
3. Finding dataclass gains `internet_facing: bool = False`; DB migration adds column; `save_finding` + schema-aware load include it.
4. `make findings` adds an exposure column (🌐 / —); Telegram notification format surfaces 🌐 marker.
5. KEV still wins over internet_facing — regression guard preserved.
6. Operator can disable detection with `BEACONRY_INTERNET_FACING_DETECTION_ENABLED=0` — hub ignores the exposure map and classifies everything as `internet_facing=False` (pre-BC-030 behavior). Scout still collects (cheap single Docker API call) so re-enabling is instant; the dial lives at hub level only.

---

## Scope

- `poc/scout/scout.py` — new `is_container_internet_facing()` helper, new `collect_container_ports()`, payload field.
- `poc/hub/hub.py` — Finding dataclass gets `internet_facing`; DB column + idempotent migration + legacy backfill (default False — matches legacy pre-BC-030 semantics); `process_sbom_entry` accepts port map arg; `receive_scan` parses + threads it through; `save_finding` writes it; `format_notification` + `format_heartbeat` + `format_regression` include 🌐 for exposed.
- `poc/hub/executor/base.py` — Finding dataclass field.
- `poc/scripts/findings.py` — new exposure column.
- `poc/tests/unit/test_scout_container_ports.py` — port-map parsing + is-exposed helper tests.
- `poc/tests/unit/test_internet_facing_classification.py` — ingest flow: finding ingest with/without port map → correct tier.
- `poc/tests/integration/test_BC_030_internet_facing_detection.py` — Gherkin scenarios 1–4 as integration test over the hub POST path.
- `poc/tests/unit/test_findings_cli.py` — exposure column render.
- `docs/CAPABILITIES.md` — CAP-T068, CAP-T069, CAP-T070, CAP-B020.
- `docs/stories/BC-030-internet-facing-detection.md` — status → in-progress.
- `docs/stories/INDEX.md` — status flip.

---

## Non-goals

- Firewall rule introspection (`iptables -L`): a container published to `0.0.0.0` may be firewalled off; honest conservative default is "treat as internet-facing".
- Reverse-proxy-aware detection (nginx-proxy fronts an internal container → internal container looks LAN-only even though its code is reachable). Each container evaluated independently.
- Non-Docker exposure (host-level services bound to `0.0.0.0`). V1 is Docker containers only.
- Dynamic re-classification: tier is set at ingest time; if a container is re-published later, the next scout cycle writes a NEW finding row for the same CVE (via `save_finding`'s `INSERT OR IGNORE`, the existing row keeps its old tier). Trade-off accepted — fixing requires BC-031-style reconciliation on tier changes, out of scope here.

---

## Design decisions

- **Scout returns `{image_tag: bool}`, not raw port maps**: hub doesn't need port details, just the exposure dimension. Reduces payload bytes, hides the host-ip-parsing logic in scout.
- **Loopback-only ≠ internet-facing**: ports bound to `127.0.0.1`, `::1`, or `localhost` return False. Empty `HostIp` AND `0.0.0.0` AND `::` → True.
- **Image-level, not container-level**: multiple containers can share an image. If ANY container running that image publishes externally, the image is marked exposed. Hub matches by `source_ref` which is image tag.
- **DB column default False for legacy rows**: `internet_facing=False` is the pre-BC-030 behavior → legacy rows render the same way they always did (no 🌐 marker). No backfill needed because the real detection only runs on fresh scans anyway.
- **Rejected: iptables introspection** — root-level, platform-fragmented (nftables on Arch, firewalld on RHEL, UFW on Ubuntu). Honest conservative default is enough for V1.
- **Enable/disable toggle lives at hub, not scout**: `BEACONRY_INTERNET_FACING_DETECTION_ENABLED=0` → hub ignores the exposure map and treats everything as `internet_facing=False`. Scout always collects (one `docker.containers.list()` call ≈ free) so flipping the toggle is a hub restart only. Default ON because the whole point of the story is making Tier 1 reachable; mirrors BC-031 reconcile's "privacy-knob off → classifier dumbs down to legacy" pattern.

---

## Breaking changes

### 1. API / return shape

**Payload**: scout's POST body gains an optional `container_exposure: {image_tag: bool}` field. Hub tolerates its absence (treats as all-False → unchanged behavior for older scouts). Older scouts hitting newer hub: still work, Tier 1 unreachable (same as pre-BC-030).

**Finding dataclass**: gains `internet_facing: bool = False`. All existing constructors keep working (positional + keyword) because the field has a default.

### 2. Data model (schema + legacy rows)

Added column `internet_facing INTEGER DEFAULT 0`. Legacy rows (pre-BC-030) show `internet_facing=0` → render as "—" (not 🌐). This is honest: pre-BC-030 we didn't *know* the exposure, we just hard-coded False. Legacy rows accurately reflect "unknown-treated-as-false" which was the previous behavior. **No backfill required** — the behavioral meaning is preserved.

**Migration**: idempotent `ALTER TABLE findings ADD COLUMN internet_facing INTEGER DEFAULT 0` in `init_db`, wrapped in `contextlib.suppress(sqlite3.OperationalError)`.

### 3. Behavioral semantics

- **Operators who had CVSS≥9 findings on exposed containers**: these previously tiered as Tier 2. Post-BC-030 they tier as Tier 1 → may fire a `format_notification` on the next scout cycle (first-fire notification) because the finding row is new (new CVE on an image not seen before) OR because this new scan creates a different `last_notified_ts` cadence. Current open rows retain their stored tier (data model symmetry preserved); only fresh ingestions get Tier 1.
- **Noisier notifications**: no — Tier 1 is simply correctly surfacing the differentiation the design always promised. If anything the operator *wanted* this.
- **Opt-out knob available**: `BEACONRY_INTERNET_FACING_DETECTION_ENABLED=0` restores pre-BC-030 behavior (all `internet_facing=False`, Tier 1 unreachable). Default ON because the whole point is correctness.

No breaking changes expected to be discovered during implementation — the three dimensions were walked in planning. The one real risk zone is **schema evolution for legacy rows**, handled above with "default 0 = pre-BC-030 unknown-False".

---

## Merge plan

1. All work on `feat/BC-030-internet-facing-detection`.
2. `make check` must pass on branch tip.
3. PR to `dev`; Claude opens, user runs `/ultrareview`, Claude runs `/code-review`.
4. Merge strategy: fast-forward (atomic feature).
5. Post-merge: story status → `shipped`, update `docs/stories/INDEX.md`, run `make live-rebuild`.

---

## Rollout plan

Additive. Operators who do nothing continue running — hub tolerates missing `container_exposure` field (treats as all-False → pre-BC-030 behavior preserved). After both services rebuild, exposure info starts flowing on the next scout cycle.

---

## Shipping evidence

| Scenario / goal | Evidence type | Pointer | Status |
|---|---|---|---|
| Published 0.0.0.0:80 → internet_facing=True → tier=1 | integration | `test_BC_030_internet_facing_detection.py::test_exposed_container_tier_1` | ✓ |
| No port map → internet_facing=False → tier=2 | integration | `test_BC_030_internet_facing_detection.py::test_no_ports_tier_2` | ✓ |
| Loopback 127.0.0.1:80 → internet_facing=False | integration | `test_BC_030_internet_facing_detection.py::test_loopback_only_not_exposed` | ✓ |
| KEV overrides internet_facing | integration | `test_BC_030_internet_facing_detection.py::test_kev_wins_over_internet_facing` | ✓ |
| Detection disabled → all findings treated as not exposed | integration | `test_BC_030_internet_facing_detection.py::test_detection_disabled_forces_lan_only` | ✓ |
| Scout parses `0.0.0.0`/`::`/empty as exposed | unit | `test_scout_container_ports.py::test_is_exposed_truthy_hosts` | ✓ |
| Scout parses `127.0.0.1`/`::1` as not exposed | unit | `test_scout_container_ports.py::test_is_exposed_loopback_falsy` | ✓ |
| Scout handles null Ports dict | unit | `test_scout_container_ports.py::test_no_ports_dict_safe` | ✓ |
| Scout aggregates multi-container-same-image | unit | `test_scout_container_ports.py::test_multi_container_or_aggregate` | ✓ |
| Finding dataclass has internet_facing default False | unit | `test_internet_facing_classification.py::test_finding_default_false` | ✓ |
| DB migration idempotent | unit | `test_internet_facing_classification.py::test_init_db_migration_idempotent` | ✓ |
| save_finding persists internet_facing | unit | `test_internet_facing_classification.py::test_save_finding_persists_internet_facing` | ✓ |
| make findings renders 🌐 for exposed, — for LAN-only | unit | `test_findings_cli_internet_facing.py::test_exposure_column_render_exposed` | ✓ |
| Telegram notification body carries 🌐 for Tier 1 | unit | `test_hub_helpers.py::test_format_notification_tier_1_marker` | ✓ |
| KEV header still wins over Tier 1 marker | unit | `test_hub_helpers.py::test_format_notification_tier_0_wins_over_tier_1_marker` | ✓ |
| LAN-only CVSS≥9 omits 🌐 marker | unit | `test_hub_helpers.py::test_format_notification_tier_1_marker_absent_for_lan_only` | ✓ |
| Backward-compat: process_sbom_entry works without port_exposure arg | integration | `test_BC_030_internet_facing_detection.py::test_missing_port_exposure_arg_defaults_safe` | ✓ |
| CAP-T068 + CAP-T069 + CAP-T070 + CAP-B020 registered + bidirectional | verify-traceability | `docs/CAPABILITIES.md` diff | ✓ |
| make check green | multi-gate | `make check` | ✓ |
| Memory drift pattern added if applicable | N/A | no documented behavior deprecated | ✓ |

Legend: ✓ covered · ⚠️ partial (notes required) · ❌ open (blocks shipping).

---

## Testing strategy

- Unit tests pin all branches of the port-parsing helper (truthy host IPs, loopback IPs, missing fields, multi-container aggregation).
- Integration test spins up the hub POST path with a crafted `container_exposure` map and asserts tier assignment matches the Gherkin.
- CLI render test confirms the exposure column shows 🌐/— correctly.

---

## Testing commands

```bash
make check
pytest poc/tests/integration/test_BC_030_internet_facing_detection.py -v
pytest poc/tests/unit/test_scout_container_ports.py poc/tests/unit/test_internet_facing_classification.py -v
```
