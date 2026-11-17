# BC-141 + BC-142 + BC-143 + BC-144 — Memory-safety bundle (combined branch plan)

Four stories, one branch, one release — the response to the
2026-11-17 host-OOM outages.

- **BC-141** container + systemd memory/CPU caps → `docs/plan/BC-141-process-memory-caps-plan.md`
- **BC-142** `/healthz` memory metrics → `docs/plan/BC-142-memory-metrics-on-healthz-plan.md`
- **BC-143** tracemalloc toggle + playbook → `docs/plan/BC-143-tracemalloc-toggle-plan.md`
- **BC-144** `make perf` memory-growth gate → `docs/plan/BC-144-perf-gate-memory-assertion-plan.md`

Each sub-plan ships independently reviewable code. One PR, one
squash-merge, one `v0.22.18` release tag.

Co-ship rationale: the 4 BCs together close the full loop — BC-141
contains OOM blast radius, BC-142 makes memory climb visible, BC-143
lets operators identify the source, BC-144 keeps future BCs from
re-shipping a leak. Any one on its own leaves a gap:

- BC-141 alone: container cycles forever without the operator
  knowing why.
- BC-142 alone: operator sees growth but the hub still takes the host
  down on OOM.
- BC-143 alone: debug tool with no production backstop.
- BC-144 alone: gates future regressions but doesn't address today's.

## Branch-level shipping evidence

| Gate | Evidence |
|---|---|
| BC-141 Gherkin covered | sub-plan ✓ |
| BC-142 Gherkin covered | sub-plan ✓ |
| BC-143 Gherkin covered | sub-plan ✓ |
| BC-144 Gherkin covered | sub-plan ✓ |
| `make check` | fast tier green on branch tip |
| `BEACONRY_SKIP_DOGFOOD=1 make check-full` | full tier green |
| Live post-deploy — hub comes up with caps + metrics + default-off tracemalloc | manual, on hetzner-staging post-redeploy |
| Live post-deploy — `make perf` reports Memory check row | manual, on bb-82jq laptop |
