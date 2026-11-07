"""FastAPI app: serves the Inngest workflows plus a small read-only API.

Run: `uvicorn tc.main:app --port 8000`
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import inngest
import inngest.fast_api
from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import select

from tc.bot.handlers import handle_update
from tc.bot.telegram import TelegramClient
from tc.config import settings
from tc.db import Commitment, Member, Team, create_tables, get_session, utcnow
from tc.inngest_client import client
from tc.repo import tracked_commitments
from tc.scoring import TrackedItem, delivery_report
from tc.workflows import FUNCTIONS

log = logging.getLogger("teamcaptain.webhook")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await create_tables()
    yield


app = FastAPI(title="TeamCaptain", lifespan=lifespan)
inngest.fast_api.serve(app, client, FUNCTIONS)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# --- Telegram webhook (always-on hosting; local dev uses tc.bot.poller instead) ---

_tg: TelegramClient | None = None


def _telegram() -> TelegramClient:
    global _tg
    if _tg is None:
        _tg = TelegramClient()
    return _tg


async def send_event(name: str, data: dict[str, Any]) -> None:
    await client.send(inngest.Event(name=name, data=data))


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> dict[str, bool]:
    # Only active when a secret is configured; the secret proves the caller is Telegram.
    if not settings.webhook_secret:
        raise HTTPException(404, "webhook not enabled")
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != settings.webhook_secret:
        raise HTTPException(403, "bad webhook secret")
    update = await request.json()
    try:
        await handle_update(update, _telegram(), send_event)
    except Exception:
        # One bad update must never 500 the webhook: Telegram would retry it forever.
        log.exception("Failed to handle update %s", update.get("update_id"))
    return {"ok": True}


async def _team(session: Any, chat_id: int) -> Team:
    team = await session.scalar(select(Team).where(Team.chat_id == chat_id))
    if team is None:
        raise HTTPException(404, "team not found")
    return team


@app.get("/teams/{chat_id}/commitments")
async def commitments(chat_id: int) -> list[dict[str, Any]]:
    async with get_session() as session:
        team = await _team(session, chat_id)
        rows = await session.scalars(
            select(Commitment).where(Commitment.team_id == team.id).order_by(Commitment.short_no)
        )
        return [
            {
                "no": c.short_no, "task": c.task, "kind": c.kind, "status": c.status,
                "owner_member_id": c.owner_member_id, "deadline_at": c.deadline_at,
                "deadline_phrase": c.deadline_phrase, "deadline_rule": c.deadline_rule,
                "evidence_quote": c.evidence_quote, "confidence": c.confidence,
                "extracted_by": c.extracted_by,
            }
            for c in rows
        ]


@app.get("/teams/{chat_id}/report")
async def report(chat_id: int) -> list[dict[str, Any]]:
    async with get_session() as session:
        team = await _team(session, chat_id)
        names = {
            m.id: m.display_name
            for m in await session.scalars(select(Member).where(Member.team_id == team.id))
        }
        items = [
            TrackedItem(names.get(c.owner_member_id or ""), c.status, c.deadline_at,
                        c.completed_at)
            for c in await tracked_commitments(session, team.id)
        ]
    return [
        {**s.__dict__, "reliability": s.reliability}
        for s in delivery_report(items, utcnow())
    ]
