from datetime import UTC, datetime, timedelta

from tc.evaluation import Expected, Metrics, score_example
from tc.extraction.schema import Candidate
from tc.scoring import TrackedItem, delivery_report

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def test_delivery_report_counts() -> None:
    d = NOW - timedelta(days=2)
    items = [
        TrackedItem("Priya", "done", d, d - timedelta(hours=1)),  # on time
        TrackedItem("Priya", "done", d, d + timedelta(hours=1)),  # late
        TrackedItem("Rahul", "confirmed", d, None),  # overdue
        TrackedItem("Rahul", "confirmed", NOW + timedelta(days=1), None),  # on track
        TrackedItem(None, "done", None, NOW),  # unassigned, no deadline
    ]
    stats = {s.name: s for s in delivery_report(items, NOW)}
    assert stats["Priya"].done_on_time == 1 and stats["Priya"].done_late == 1
    assert stats["Priya"].reliability == 0.5
    assert stats["Rahul"].overdue_open == 1 and stats["Rahul"].open_on_track == 1
    assert stats["Rahul"].reliability == 0.0
    assert stats["Unassigned"].done_no_deadline == 1
    assert stats["Unassigned"].reliability is None


def cand(owner: str, task: str, deadline: datetime | None = None, kind: str = "commitment"):
    return Candidate(task=task, owner=owner, kind=kind, evidence_quote=task,
                     deadline_phrase=None, deadline_at=deadline, deadline_rule=None,
                     confidence=1.0)


def test_eval_scoring() -> None:
    m = Metrics()
    expected = [Expected("Priya", "commitment", "2026-11-13", ["slides"])]
    friday = datetime(2026, 11, 13, 18, 29, tzinfo=UTC)  # 23:59 IST
    problems = score_example([cand("Priya", "Make slides", friday), cand("Aman", "Buy chai")],
                             expected, m)
    assert (m.tp, m.fp, m.fn) == (1, 1, 0)
    assert m.deadline_correct == 1 and m.kind_correct == 1
    assert any("false positive" in p for p in problems)


def test_eval_negative_false_alarm() -> None:
    m = Metrics()
    score_example([cand("Akshu", "Be late")], [], m)
    assert m.negatives == 1 and m.negatives_with_false_alarm == 1
