"""Map the owner the model names onto a real team member — deterministically."""

from __future__ import annotations

import re


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", name.lower().lstrip("@")).strip()


def resolve_owner(
    owner: str | None,
    speaker: str,
    members: list[str],
    aliases: dict[str, str] | None = None,
) -> str | None:
    """Return the matching member display name, or None if it can't be matched exactly.

    Matching order: "speaker"/"me" → speaker; exact name; alias (e.g. @username);
    unique first name. An ambiguous or unknown name returns None — the confirmation
    step then asks a human instead of guessing.
    """
    if owner is None:
        return None
    key = _norm(owner)
    if key in {"speaker", "me", "i", "self", "main", "mai"}:
        return speaker
    if not key:
        return None
    by_full = {_norm(m): m for m in members}
    if key in by_full:
        return by_full[key]
    for alias, display in (aliases or {}).items():
        if _norm(alias) == key and display in members:
            return display
    first_matches = [m for m in members if _norm(m).split(" ")[0] == key.split(" ")[0]]
    if len(first_matches) == 1:
        return first_matches[0]
    return None
