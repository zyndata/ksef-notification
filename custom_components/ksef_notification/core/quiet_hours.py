"""Quiet hours: a daily window of local wall-clock time in which no check is scheduled.

The window may cross midnight (22:00 to 06:00). Times are compared in the zone the caller
passes, so a window keeps its wall-clock meaning across daylight-saving changes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, tzinfo


@dataclass(frozen=True)
class QuietHours:
    """From `start` (inclusive) to `end` (exclusive); naive times, `start != end`."""

    start: time
    end: time

    def contains(self, moment: datetime, zone: tzinfo) -> bool:
        wall = _wall(moment.astimezone(zone))
        if self.start < self.end:
            return self.start <= wall < self.end
        return wall >= self.start or wall < self.end

    def postpone(self, when: datetime, zone: tzinfo) -> datetime:
        """`when`, or the end of the quiet window it falls in (in UTC)."""
        if not self.contains(when, zone):
            return when
        local = when.astimezone(zone)
        day = local.date()
        if self.start > self.end and _wall(local) >= self.start:
            day += timedelta(days=1)
        end = datetime.combine(day, self.end, tzinfo=zone)
        # Compared in UTC: within one zone Python compares wall-clock values and ignores
        # `fold`. An end inside the hour repeated when clocks go back exists twice; take
        # the later one if the earlier is already past.
        after = when.astimezone(UTC)
        for candidate in (end, end.replace(fold=1)):
            if (instant := candidate.astimezone(UTC)) > after:
                return instant
        return when


def parse(start: str, end: str) -> QuietHours | None:
    """Two `HH:MM[:SS]` times as a window, or None when they are equal."""
    window = QuietHours(time.fromisoformat(start), time.fromisoformat(end))
    return None if window.start == window.end else window


def _wall(local: datetime) -> time:
    return local.timetz().replace(tzinfo=None)
