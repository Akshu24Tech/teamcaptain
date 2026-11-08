"""Cheap deterministic gate in front of the LLM.

Most chat messages ("lol", "send the link") contain no commitment. Skipping them
saves free-tier quota and removes a whole class of false positives. The gate is
tuned for high recall: when in doubt it lets the message through, because the LLM
and the human confirmation step catch the rest. Its own recall is measured in evals.
"""

from __future__ import annotations

import re

_CUES = [
    # English promises and assignments
    r"\bi'?ll\b", r"\bi will\b", r"\bi'?m going to\b", r"\bim going to\b", r"\bi am going to\b",
    r"\bgonna\b", r"\bi can (do|handle|take|make|write|finish|send|prepare)\b", r"\blet me\b",
    r"\bi'?ll take\b", r"\bon it\b", r"\bleave it (to|with) me\b", r"\bi'?ve got (this|it)\b",
    r"\bcan you\b", r"\bcould you\b", r"\bwill you\b", r"\bplease\b", r"\bpls\b", r"\bplz\b",
    r"\bhandle\b", r"\btake care of\b", r"\bresponsible for\b", r"\bassign", r"\bin charge\b",
    r"\bdeadline\b", r"\bby (today|tonight|tomorrow|monday|tuesday|wednesday|thursday|friday|"
    r"saturday|sunday|eod|end of)\b", r"\bbefore\b", r"\bsubmit\b",
    # Hinglish promises and assignments
    r"\b(kar|bana|bhej|likh|dekh|sambhal|nipta|complete kar|finish kar)\s?(dunga|dungi|lunga|lungi|"
    r"dena|lena|do|lo|denge|lenge)\b",
    r"\b(karunga|karungi|karenge|banaunga|banaungi|bhejunga|bhejungi|likhunga|likhungi)\b",
    r"\bmain (kar|bana|dekh|bhej|likh)\b", r"\bmai (kar|bana|dekh|bhej|likh)\b",
    r"\btu (kar|bana|dekh|bhej|likh)\b", r"\btum (kar|bana|dekh|bhej|likh)\b",
    r"\b(tak|se pehle)\b", r"\bkoshish\b", r"\btry\b",
]
_CUE_RE = re.compile("|".join(_CUES), re.IGNORECASE)

# Bare acceptances only matter as replies to an assignment, so they need context.
_ACCEPT_RE = re.compile(
    r"^\s*(ok|okay|okk|k|sure|done|yes|yeah|yup|haan|ha|theek hai|thik hai|chalo|pakka|"
    r"bilkul|will do|on it|got it)\b",
    re.IGNORECASE,
)

# Someone reporting a tracked task is finished. Only meaningful when tasks are open;
# the verifier drops it if it matches none, and the human still confirms the close.
_DONE_RE = re.compile(
    r"\b(done|finished|completed|complete|submitted|sent it|wrapped up|ho gaya|ho gya|"
    r"kar diya|kar liya|bana diya|bana li|bhej diya|likh diya|de diya)\b",
    re.IGNORECASE,
)
# A question about status isn't a completion report — don't treat "is it done?" as done.
_QUESTION_RE = re.compile(r"\b(is|are|kya|kab|when|has|have)\b.*\?|\?\s*$", re.IGNORECASE)


def should_extract(
    text: str, *, has_context: bool, has_open_tasks: bool = False
) -> tuple[bool, str]:
    """Return (pass?, reason). The reason is logged so the gate stays auditable."""
    stripped = text.strip()
    if not stripped:
        return False, "empty"
    if stripped.startswith("/"):
        return False, "bot_command"
    if _CUE_RE.search(stripped):
        return True, "commitment_cue"
    if has_open_tasks and _DONE_RE.search(stripped) and not _QUESTION_RE.search(stripped):
        return True, "completion_cue"
    if has_context and _ACCEPT_RE.search(stripped):
        return True, "acceptance_reply"
    return False, "no_cue"
