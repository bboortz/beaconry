"""BC-018 telegram-mock — mini Telegram Bot API shim.

Replaces api.telegram.org for e2e tests. Implements the subset of endpoints
python-telegram-bot actually calls, plus /__driver__/* endpoints the test
runner uses to inject fake user taps and inspect what the hub sent back.

Hub's python-telegram-bot client talks here when BEACONRY_TELEGRAM_BASE_URL
is set to this service's URL. Hub.py has NO test-specific code — the mock
replaces the external dependency, hub runs its real production code path.

Health endpoints (/livez /readyz /startupz /healthz) wired via the shared
`beaconry_health.build_router` factory (BC-012) — same convention hub and
scout follow. The restart-loop trap guard applies here too.

Started as uvicorn mock_bot:app on 0.0.0.0:8080. Driver port exposed on
127.0.0.1:9080 by docker-compose.test.yml.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from beaconry_health import build_router
from fastapi import FastAPI, Request

VERSION = "0.1.0"

app = FastAPI(title="telegram-mock", version=VERSION)

# In-memory state. One instance per process; test resets via /__driver__/reset.
_update_queue: list[dict[str, Any]] = []
_sent: list[dict[str, Any]] = []
_answered_callbacks: list[dict[str, Any]] = []
_next_update_id: int = 1
_next_message_id: int = 1


# ── Shared beaconry_health four-endpoint convention ──────────────────────────


def _readyz_providers() -> dict[str, str]:
    # Mock has no external deps; always ready once uvicorn is up.
    return {}


def _healthz_summary() -> dict:
    return {
        "status": "ok",
        "version": VERSION,
        "checks": {
            "pending_updates": len(_update_queue),
            "sent_count": len(_sent),
            "answered_callbacks": len(_answered_callbacks),
        },
    }


app.include_router(
    build_router(
        livez_provider=lambda: "alive",
        readyz_providers=_readyz_providers,
        startup_flag_getter=lambda: True,
        healthz_summary_getter=_healthz_summary,
    )
)


async def _parse_body(request: Request) -> dict:
    """Permissive body parser: tries JSON then form; empty is OK."""
    body = await request.body()
    if not body:
        return {}
    # Try JSON first — python-telegram-bot v20's default.
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            return parsed
    except Exception:  # noqa: BLE001
        pass
    # Fall back to form-encoded (older PTB versions / curl).
    try:
        from urllib.parse import parse_qs

        pairs = parse_qs(body.decode())
        return {k: v[0] if len(v) == 1 else v for k, v in pairs.items()}
    except Exception:  # noqa: BLE001
        return {}


# ── Telegram Bot API shim ─────────────────────────────────────────────────────
#
# python-telegram-bot (v20) hits URLs like:
#   https://api.telegram.org/bot<token>/getUpdates
#   https://api.telegram.org/bot<token>/sendMessage
# When base_url is overridden to point here, it becomes:
#   http://telegram-mock:8080/bot<token>/getUpdates
# etc. We accept both GET and POST since both are used.


@app.api_route("/bot{token}/getMe", methods=["GET", "POST"])
async def get_me(token: str) -> dict:
    return {
        "ok": True,
        "result": {
            "id": 123456,
            "is_bot": True,
            "first_name": "Beaconry Mock Bot",
            "username": "beaconry_mock_bot",
            "can_join_groups": False,
            "can_read_all_group_messages": False,
            "supports_inline_queries": False,
        },
    }


@app.api_route("/bot{token}/getUpdates", methods=["GET", "POST"])
async def get_updates(token: str, request: Request) -> dict:
    """Long-polling. Returns queued updates with update_id >= offset.

    When offset > 0, drop confirmed-received updates (PTB protocol).
    """
    global _update_queue
    body = await _parse_body(request)
    offset = int(request.query_params.get("offset", body.get("offset", 0)) or 0)
    timeout = int(request.query_params.get("timeout", body.get("timeout", 0)) or 0)
    # Drop any updates PTB has already acknowledged.
    if offset > 0:
        _update_queue = [u for u in _update_queue if u["update_id"] >= offset]
    deadline = time.time() + max(0, min(timeout, 30))
    while True:
        pending = list(_update_queue)
        if pending or time.time() >= deadline:
            return {"ok": True, "result": pending}
        await asyncio.sleep(0.05)


@app.api_route("/bot{token}/sendMessage", methods=["POST", "GET"])
async def send_message(token: str, request: Request) -> dict:
    global _next_message_id
    body = await _parse_body(request)
    msg_id = _next_message_id
    _next_message_id += 1
    _sent.append({"type": "sendMessage", **body})
    return {
        "ok": True,
        "result": {
            "message_id": msg_id,
            "date": int(time.time()),
            "chat": {"id": int(body.get("chat_id", 0) or 0), "type": "private"},
            "text": body.get("text", ""),
        },
    }


@app.api_route("/bot{token}/editMessageText", methods=["POST", "GET"])
async def edit_message_text(token: str, request: Request) -> dict:
    body = await _parse_body(request)
    _sent.append({"type": "editMessageText", **body})
    msg_id = int(body.get("message_id", 0) or 0)
    return {
        "ok": True,
        "result": {
            "message_id": msg_id,
            "date": int(time.time()),
            "chat": {"id": int(body.get("chat_id", 0) or 0), "type": "private"},
            "text": body.get("text", ""),
        },
    }


@app.api_route("/bot{token}/answerCallbackQuery", methods=["POST", "GET"])
async def answer_callback_query(token: str, request: Request) -> dict:
    body = await _parse_body(request)
    _answered_callbacks.append(body)
    return {"ok": True, "result": True}


@app.api_route("/bot{token}/deleteWebhook", methods=["POST", "GET"])
async def delete_webhook(token: str, request: Request) -> dict:
    """python-telegram-bot calls this at startup to switch to long-polling."""
    _ = await _parse_body(request)
    return {"ok": True, "result": True}


# ── Test driver endpoints ────────────────────────────────────────────────────


@app.post("/__driver__/inject-callback")
async def driver_inject_callback(payload: dict) -> dict:
    """Queue a callback_query update to be delivered via the next getUpdates poll."""
    global _next_update_id
    update = {
        "update_id": _next_update_id,
        "callback_query": {
            "id": f"cb_{_next_update_id}",
            "from": {"id": 1, "is_bot": False, "first_name": "driver"},
            "chat_instance": "test",
            "data": payload["data"],
            "message": {
                "message_id": _next_update_id,
                "date": int(time.time()),
                "chat": {"id": int(payload.get("chat_id", 0)), "type": "private"},
                "text": payload.get("message_text", "fixture message"),
            },
        },
    }
    _next_update_id += 1
    _update_queue.append(update)
    return {"ok": True, "injected_update_id": update["update_id"]}


@app.get("/__driver__/sent")
async def driver_sent() -> dict:
    return {"sent": list(_sent), "answered_callbacks": list(_answered_callbacks)}


@app.post("/__driver__/reset")
async def driver_reset() -> dict:
    _update_queue.clear()
    _sent.clear()
    _answered_callbacks.clear()
    return {"ok": True}


@app.get("/__driver__/state")
async def driver_state() -> dict:
    return {
        "pending_updates": len(_update_queue),
        "sent_count": len(_sent),
        "answered_callbacks": len(_answered_callbacks),
        "next_update_id": _next_update_id,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("mock_bot:app", host="0.0.0.0", port=8080, log_level="warning")
