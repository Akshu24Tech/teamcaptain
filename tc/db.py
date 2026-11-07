"""Database models and session. Works on Postgres (asyncpg) and SQLite (aiosqlite, tests)."""

from __future__ import annotations

import enum
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from tc.config import settings


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def new_id() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class CommitmentStatus(enum.StrEnum):
    PROPOSED = "proposed"  # extracted, waiting for the owner to confirm
    CONFIRMED = "confirmed"  # owner said yes; being tracked
    REJECTED = "rejected"  # owner said no — the extraction was wrong or unwanted
    EXPIRED = "expired"  # nobody confirmed in time; never tracked
    DONE = "done"
    OVERDUE = "overdue"  # deadline passed, still open
    CANCELLED = "cancelled"


OPEN_STATUSES = (CommitmentStatus.CONFIRMED, CommitmentStatus.OVERDUE)


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    members: Mapped[list[Member]] = relationship(back_populates="team")


class Member(Base):
    __tablename__ = "members"
    __table_args__ = (UniqueConstraint("team_id", "telegram_user_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    display_name: Mapped[str] = mapped_column(String(255))

    team: Mapped[Team] = relationship(back_populates="members")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("team_id", "telegram_message_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id"))
    telegram_message_id: Mapped[int] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(Text)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class Commitment(Base):
    __tablename__ = "commitments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), index=True)
    source_message_id: Mapped[str] = mapped_column(ForeignKey("messages.id"))
    creator_member_id: Mapped[str] = mapped_column(ForeignKey("members.id"))
    owner_member_id: Mapped[str | None] = mapped_column(ForeignKey("members.id"), nullable=True)
    short_no: Mapped[int] = mapped_column(Integer)  # per-team number users type: /done 4
    task: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16))  # commitment | assignment | tentative
    evidence_quote: Mapped[str] = mapped_column(Text)
    deadline_phrase: Mapped[str | None] = mapped_column(String(255), nullable=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deadline_rule: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Bumped on every deadline change; a running workflow with an older version stops itself.
    schedule_version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default=CommitmentStatus.PROPOSED, index=True)
    extracted_by: Mapped[str] = mapped_column(String(128))
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CommitmentEvent(Base):
    """Append-only audit log: every state change and every reminder, with why."""

    __tablename__ = "commitment_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    commitment_id: Mapped[str] = mapped_column(ForeignKey("commitments.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def configure(database_url: str | None = None) -> AsyncEngine:
    """(Re)create the engine. Tests call this with a SQLite URL."""
    global _engine, _sessionmaker
    _engine = create_async_engine(database_url or settings.database_url, pool_pre_ping=True)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


async def create_tables() -> None:
    engine = _engine or configure()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    if _sessionmaker is None:
        configure()
    assert _sessionmaker is not None
    async with _sessionmaker() as session:
        yield session


def as_aware(dt: datetime | None) -> datetime | None:
    """SQLite drops tzinfo; treat naive datetimes from the DB as UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
