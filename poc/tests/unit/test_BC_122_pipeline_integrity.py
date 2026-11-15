"""BC-122 — Pipeline integrity: hashes + runtime verifier.

Covers:
    - CAP-T223: Scout computes iteration_hash + transfer_hash per cycle
      and embeds both in /scan body. Hub verifies on receipt, computes
      processing_hash after ingest, emits scan_integrity audit event.
      verify_integrity CLI walks the audit log and reports lossless
      evidence. `beaconry status` surfaces mismatch counter.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from poc.scout import scout


def _canonical_body(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


# ── Hashing helpers (deterministic + stable) ─────────────────────────────


def test_iteration_hash_is_stable_across_calls() -> None:
    h1 = scout._compute_iteration_hash("host-a", 1000, ["nginx:alpine", "redis:7"])
    h2 = scout._compute_iteration_hash("host-a", 1000, ["nginx:alpine", "redis:7"])
    assert h1 == h2


def test_iteration_hash_is_stable_across_image_ref_order() -> None:
    """Two scouts seeing the same set in different order emit the same hash."""
    h1 = scout._compute_iteration_hash("host-a", 1000, ["nginx:alpine", "redis:7"])
    h2 = scout._compute_iteration_hash("host-a", 1000, ["redis:7", "nginx:alpine"])
    assert h1 == h2


def test_iteration_hash_differs_by_host() -> None:
    h1 = scout._compute_iteration_hash("host-a", 1000, ["nginx:alpine"])
    h2 = scout._compute_iteration_hash("host-b", 1000, ["nginx:alpine"])
    assert h1 != h2


def test_iteration_hash_differs_by_timestamp() -> None:
    h1 = scout._compute_iteration_hash("host-a", 1000, ["nginx:alpine"])
    h2 = scout._compute_iteration_hash("host-a", 1001, ["nginx:alpine"])
    assert h1 != h2


def test_iteration_hash_16_hex_chars() -> None:
    h = scout._compute_iteration_hash("host-a", 1000, ["nginx:alpine"])
    assert len(h) == 16
    int(h, 16)  # must parse as hex


def test_transfer_hash_identifies_canonical_body() -> None:
    body = _canonical_body({"host": "h", "sboms": []})
    h = scout._compute_transfer_hash(body)
    expected = hashlib.sha256(body.encode()).hexdigest()[:16]
    assert h == expected


def test_transfer_hash_differs_when_body_mutates() -> None:
    body_a = _canonical_body({"host": "h", "sboms": []})
    body_b = _canonical_body({"host": "h", "sboms": [{"ref": "nginx"}]})
    assert scout._compute_transfer_hash(body_a) != scout._compute_transfer_hash(body_b)


# ── Scout embeds hashes + n_sboms into payload ────────────────────────────


def test_scout_builds_integrity_payload_section() -> None:
    """Helper that builds the integrity dict scout embeds in /scan body."""
    integrity = scout._build_integrity_section(
        host="host-a",
        iteration_ts=1000,
        image_refs=["nginx:alpine", "redis:7"],
        n_sboms=2,
        canonical_body='{"host":"host-a","sboms":[]}',
    )
    assert "iteration_hash" in integrity
    assert "transfer_hash" in integrity
    assert integrity["n_sboms"] == 2
    assert len(integrity["iteration_hash"]) == 16
    assert len(integrity["transfer_hash"]) == 16


# ── Hub-side verification ─────────────────────────────────────────────────


def test_hub_verifies_matching_transfer_hash() -> None:
    from poc.hub import hub

    body = _canonical_body({"host": "h", "sboms": [], "iteration_hash": "abc"})
    expected = hashlib.sha256(body.encode()).hexdigest()[:16]
    claimed = expected
    assert hub._verify_transfer_hash(body, claimed) is True


def test_hub_detects_mismatched_transfer_hash() -> None:
    from poc.hub import hub

    body = _canonical_body({"host": "h", "sboms": []})
    assert hub._verify_transfer_hash(body, "deadbeefdeadbeef") is False


def test_hub_degrades_cleanly_on_null_claimed_hash() -> None:
    """Legacy pre-BC-122 scouts omit transfer_hash → match is None (unknown),
    processing still proceeds. hub._verify_transfer_hash(..., None)."""
    from poc.hub import hub

    body = _canonical_body({"host": "h", "sboms": []})
    assert hub._verify_transfer_hash(body, None) is None


# ── processing_hash is deterministic over sorted IDs ──────────────────────


def test_processing_hash_is_order_independent() -> None:
    from poc.hub import hub

    h1 = hub._compute_processing_hash(["abc123", "def456"])
    h2 = hub._compute_processing_hash(["def456", "abc123"])
    assert h1 == h2


def test_processing_hash_differs_by_content() -> None:
    from poc.hub import hub

    h1 = hub._compute_processing_hash(["abc123"])
    h2 = hub._compute_processing_hash(["abc124"])
    assert h1 != h2


def test_processing_hash_empty_findings_is_stable() -> None:
    from poc.hub import hub

    assert hub._compute_processing_hash([]) == hub._compute_processing_hash([])


# ── /healthz counter + status surface ────────────────────────────────────


def test_healthz_surfaces_mismatch_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    from poc.hub import hub

    monkeypatch.setattr(hub, "_pipeline_integrity_mismatches", 3)
    summary = hub._hub_healthz_summary()
    assert summary["checks"]["pipeline_integrity_mismatches"] == 3


def test_status_renders_integrity_mismatches(monkeypatch: pytest.MonkeyPatch) -> None:
    """When hub reports pipeline_integrity_mismatches > 0, `beaconry status`
    DETAILS cell gets `⚠ integrity: N mismatches`."""
    from poc.scripts import status as sm

    monkeypatch.setattr(
        sm,
        "fetch_healthz",
        lambda url, timeout=2.0, token="": {
            "status": "ok",
            "checks": {
                "last_scan_ts": 0,
                "grype_db": "ok",
                "telegram": "polling",
                "pipeline_integrity_mismatches": 3,
            },
        },
    )
    s = sm.gather_hub_status(url="http://probe/healthz", token="")
    assert "integrity" in s.details
    assert "3" in s.details


def test_status_omits_integrity_when_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    from poc.scripts import status as sm

    monkeypatch.setattr(
        sm,
        "fetch_healthz",
        lambda url, timeout=2.0, token="": {
            "status": "ok",
            "checks": {
                "last_scan_ts": 0,
                "grype_db": "ok",
                "telegram": "polling",
                "pipeline_integrity_mismatches": 0,
            },
        },
    )
    s = sm.gather_hub_status(url="http://probe/healthz", token="")
    assert "integrity" not in s.details


# ── Verifier CLI ──────────────────────────────────────────────────────────


def _integrity_event(**kwargs: object) -> dict:
    """Mirrors the actual `scan_integrity` audit shape hub.py emits.
    MIN_CVSS filtering happens upstream in process_sbom_entry; by the
    time _ingest_candidates emits the audit, candidates are already
    post-MIN_CVSS. The four drop buckets accounted for are:
    n_dedup (same-CVE collapse), n_capped (BEACONRY_MAX_FINDINGS_PER_RUN
    overflow → digest, not DB), n_dropped_in_save (save_finding returned
    False), n_filtered_other (slack).
    """
    base: dict[str, object] = {
        "event": "scan_integrity",
        "ts": "2026-11-15T18:00:00",
        "iteration_hash": "aaaaaaaaaaaaaaaa",
        "host": "host-a",
        "transfer_hash_claimed": "bbbbbbbbbbbbbbbb",
        "transfer_hash_computed": "bbbbbbbbbbbbbbbb",
        "transfer_match": True,
        "n_sboms_claimed": 3,
        "n_sboms_received": 3,
        "n_candidates": 5,
        "n_persisted": 3,
        "n_dedup": 2,
        "n_capped": 0,
        "n_dropped_in_save": 0,
        "n_filtered_other": 0,
        "processing_hash": "cccccccccccccccc",
    }
    base.update(kwargs)
    return base


def test_verifier_clean_fleet_exits_zero() -> None:
    from poc.scripts import verify_integrity as vi

    events = [_integrity_event(), _integrity_event(iteration_hash="1" * 16)]
    report = vi.build_report(events)
    assert report.exit_code == 0
    assert report.verified_count == 2
    assert not report.mismatches
    assert not report.unexplained_drops


def test_verifier_mismatch_exits_nonzero() -> None:
    from poc.scripts import verify_integrity as vi

    events = [
        _integrity_event(),
        _integrity_event(
            iteration_hash="2" * 16,
            transfer_match=False,
            transfer_hash_claimed="bbbb",
            transfer_hash_computed="cccc",
        ),
    ]
    report = vi.build_report(events)
    assert report.exit_code != 0
    assert len(report.mismatches) == 1
    assert report.mismatches[0]["iteration_hash"] == "2" * 16


def test_verifier_flags_unexplained_drops() -> None:
    from poc.scripts import verify_integrity as vi

    # 10 candidates, 5 persisted — known buckets only sum to 2
    # (n_dedup + n_capped + n_dropped_in_save + n_filtered_other).
    # Delta is 3; verifier flags it as unexplained.
    events = [
        _integrity_event(
            n_candidates=10,
            n_persisted=5,
            n_dedup=2,
            n_capped=0,
            n_dropped_in_save=0,
            n_filtered_other=0,
        ),
    ]
    report = vi.build_report(events)
    assert report.exit_code != 0
    assert len(report.unexplained_drops) == 1


def test_verifier_tolerates_legacy_null_match() -> None:
    """Pre-BC-122 scouts emit transfer_match=null — not a failure, just
    a visibility gap. Report tracks it as 'unknown' and exits 0 iff
    there are no HARD mismatches."""
    from poc.scripts import verify_integrity as vi

    events = [
        _integrity_event(
            transfer_match=None, transfer_hash_claimed=None, transfer_hash_computed="aaaa"
        ),
    ]
    report = vi.build_report(events)
    assert report.exit_code == 0
    assert report.unknown_count == 1
    assert not report.mismatches


# ── verifier internals: parsing + rendering + I/O ────────────────────────


def test_parse_since_valid_forms() -> None:
    from poc.scripts import verify_integrity as vi

    assert vi._parse_since(None) is None
    assert vi._parse_since("") is None
    assert vi._parse_since("garbage") is None
    assert vi._parse_since("24h") is not None
    assert vi._parse_since("60m") is not None
    assert vi._parse_since("7d") is not None
    assert vi._parse_since("30s") is not None


def test_format_report_empty_events() -> None:
    from poc.scripts import verify_integrity as vi

    out = vi._format_report(vi.IntegrityReport())
    assert "No scan_integrity events" in out


def test_format_report_clean_fleet() -> None:
    from poc.scripts import verify_integrity as vi

    rep = vi.IntegrityReport(verified_count=5, unknown_count=1)
    text = vi._format_report(rep)
    assert "✓" in text
    assert "5 verified" in text
    assert "1 unknown" in text


def test_format_report_includes_mismatch_rows() -> None:
    from poc.scripts import verify_integrity as vi

    ev = _integrity_event(
        transfer_match=False,
        transfer_hash_claimed="bbbb",
        transfer_hash_computed="cccc",
    )
    rep = vi.IntegrityReport(mismatches=[ev])
    text = vi._format_report(rep)
    assert "✗" in text
    assert "bbbb" in text
    assert "cccc" in text
    assert "host-a" in text


def test_format_report_includes_unexplained_drops() -> None:
    from poc.scripts import verify_integrity as vi

    ev = _integrity_event(n_candidates=10, n_persisted=5, n_dedup=1)
    rep = vi.IntegrityReport(verified_count=1, unexplained_drops=[ev])
    text = vi._format_report(rep)
    assert "Unexplained" in text
    assert "candidates=10" in text


def test_load_events_filters_by_since(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    import datetime as _dt

    from poc.scripts import verify_integrity as vi

    audit = tmp_path / "audit.log"
    old = _dt.datetime(2020, 1, 1, 0, 0, 0).isoformat()
    new = _dt.datetime(2026, 11, 15, 18, 0, 0).isoformat()
    audit.write_text(
        json.dumps({"event": "scan_integrity", "ts": old})
        + "\n"
        + json.dumps({"event": "scan_integrity", "ts": new})
        + "\n"
    )
    # Cutoff = 2024 → old filtered out, new kept.
    cutoff = int(_dt.datetime(2024, 1, 1).timestamp())
    events = vi.load_events(audit, since_ts=cutoff)
    assert len(events) == 1
    assert events[0]["ts"] == new


def test_load_events_tolerates_malformed_lines(tmp_path) -> None:
    from poc.scripts import verify_integrity as vi

    audit = tmp_path / "audit.log"
    audit.write_text(
        '{"event":"scan_integrity","ts":"2026-11-15T18:00:00"}\n'
        "not-json-at-all\n"
        "\n"
        '{"event":"scan_integrity","ts":"2026-11-15T18:00:01"}\n'
    )
    assert len(vi.load_events(audit)) == 2


def test_load_events_missing_file_returns_empty(tmp_path) -> None:
    from poc.scripts import verify_integrity as vi

    assert vi.load_events(tmp_path / "nope.log") == []


def test_main_exits_2_on_missing_audit(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from poc.scripts import verify_integrity as vi

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "nowhere"))
    rc = vi.main([])
    assert rc == 2


def test_main_round_trip_with_events(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    from poc.scripts import verify_integrity as vi

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    audit = data_dir / "audit.log"
    audit.write_text(json.dumps(_integrity_event()) + "\n")
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    rc = vi.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "1 verified" in out


def test_verifier_accounts_every_drop_cleanly() -> None:
    """Scenario 6: 10 candidates = 5 persisted + 2 dedup + 3 capped."""
    from poc.scripts import verify_integrity as vi

    events = [
        _integrity_event(
            n_candidates=10,
            n_persisted=5,
            n_dedup=2,
            n_capped=3,
            n_dropped_in_save=0,
            n_filtered_other=0,
        ),
    ]
    report = vi.build_report(events)
    assert report.exit_code == 0
    assert not report.unexplained_drops
