"""Durable workflows (Inngest). Each `ctx.step.*` call is checkpointed: if the app crashes
or restarts, the run resumes from the last finished step instead of starting over.

Rules every function here follows (see CLAUDE.md):
1. Side effects (DB writes, Telegram messages, LLM calls) happen only inside steps.
2. "Now" is read inside a step, so replays see the same clock value.
3. Every notification step re-checks the database first. Waits are an optimisation;
   the database is the source of truth. A stale workflow can never send a wrong reminder.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import inngest

from tc import views
from tc.bot.telegram import TelegramClient
from tc.config import settings
from tc.db import OPEN_STATUSES, Commitment, CommitmentStatus, as_aware, get_session, utcnow
from tc.events import (
    COMMITMENT_CANCELLED,
    COMMITMENT_COMPLETED,
    COMMITMENT_DECIDED,
    COMMITMENT_PROPOSED,
    COMMITMENT_RESCHEDULED,
    MESSAGE_RECEIVED,
)
from tc.extraction.extractor import run_extraction
from tc.extraction.factory import get_extractor
from tc.inngest_client import client
from tc.repo import all_teams, build_extraction_input, create_proposals, log_event, set_status

_MATCH_ID = "async.data.commitment_id == event.data.commitment_id"
_MIN_WAIT = timedelta(seconds=1)


# --------------------------------------------------------------------------- helpers

async def _send(chat_id: int, text: str, markup: dict[str, Any] | None = None) -> None:
    tg = TelegramClient()
    try:
        await tg.send_message(chat_id, text, markup)
    finally:
        await tg.close()


async def _clock() -> str:
    return utcnow().isoformat()


def _wait(until: datetime, now: datetime) -> timedelta:
    return max(_MIN_WAIT, until - now)


async def _is_active(commitment_id: str, version: int) -> bool:
    async with get_session() as session:
        c = await session.get(Commitment, commitment_id)
        return bool(c and c.status in OPEN_STATUSES and c.schedule_version == version)


async def _notify(commitment_id: str, version: int, kind: str) -> dict[str, Any]:
    """Check-then-send in ONE step, so a reminder is never sent for a closed task."""
    async with get_session() as session:
        c = await session.get(Commitment, commitment_id)
        if not (c and c.status in OPEN_STATUSES and c.schedule_version == version):
            return {"sent": False, "reason": "no_longer_active"}
        v = await views.load_view(session, commitment_id)
        if kind == "overdue":
            await set_status(session, c, CommitmentStatus.OVERDUE)
            text, markup = views.overdue(v)
        elif kind == "reminder":
            text, markup = views.reminder(v)
        elif kind == "checkin":
            text, markup = views.checkin(v)
        else:
            text, markup = views.escalation(v), None
        await log_event(session, c.id, f"notified:{kind}", {"version": version})
        await session.commit()
        chat_id = v.team.chat_id
    await _send(chat_id, text, markup)
    return {"sent": True}


# --------------------------------------------------------------------------- functions

@client.create_function(
    fn_id="process-message",
    name="Extract commitments from a message",
    trigger=inngest.TriggerEvent(event=MESSAGE_RECEIVED),
    idempotency="event.data.message_id",
    concurrency=[inngest.Concurrency(limit=settings.extraction_concurrency)],
    # Free LLM tiers are rate-limited: queue extra runs instead of failing them.
    throttle=inngest.Throttle(limit=settings.extraction_runs_per_minute,
                              period=timedelta(minutes=1)),
    retries=4,
)
async def process_message(ctx: inngest.Context) -> dict[str, Any]:
    message_id = ctx.event.data["message_id"]

    async def extract() -> dict[str, Any]:
        async with get_session() as session:
            inp, aliases, _ = await build_extraction_input(session, message_id)
        result = await run_extraction(get_extractor(), inp, settings.tz, aliases=aliases)
        return result.model_dump(mode="json")

    result = await ctx.step.run("extract", extract)
    if result["skipped_by_prefilter"] or not result["candidates"]:
        return {"proposals": 0, "rejected": len(result["rejected"]),
                "skipped": result["skipped_by_prefilter"]}

    async def save() -> list[str]:
        from tc.extraction.schema import Candidate

        candidates = [Candidate.model_validate(c) for c in result["candidates"]]
        async with get_session() as session:
            _, _, name_to_id = await build_extraction_input(session, message_id)
            ids = await create_proposals(
                session, message_id, candidates, result["extractor"], name_to_id
            )
            await session.commit()
            return ids

    ids = await ctx.step.run("save-proposals", save)
    await ctx.step.send_event(
        "emit-proposals",
        [inngest.Event(name=COMMITMENT_PROPOSED, data={"commitment_id": i}) for i in ids],
    )
    return {"proposals": len(ids), "rejected": len(result["rejected"])}


@client.create_function(
    fn_id="commitment-lifecycle",
    name="Commitment lifecycle: confirm, remind, escalate",
    trigger=[
        inngest.TriggerEvent(event=COMMITMENT_PROPOSED),
        inngest.TriggerEvent(event=COMMITMENT_RESCHEDULED),
    ],
    cancel=[inngest.Cancel(event=COMMITMENT_CANCELLED, if_exp=_MATCH_ID)],
    retries=4,
)
async def commitment_lifecycle(ctx: inngest.Context) -> dict[str, Any]:
    cid: str = ctx.event.data["commitment_id"]
    step = ctx.step

    if ctx.event.name == COMMITMENT_PROPOSED:
        # ---- Phase 1: human-in-the-loop confirmation -------------------------------
        async def announce() -> None:
            async with get_session() as session:
                v = await views.load_view(session, cid)
                text, markup = views.proposal(v)
                chat_id = v.team.chat_id
            await _send(chat_id, text, markup)

        await step.run("announce", announce)

        async def current_status() -> str:
            async with get_session() as session:
                c = await session.get(Commitment, cid)
                return c.status if c else "missing"

        # Re-check right before waiting: the owner may have tapped already.
        status = await step.run("status-before-wait", current_status)
        if status == CommitmentStatus.PROPOSED:
            await step.wait_for_event(
                "await-decision",
                event=COMMITMENT_DECIDED,
                if_exp=_MATCH_ID,
                timeout=timedelta(hours=settings.confirmation_timeout_hours),
            )
            status = await step.run("status-after-wait", current_status)

        if status == CommitmentStatus.PROPOSED:
            async def expire() -> None:
                async with get_session() as session:
                    c = await session.get(Commitment, cid)
                    if c and c.status == CommitmentStatus.PROPOSED:
                        await set_status(session, c, CommitmentStatus.EXPIRED)
                        await session.commit()

            await step.run("expire", expire)
            return {"outcome": "expired_unconfirmed"}
        if status != CommitmentStatus.CONFIRMED:
            return {"outcome": status}
        version = 1
    else:
        version = int(ctx.event.data["version"])

    # ---- Phase 2: tracking until done -------------------------------------------
    async def snapshot() -> dict[str, Any]:
        async with get_session() as session:
            c = await session.get(Commitment, cid)
            deadline = as_aware(c.deadline_at) if c else None
            return {
                "active": bool(c and c.status in OPEN_STATUSES and c.schedule_version == version),
                "deadline": deadline.isoformat() if deadline else None,
                "now": utcnow().isoformat(),
            }

    snap = await step.run(f"v{version}-snapshot", snapshot)
    if not snap["active"]:
        return {"outcome": "not_active"}
    now = datetime.fromisoformat(snap["now"])

    async def finished(label: str) -> bool:
        return not await step.run(f"v{version}-active-{label}", _is_active, cid, version)

    async def wait_done(label: str, timeout: timedelta) -> bool:
        """True if the task was completed (or closed) during the wait."""
        await step.wait_for_event(
            f"v{version}-await-done-{label}", event=COMMITMENT_COMPLETED, if_exp=_MATCH_ID,
            timeout=timeout,
        )
        return await finished(label)

    if snap["deadline"] is None:
        for i in range(settings.no_deadline_max_checkins):
            if await wait_done(f"checkin-{i}", timedelta(days=settings.no_deadline_checkin_days)):
                return {"outcome": "done"}
            await step.run(f"v{version}-checkin-{i}", _notify, cid, version, "checkin")
        return {"outcome": "open_no_deadline"}

    deadline = datetime.fromisoformat(snap["deadline"])
    remind_at = deadline - timedelta(hours=settings.reminder_lead_hours)
    if remind_at > now:
        if await wait_done("before-reminder", _wait(remind_at, now)):
            return {"outcome": "done_before_reminder"}
        await step.run(f"v{version}-reminder", _notify, cid, version, "reminder")

    now = datetime.fromisoformat(await step.run(f"v{version}-clock-deadline", _clock))
    if deadline > now and await wait_done("before-deadline", _wait(deadline, now)):
        return {"outcome": "done_on_time"}
    if await finished("at-deadline"):
        return {"outcome": "done_on_time"}

    await step.run(f"v{version}-overdue", _notify, cid, version, "overdue")
    if await wait_done("before-escalation", timedelta(hours=settings.escalation_delay_hours)):
        return {"outcome": "done_late"}
    await step.run(f"v{version}-escalate", _notify, cid, version, "escalation")
    return {"outcome": "escalated"}



@client.create_function(
    fn_id="daily-standup",
    name="Daily standup summary",
    trigger=inngest.TriggerCron(cron=settings.standup_cron),
)
async def daily_standup(ctx: inngest.Context) -> dict[str, Any]:
    async def post_all() -> int:
        sent = 0
        now = utcnow()
        async with get_session() as session:
            teams = await all_teams(session)
            messages = []
            for team in teams:
                text = await views.task_list(session, team, now)
                if not text.startswith("✨"):
                    messages.append((team.chat_id, "🌙 <b>Daily standup</b>\n" + text))
        for chat_id, text in messages:
            await _send(chat_id, text)
            sent += 1
        return sent

    sent = await ctx.step.run("post-standups", post_all)
    return {"teams": sent}


FUNCTIONS = [process_message, commitment_lifecycle, daily_standup]
