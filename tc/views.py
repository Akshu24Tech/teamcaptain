"""Everything the bot says, built from the database. Pure formatting lives here."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tc.bot.telegram import buttons, esc, mention
from tc.config import settings
from tc.db import Commitment, Member, Team, as_aware
from tc.repo import decider_id, open_commitments, team_members, tracked_commitments
from tc.scoring import TrackedItem, delivery_report


@dataclass
class CommitmentView:
    commitment: Commitment
    team: Team
    owner: Member | None
    creator: Member
    decider: Member

    @property
    def owner_tag(self) -> str:
        if self.owner is None:
            return "<i>unassigned</i>"
        return mention(self.owner.telegram_user_id, self.owner.display_name)

    @property
    def decider_tag(self) -> str:
        return mention(self.decider.telegram_user_id, self.decider.display_name)


async def load_view(session: AsyncSession, commitment_id: str) -> CommitmentView:
    c = await session.get(Commitment, commitment_id)
    if c is None:
        raise LookupError(f"commitment {commitment_id} not found")
    team = await session.get(Team, c.team_id)
    creator = await session.get(Member, c.creator_member_id)
    owner = await session.get(Member, c.owner_member_id) if c.owner_member_id else None
    decider = await session.get(Member, decider_id(c))
    assert team and creator and decider
    return CommitmentView(c, team, owner, creator, decider)


def fmt_deadline(dt: datetime | None) -> str:
    if dt is None:
        return "no deadline"
    local = as_aware(dt).astimezone(settings.tz)  # type: ignore[union-attr]
    return local.strftime("%a %d %b, %I:%M %p")


def proposal(v: CommitmentView) -> tuple[str, dict[str, Any]]:
    c = v.commitment
    verb = {
        "commitment": "I noted a promise",
        "assignment": "I noted a task request",
        "tentative": "I noted a maybe",
    }[c.kind]
    lines = [
        f"📌 <b>{verb}</b> (#{c.short_no})",
        f"<b>Task:</b> {esc(c.task)}",
        f"<b>Owner:</b> {v.owner_tag}",
        f"<b>Deadline:</b> {fmt_deadline(c.deadline_at)}"
        + (f" <i>(from “{esc(c.deadline_phrase)}”)</i>" if c.deadline_phrase else ""),
        f"<b>Because you wrote:</b> “{esc(c.evidence_quote)}”",
        "",
        f"{v.decider_tag}, should I track this?",
    ]
    markup = buttons([[("✅ Yes, track it", f"y:{c.id}"), ("❌ No", f"n:{c.id}")]])
    return "\n".join(lines), markup


def confirm_done(v: CommitmentView, evidence_quote: str) -> tuple[str, dict[str, Any]]:
    """Detected 'I finished it' in chat — ask the owner to confirm before closing."""
    c = v.commitment
    text = (
        f"✅ {v.decider_tag}, did you finish #{c.short_no}: <b>{esc(c.task)}</b>?\n"
        f"<i>Because you wrote:</i> “{esc(evidence_quote)}”"
    )
    markup = buttons([[("✅ Yes, done", f"d:{c.id}"), ("↩️ No, still open", f"dx:{c.id}")]])
    return text, markup


def reminder(v: CommitmentView) -> tuple[str, dict[str, Any]]:
    c = v.commitment
    text = (
        f"⏰ {v.owner_tag}, reminder for #{c.short_no}: <b>{esc(c.task)}</b>\n"
        f"Due {fmt_deadline(c.deadline_at)}. How's it going?"
    )
    return text, buttons([[("✅ Mark done", f"d:{c.id}")]])


def checkin(v: CommitmentView) -> tuple[str, dict[str, Any]]:
    c = v.commitment
    text = (
        f"👋 {v.owner_tag}, checking in on #{c.short_no}: <b>{esc(c.task)}</b>\n"
        "It has no deadline. Done already? Or reply with a date, e.g. "
        f"<code>/deadline {c.short_no} friday</code>"
    )
    return text, buttons([[("✅ Mark done", f"d:{c.id}")]])


def overdue(v: CommitmentView) -> tuple[str, dict[str, Any]]:
    c = v.commitment
    text = (
        f"🔴 {v.owner_tag}, #{c.short_no} <b>{esc(c.task)}</b> was due "
        f"{fmt_deadline(c.deadline_at)} and is still open.\n"
        f"Need help or a new date? <code>/deadline {c.short_no} tomorrow</code>"
    )
    return text, buttons([[("✅ Mark done", f"d:{c.id}")]])


def escalation(v: CommitmentView) -> str:
    c = v.commitment
    return (
        f"📣 Team heads-up: #{c.short_no} <b>{esc(c.task)}</b> "
        f"({esc(v.owner.display_name) if v.owner else 'unassigned'}) is overdue since "
        f"{fmt_deadline(c.deadline_at)}. Can someone help?"
    )


async def task_list(session: AsyncSession, team: Team, now: datetime) -> str:
    items = await open_commitments(session, team.id)
    if not items:
        return "✨ No open tasks. Promise something and I'll track it."
    lines = ["📋 <b>Open tasks</b>"]
    for c in items:
        owner = await session.get(Member, c.owner_member_id) if c.owner_member_id else None
        deadline = as_aware(c.deadline_at)
        icon = "🔴" if deadline and deadline < now else "⏳"
        who = esc(owner.display_name) if owner else "unassigned"
        lines.append(f"{icon} #{c.short_no} {esc(c.task)} — {who}, {fmt_deadline(deadline)}")
    lines.append("\nFinished something? <code>/done N</code>")
    return "\n".join(lines)


async def report(session: AsyncSession, team: Team, now: datetime) -> str:
    rows = await tracked_commitments(session, team.id)
    names = {m.id: m.display_name for m in await team_members(session, team.id)}
    items = [
        TrackedItem(
            owner=names.get(c.owner_member_id) if c.owner_member_id else None,
            status=c.status,
            deadline_at=c.deadline_at,
            completed_at=c.completed_at,
        )
        for c in rows
    ]
    stats = delivery_report(items, now)
    if not stats:
        return "No confirmed commitments yet, so there's nothing to report."
    lines = ["📊 <b>Delivery report</b> (confirmed commitments only)"]
    for s in stats:
        rate = f"{int(s.reliability * 100)}% on time" if s.reliability is not None else "n/a"
        lines.append(
            f"• <b>{esc(s.name)}</b>: {s.tracked} tracked · ✅ {s.done_on_time} on time · "
            f"🐢 {s.done_late} late · ☑️ {s.done_no_deadline} done (no deadline) · "
            f"🔴 {s.overdue_open} overdue · ⏳ {s.open_on_track} open — {rate}"
        )
    lines.append("\n<i>Measures promises kept, not the size of the work.</i>")
    return "\n".join(lines)
