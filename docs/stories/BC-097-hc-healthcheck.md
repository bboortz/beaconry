---
id: BC-097
title: Compose healthchecks use forge's `/hc`; runtime base tracks `:latest`
status: shipped
created: 2026-11-08
branch: feat/BC-097-hc-healthcheck
plan: docs/plan/BC-097-hc-healthcheck-plan.md
---

# BC-097: Swap inline `python3 -c "import urllib.request..."` compose healthchecks for forge's `/hc` + move runtime base from `:3.13` → `:latest`

## Narrative

Beaconry's runtime image (`ghcr.io/bboortz/python-scratch-trixie:3.13`,
BC-083) is scratch — no shell, no curl. The compose healthcheck
convention from BC-010 (`memory/docker-standard.md § Healthcheck
patterns`) works around that by invoking the already-present python3
interpreter inline:

```yaml
test: ["CMD", "python3", "-c",
       "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://localhost:8000/readyz').status==200 else 1)"]
```

Correct, but noisy: a full CPython cold-start boot per probe every
30 s per container, for one HTTP GET. Forge's **FG-031** (2026-11-08)
addresses this upstream — every `*-scratch-trixie` image now ships
`/hc`, a ~400 KB static healthcheck binary with a `curl`-exit-code
contract: `/hc -t <timeout_s> <url>` exits 0 iff the URL returned 2xx.

Bundled with the probe swap: move both Dockerfile runtime stages
from `python-scratch-trixie:3.13` to `python-scratch-trixie:latest`.
Rationale — forge rebuilds `:latest` on every push to main, which is
how FG-031's `/hc` + future patches land fastest. The `:3.13`
semver-pinned mirror lags. Keeping `:3.13` would stretch out every
forge-side improvement by the next rebuild window; `:latest` matches
how we already consume `forge-builder-trixie:latest` on the builder
side (BC-095).

Compose probes shrink to:

```yaml
test: ["CMD", "/hc", "-t", "3", "http://localhost:8000/readyz"]
```

Six probe sites across five compose files (`docker-compose.yml`,
`docker-compose.cold-start.yml`, `docker-compose.scout-only.yml`,
`scenarios/compose.yml`) adopt the new shape in one branch. The
canonical recipe in `memory/docker-standard.md` + `memory/health-
checks.md` rewrites to lead with `/hc`; the python-inline form stays
documented as a fallback for services on bases that don't ship
`/hc`. `verify_pinned_versions.py`'s `RUNTIME_IMAGE_RE` gains
`:latest` alongside the existing `:3.13` + distroless fallbacks.

## Trigger

Not event-driven. Follow-on to forge FG-031 — `ghcr.io/bboortz/python-
scratch-trixie:3.13` now carries `/hc` on PATH. Beaconry's daily
pull picks it up automatically; the compose probes just need to use
it.

## Expected outcome

- `poc/hub/Dockerfile` + `poc/scout/Dockerfile` runtime stage FROM
  `ghcr.io/bboortz/python-scratch-trixie:latest AS runtime`. No
  `:3.13` reference remains. `verify_pinned_versions.py` accepts the
  new tag.
- Every beaconry compose healthcheck across the 5 compose files uses
  `["CMD", "/hc", "-t", "3", "http://localhost:<port>/<path>z"]`. Zero
  inline `python3 -c` probes remain in compose YAML.
- `docker inspect beaconry-hub --format '{{.State.Health.Status}}'`
  still reaches `healthy` within the existing `start_period` budget
  (60 s). Cold-start smoke (BC-082) stays green.
- `memory/docker-standard.md § Healthcheck patterns` + `memory/health-
  checks.md § Compose consumes only one healthcheck` lead with the
  `/hc` form. The python-inline recipe stays documented as the
  fallback for non-forge-based services.
- No change to probe semantics — still points at `/readyz` on hub +
  scout, still points at `/startupz` on `scenarios/compose.yml` (per
  BC-038's rationale that scenarios don't invoke grype).

## Risk: Python minor-version drift under `:latest`

`COPY --from=builder /install/lib/python3.13/site-packages …` hardcodes
the Python minor version both in the builder prefix AND in the runtime
destination path. Today builder + runtime both ship Python 3.13.18
(ABI-matched). If forge ever bumps the base to Python 3.14 in
`:latest` while we still pull `forge-builder-trixie:latest` (also
`:latest`), the ABI stays matched but the hardcoded `/python3.13/`
paths break the COPY.

Mitigation: this story does NOT introduce a Python-version detect
step — too much complexity for a single upgrade window. Instead,
`make check-full`'s cold-start test exercises a full rebuild on every
PR; a surprise 3.14 bump trips cold-start the same day forge
publishes. Operator remedy: bump `COPY /install/lib/python3.X/` on
both Dockerfiles. Future BC can introduce a dynamic path if the
cadence of Python minor bumps becomes painful.

## Out of scope

- **Baking a `HEALTHCHECK` instruction into `poc/{hub,scout}/Dockerfile`.**
  FG-031 explicitly declined to bake `HEALTHCHECK` into
  `python-scratch-trixie` because consumer ports + paths vary — the
  compose block remains the right layer for beaconry.
