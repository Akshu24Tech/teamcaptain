"""FastAPI app: serves the Inngest workflows plus a small read-only API.

Run: `uvicorn tc.main:app --port 8000`
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import inngest.fast_api
from fastapi import FastAPI, HTTPException
from sqlalchemy import select

from tc.db import Commitment, Member, Team, create_tables, get_session, utcnow
from tc.inngest_client import client
from tc.repo import tracked_commitments
from tc.scoring import TrackedItem, delivery_report
from tc.workflows import FUNCTIONS


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await create_tables()
    yield


app = FastAPI(title="TeamCaptain", lifespan=lifespan)
inngest.fast_api.serve(app, client, FUNCTIONS)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


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
