"""Deterministic deadline resolution.

Design rule: the LLM may *point at* a deadline phrase ("by friday 5pm"), but it never
computes the date. Turning a phrase into a datetime is plain code, so it is testable,
explainable (every result carries the rule that fired) and never hallucinated.

Conventions (documented so they can be defended in a viva):
- A date without a time means the end of that day (default 23:59 local time).
- A weekday ("friday") means its next occurrence; today counts if today is that day.
- "next friday" means the Friday of the next calendar week (weeks start on Monday).
- "end of week" / "weekend" means the coming Sunday.
- "next week" means the end (Sunday) of the next calendar week.
- "kal" is read as tomorrow: in a commitment it always refers to the future.
- A day/month without a year that has already passed rolls over to next year.
- Numeric dates are day-first (15/11 = 15 November), as used in India.
- "N baje" without am/pm: hours 1-7 are read as pm (nobody promises work at 5 am).
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_RE = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"

WEEKDAYS = {
    "monday": 0, "mon": 0, "somvar": 0, "somvaar": 0,
    "tuesday": 1, "tue": 1, "tues": 1, "mangalvar": 1, "mangalvaar": 1,
    "wednesday": 2, "wed": 2, "budhvar": 2, "budhvaar": 2,
    "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "guruvar": 3, "veervar": 3,
    "friday": 4, "fri": 4, "shukravar": 4, "shukravaar": 4,
    "saturday": 5, "sat": 5, "shanivar": 5, "shanivaar": 5,
    "sunday": 6, "ravivar": 6, "ravivaar": 6,
}
_WEEKDAY_RE = "|".join(sorted(WEEKDAYS, key=len, reverse=True))

NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5,
}
_NUM_RE = r"(\d{1,2}|" + "|".join(NUMBER_WORDS) + ")"
_UNIT_RE = r"(days?|din|hours?|hrs?|ghante|ghanta|minutes?|mins?|minat|weeks?|hafte|hafta)"


@dataclass(frozen=True)
class ResolvedDeadline:
    at: datetime  # timezone-aware
    has_time: bool  # True if the text named a clock time ("5pm"), not just a day
    rule: str  # which rule fired — shown to users and logged for explainability
    matched: str  # the exact text span the rule matched


def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"\b(\d{1,2})\.(\d{2})(?!\d)", r"\1:\2", text)  # 5.30pm -> 5:30pm
    text = re.sub(r"[^\w\s:/\-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _num(token: str) -> int:
    return int(token) if token.isdigit() else NUMBER_WORDS[token]


def _find_time(text: str) -> tuple[time | None, str]:
    """Return (clock time, matched span) if the text names a time of day."""
    m = re.search(r"\b(\d{1,2}):(\d{2})\s*(am|pm)?\b", text)
    if m:
        hour, minute = int(m.group(1)), int(m.group(2))
        hour = _apply_meridiem(hour, m.group(3))
        if hour is not None and 0 <= minute < 60:
            return time(hour, minute), m.group(0)
    m = re.search(r"\b(\d{1,2})\s*(am|pm)\b", text)
    if m:
        hour = _apply_meridiem(int(m.group(1)), m.group(2))
        if hour is not None:
            return time(hour, 0), m.group(0)
    m = re.search(r"\b(\d{1,2})\s*baje\b", text)
    if m:
        hour = int(m.group(1))
        if 1 <= hour <= 7:
            hour += 12
        if 0 <= hour <= 23:
            return time(hour, 0), m.group(0)
    for word, value in (("noon", time(12, 0)), ("midnight", time(23, 59))):
        if re.search(rf"\b{word}\b", text):
            return value, word
    return None, ""


def _apply_meridiem(hour: int, meridiem: str | None) -> int | None:
    if meridiem is None:
        return hour if 0 <= hour <= 23 else None
    if not 1 <= hour <= 12:
        return None
    if meridiem == "am":
        return 0 if hour == 12 else hour
    return 12 if hour == 12 else hour + 12


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _roll_year(today: date, month: int, day: int, year: int | None) -> date | None:
    if year is not None:
        if year < 100:
            year += 2000
        return _safe_date(year, month, day)
    candidate = _safe_date(today.year, month, day)
    if candidate is not None and candidate < today:
        candidate = _safe_date(today.year + 1, month, day)
    return candidate


def _next_weekday(today: date, weekday: int) -> date:
    return today + timedelta(days=(weekday - today.weekday()) % 7)


def _find_date(text: str, today: date) -> tuple[date | None, str, str]:
    """Return (date, rule, matched span). Rules are tried from most to least specific."""
    m = re.search(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", text)
    if m:
        d = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            return d, "iso_date", m.group(0)

    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\b", text)
    if m:
        year = int(m.group(3)) if m.group(3) else None
        d = _roll_year(today, int(m.group(2)), int(m.group(1)), year)
        if d:
            return d, "numeric_date_day_first", m.group(0)

    m = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MONTH_RE}\b", text)
    if m:
        d = _roll_year(today, MONTHS[m.group(2)], int(m.group(1)), None)
        if d:
            return d, "day_month", m.group(0)

    m = re.search(rf"\b{_MONTH_RE}\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", text)
    if m:
        d = _roll_year(today, MONTHS[m.group(1)], int(m.group(2)), None)
        if d:
            return d, "month_day", m.group(0)

    m = re.search(r"\b(day after tomorrow|parso|parson|parsoon)\b", text)
    if m:
        return today + timedelta(days=2), "day_after_tomorrow", m.group(0)

    m = re.search(r"\b(tomorrow|tmrw|tmr|tomorow|kal)\b", text)
    if m:
        return today + timedelta(days=1), "tomorrow", m.group(0)

    m = re.search(r"\b(today|tonight|aaj|eod|end of (?:the )?day)\b", text)
    if m:
        return today, "today", m.group(0)

    m = re.search(r"\b(end of (?:the |this )?week|eow|weekend|this week|is week)\b", text)
    if m:
        return _next_weekday(today, 6), "end_of_week", m.group(0)

    m = re.search(r"\b(end of (?:the |this )?month|eom|month end)\b", text)
    if m:
        last = calendar.monthrange(today.year, today.month)[1]
        return today.replace(day=last), "end_of_month", m.group(0)

    m = re.search(r"\b(next week|agle hafte|agle week|agla week)\b", text)
    if m:
        next_monday = today + timedelta(days=7 - today.weekday())
        return next_monday + timedelta(days=6), "next_week", m.group(0)

    m = re.search(rf"\bnext\s+({_WEEKDAY_RE})\b", text)
    if m:
        next_monday = today + timedelta(days=7 - today.weekday())
        return next_monday + timedelta(days=WEEKDAYS[m.group(1)]), "next_weekday", m.group(0)

    m = re.search(rf"\b({_WEEKDAY_RE})\b", text)
    if m:
        return _next_weekday(today, WEEKDAYS[m.group(1)]), "weekday", m.group(0)

    m = re.search(r"\b(\d{1,2})(st|nd|rd|th)\b", text)
    if m:
        day = int(m.group(1))
        d = _safe_date(today.year, today.month, day)
        if d is not None and d < today:
            if today.month == 12:
                year, month = today.year + 1, 1
            else:
                year, month = today.year, today.month + 1
            d = _safe_date(year, month, day)
        if d:
            return d, "ordinal_day", m.group(0)

    return None, "", ""


def _find_relative(text: str, now: datetime) -> ResolvedDeadline | None:
    m = re.search(rf"\b(?:in|within)\s+(?:the\s+next\s+)?{_NUM_RE}\s*{_UNIT_RE}\b", text)
    if not m:
        m = re.search(rf"\b{_NUM_RE}\s*{_UNIT_RE}\s+(?:mein|me|main|mai)\b", text)
    if not m:
        return None
    n, unit = _num(m.group(1)), m.group(2)
    if unit.startswith(("min",)):
        return ResolvedDeadline(now + timedelta(minutes=n), True, "in_n_minutes", m.group(0))
    if unit.startswith(("hour", "hr", "ghant")):
        return ResolvedDeadline(now + timedelta(hours=n), True, "in_n_hours", m.group(0))
    days = n * 7 if unit.startswith(("week", "haft")) else n
    return ResolvedDeadline(now + timedelta(days=days), False, "in_n_days", m.group(0))


def resolve_deadline(
    text: str | None,
    now: datetime,
    tz: ZoneInfo,
    *,
    default_hour: int = 23,
    default_minute: int = 59,
) -> ResolvedDeadline | None:
    """Resolve a deadline mentioned anywhere in `text`, relative to `now`.

    Returns None when the text names no deadline. Never guesses.
    """
    if not text:
        return None
    norm = _normalize(text)
    local_now = now.astimezone(tz)
    today = local_now.date()

    relative = _find_relative(norm, local_now)
    clock, time_span = _find_time(norm)
    without_time = norm.replace(time_span, " ") if time_span else norm
    day, rule, day_span = _find_date(without_time, today)

    if relative is not None and day is None:
        if relative.has_time or clock is None:
            return relative
        d = relative.at.date()
        return ResolvedDeadline(
            datetime.combine(d, clock, tzinfo=tz), True, relative.rule, relative.matched
        )

    if day is None and clock is None:
        return None

    if day is None:
        assert clock is not None
        at = datetime.combine(today, clock, tzinfo=tz)
        if at <= local_now:
            at += timedelta(days=1)
        return ResolvedDeadline(at, True, "time_only", time_span)

    if clock is not None:
        return ResolvedDeadline(
            datetime.combine(day, clock, tzinfo=tz), True, rule, f"{day_span} {time_span}".strip()
        )
    if "tonight" in day_span or re.search(r"\baaj raat\b", norm):
        return ResolvedDeadline(datetime.combine(day, time(22, 0), tzinfo=tz), True, rule, day_span)
    end_of_day = time(default_hour, default_minute)
    return ResolvedDeadline(datetime.combine(day, end_of_day, tzinfo=tz), False, rule, day_span)
