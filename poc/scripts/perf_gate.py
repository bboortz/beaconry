"""BC-132 — HTTP latency budget gate.

Hits every endpoint declared in `perf-endpoint-fixtures.toml` against
a perf-seeded hub (via FastAPI TestClient), measures 10 consecutive
calls, reports cold/P50/P95/P99, and fails the gate on breach:

    P95 ≤ BUDGET_P95_MS  (hard ceiling)
    P99 ≤ BUDGET_P99_MS  (allows cold + rare-fallback)

Endpoints listed in `perf-exceptions.toml` with a non-expired
`fix_by` or `permanent = true` are measured but don't fail the gate.

Usage:
    python -m poc.scripts.perf_gate              # local in-process run
    python -m poc.scripts.perf_gate --json out.json
    BEACONRY_PERF_HUB_URL=http://host:port \\
        python -m poc.scripts.perf_gate          # measure a live hub
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import statistics
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Budget (tunable via env, not query-time) ──────────────────────────

BUDGET_P50_MS = float(os.environ.get("BEACONRY_PERF_BUDGET_P50_MS", "100"))
BUDGET_P95_MS = float(os.environ.get("BEACONRY_PERF_BUDGET_P95_MS", "300"))
BUDGET_P99_MS = float(os.environ.get("BEACONRY_PERF_BUDGET_P99_MS", "1000"))

SAMPLES_PER_ENDPOINT = 10
WARM_START = 1  # first call is "cold"; warm stats skip index 0

HERE = Path(__file__).resolve().parent
FIXTURES_PATH = HERE / "perf-endpoint-fixtures.toml"
EXCEPTIONS_PATH = HERE / "perf-exceptions.toml"


# ── Data model ────────────────────────────────────────────────────────


@dataclass
class EndpointSpec:
    key: str  # "/audit#default"
    path: str  # "/audit"
    method: str  # "GET" / "POST"
    query: dict[str, str] = field(default_factory=dict)
    body: dict[str, Any] | None = None


@dataclass
class Measurement:
    key: str
    cold_ms: float
    warm: list[float]  # ms

    @property
    def p50(self) -> float:
        return statistics.median(self.warm)

    @property
    def p95(self) -> float:
        return _pctile(self.warm, 95)

    @property
    def p99(self) -> float:
        return _pctile(self.warm, 99)


@dataclass
class PerfException:
    key: str
    reason: str
    fix_by: dt.date | None
    permanent: bool

    def is_active(self, today: dt.date) -> bool:
        if self.permanent:
            return True
        return self.fix_by is not None and today <= self.fix_by


# ── Helpers ───────────────────────────────────────────────────────────


def _pctile(values: list[float], p: int) -> float:
    """Nearest-rank percentile: for a sorted list of N values, the
    p-th percentile is the value at rank ceil(p/100 * N) (1-indexed).
    Robust for small sample sizes."""
    if not values:
        return 0.0
    import math

    n = len(values)
    rank = max(1, min(n, math.ceil(p / 100 * n)))
    return sorted(values)[rank - 1]


def load_fixtures() -> list[EndpointSpec]:
    """Parse perf-endpoint-fixtures.toml into EndpointSpec list.
    Each toml table's key doubles as the endpoint spec key (e.g.
    "/audit#default"); the `path` field defaults to the key if the key
    has no `#label` suffix."""
    with FIXTURES_PATH.open("rb") as f:
        raw = tomllib.load(f)
    specs: list[EndpointSpec] = []
    for key, value in raw.get("endpoints", {}).items():
        path = value.get("path", key.split("#", 1)[0])
        specs.append(
            EndpointSpec(
                key=key,
                path=path,
                method=value.get("method", "GET"),
                query={k: str(v) for k, v in value.get("query", {}).items()},
                body=value.get("body"),
            ),
        )
    return specs


def load_exceptions() -> dict[str, PerfException]:
    """Parse perf-exceptions.toml. Expired `fix_by` entries are still
    loaded; the caller decides what's active via `is_active(today)`."""
    if not EXCEPTIONS_PATH.exists():
        return {}
    with EXCEPTIONS_PATH.open("rb") as f:
        raw = tomllib.load(f)
    out: dict[str, PerfException] = {}
    for key, value in raw.get("endpoints", {}).items():
        fix_by = value.get("fix_by")
        parsed_date: dt.date | None = None
        if isinstance(fix_by, dt.date):
            parsed_date = fix_by
        elif isinstance(fix_by, str):
            parsed_date = dt.date.fromisoformat(fix_by)
        out[key] = PerfException(
            key=key,
            reason=value.get("reason", ""),
            fix_by=parsed_date,
            permanent=bool(value.get("permanent", False)),
        )
    return out


