---
id: BC-081
title: fix — bump docker CLI to Go stdlib ≥1.26.9
status: shipped
created: 2026-11-03
branch: fix/BC-081-docker-cli-go-stdlib
plan: docs/plan/BC-081-docker-cli-go-stdlib-plan.md
---

# BC-081: Bump docker CLI to pick up Go stdlib ≥1.26.9

Fix with a clear repro — not a user story. See the plan doc for
Telegram screenshot + full detail.

Operator saw a Tier-1 alert against `beaconry-hub:latest` on
hetzner-staging: `GO-2026-6604 · stdlib go1.22.11 → fixed in
1.26.9`. The vuln is in the Go stdlib embedded in the
`/usr/bin/docker` CLI binary we `COPY --from=builder` into the
distroless runtime. 27.5.1 was built with go1.22.11; 29.9.2 was
built with go1.26.10.

**Reachability**: zero. GO-2026-6604 is a Windows-only path
traversal in `os.Root.Mkdir`. We run Linux + distroless. But
beaconry alerting on itself erodes trust in all alerts, so fix
at the source.

**Change**: one-line Dockerfile bump of `ARG DOCKER_CLI_VERSION`.
