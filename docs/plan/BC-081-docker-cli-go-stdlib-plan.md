# fix/BC-081 — bump docker CLI to pick up Go stdlib ≥1.26.9

*Branch: `fix/BC-081-docker-cli-go-stdlib`*
*Start: 2026-11-03*

## Repro

Operator Telegram (hetzner-staging, `beaconry-hub:latest`):

```
🔴 INTERNET-FACING · Tier 1
GO-2026-6604 · CVSS 9.8
Package: stdlib go1.22.11 → fixed in 1.26.9
Fix viability: ✅ likely — mutable tag 'latest'
```

Scout + grype correctly matched the Go stdlib version bundled in
the `/usr/bin/docker` CLI binary of beaconry's own hub image. The
binary was `docker 27.5.1` (released by Docker Inc. with go1.22.11
embedded). GO-2026-6604 is a Windows-only path-traversal in
`os.Root.Mkdir`; not reachable from a Linux container running in
distroless. But beaconry's own alert kept firing on its own tool
image, which erodes trust in all alerts.

## Fix

One-line Dockerfile change: `ARG DOCKER_CLI_VERSION=27.5.1` →
`ARG DOCKER_CLI_VERSION=29.9.2`. 29.9.2 is built with go1.26.10,
which is past the fixed version. Verified by downloading the
static tarball + running `docker version --format '{{.Client.GoVersion}}'`.

## Verification

- `docker exec beaconry-hub /usr/bin/docker --version` on a rebuilt
  image shows `27.5.1 → 29.9.2`.
- `grype` run against the rebuilt image no longer reports
  GO-2026-6604 against the docker CLI binary. (Hub also ships
  grype + syft binaries built with go1.27.3; those were never
  flagged.)
- `make check-full` green.

## Breaking changes

None — docker CLI 29.x is backwards-compatible with the hub's
subset of commands (`docker pull`, `docker tag`, `docker ps`).

## Shipping evidence

| Scenario / goal | Evidence | Pointer | Status |
|---|---|---|---|
| Dockerfile pins DOCKER_CLI_VERSION ≥ 29.9.2 | manual | `grep DOCKER_CLI_VERSION poc/hub/Dockerfile` | ✓ |
| CHANGELOG documents the bump | manual | `grep DOCKER_CLI_VERSION CHANGELOG.md` | ✓ |
| `make check` + `make check-full` green | multi-gate | logs | ✓ |

## Version

0.8.1 → 0.8.2 (patch — pinned-dep bump, no behavior change).
