"""End-to-end bot behaviour against a real SQLite database, with Telegram faked."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from tc import db, repo
from tc.bot.handlers import handle_update
from tc.db import Commitment, CommitmentStatus, get_session
from tc.events import (
    COMMITMENT_COMPLETED,
    COMMITMENT_DECIDED,
    COMMITMENT_RESCHEDULED,
    MESSAGE_RECEIVED,
)
from tc.extraction.schema import Candidate

CHAT = {"id": -100, "type": "group", "title": "Final Year Project"}
AKSHU = {"id": 1, "first_name": "Akshu", "username": "akshu"}
RAHUL = {"id": 2, "first_name": "Rahul", "last_name": "Sharma", "username": "rahul_s"}


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.answers: list[str] = []

    async def send_message(self, chat_id: int, text: str, *a: Any, **k: Any) -> dict:
        self.sent.append(text)
        return {}

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        self.sent.append(text)

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        self.answers.append(text)


@pytest.fixture
async def env(tmp_path):
    db.configure(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    await db.create_tables()
    events: list[tuple[str, dict]] = []

    async def send_event(name: str, data: dict) -> None:
        events.append((name, data))

    return FakeTelegram(), send_event, events


def msg(user: dict, text: str, message_id: int) -> dict:
    return {"message": {"message_id": message_id, "from": user, "chat": CHAT, "text": text,
                        "date": int(datetime(2026, 11, 9, 4, 30, tzinfo=UTC).timestamp())}}


def tap(user: dict, data: str) -> dict:
    return {"callback_query": {"id": "cb", "from": user, "data": data,
                               "message": {"message_id": 99, "chat": CHAT}}}


async def _propose(owner_name: str) -> str:
    """Simulate what the process-message workflow stores after extraction."""
    async with get_session() as session:
        message = await session.scalar(
            select(db.Message).where(db.Message.text.like("%database%"))
        )
        _, _, name_to_id = await repo.build_extraction_input(session, message.id)
        ids = await repo.create_proposals(
            session, message.id,
            [Candidate(task="Set up database", owner=owner_name, kind="assignment",
                       evidence_quote="Rahul can you set up the database",
                       deadline_phrase="tomorrow",
                       deadline_at=datetime(2026, 11, 10, 18, 29, tzinfo=UTC),
                       deadline_rule="tomorrow", confidence=0.9)],
            "test", name_to_id,
        )
        await session.commit()
        return ids[0]


async def test_message_is_stored_once_and_emits_event(env) -> None:
    tg, send_event, events = env
    await handle_update(msg(AKSHU, "Rahul can you set up the database by tomorrow?", 1), tg,
                        send_event)
    await handle_update(msg(AKSHU, "Rahul can you set up the database by tomorrow?", 1), tg,
                        send_event)  # Telegram re-delivery
    assert [e[0] for e in events] == [MESSAGE_RECEIVED]


async def test_only_owner_can_confirm_then_done(env) -> None:
    tg, send_event, events = env
    await handle_update(msg(RAHUL, "hi", 1), tg, send_event)  # Rahul joins the chat
    await handle_update(msg(AKSHU, "Rahul can you set up the database by tomorrow?", 2), tg,
                        send_event)
    cid = await _propose("Rahul Sharma")

    await handle_update(tap(AKSHU, f"y:{cid}"), tg, send_event)  # wrong person
    assert "Only Rahul Sharma can confirm." in tg.answers
    await handle_update(tap(RAHUL, f"y:{cid}"), tg, send_event)
    assert (COMMITMENT_DECIDED, {"commitment_id": cid, "decision": "yes"}) in events

    async with get_session() as session:
        assert (await session.get(Commitment, cid)).status == CommitmentStatus.CONFIRMED

    await handle_update(msg(AKSHU, "/done 1", 3), tg, send_event)  # not the owner
    assert any("Only the task owner" in t for t in tg.sent)
    await handle_update(msg(RAHUL, "/done 1", 4), tg, send_event)
    assert (COMMITMENT_COMPLETED, {"commitment_id": cid}) in events


async def test_reschedule_bumps_version(env) -> None:
    tg, send_event, events = env
    await handle_update(msg(RAHUL, "hi", 1), tg, send_event)
    await handle_update(msg(AKSHU, "Rahul can you set up the database by tomorrow?", 2), tg,
                        send_event)
    cid = await _propose("Rahul Sharma")
    await handle_update(tap(RAHUL, f"y:{cid}"), tg, send_event)
    future = (datetime.now(tz=UTC) + timedelta(days=30)).strftime("%d/%m/%Y")
    await handle_update(msg(RAHUL, f"/deadline 1 {future}", 3), tg, send_event)
    assert (COMMITMENT_RESCHEDULED, {"commitment_id": cid, "version": 2}) in events


async def test_report_and_tasks_commands(env) -> None:
    tg, send_event, _ = env
    await handle_update(msg(AKSHU, "/tasks", 1), tg, send_event)
    await handle_update(msg(AKSHU, "/report", 2), tg, send_event)
    assert "No open tasks" in tg.sent[0]
    assert "nothing to report" in tg.sent[1]
