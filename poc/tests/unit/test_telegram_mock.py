"""Unit tests for the telegram-mock service.

Drives mock_bot.app via FastAPI TestClient — no docker required. Verifies
the five Bot API endpoints python-telegram-bot calls plus the driver
endpoints the e2e harness depends on.

Covers:
    - CAP-T055: telegram-mock service — mini Telegram Bot API shim + driver
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from poc.telegram_mock import mock_bot

TOKEN = "test-token-1234567890"


@pytest.fixture
def client() -> TestClient:
    # Reset in-process state so each test starts clean.
    mock_bot._update_queue.clear()
    mock_bot._sent.clear()
    mock_bot._answered_callbacks.clear()
    mock_bot._next_update_id = 1
    mock_bot._next_message_id = 1
    return TestClient(mock_bot.app)


def test_bot_api_subset_present(client: TestClient) -> None:
    """telegram-mock implements the five endpoints PTB calls at /bot<token>/*."""
    r = client.get(f"/bot{TOKEN}/getMe")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["result"]["is_bot"] is True
    assert "username" in body["result"]

    # No queued updates → non-blocking getUpdates returns empty list.
    r = client.get(f"/bot{TOKEN}/getUpdates", params={"timeout": 0})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "result": []}

    r = client.post(f"/bot{TOKEN}/sendMessage", json={"chat_id": 42, "text": "hello"})
    assert r.status_code == 200
    assert r.json()["result"]["text"] == "hello"

    r = client.post(
        f"/bot{TOKEN}/editMessageText",
        json={"chat_id": 42, "message_id": 1, "text": "edited"},
    )
    assert r.status_code == 200
    assert r.json()["result"]["text"] == "edited"

    r = client.post(f"/bot{TOKEN}/answerCallbackQuery", json={"callback_query_id": "cb_1"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "result": True}

    r = client.post(f"/bot{TOKEN}/deleteWebhook")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "result": True}


def test_injected_callback_delivered_via_getupdates(client: TestClient) -> None:
    """/__driver__/inject-callback queues an update that getUpdates returns."""
    inj = client.post("/__driver__/inject-callback", json={"data": "verify:abc"})
    assert inj.status_code == 200
    inj_body = inj.json()
    assert inj_body["ok"] is True
    uid = inj_body["injected_update_id"]

    poll = client.get(f"/bot{TOKEN}/getUpdates", params={"timeout": 0})
    assert poll.status_code == 200
    updates = poll.json()["result"]
    assert len(updates) == 1
    u = updates[0]
    assert u["update_id"] == uid
    assert u["callback_query"]["data"] == "verify:abc"
    assert "message" in u["callback_query"], "PTB requires message on callback_query"


def test_getupdates_offset_trims_acked(client: TestClient) -> None:
    """offset > 0 drops updates PTB has already acknowledged (BC-018 regression)."""
    uid1 = client.post("/__driver__/inject-callback", json={"data": "verify:a"}).json()[
        "injected_update_id"
    ]
    uid2 = client.post("/__driver__/inject-callback", json={"data": "verify:b"}).json()[
        "injected_update_id"
    ]
    # ACK everything through uid1 → only uid2 remains.
    poll = client.get(f"/bot{TOKEN}/getUpdates", params={"offset": uid1 + 1, "timeout": 0})
    remaining = poll.json()["result"]
    assert len(remaining) == 1
    assert remaining[0]["update_id"] == uid2


def test_sent_captures_outbound(client: TestClient) -> None:
    """/__driver__/sent captures sendMessage + editMessageText for assertion."""
    client.post(f"/bot{TOKEN}/sendMessage", json={"chat_id": 1, "text": "first"})
    client.post(
        f"/bot{TOKEN}/editMessageText",
        json={"chat_id": 1, "message_id": 1, "text": "second"},
    )
    client.post(f"/bot{TOKEN}/answerCallbackQuery", json={"callback_query_id": "cb_x"})

    r = client.get("/__driver__/sent")
    assert r.status_code == 200
    body = r.json()
    kinds = [e["type"] for e in body["sent"]]
    assert kinds == ["sendMessage", "editMessageText"]
    assert body["sent"][0]["text"] == "first"
    assert body["sent"][1]["text"] == "second"
    assert len(body["answered_callbacks"]) == 1


def test_reset_clears_all_state(client: TestClient) -> None:
    client.post("/__driver__/inject-callback", json={"data": "verify:x"})
    client.post(f"/bot{TOKEN}/sendMessage", json={"chat_id": 1, "text": "x"})

    r = client.post("/__driver__/reset")
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    state = client.get("/__driver__/state").json()
    assert state["pending_updates"] == 0
    assert state["sent_count"] == 0
    assert state["answered_callbacks"] == 0


def test_health_router_four_endpoints_present(client: TestClient) -> None:
    """BC-012: mock_bot reuses shared beaconry_health.build_router — four endpoints."""
    for path in ("/livez", "/readyz", "/startupz", "/healthz"):
        r = client.get(path)
        assert r.status_code == 200, f"{path} must respond 200"
    hz = client.get("/healthz").json()
    assert hz["status"] == "ok"
    assert hz["version"] == mock_bot.VERSION


def test_permissive_body_parser_handles_form_encoded(client: TestClient) -> None:
    """getUpdates/sendMessage must accept both JSON and form-encoded bodies."""
    r = client.post(
        f"/bot{TOKEN}/sendMessage",
        data={"chat_id": "99", "text": "form-body"},
    )
    assert r.status_code == 200
    assert r.json()["result"]["text"] == "form-body"
