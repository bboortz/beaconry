---
id: BC-075
title: Beaconry metrics + threshold-based soak signals
status: shipped
created: 2026-11-02
branch: feat/BC-075-metrics-and-soak-signals
plan: docs/plan/BC-075-metrics-and-soak-signals-plan.md
---

# BC-075: Metrics + soak signals

Operator asked to shift from building to measuring, to verify
beaconry's usefulness + correctness. Two personas, two lenses, one
audit log:

- **Key user**: "is this tool worth it?" → usability lens
- **Developer**: "which features earn their keep?" → features lens

Plus: `make soak` learns threshold-based signals so it can also
answer "would a developer deny the merge?"

## Shipped

- `poc/scripts/metrics.py` + `make metrics` + `beaconry metrics`
  subcommand. Three lenses: `usability` / `features` / `all`
  (default). `--since 7d|24h|30m` + `--json` flags.
- `poc/scripts/soak.py` gains `_detect_anomalies` + `_detect_pipeline_silence`:
  notifier-failure ratio, partial-inventory ratio, reminder storm,
  pipeline silence. Section-separated output; same exit codes.
- `docs/METRICS.md` interprets every metric + threshold.
- CAP-T155, CAP-T156, docs/CAPABILITIES.md updated.

## Version

0.4.0 → 0.5.0 (minor — new user-visible subcommand + visibly
changed soak output).
