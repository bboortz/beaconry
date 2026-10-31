"""ScenarioContext — primitives scenarios compose into assertions.

Scenarios never touch SQLite / audit.log / HTTP directly; they call context
methods. This keeps assertion logic uniform across scenarios AND makes it
easy to swap the transport (e.g. in-process TestClient for the unit tests
vs. httpx against a live compose stack).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import httpx


class ScenarioContext:
    """Primitives for mutating state + asserting outcomes."""

    def __init__(
        self,
        *,
        hub_url: str,
        db_path: Path,
        audit_path: Path,
        http_client: httpx.Client | None = None,
    ) -> None:
        self.hub_url = hub_url
        self.db_path = db_path
        self.audit_path = audit_path
        # Caller may inject a client (e.g. an httpx.Client wrapping TestClient).
        self._client = http_client or httpx.Client(timeout=15.0)

    # ── Mutations ───────────────────────────────────────────────────────

    def wipe(self) -> None:
        """Reset DB + audit.log via POST /test/wipe."""
        r = self._client.post(f"{self.hub_url}/test/wipe")
        r.raise_for_status()

    def inject_scan(
        self,
        candidates: list[dict[str, Any]],
        *,
        host: str = "scenario-host",
        port_exposure: dict[str, bool] | None = None,
        at: int | None = None,
    ) -> dict[str, Any]:
        """POST /test/scan with pre-matched candidates. Returns the hub response."""
        payload: dict[str, Any] = {"host": host, "candidates": candidates}
        if port_exposure is not None:
            payload["port_exposure"] = port_exposure
        if at is not None:
            payload["at"] = at
        r = self._client.post(f"{self.hub_url}/test/scan", json=payload)
        r.raise_for_status()
        return r.json()

    def trigger_sweep(self, *, at: int) -> dict[str, Any]:
        """POST /test/sweep with injected `now` timestamp."""
        r = self._client.post(f"{self.hub_url}/test/sweep", json={"at": at})
        r.raise_for_status()
        return r.json()

    def set_db_status(self, cve_id: str, status: str, *, host: str = "scenario-host") -> None:
        """BC-038: direct SQL mutation for scenarios that need to simulate
        operator actions (snooze, accept) without going through the Telegram
        callback path. Writes `last_fixed_ts` for fix-like statuses to keep
        BC-031 reconcile honest."""
        import time as _t

        con = sqlite3.connect(self.db_path)
        try:
            if status in ("fixed", "fixed_verified"):
                con.execute(
                    "UPDATE findings SET status=?, last_fixed_ts=? WHERE cve_id=? AND host=?",
                    (status, int(_t.time()), cve_id, host),
                )
            else:
                con.execute(
                    "UPDATE findings SET status=? WHERE cve_id=? AND host=?",
                    (status, cve_id, host),
                )
            con.commit()
        finally:
            con.close()

    # ── Reads ───────────────────────────────────────────────────────────

    def find(self, cve_id: str, *, host: str = "scenario-host") -> dict[str, Any] | None:
        """Return the DB row for (host, cve_id) as a dict, or None if absent."""
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute(
                "SELECT * FROM findings WHERE cve_id=? AND host=?",
                (cve_id, host),
            ).fetchone()
        finally:
            con.close()
        return dict(row) if row is not None else None

    def count_findings(self, *, host: str | None = None) -> int:
        """How many rows in the findings table (optionally filtered by host)."""
        con = sqlite3.connect(self.db_path)
        try:
            if host is None:
                row = con.execute("SELECT COUNT(*) FROM findings").fetchone()
            else:
                row = con.execute("SELECT COUNT(*) FROM findings WHERE host=?", (host,)).fetchone()
        finally:
            con.close()
        return int(row[0])

    def audit_events(self, event: str | None = None) -> list[dict[str, Any]]:
        """Return audit log entries, optionally filtered by event name."""
        if not self.audit_path.exists():
            return []
        events: list[dict[str, Any]] = []
        for line in self.audit_path.read_text().splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            if event is None or e.get("event") == event:
                events.append(e)
        return events

    # ── Assertions ──────────────────────────────────────────────────────

    def assert_status(self, cve_id: str, expected: str, *, host: str = "scenario-host") -> None:
        row = self.find(cve_id, host=host)
        assert row is not None, f"finding {cve_id} on {host} not in DB"
        actual = row["status"]
        assert actual == expected, f"{cve_id}@{host}: expected status={expected!r}, got {actual!r}"

    def assert_tier(self, cve_id: str, expected: int, *, host: str = "scenario-host") -> None:
        row = self.find(cve_id, host=host)
        assert row is not None, f"finding {cve_id} on {host} not in DB"
        actual = row["tier"]
        assert actual == expected, f"{cve_id}@{host}: expected tier={expected}, got {actual}"

    def assert_internet_facing(
        self, cve_id: str, expected: bool, *, host: str = "scenario-host"
    ) -> None:
        row = self.find(cve_id, host=host)
        assert row is not None, f"finding {cve_id} on {host} not in DB"
        actual = bool(row["internet_facing"])
        assert actual == expected, (
            f"{cve_id}@{host}: expected internet_facing={expected}, got {actual}"
        )

    def assert_audit_fired(
        self, event: str, *, cve_id: str | None = None, count: int | None = None
    ) -> None:
        """Assert the audit log has at least one `event` entry, optionally
        matching a cve_id and/or an exact count."""
        matching = self.audit_events(event)
        if cve_id is not None:
            matching = [e for e in matching if e.get("cve_id") == cve_id]
        if count is not None:
            assert len(matching) == count, (
                f"expected {count} '{event}' events"
                + (f" for {cve_id}" if cve_id else "")
                + f", got {len(matching)}"
            )
        else:
            assert matching, (
                f"expected ≥1 '{event}' event"
                + (f" for {cve_id}" if cve_id else "")
                + f", got none. All events: {[e.get('event') for e in self.audit_events()]}"
            )

    def assert_audit_absent(self, event: str, *, cve_id: str | None = None) -> None:
        """Assert no audit entry matches event (+ optional cve_id)."""
        matching = self.audit_events(event)
        if cve_id is not None:
            matching = [e for e in matching if e.get("cve_id") == cve_id]
        assert not matching, (
            f"expected zero '{event}' events"
            + (f" for {cve_id}" if cve_id else "")
            + f", got {len(matching)}"
        )

    def assert_count(self, expected: int, *, host: str | None = None) -> None:
        actual = self.count_findings(host=host)
        assert actual == expected, (
            f"expected {expected} finding(s)" + (f" on {host}" if host else "") + f", got {actual}"
        )
