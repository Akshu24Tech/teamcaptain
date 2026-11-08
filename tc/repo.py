"""All database reads and writes. Workflows and the bot never write SQL themselves."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tc.config import settings
from tc.db import (
    OPEN_STATUSES,
    Commitment,
    CommitmentEvent,
    CommitmentStatus,
    Member,
    Message,
    Team,
    as_aware,
    utcnow,
)
from tc.extraction.schema import Candidate, ChatLine, ExtractionInput, OpenTask


async def upsert_team(session: AsyncSession, chat_id: int, title: str) -> Team:
    team = await session.scalar(select(Team).where(Team.chat_id == chat_id))
    if team is None:
        team = Team(chat_id=chat_id, title=title)
        session.add(team)
        await session.flush()
    elif title and team.title != title:
        team.title = title
    return team


async def upsert_member(
    session: AsyncSession, team: Team, telegram_user_id: int, username: str | None, name: str
) -> Member:
    member = await session.scalar(
        select(Member).where(
            Member.team_id == team.id, Member.telegram_user_id == telegram_user_id
        )
    )
    if member is None:
        member = Member(
            team_id=team.id, telegram_user_id=telegram_user_id, username=username, display_name=name
        )
        session.add(member)
        await session.flush()
    else:
        member.username, member.display_name = username, name
    return member


async def save_message(
    session: AsyncSession,
    team: Team,
    member: Member,
    telegram_message_id: int,
    text: str,
    sent_at: datetime,
) -> Message | None:
    """Store a chat message. Returns None if it was already stored (Telegram re-delivery)."""
    existing = await session.scalar(
        select(Message).where(
            Message.team_id == team.id, Message.telegram_message_id == telegram_message_id
        )
    )
    if existing is not None:
        return None
    message = Message(
        team_id=team.id,
        member_id=member.id,
        telegram_message_id=telegram_message_id,
        text=text,
        sent_at=sent_at,
    )
    session.add(message)
    await session.flush()
    return message


async def team_members(session: AsyncSession, team_id: str) -> list[Member]:
    return list(await session.scalars(select(Member).where(Member.team_id == team_id)))


async def build_extraction_input(
    session: AsyncSession, message_id: str
) -> tuple[ExtractionInput, dict[str, str], dict[str, str]]:
    """Return (input, aliases @username→name, name→member_id) for one stored message."""
    message = await session.get(Message, message_id)
    if message is None:
        raise LookupError(f"message {message_id} not found")
    members = await team_members(session, message.team_id)
    by_id = {m.id: m for m in members}
    previous = list(
        await session.scalars(
            select(Message)
            .where(Message.team_id == message.team_id, Message.sent_at <= message.sent_at,
                   Message.id != message.id)
            .order_by(Message.sent_at.desc())
            .limit(settings.context_messages)
        )
    )
    context = [
        ChatLine(speaker=by_id[m.member_id].display_name, text=m.text)
        for m in reversed(previous)
        if m.member_id in by_id
    ]
    open_tasks = [
        OpenTask(
            short_no=c.short_no,
            task=c.task,
            owner=by_id[c.owner_member_id].display_name if c.owner_member_id in by_id else None,
        )
        for c in await open_commitments(session, message.team_id)
    ]
    inp = ExtractionInput(
        speaker=by_id[message.member_id].display_name,
        text=message.text,
        sent_at=as_aware(message.sent_at),  # type: ignore[arg-type]
        members=[m.display_name for m in members],
        context=context,
        open_tasks=open_tasks,
    )
    aliases = {f"@{m.username}": m.display_name for m in members if m.username}
    name_to_id = {m.display_name: m.id for m in members}
    return inp, aliases, name_to_id


async def _next_short_no(session: AsyncSession, team_id: str) -> int:
    current = await session.scalar(
        select(func.max(Commitment.short_no)).where(Commitment.team_id == team_id)
    )
    return int(current or 0) + 1


async def log_event(
    session: AsyncSession, commitment_id: str, kind: str, detail: dict[str, Any] | None = None
) -> None:
    session.add(CommitmentEvent(commitment_id=commitment_id, kind=kind, detail=detail or {}))


async def create_proposals(
    session: AsyncSession,
    message_id: str,
    candidates: list[Candidate],
    extractor_name: str,
    name_to_id: dict[str, str],
) -> list[str]:
    message = await session.get(Message, message_id)
    if message is None:
        raise LookupError(f"message {message_id} not found")
    ids: list[str] = []
    for cand in candidates:
        commitment = Commitment(
            team_id=message.team_id,
            source_message_id=message.id,
            creator_member_id=message.member_id,
            owner_member_id=name_to_id.get(cand.owner) if cand.owner else None,
            short_no=await _next_short_no(session, message.team_id),
            task=cand.task,
            kind=cand.kind,
            evidence_quote=cand.evidence_quote,
            deadline_phrase=cand.deadline_phrase,
            deadline_at=cand.deadline_at,
            deadline_rule=cand.deadline_rule,
            extracted_by=extractor_name,
            confidence=cand.confidence,
        )
        session.add(commitment)
        await session.flush()
        await log_event(session, commitment.id, "proposed", {"confidence": cand.confidence})
        ids.append(commitment.id)
    return ids


async def by_short_no(session: AsyncSession, team_id: str, short_no: int) -> Commitment | None:
    return await session.scalar(
        select(Commitment).where(Commitment.team_id == team_id, Commitment.short_no == short_no)
    )


def decider_id(commitment: Commitment) -> str:
    """Only the person who would be held accountable may confirm a commitment."""
    return commitment.owner_member_id or commitment.creator_member_id


async def set_status(
    session: AsyncSession,
    commitment: Commitment,
    status: CommitmentStatus,
    *,
    by_member_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    previous = commitment.status
    commitment.status = status
    now = utcnow()
    if status == CommitmentStatus.CONFIRMED:
        commitment.confirmed_at = now
    if status == CommitmentStatus.DONE:
        commitment.completed_at = now
    await log_event(
        session,
        commitment.id,
        f"status:{status}",
        {"from": previous, "by": by_member_id, **(detail or {})},
    )


async def open_commitments(session: AsyncSession, team_id: str) -> list[Commitment]:
    return list(
        await session.scalars(
            select(Commitment)
            .where(Commitment.team_id == team_id, Commitment.status.in_(OPEN_STATUSES))
            .order_by(Commitment.deadline_at.is_(None), Commitment.deadline_at, Commitment.short_no)
        )
    )


async def tracked_commitments(session: AsyncSession, team_id: str) -> list[Commitment]:
    """Everything a human confirmed — the basis of the delivery report."""
    tracked = (*OPEN_STATUSES, CommitmentStatus.DONE)
    return list(
        await session.scalars(
            select(Commitment).where(
                Commitment.team_id == team_id, Commitment.status.in_(tracked)
            )
        )
    )


async def all_teams(session: AsyncSession) -> list[Team]:
    return list(await session.scalars(select(Team)))