# ── Measurement ──────────────────────────────────────────────────────


def _measure(client, spec: EndpointSpec, n: int = SAMPLES_PER_ENDPOINT) -> Measurement:
    """Run n consecutive calls. Record each's time.perf_counter()
    elapsed. First is "cold"; the rest make up the warm set."""
    times_ms: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        _call(client, spec)
        times_ms.append((time.perf_counter() - t0) * 1000)
    return Measurement(key=spec.key, cold_ms=times_ms[0], warm=times_ms[WARM_START:])


def _call(client, spec: EndpointSpec) -> None:
    if spec.method == "GET":
        client.get(spec.path, params=spec.query)
    elif spec.method == "POST":
        client.post(spec.path, params=spec.query, json=spec.body or {})
    else:
        raise ValueError(f"unsupported method: {spec.method}")


def _make_client():
    """In-process TestClient over the hub app, seeded with the perf
    fixture. If `BEACONRY_PERF_HUB_URL` is set, return a thin wrapper
    that proxies to the live hub via requests instead."""
    live_url = os.environ.get("BEACONRY_PERF_HUB_URL")
    if live_url:
        return _RemoteClient(live_url)

    # Build a temp fixture dir, seed it, then import the hub so its
    # DATA_DIR + DB_PATH module globals resolve against our temp dir.
    tmp = tempfile.mkdtemp(prefix="beaconry-perf-")
    os.environ["DATA_DIR"] = tmp
    os.environ["HUB_SCOUT_TOKEN"] = ""
    os.environ["HUB_CLI_TOKEN"] = ""
    os.environ["TELEGRAM_BOT_TOKEN"] = ""
    os.environ["BEACONRY_SKIP_GRYPE_DB"] = "1"

    repo_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo_root))
    sys.path.insert(0, str(repo_root / "poc" / "hub"))
    from fastapi.testclient import TestClient

    from poc.hub import hub
    from poc.scripts import perf_seeder

    hub.DATA_DIR = Path(tmp)
    hub.DB_PATH = Path(tmp) / "beaconry.db"
    n_findings = int(os.environ.get("BEACONRY_PERF_FIXTURE_FINDINGS", "1000"))
    n_events = int(os.environ.get("BEACONRY_PERF_FIXTURE_EVENTS", "10000"))
    perf_seeder.seed_perf_fixture(
        Path(tmp),
        n_findings=n_findings,
        n_audit_events=n_events,
    )
    # init_db is idempotent; seed already called it.
    hub.load_integrity_persistence()
    # BC-132: register the audit_index hook + rebuild from the seeded
    # audit.log so the perf gate exercises the indexed read path.
    from poc.hub import audit as _audit_mod

    _audit_mod.clear_post_append_hooks()
    _audit_mod.register_post_append_hook(hub._audit_index_append)
    hub._rebuild_audit_index_if_empty()
    return TestClient(hub.api)


class _RemoteClient:
    def __init__(self, base: str) -> None:
        import requests

        self.base = base.rstrip("/")
        self.requests = requests

    def get(self, path: str, params: dict | None = None):
        return self.requests.get(self.base + path, params=params, timeout=15)

    def post(self, path: str, params: dict | None = None, json: dict | None = None):
        return self.requests.post(self.base + path, params=params, json=json, timeout=15)


# ── Verdict + rendering ──────────────────────────────────────────────


@dataclass
class Verdict:
    measurements: list[Measurement]
    exceptions: dict[str, PerfException]
    today: dt.date
    baseline: dict[str, float] | None = None

    @property
    def breaches(self) -> list[tuple[Measurement, PerfException | None]]:
        """List of (measurement, matched-exception-or-None) tuples
        where the measurement exceeds a budget and no ACTIVE exception
        covers it."""
        out: list[tuple[Measurement, PerfException | None]] = []
        for m in self.measurements:
            if m.p95 <= BUDGET_P95_MS and m.p99 <= BUDGET_P99_MS:
                continue
            exc = self.exceptions.get(m.key)
            if exc and exc.is_active(self.today):
                continue
            out.append((m, exc))
        return out

    @property
    def warnings(self) -> list[Measurement]:
        """Measurements under budget but ≥ 2× the baseline P50."""
        if not self.baseline:
            return []
        out: list[Measurement] = []
        for m in self.measurements:
            base_p50 = self.baseline.get(m.key)
            if base_p50 and m.p50 >= 2 * base_p50 and m.p95 <= BUDGET_P95_MS:
                out.append(m)
        return out


