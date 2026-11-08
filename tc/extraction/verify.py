"""The trust layer: every candidate an extractor proposes is checked by plain code.

An LLM can invent a quote, a person or a date. Nothing it says reaches the team
unless it survives these checks, and every rejection is recorded with a reason.
"""

from __future__ import annotations

import re
from zoneinfo import ZoneInfo

from tc.config import settings
from tc.extraction.dates import resolve_deadline
from tc.extraction.owners import resolve_owner
from tc.extraction.schema import (
    Candidate,
    CommitmentUpdate,
    ExtractionInput,
    OpenTask,
    RawCandidate,
    RawUpdate,
    Rejection,
)

KIND_CONFIDENCE = {"commitment": 1.0, "assignment": 0.9, "tentative": 0.6}

# Words too generic to identify which task is meant. Matching ignores them.
_STOPWORDS = frozenset(
    (
        "the a an my our your is are was were done finished complete completed task tasks "
        + "for to of and with it that this on i we have has had just now today"
    ).split()
)


def normalize(text: str) -> str:
    text = text.lower().replace("’", "'")
    text = re.sub(r"[^\w\s']", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def appears_in(fragment: str, source: str) -> bool:
    frag = normalize(fragment)
    return bool(frag) and frag in normalize(source)


def verify_candidates(
    raw: list[RawCandidate],
    inp: ExtractionInput,
    tz: ZoneInfo,
    *,
    aliases: dict[str, str] | None = None,
) -> tuple[list[Candidate], list[Rejection]]:
    accepted: list[Candidate] = []
    rejected: list[Rejection] = []
    seen_tasks: set[str] = set()
    all_text = " ".join([line.text for line in inp.context] + [inp.text])

    for cand in raw:
        # 1. Grounding: the evidence must be a verbatim span of the latest message.
        if not appears_in(cand.evidence_quote, inp.text):
            rejected.append(Rejection(task=cand.task, reason="evidence_not_in_message"))
            continue

        # 2. Owner: map onto a real member; never accept an invented person.
        owner = resolve_owner(cand.owner, inp.speaker, inp.members, aliases)
        kind = cand.kind
        if kind == "commitment" and owner is not None and owner != inp.speaker:
            kind = "assignment"  # the model said "promise" but named someone else
        if kind == "assignment" and owner == inp.speaker:
            kind = "commitment"
        if kind in ("commitment", "tentative") and owner is None:
            owner = inp.speaker
        confidence = KIND_CONFIDENCE[kind]
        if owner is None:
            confidence -= 0.2

        # 3. Deadline: the phrase must exist in the chat; the date is computed by code.
        phrase = cand.deadline_phrase
        if phrase and not appears_in(phrase, all_text):
            rejected.append(Rejection(task=cand.task, reason="deadline_phrase_not_in_chat"))
            phrase = None
            confidence -= 0.1
        if phrase is None and len(raw) == 1:
            # Single candidate: recover a deadline the model failed to point at.
            fallback = resolve_deadline(
                inp.text, inp.sent_at, tz,
                default_hour=settings.default_deadline_hour,
                default_minute=settings.default_deadline_minute,
            )
            if fallback is not None:
                phrase = fallback.matched
        resolved = resolve_deadline(
            phrase, inp.sent_at, tz,
            default_hour=settings.default_deadline_hour,
            default_minute=settings.default_deadline_minute,
        )
        if phrase and resolved is None:
            confidence -= 0.1  # a deadline was mentioned but couldn't be pinned to a date
        if resolved is not None and resolved.at < inp.sent_at:
            rejected.append(Rejection(task=cand.task, reason="deadline_in_past"))
            resolved = None

        # 4. De-duplicate within one message.
        task_key = normalize(cand.task)
        if task_key in seen_tasks:
            rejected.append(Rejection(task=cand.task, reason="duplicate"))
            continue
        seen_tasks.add(task_key)

        accepted.append(
            Candidate(
                task=cand.task.strip().rstrip("."),
                owner=owner,
                kind=kind,
                evidence_quote=cand.evidence_quote,
                deadline_phrase=phrase,
                deadline_at=resolved.at if resolved else None,
                deadline_rule=resolved.rule if resolved else None,
                confidence=round(max(0.0, min(1.0, confidence)), 2),
            )
        )
    return accepted, rejected


def _match_open_task(
    task_ref: str, message_text: str, open_tasks: list[OpenTask], speaker: str
) -> tuple[OpenTask, float] | None:
    """Pick the open task an update points at, or None. Deterministic — never a guess.

    Returns (task, confidence). An explicit "#N" is certain; a unique keyword match is
    strong; falling back to the speaker's only open task is weak but safe (they confirm).
    """
    if not open_tasks:
        return None
    explicit = re.search(r"#\s*(\d+)", f"{task_ref} {message_text}")
    if explicit:
        n = int(explicit.group(1))
        hit = next((t for t in open_tasks if t.short_no == n), None)
        return (hit, 1.0) if hit else None
    ref = set(normalize(task_ref).split()) - _STOPWORDS
    overlaps = sorted(
        ((len(ref & (set(normalize(t.task).split()) - _STOPWORDS)), t) for t in open_tasks),
        key=lambda pair: pair[0],
        reverse=True,
    )
    best = overlaps[0]
    if best[0] > 0 and (len(overlaps) == 1 or best[0] > overlaps[1][0]):
        return (best[1], 0.8)
    # No distinguishing words: only safe if the speaker has exactly one open task.
    owned = [t for t in open_tasks if t.owner == speaker]
    if len(owned) == 1:
        return (owned[0], 0.6)
    return None


def verify_updates(
    raw: list[RawUpdate], inp: ExtractionInput
) -> tuple[list[CommitmentUpdate], list[Rejection]]:
    """Ground each proposed update in a real open task before it can reach the team."""
    accepted: list[CommitmentUpdate] = []
    rejected: list[Rejection] = []
    seen: set[int] = set()

    for up in raw:
        # 1. Grounding: the evidence must be a verbatim span of the latest message.
        if not appears_in(up.evidence_quote, inp.text):
            rejected.append(Rejection(task=up.task_ref, reason="evidence_not_in_message"))
            continue
        # 2. It must point at a real open task, resolved by code.
        match = _match_open_task(up.task_ref, inp.text, inp.open_tasks, inp.speaker)
        if match is None:
            rejected.append(Rejection(task=up.task_ref, reason="no_matching_open_task"))
            continue
        task, confidence = match
        # 3. You can only report your own task done (or an unassigned one).
        if task.owner is not None and task.owner != inp.speaker:
            rejected.append(Rejection(task=task.task, reason="not_speakers_task"))
            continue
        if task.short_no in seen:
            rejected.append(Rejection(task=task.task, reason="duplicate"))
            continue
        seen.add(task.short_no)
        accepted.append(
            CommitmentUpdate(
                intent=up.intent,
                short_no=task.short_no,
                task=task.task,
                evidence_quote=up.evidence_quote,
                confidence=round(confidence, 2),
            )
        )
    return accepted, rejected
