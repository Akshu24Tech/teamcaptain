"""Extraction pipeline: prefilter → extractor (LLM or rules) → verifier.

Two interchangeable extractors exist so they can be compared in the evaluation chapter:
- LLMExtractor: any OpenAI-compatible model (Groq, Ollama, Gemini).
- RuleBasedExtractor: a regex baseline with no AI at all.
"""

from __future__ import annotations

import re
import time
from typing import Protocol
from zoneinfo import ZoneInfo

from tc.extraction.prefilter import should_extract
from tc.extraction.prompts import EXTRACTION_SYSTEM, build_user_prompt
from tc.extraction.schema import (
    ExtractionInput,
    ExtractionResult,
    RawCandidate,
    RawExtraction,
    RawUpdate,
)
from tc.extraction.verify import normalize, verify_candidates, verify_updates
from tc.llm import ChatModel, complete_json


class Extractor(Protocol):
    name: str

    async def propose(self, inp: ExtractionInput) -> RawExtraction: ...


class LLMExtractor:
    def __init__(self, model: ChatModel) -> None:
        self.model = model
        self.name = f"llm:{model.name}"

    async def propose(self, inp: ExtractionInput) -> RawExtraction:
        parsed, _usage = await complete_json(
            self.model, EXTRACTION_SYSTEM, build_user_prompt(inp), RawExtraction
        )
        return parsed


_TENTATIVE = re.compile(
    r"\b(i'?ll try|i will try|try karunga|try karungi|koshish|maybe i|i might|shayad)\b", re.I
)
_PROMISE = re.compile(
    r"\b(i'?ll|i will|i'?m going to|im going to|i am going to|let me|i can do|i'?ve got (this|it)|"
    r"leave it to me|on it|(kar|bana|bhej|likh|dekh|sambhal)\s?(dunga|dungi|lunga|lungi)|"
    r"karunga|karungi|banaunga|banaungi|bhejunga|bhejungi|likhunga|likhungi)\b",
    re.I,
)
_REQUEST = re.compile(
    r"\b(can you|could you|will you|please|pls|plz|handle|take care of|"
    r"(kar|bana|bhej|likh|dekh|sambhal)\s?(dena|lena|do|lo))\b",
    re.I,
)
_DONE = re.compile(
    r"\b(done|finished|completed|complete|submitted|ho gaya|ho gya|kar diya|kar liya|"
    r"bana diya|bhej diya|likh diya|de diya)\b",
    re.I,
)
_ACCEPT = re.compile(
    r"^\s*(ok|okay|sure|yes|yeah|haan|ha|theek hai|thik hai|pakka|will do)\b", re.I
)


def _short_task(text: str) -> str:
    words = re.sub(r"\s+", " ", text).strip().split(" ")
    return " ".join(words[:12])


def _mentioned_member(text: str, speaker: str, members: list[str]) -> str | None:
    norm = f" {normalize(text)} "
    hits = [
        m for m in members
        if m != speaker and f" {normalize(m).split(' ')[0]} " in norm
    ]
    return hits[0] if len(hits) == 1 else None


class RuleBasedExtractor:
    """No-AI baseline. Exists to show what the LLM actually adds."""

    name = "rule-based"

    async def propose(self, inp: ExtractionInput) -> RawExtraction:
        text = inp.text
        if _DONE.search(text) and not _PROMISE.search(text):
            # A finished-work report is an update to an existing task, not a new one.
            if inp.open_tasks:
                return RawExtraction(
                    updates=[RawUpdate(intent="done", task_ref=text, evidence_quote=text)]
                )
            return RawExtraction()
        if _TENTATIVE.search(text):
            return RawExtraction(commitments=[RawCandidate(
                task=_short_task(text), owner="speaker", kind="tentative", evidence_quote=text)])
        if _PROMISE.search(text):
            return RawExtraction(commitments=[RawCandidate(
                task=_short_task(text), owner="speaker", kind="commitment", evidence_quote=text)])
        target = _mentioned_member(text, inp.speaker, inp.members)
        if target and _REQUEST.search(text):
            return RawExtraction(commitments=[RawCandidate(
                task=_short_task(text), owner=target, kind="assignment", evidence_quote=text)])
        if inp.context and _ACCEPT.search(text):
            previous = inp.context[-1]
            if (_mentioned_member(previous.text, previous.speaker, inp.members) == inp.speaker
                    and _REQUEST.search(previous.text)):
                return RawExtraction(commitments=[RawCandidate(
                    task=_short_task(previous.text), owner="speaker", kind="commitment",
                    evidence_quote=text)])
        return RawExtraction()


async def run_extraction(
    extractor: Extractor,
    inp: ExtractionInput,
    tz: ZoneInfo,
    *,
    aliases: dict[str, str] | None = None,
    use_prefilter: bool = True,
) -> ExtractionResult:
    started = time.perf_counter()
    if use_prefilter:
        passed, _reason = should_extract(
            inp.text, has_context=bool(inp.context), has_open_tasks=bool(inp.open_tasks)
        )
        if not passed:
            return ExtractionResult(skipped_by_prefilter=True, extractor=extractor.name)
    raw = await extractor.propose(inp)
    candidates, rejected = verify_candidates(raw.commitments, inp, tz, aliases=aliases)
    updates, update_rejections = verify_updates(raw.updates, inp)
    return ExtractionResult(
        candidates=candidates,
        updates=updates,
        rejected=rejected + update_rejections,
        extractor=extractor.name,
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
