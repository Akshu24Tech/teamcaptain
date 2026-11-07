from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tc.extraction.dates import resolve_deadline

TZ = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 9, 10, 0, tzinfo=TZ)  # Monday 10:00


def resolve(text: str):
    return resolve_deadline(text, NOW, TZ)


@pytest.mark.parametrize(
    ("text", "expected", "rule"),
    [
        ("by friday", "2026-11-13 23:59", "weekday"),
        ("monday", "2026-11-09 23:59", "weekday"),  # today counts
        ("next friday", "2026-11-20 23:59", "next_weekday"),
        ("kal tak", "2026-11-10 23:59", "tomorrow"),
        ("tomorrow 5pm", "2026-11-10 17:00", "tomorrow"),
        ("parso", "2026-11-11 23:59", "day_after_tomorrow"),
        ("day after tomorrow", "2026-11-11 23:59", "day_after_tomorrow"),
        ("by eod", "2026-11-09 23:59", "today"),
        ("tonight", "2026-11-09 22:00", "today"),
        ("end of week", "2026-11-15 23:59", "end_of_week"),
        ("this week", "2026-11-15 23:59", "end_of_week"),
        ("next week", "2026-11-22 23:59", "next_week"),
        ("end of month", "2026-11-30 23:59", "end_of_month"),
        ("15 nov", "2026-11-15 23:59", "day_month"),
        ("november 20th", "2026-11-20 23:59", "month_day"),
        ("by 25/11", "2026-11-25 23:59", "numeric_date_day_first"),
        ("2026-12-01", "2026-12-01 23:59", "iso_date"),
        ("by 20th", "2026-11-20 23:59", "ordinal_day"),
        ("thursday 5pm tak", "2026-11-12 17:00", "weekday"),
        ("friday 5.30pm", "2026-11-13 17:30", "weekday"),
        ("shukravar", "2026-11-13 23:59", "weekday"),
        ("5 baje", "2026-11-09 17:00", "time_only"),
    ],
)
def test_resolves(text: str, expected: str, rule: str) -> None:
    got = resolve(text)
    assert got is not None, text
    assert got.at.strftime("%Y-%m-%d %H:%M") == expected
    assert got.rule == rule


def test_past_day_month_rolls_to_next_year() -> None:
    got = resolve("1 jan")
    assert got is not None and got.at.year == 2027


def test_past_ordinal_rolls_to_next_month() -> None:
    got = resolve("by 5th")
    assert got is not None and got.at.strftime("%Y-%m-%d") == "2026-12-05"


def test_time_already_passed_today_means_tomorrow() -> None:
    got = resolve("9am")
    assert got is not None and got.at.strftime("%Y-%m-%d %H:%M") == "2026-11-10 09:00"


def test_relative_hours_and_days() -> None:
    hours = resolve("in 2 hours")
    days = resolve("3 din mein")
    assert hours is not None and hours.at == datetime(2026, 11, 9, 12, 0, tzinfo=TZ)
    assert hours.has_time
    assert days is not None and days.at.date().isoformat() == "2026-11-12"
    minutes = resolve("in 3 minutes")
    assert minutes is not None and minutes.at == datetime(2026, 11, 9, 10, 3, tzinfo=TZ)


@pytest.mark.parametrize("text", ["", "I'll do the slides", "maybe later", "lol", "31/02"])
def test_no_deadline_returns_none(text: str) -> None:
    assert resolve(text) is None


def test_result_is_timezone_aware() -> None:
    got = resolve("friday")
    assert got is not None and got.at.tzinfo is not None
