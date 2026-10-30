# fix/BC-007-close-gaps — Plan

*Branch: `fix/BC-007-close-gaps`*
*Start date: 2026-10-30*

---

## Situation

Post-ship audit of BC-007 found three gaps:

1. **Sort key missed `tier`.** The Gherkin + Expected outcome said rows sort by `(status, tier, -cvss)`. The implementation sorts by `(status_rank, -cvss)` only. Current data all has `tier=2` so visible order looks right by accident, but if tier-0 and tier-2 findings coexist within an "open" group, tier-2 would come first on a high-cvss tie. Latent correctness issue.
2. **Compose named-volume access undocumented.** The code honors `DATA_DIR`, but the Compose volume `beaconry-data` is a Docker named volume (mount point under `/var/lib/docker/volumes/...`), not a trivially-reachable host path. The "works against both local and Compose" promise is a half-truth; operator needs a specific docker command to reach the volume.
3. **README dev-quickstart doesn't name `make findings`.** The plan said it should. Only the gate-tiers table was updated.

## Problems to solve

- Sort correctness
- Operator can't find instructions for querying the Compose DB
- Dev-quickstart section incomplete

## Goals

1. Findings reporter includes `tier` in the SELECT list and in the sort key — rows ordered by `(status_rank, tier, -cvss)`
2. Regression test asserting the sort order against a fixture with mixed tiers
3. README dev-quickstart lists `make findings` as a daily-use target
4. README documents the "query the Compose volume" workaround (a one-liner `docker run --rm` snippet)

## Scope

- `poc/scripts/findings.py` — add `tier` to COLUMNS + sort key
- `poc/tests/integration/test_BC_007_findings_cli.py` — new sort-with-tier test, update existing `_row` to carry a tier default
- `README.md` — findings line in dev-quickstart; workaround snippet
- `docs/CAPABILITIES.md` — CAP-B012 description now notes tier in the sort
- This plan doc

## Non-goals

- Making the Compose volume path transparent (would require either `docker volume inspect` plumbing in the CLI, or a published volume mount in compose yml — both scope creep)
- Adding a `--tier` filter flag (future, if asked)
- Changing column order (tier isn't shown; just used for sort)

## Design decisions

**Query tier but don't display it.** Row width is already at 110 chars; adding a tier column pushes to 113+. Operators mostly see "Critical / High / Medium" (severity), which carries the same signal. Keep tier internal to the sort.

**Rejected: `make findings` could `docker volume inspect` and auto-locate the Compose volume.** Too much platform-specific logic (docker ≠ podman ≠ containerd); error paths proliferate. Document the workaround instead.

## Breaking changes

None.

## Merge plan

1. Plan + implementation + regression test
2. `make check` + `make check-full` green
3. Fast-forward merge to dev

## Testing strategy

- Unit-ish test: fixture rows with mixed statuses + mixed tiers + mixed cvss; assert the output order matches `(status_rank, tier, -cvss)`.
- Existing tests continue to pass (default `_row` tier added).

## Testing commands

```bash
source .venv/bin/activate
make check
make findings
```
