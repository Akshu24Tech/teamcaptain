"""Shapes passed between extraction stages."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Kind = Literal["commitment", "assignment", "tentative"]


class ChatLine(BaseModel):
    speaker: str
    text: str


class ExtractionInput(BaseModel):
    """Everything the extractor may look at — and nothing else."""

    speaker: str  # display name of whoever sent the message
    text: str
    sent_at: datetime  # timezone-aware
    members: list[str]  # display names (and @usernames) of the team
    context: list[ChatLine] = Field(default_factory=list)  # earlier messages, oldest first


class RawCandidate(BaseModel):
    """What the model is asked to return. Untrusted until verified."""

    task: str = Field(min_length=2, max_length=300)
    owner: str | None = None  # "speaker", a member name, or null if unclear
    kind: Kind
    deadline_phrase: str | None = None
    evidence_quote: str = Field(min_length=2)


class RawExtraction(BaseModel):
    commitments: list[RawCandidate] = Field(default_factory=list)


class Candidate(BaseModel):
    """A verified candidate, ready to be proposed to the team for confirmation."""

    task: str
    owner: str | None  # resolved member display name, or None if unresolved
    kind: Kind
    evidence_quote: str
    deadline_phrase: str | None
    deadline_at: datetime | None
    deadline_rule: str | None
    confidence: float


class Rejection(BaseModel):
    """A candidate the verifier threw away, and why. Logged for the evaluation chapter."""

    task: str
    reason: str


class ExtractionResult(BaseModel):
    candidates: list[Candidate] = Field(default_factory=list)
    rejected: list[Rejection] = Field(default_factory=list)
    skipped_by_prefilter: bool = False
    extractor: str = ""
    latency_ms: int = 0
