"""Turns Telegram updates into database changes and workflow events.

The handler never calls the LLM and never sleeps: slow or long-running work is
handed to the durable workflows as events.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tc import repo, views
from tc.bot.telegram import TelegramClient, esc
from tc.config import settings
from tc.db import OPEN_STATUSES, Commitment, CommitmentStatus, Member, Team, get_session, utcnow
from tc.events import (
    COMMITMENT_CANCELLED,
    COMMITMENT_COMPLETED,
    COMMITMENT_DECIDED,
    COMMITMENT_RESCHEDULED,
    MESSAGE_RECEIVED,
)
from tc.extraction.dates import resolve_deadline

SendEvent = Callable[[str, dict[str, Any]], Awaitable[None]]

HELP = (
    "👋 <b>I'm TeamCaptain.</b> Just chat normally. When someone promises or assigns work, "
    "I'll ask them to confirm, then track it, remind before the deadline and follow up.\n\n"
    "<code>/tasks</code> open tasks\n"
    "<code>/done N</code> mark task N done\n"
    "<code>/take N</code> take an unassigned task\n"
    "<code>/deadline N friday 5pm</code> change a deadline\n"
    "<code>/cancel N</code> stop tracking a task\n"
    "<code>/report</code> delivery report"
)


def _name(user: dict[str, Any]) -> str:
    return " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p) or "Someone"


async def _actor(session: AsyncSession, chat: dict[str, Any], user: dict[str, Any]
                 ) -> tuple[Team, Member]:
    team = await repo.upsert_team(session, chat["id"], chat.get("title", ""))
    member = await repo.upsert_member(session, team, user["id"], user.get("username"), _name(user))
    return team, member


async def handle_update(update: dict[str, Any], tg: TelegramClient, send_event: SendEvent
                        ) -> None:
    if "callback_query" in update:
        await _handle_callback(update["callback_query"], tg, send_event)
        return
    message = update.get("message")
    if not message or "text" not in message or message.get("from", {}).get("is_bot"):
        return
    chat = message["chat"]
    if chat.get("type") not in ("group", "supergroup"):
        await tg.send_message(chat["id"], "Add me to your team's group chat to get started.\n\n"
                              + HELP)
        return
    text: str = message["text"]
    if text.startswith("/"):
        await _handle_command(message, tg, send_event)
        return

    async with get_session() as session:
        team, member = await _actor(session, chat, message["from"])
        stored = await repo.save_message(
            session, team, member, message["message_id"], text,
            datetime.fromtimestamp(message["date"], tz=UTC),
        )
        await session.commit()
    if stored is not None:
        await send_event(MESSAGE_RECEIVED, {"message_id": stored.id})


async def _handle_command(message: dict[str, Any], tg: TelegramClient, send_event: SendEvent
                          ) -> None:
    chat_id = message["chat"]["id"]
    match = re.match(r"^/(\w+)(?:@\w+)?\s*(.*)$", message["text"].strip(), re.DOTALL)
    if not match:
        return
    command, arg = match.group(1).lower(), match.group(2).strip()

    async with get_session() as session:
        team, member = await _actor(session, message["chat"], message["from"])
        await session.commit()
        now = utcnow()

        if command in ("start", "help"):
            await tg.send_message(chat_id, HELP)
            return
        if command == "tasks":
            await tg.send_message(chat_id, await views.task_list(session, team, now))
            return
        if command == "report":
            await tg.send_message(chat_id, await views.report(session, team, now))
            return

        number = re.match(r"^#?(\d+)\s*(.*)$", arg)
        if command not in ("done", "take", "deadline", "cancel"):
            return
        if not number:
            await tg.send_message(chat_id, f"Usage: <code>/{command} N</code> (see /tasks)")
            return
        c = await repo.by_short_no(session, team.id, int(number.group(1)))
        if c is None or c.status not in OPEN_STATUSES:
            await tg.send_message(chat_id, f"No open task #{number.group(1)}. See /tasks")
            return
        reply = await _apply_command(session, command, c, member, number.group(2), now)
        await session.commit()

    text, event = reply
    await tg.send_message(chat_id, text)
    if event:
        await send_event(*event)


async def _apply_command(
    session: AsyncSession, command: str, c: Commitment, member: Member, rest: str, now: datetime
) -> tuple[str, tuple[str, dict[str, Any]] | None]:
    is_owner = c.owner_member_id == member.id
    is_creator = c.creator_member_id == member.id
    responsible = is_owner or (c.owner_member_id is None and is_creator)

    if command == "take":
        if c.owner_member_id is not None:
            return f"#{c.short_no} already has an owner.", None
        c.owner_member_id = member.id
        await repo.log_event(session, c.id, "taken", {"by": member.id})
        return f"👍 {esc(member.display_name)} took #{c.short_no}: {esc(c.task)}", None

    if command == "done":
        if not responsible:
            return "Only the task owner can mark it done.", None
        await repo.set_status(session, c, CommitmentStatus.DONE, by_member_id=member.id)
        return (f"🎉 #{c.short_no} done: {esc(c.task)}",
                (COMMITMENT_COMPLETED, {"commitment_id": c.id}))

    if command == "cancel":
        if not (is_owner or is_creator):
            return "Only the owner or the person who created it can cancel it.", None
        await repo.set_status(session, c, CommitmentStatus.CANCELLED, by_member_id=member.id)
        return (f"🗑️ Stopped tracking #{c.short_no}.",
                (COMMITMENT_CANCELLED, {"commitment_id": c.id}))

    # deadline
    if not (is_owner or is_creator):
        return "Only the owner or the person who created it can change the deadline.", None
    resolved = resolve_deadline(rest, now, settings.tz,
                                default_hour=settings.default_deadline_hour,
                                default_minute=settings.default_deadline_minute)
    if resolved is None or resolved.at <= now:
        return ("I couldn't read that as a future date. Try "
                f"<code>/deadline {c.short_no} friday 5pm</code>"), None
    c.deadline_at, c.deadline_phrase, c.deadline_rule = resolved.at, rest, resolved.rule
    c.schedule_version += 1
    if c.status == CommitmentStatus.OVERDUE:
        c.status = CommitmentStatus.CONFIRMED
    await repo.log_event(session, c.id, "rescheduled",
                         {"by": member.id, "to": resolved.at.isoformat(), "rule": resolved.rule})
    return (f"📅 #{c.short_no} now due {views.fmt_deadline(resolved.at)}",
            (COMMITMENT_RESCHEDULED, {"commitment_id": c.id, "version": c.schedule_version}))


async def _handle_callback(cq: dict[str, Any], tg: TelegramClient, send_event: SendEvent) -> None:
    action, _, cid = cq.get("data", "").partition(":")
    msg = cq.get("message") or {}
    async with get_session() as session:
        c = await session.get(Commitment, cid)
        if c is None:
            await tg.answer_callback(cq["id"], "This task no longer exists.")
            return
        team = await session.get(Team, c.team_id)
        assert team is not None
        _, member = await _actor(session, msg.get("chat", {"id": team.chat_id}), cq["from"])

        if action in ("y", "n"):
            if member.id != repo.decider_id(c):
                v = await views.load_view(session, c.id)
                await tg.answer_callback(cq["id"], f"Only {v.decider.display_name} can confirm.")
                return
            if c.status != CommitmentStatus.PROPOSED:
                await tg.answer_callback(cq["id"], "Already decided.")
                return
            yes = action == "y"
            await repo.set_status(
                session, c, CommitmentStatus.CONFIRMED if yes else CommitmentStatus.REJECTED,
                by_member_id=member.id,
            )
            await session.commit()
            v = await views.load_view(session, c.id)
            outcome = (f"✅ Tracking #{c.short_no}: <b>{esc(c.task)}</b> — {v.owner_tag}, "
                       f"{views.fmt_deadline(c.deadline_at)}") if yes else (
                f"❌ Not tracked: {esc(c.task)}")
            await tg.answer_callback(cq["id"], "Tracked!" if yes else "Okay, ignored.")
            if msg:
                await tg.edit_message(msg["chat"]["id"], msg["message_id"], outcome)
            await send_event(COMMITMENT_DECIDED,
                             {"commitment_id": c.id, "decision": "yes" if yes else "no"})
            return

        if action == "dx":
            # Owner tapped "No, still open" on a detected-completion prompt.
            await tg.answer_callback(cq["id"], "Okay, still tracking it.")
            if msg:
                await tg.edit_message(msg["chat"]["id"], msg["message_id"],
                                      f"↩️ #{c.short_no} still open: {esc(c.task)}")
            return

        if action == "d":
            responsible = c.owner_member_id == member.id or (
                c.owner_member_id is None and c.creator_member_id == member.id)
            if not responsible:
                await tg.answer_callback(cq["id"], "Only the owner can mark this done.")
                return
            if c.status not in OPEN_STATUSES:
                await tg.answer_callback(cq["id"], "Already closed.")
                return
            await repo.set_status(session, c, CommitmentStatus.DONE, by_member_id=member.id)
            await session.commit()
            await tg.answer_callback(cq["id"], "Nice work! 🎉")
            if msg:
                await tg.edit_message(msg["chat"]["id"], msg["message_id"],
                                      f"🎉 #{c.short_no} done: {esc(c.task)}")
            await send_event(COMMITMENT_COMPLETED, {"commitment_id": c.id})