def _render(v: Verdict) -> str:
    rows = []
    rows.append(
        f"{'Endpoint':<45} {'cold':>8} {'P50':>8} {'P95':>8} {'P99':>8}  verdict",
    )
    rows.append("─" * 92)
    for m in v.measurements:
        exc = v.exceptions.get(m.key)
        active_exc = bool(exc and exc.is_active(v.today))
        over_p95 = m.p95 > BUDGET_P95_MS
        over_p99 = m.p99 > BUDGET_P99_MS
        if active_exc and exc is not None and (over_p95 or over_p99):
            verdict = f"⚠ exempt ({exc.fix_by if exc.fix_by else 'permanent'})"
        elif over_p95 or over_p99:
            verdict = "✗ BREACH"
        else:
            verdict = "✓"
        rows.append(
            f"{m.key:<45} {m.cold_ms:>7.0f}ms {m.p50:>7.0f}ms {m.p95:>7.0f}ms {m.p99:>7.0f}ms  {verdict}",
        )

    breaches = v.breaches
    warnings = v.warnings
    rows.append("")
    if not breaches:
        n_exempt = sum(
            1 for m in v.measurements if (exc := v.exceptions.get(m.key)) and exc.is_active(v.today)
        )
        rows.append(
            f"✓ {len(v.measurements)} endpoints measured, all under "
            f"{BUDGET_P95_MS:.0f}ms P95"
            + (f" ({n_exempt} within fix-by window)" if n_exempt else "")
            + (f" · {len(warnings)} regressions vs baseline" if warnings else ""),
        )
    else:
        rows.append(
            f"✗ {len(breaches)} endpoint(s) breach the budget "
            f"(P95 ≤ {BUDGET_P95_MS:.0f}ms, P99 ≤ {BUDGET_P99_MS:.0f}ms):",
        )
        for m, _ in breaches:
            rows.append(f"  - {m.key}: P95={m.p95:.0f}ms P99={m.p99:.0f}ms")
    if warnings:
        rows.append("")
        rows.append("⚠ Regressions vs baseline (≥ 2× P50, still under budget):")
        for m in warnings:
            base_p50 = v.baseline[m.key]  # type: ignore[index]
            rows.append(f"  - {m.key}: {base_p50:.0f}ms → {m.p50:.0f}ms")
    return "\n".join(rows)


# ── Main ──────────────────────────────────────────────────────────────


def _load_baseline(path: Path) -> dict[str, float] | None:
    if not path.exists():
        return None
    with path.open() as f:
        return json.load(f)


def _save_baseline(path: Path, v: Verdict) -> None:
    out = {m.key: m.p50 for m in v.measurements}
    path.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    # Same opt-out knob the Makefile honors — lets `python -m poc.scripts.perf_gate`
    # be invoked directly from a hook while still respecting the env.
    if os.environ.get("BEACONRY_SKIP_PERF") == "1":
        print("↷ perf: skipped per BEACONRY_SKIP_PERF")
        return 0
    p = argparse.ArgumentParser(prog="perf_gate")
    p.add_argument("--baseline", type=Path, default=Path("perf-baseline.json"))
    p.add_argument("--update-baseline", action="store_true")
    p.add_argument("--json", type=Path, default=None, help="Write the full verdict as JSON.")
    args = p.parse_args(argv)

    specs = load_fixtures()
    exceptions = load_exceptions()
    client = _make_client()
    measurements = [_measure(client, spec) for spec in specs]
    baseline = _load_baseline(args.baseline)
    v = Verdict(
        measurements=measurements,
        exceptions=exceptions,
        today=dt.date.today(),
        baseline=baseline,
    )

    print(_render(v))

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "budget_ms": {"p50": BUDGET_P50_MS, "p95": BUDGET_P95_MS, "p99": BUDGET_P99_MS},
                    "measurements": [
                        {
                            "key": m.key,
                            "cold_ms": m.cold_ms,
                            "p50_ms": m.p50,
                            "p95_ms": m.p95,
                            "p99_ms": m.p99,
                        }
                        for m in measurements
                    ],
                    "breaches": [m.key for m, _ in v.breaches],
                },
                indent=2,
            )
        )

    if args.update_baseline and not v.breaches:
        _save_baseline(args.baseline, v)
        print(f"\nBaseline updated at {args.baseline}")

    return 1 if v.breaches else 0


if __name__ == "__main__":
    sys.exit(main())
