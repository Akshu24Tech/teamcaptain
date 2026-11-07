"""Delivery report: who promised what, and who delivered on time.

Deliberately simple and explainable (see the ethics section of the report):
- Only commitments a human CONFIRMED count. Unconfirmed extractions never affect anyone.
- Raw counts are always shown; the reliability rate is secondary.
- It measures reliability (keeping promises), NOT how much someone contributed:
  one big task and one small task count the same, and that limitation is stated openly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from tc.db import CommitmentStatus, as_aware


@dataclass
class TrackedItem:
    owner: str | None
    status: str
    deadline_at: datetime | None
    completed_at: datetime | None


@dataclass
class MemberStats:
    name: str
    tracked: int = 0
    done_on_time: int = 0
    done_late: int = 0
    done_no_deadline: int = 0
    overdue_open: int = 0
    open_on_track: int = 0
    tasks: list[str] = field(default_factory=list)

    @property
    def reliability(self) -> float | None:
        """Share of commitments with a deadline that were kept on time. None if no data."""
        judged = self.done_on_time + self.done_late + self.overdue_open
        return round(self.done_on_time / judged, 2) if judged else None


def delivery_report(items: list[TrackedItem], now: datetime) -> list[MemberStats]:
    stats: dict[str, MemberStats] = {}
    for item in items:
        name = item.owner or "Unassigned"
        s = stats.setdefault(name, MemberStats(name=name))
        s.tracked += 1
        deadline = as_aware(item.deadline_at)
        completed = as_aware(item.completed_at)
        if item.status == CommitmentStatus.DONE:
            if deadline is None:
                s.done_no_deadline += 1
            elif completed is not None and completed <= deadline:
                s.done_on_time += 1
            else:
                s.done_late += 1
        elif deadline is not None and deadline < now:
            s.overdue_open += 1
        else:
            s.open_on_track += 1
    return sorted(stats.values(), key=lambda s: (-(s.done_on_time + s.done_no_deadline), s.name))
