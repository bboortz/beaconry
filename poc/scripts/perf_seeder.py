"""BC-132 — Deterministic perf-fixture seeder.

Populates ``$DATA_DIR/beaconry.db`` + ``$DATA_DIR/audit.log`` with a
representative-but-small fleet state so `make perf` measures endpoints
under realistic shape without taking minutes.

Default seed: 3 scouts · 1 000 open findings · 10 000 audit events
with the event-type mix observed on 2026-11-17 production:

- 85% finding_filtered (dominant — rate-cap MIN_CVSS rejections)
- 10% heartbeat_received (one per 60 s per opt-in scout)
- 3% scan_integrity (one per scan per host)
- 1% scan_received (one per /scan POST)
- rare: digest_sent, scout_offline, hub_startup, notify_failed,
  heartbeat_integrity_mismatch, transfer_hash_mismatch

Same ``random.Random(seed)`` input → byte-equivalent output across
machines so perf measurements from different operators compare cleanly.

Usage:

    python -m poc.scripts.perf_seeder                   # seeds $DATA_DIR
    python -m poc.scripts.perf_seeder --out /tmp/x      # custom dir
    python -m poc.scripts.perf_seeder --seed 7          # alternate seed
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import sys
import time
from pathlib import Path

# ── Workload shape ─────────────────────────────────────────────────────

DEFAULT_SCOUTS = ["hetzner-staging", "server2", "bb-82jq"]
DEFAULT_SOURCE_REFS = [
    "ghcr.io/bboortz/beaconry-hub:latest",
    "ghcr.io/bboortz/beaconry-scout:latest",
    "ghcr.io/bboortz/cadvisor-slim-trixie:latest",
    "ghcr.io/bboortz/postgres-slim-trixie:18",
    "ghcr.io/bboortz/uptime-kuma-slim-trixie:latest",
    "unifi-network:latest",
    "homeassistant-patched:latest",
    "homelab-bot:latest",
]
DEFAULT_PACKAGES = [
    ("golang.org/x/crypto", "0.14.0", "0.21.0"),
    ("golang.org/x/net", "0.17.0", "0.23.0"),
    ("google.golang.org/grpc", "1.57.0", "1.64.0"),
    ("tomcat-embed-core", "10.1.5", "10.1.19"),
    ("libxml2", "2.9.10", "2.9.14"),
    ("curl", "7.88.1", "8.7.1"),
    ("python", "3.14.10", "3.14.11"),
    ("nginx", "1.24.0", "1.26.0"),
    ("jsonata", "1.8.6", "2.0.3"),
]
SEVERITIES = ["Critical", "High", "Medium"]
CVSS_BY_SEVERITY = {"Critical": (9.0, 10.0), "High": (7.0, 8.9), "Medium": (4.0, 6.9)}

EVENT_MIX = [
    # Weights sum to ~1.0; tuned to mirror observed live-hub rates
    # where hub_startup is ultra-rare (~1 per deploy = ~10/week vs
    # millions of finding_filtered events). This is deliberately a
    # worst-case for the tail reader when operator queries filter
    # by one of the rare types.
    ("finding_filtered", 0.86),
    ("heartbeat_received", 0.10),
    ("scan_integrity", 0.03),
    ("scan_received", 0.005),
    ("digest_sent", 0.002),
    ("scout_offline", 0.001),
    ("notify_failed", 0.001),
    ("heartbeat_integrity_mismatch", 0.0005),
    ("transfer_hash_mismatch", 0.0005),
    ("hub_startup", 0.00001),  # 1 per 100k events — real production rate
]

# Window of fake timestamps the fixture spans — 7 days backward from
# the deterministic "now". Using a fixed anchor keeps byte-equivalence.
FIXTURE_NOW = 1794870000  # 2026-11-17 00:20 UTC — matches BC-131 ship
FIXTURE_WINDOW_S = 7 * 24 * 3600


# ── Seeder ─────────────────────────────────────────────────────────────


def _seed_db(db_path: Path, rng: random.Random, n_findings: int) -> None:
    """Create schema + insert scouts/findings. Uses the hub's own
    schema via hub.init_db() so the seeder tracks schema version."""
    # Ensure hub module writes to the right paths.
    os.environ["DATA_DIR"] = str(db_path.parent)
    # Lazy-import to pick up the DATA_DIR override. pytest sets
    # pythonpath=[".", "poc/hub"] for `from executor.base import ...`;
    # replicate here so `python -m poc.scripts.perf_seeder` works.
    repo_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo_root))
    sys.path.insert(0, str(repo_root / "poc" / "hub"))
    from poc.hub import hub

    hub.DATA_DIR = db_path.parent
    hub.DB_PATH = db_path
    hub.init_db()

    con = sqlite3.connect(db_path)
    try:
        # 3 scouts.
        for i, host in enumerate(DEFAULT_SCOUTS):
            con.execute(
                "INSERT OR REPLACE INTO scouts "
                "(host, first_seen_ts, last_posted_ts, last_sbom_count, "
                " software_version, api_version, last_health, last_scan_ts, role) "
                "VALUES (?, ?, ?, ?, '0.22.15', 1, 'ok', ?, 'workstation')",
                (
                    host,
                    FIXTURE_NOW - FIXTURE_WINDOW_S,
                    FIXTURE_NOW - rng.randint(0, 300),
                    20 + i * 10,
                    FIXTURE_NOW - rng.randint(60, 900),
                ),
            )

        # 1 000 open findings distributed across scouts × source_refs.
        for i in range(n_findings):
            host = rng.choice(DEFAULT_SCOUTS)
            source_ref = rng.choice(DEFAULT_SOURCE_REFS)
            pkg, ver, fix = rng.choice(DEFAULT_PACKAGES)
            severity = rng.choice(SEVERITIES)
            cvss_lo, cvss_hi = CVSS_BY_SEVERITY[severity]
            cvss = round(rng.uniform(cvss_lo, cvss_hi), 1)
            cve_id = f"CVE-2026-{100000 + i}"
            tier = 1 if cvss >= 9.0 else (2 if cvss >= 7.0 else 3)
            fid = f"perf-fid-{i:05d}"
            found_at = FIXTURE_NOW - rng.randint(0, FIXTURE_WINDOW_S)
            con.execute(
                "INSERT OR IGNORE INTO findings "
                "(id, host, cve_id, package, version, fixed_version, severity, "
                " cvss, description, source_type, source_ref, status, found_at, "
                " tier, kev, internet_facing, last_seen_ts) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'docker', ?, 'open', ?, ?, 0, 0, ?)",
                (
                    fid,
                    host,
                    cve_id,
                    pkg,
                    ver,
                    fix,
                    severity,
                    cvss,
                    f"Representative perf fixture CVE {i}",
                    source_ref,
                    found_at,
                    tier,
                    found_at,
                ),
            )
        con.commit()
    finally:
        con.close()


def _seed_audit(audit_path: Path, rng: random.Random, n_events: int) -> None:
    """Emit n_events lines of JSONL matching the production event mix.
    Events are written oldest-first (matches real audit.log order)."""
    # Pre-compute the type lookup via a cumulative-weight list so a
    # single random draw picks the type for each event.
    types, weights = zip(*EVENT_MIX, strict=True)
    cum_weights: list[float] = []
    acc = 0.0
    for w in weights:
        acc += w
        cum_weights.append(acc)

    with audit_path.open("w") as f:
        for i in range(n_events):
            # Linear ts across the 7-day window so queries by `since`
            # hit realistic distributions.
            ts = FIXTURE_NOW - FIXTURE_WINDOW_S + int(FIXTURE_WINDOW_S * i / n_events)
            r = rng.random()
            type_idx = 0
            for j, cum in enumerate(cum_weights):
                if r <= cum:
                    type_idx = j
                    break
            event_type = types[type_idx]
            body = _build_event(event_type, ts, rng, i)
            f.write(json.dumps(body) + "\n")


def _build_event(event_type: str, ts: int, rng: random.Random, i: int) -> dict:
    """Shape each audit event to match what hub.py actually writes
    today — so the perf gate exercises the exact deserialize / enrich
    paths production uses."""
    host = rng.choice(DEFAULT_SCOUTS)
    base = {"ts": ts, "event": event_type, "host": host}

    if event_type == "finding_filtered":
        base["cve_id"] = f"CVE-2026-{100000 + rng.randint(0, 50000)}"
        base["reason"] = "min_cvss"
        base["cvss"] = round(rng.uniform(0.0, 6.9), 1)
        base["min_cvss"] = 7.0
    elif event_type == "heartbeat_received":
        base["health"] = "ok" if rng.random() > 0.05 else "degraded"
    elif event_type == "scan_integrity":
        base["iteration_hash"] = f"{rng.randrange(16**16):016x}"
        base["transfer_hash_claimed"] = f"{rng.randrange(16**16):016x}"
        base["transfer_hash_computed"] = base["transfer_hash_claimed"]
        base["transfer_match"] = True
        base["n_sboms_claimed"] = rng.randint(10, 60)
        base["n_sboms_received"] = base["n_sboms_claimed"]
        base["n_candidates"] = rng.randint(400, 3500)
        base["n_persisted"] = rng.randint(0, 30)
        base["n_notified"] = rng.randint(0, 5)
        base["n_tail_criticals_notified"] = 0
        base["n_dedup"] = base["n_candidates"] - rng.randint(100, 500)
        base["n_suppressed_from_notify"] = rng.randint(10, 400)
        base["n_dropped_in_save"] = rng.randint(0, 150)
        base["n_filtered_other"] = 0
        base["processing_hash"] = f"{rng.randrange(16**16):016x}"
        base["cache_persisted_ok"] = True
    elif event_type == "scan_received":
        base["sbom_count"] = rng.randint(10, 60)
    elif event_type == "digest_sent":
        base["hash"] = f"{rng.randrange(16**16):016x}"
        base["count"] = rng.randint(10, 300)
    elif event_type == "scout_offline":
        base["silence_s"] = rng.randint(60, 3600)
    elif event_type == "hub_startup":
        base["software_version"] = "0.22.15"
        base["api_version"] = 1
        base["cache_reload_ok"] = True
        base["hosts_in_cache"] = 3
        base["mismatch_counter_at_boot"] = rng.randint(0, 20)
    elif event_type == "notify_failed":
        base["finding_id"] = f"perf-fid-{rng.randint(0, 999):05d}"
        base["cve_id"] = f"CVE-2026-{100000 + rng.randint(0, 999)}"
        base["error"] = "Telegram API returned 429"
    elif event_type in {"heartbeat_integrity_mismatch", "transfer_hash_mismatch"}:
        base["claimed_iteration_hash"] = f"{rng.randrange(16**16):016x}"
        base["cached_iteration_hash"] = f"{rng.randrange(16**16):016x}"
        base["hub_uptime_s"] = rng.randint(60, 86400)
        base["cache_source"] = rng.choice(["live_scan", "sqlite_reload", "no_record"])

    return base


def seed_perf_fixture(
    data_dir: Path,
    *,
    seed: int = 42,
    n_findings: int = 1000,
    n_audit_events: int = 10000,
) -> None:
    """Public entrypoint. Wipes existing DB + audit.log at `data_dir`,
    then seeds. Deterministic given the same `seed`."""
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "beaconry.db"
    audit_path = data_dir / "audit.log"
    if db_path.exists():
        db_path.unlink()
    if audit_path.exists():
        audit_path.unlink()
    rng = random.Random(seed)
    _seed_db(db_path, rng, n_findings)
    # Re-seed for audit so the two streams don't share RNG state (keeps
    # audit byte-identity stable across changes to the DB side).
    _seed_audit(audit_path, random.Random(seed + 1), n_audit_events)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="perf_seeder")
    p.add_argument("--out", default=os.environ.get("DATA_DIR", ".beaconry-cache"))
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--findings", type=int, default=1000)
    p.add_argument("--audit-events", type=int, default=10000)
    args = p.parse_args(argv)
    out = Path(args.out).resolve()
    t0 = time.perf_counter()
    seed_perf_fixture(
        out,
        seed=args.seed,
        n_findings=args.findings,
        n_audit_events=args.audit_events,
    )
    elapsed = time.perf_counter() - t0
    audit_size = (out / "audit.log").stat().st_size
    db_size = (out / "beaconry.db").stat().st_size
    print(
        f"✓ perf fixture seeded in {elapsed:.2f}s at {out}: "
        f"{args.findings} findings, {args.audit_events} audit events "
        f"(audit.log {audit_size // 1024}KB, beaconry.db {db_size // 1024}KB)",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