- **Pinning the `hc` version.** Forge pins `HC_REF=ghcr.io/bboortz/hc:0.1.0`
  in its own Dockerfile; beaconry consumes whatever `/hc` the forge
  base ships. Future `/hc` CLI breakage would surface via cold-start +
  scenarios smoke, same escalation path as any forge-base regression.
- **Updating any non-compose healthcheck surface.** The file-sentinel
  path in `beaconry_health/sentinels.py` (retained for hypothetical
  HTTP-less services per BC-013) is untouched — `/hc` is HTTP-only.
- **Removing the `/healthz` operator alias.** Separate concern; `/hc`
  only replaces how compose probes the endpoints, not the endpoint
  shape itself.
- **Pacing a transition window for operators who pin an older
  `python-scratch-trixie:3.13-<date>` tag.** Mitigation: keep the
  python-inline fallback recipe in `memory/docker-standard.md` for
  one release cycle, flagged as "legacy". Hard removal in a follow-up
  BC once homelab + any downstream consumers have rotated to the
  `/hc`-bearing base.

## Acceptance criteria (Gherkin)

```gherkin
Feature: Beaconry compose healthchecks use forge's /hc

  Scenario: Both Dockerfiles track :latest for the runtime base
    Given a checkout of beaconry on this branch
    When a reader greps poc/{hub,scout}/Dockerfile for the runtime FROM
    Then both match `FROM ghcr.io/bboortz/python-scratch-trixie:latest AS runtime`
     And no :3.13 reference remains

  Scenario: verify_pinned_versions accepts :latest
    When `make verify-pinned-versions` runs
    Then the gate passes
     And `python-scratch-trixie:latest` is in its accepted-tag list

  Scenario: Zero inline-python healthchecks remain
    Given a checkout of beaconry on this branch
    When a reader greps every poc/docker-compose*.yml + poc/scenarios/compose.yml
         for `python3 -c`
    Then no match occurs inside a `healthcheck:` block

  Scenario: Every healthcheck uses /hc with a timeout
    When a reader greps every compose healthcheck test
    Then each one matches `["CMD", "/hc", "-t", "3", "http://localhost:<port>/<path>z"]`
     And the port + path still target the same endpoint as before this branch

  Scenario: Live stack turns healthy via /hc
    Given `make live-rebuild` on this branch
    When docker inspect reports State.Health.Status for hub + scout
    Then both read "healthy" within 60s of start

  Scenario: Cold-start + scenarios + e2e smoke all green
    When `make check-full` runs
    Then every healthcheck-dependent gate passes
     And no probe-boot-time regression is observed in cold-start logs

  Scenario: Memory convention leads with /hc
    When a reader opens memory/docker-standard.md § Healthcheck patterns
    Then the first recipe uses `/hc`
     And the python-inline form is marked legacy fallback

  Scenario: Memory drift lint tolerates the new pattern
    When `make verify-memory-drift` runs
    Then the python-inline recipe is not flagged (fallback remains documented)
     And no legacy-pattern-only rule fires
```

## Capabilities delivered

- **CAP-T198: Compose healthchecks use forge's `/hc`** (type:
  technical, status: active). Shrinks every healthcheck's steady-state
  cost (static-binary exec vs. Python interpreter cold start) and
  shares vocabulary with every other forge-based consumer.
- **CAP-T199: Runtime base tracks `python-scratch-trixie:latest`**
  (type: technical, status: active). Daily-rebuilt latest tag puts
  beaconry on forge's fastest patch path (symmetric with
  BC-095's builder base). Trade-off documented under "Risk: Python
  minor-version drift" above.

Reference existing CAPs:
- Extends CAP-T043 (four-endpoint convention) — same endpoints, new
  probe mechanism.
- Supports CAP-B023 (operator trust in the supply chain) — `/hc` is
  cosign-signed via forge's identity along with the rest of the base.
- Depends on forge FG-031 (image.hc-embedded).

## Open questions

- Should `start_interval: 2s` (Docker 25+ fast-probe) still be set,
  or does `/hc`'s sub-10 ms startup make it redundant? Keep it for
  now — the knob is cheap and Docker will respect whichever is set.
- Does `/hc` honour HTTP-via-stdin shapes? Not needed today; all
  beaconry probes are plain GET.
- Confirm `/hc`'s exact exit-code behavior on timeout vs. 5xx. Assumed
  non-zero for both; `make check-full`'s cold-start test catches a
  false-positive healthy.

## Links

- Forge FG-031 (origin): `~/git/forge/docs/stories/FG-031-hc-in-service-images.md`
- Forge FG-030 (`hc` tool itself): `~/git/forge/docs/stories/FG-030-healthy-scratch-v2.md`
- Upstream `hc` binary: `ghcr.io/bboortz/hc:0.1.0` from `bboortz/healthy`
- BC-010 (four-endpoint convention — same endpoints, different probe tool)
- BC-013 (HTTP probes beat file-sentinels for deadlock detection)
- BC-083 (runtime migration to `python-scratch-trixie`)
- BC-095 (builder migration to forge-builder-trixie — the sibling pattern)
- `memory/docker-standard.md § Healthcheck patterns` + `memory/health-checks.md` — rewritten by this story
