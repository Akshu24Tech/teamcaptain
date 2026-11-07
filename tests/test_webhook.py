"""The /telegram/webhook endpoint: secret check, then the same handle_update path."""

from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tc import db, main
from tc.events import MESSAGE_RECEIVED

CHAT = {"id": -100, "type": "group", "title": "Final Year Project"}
AKSHU = {"id": 1, "first_name": "Akshu", "username": "akshu"}
SECRET = "test-secret"


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_message(self, *a: Any, **k: Any) -> dict:
        self.sent.append(k.get("text", ""))
        return {}

    async def edit_message(self, *a: Any, **k: Any) -> None: ...
    async def answer_callback(self, *a: Any, **k: Any) -> None: ...


def _update() -> dict:
    return {
        "update_id": 1,
        "message": {
            "message_id": 1, "from": AKSHU, "chat": CHAT,
            "text": "Rahul can you set up the database by tomorrow?",
            "date": int(datetime(2026, 11, 9, 4, 30, tzinfo=UTC).timestamp()),
        },
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    db.configure(f"sqlite+aiosqlite:///{tmp_path / 'wh.db'}")
    events: list[tuple[str, dict]] = []

    async def fake_send_event(name: str, data: dict) -> None:
        events.append((name, data))

    monkeypatch.setattr(main.settings, "webhook_secret", SECRET)
    monkeypatch.setattr(main, "_tg", FakeTelegram())
    monkeypatch.setattr(main, "send_event", fake_send_event)
    with TestClient(main.app) as c:
        yield c, events


def test_webhook_rejects_bad_secret(client) -> None:
    c, events = client
    r = c.post(
        "/telegram/webhook", json=_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
    )
    assert r.status_code == 403
    assert events == []


def test_webhook_processes_update_with_correct_secret(client) -> None:
    c, events = client
    r = c.post(
        "/telegram/webhook", json=_update(),
        headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
    )
    assert r.status_code == 200
    assert r.json() == {"ok": True}
    assert MESSAGE_RECEIVED in [name for name, _ in events]
