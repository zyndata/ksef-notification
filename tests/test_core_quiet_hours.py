"""Quiet hours: which moments are quiet and when a postponed check runs (core/quiet_hours.py)."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.ksef_notification.core.quiet_hours import QuietHours, parse

WARSAW = ZoneInfo("Europe/Warsaw")
DAY = QuietHours(time(9, 0), time(17, 0))
NIGHT = QuietHours(time(22, 0), time(6, 0))


def _local(*args: int, fold: int = 0) -> datetime:
    return datetime(*args, tzinfo=WARSAW, fold=fold)  # type: ignore[misc]


@pytest.mark.parametrize(
    ("window", "wall", "quiet"),
    [
        (DAY, (8, 59), False),
        (DAY, (9, 0), True),
        (DAY, (16, 59), True),
        (DAY, (17, 0), False),
        (NIGHT, (21, 59), False),
        (NIGHT, (22, 0), True),
        (NIGHT, (0, 0), True),
        (NIGHT, (5, 59), True),
        (NIGHT, (6, 0), False),
        (NIGHT, (12, 0), False),
    ],
)
def test_start_is_quiet_and_end_is_not(
    window: QuietHours, wall: tuple[int, int], quiet: bool
) -> None:
    assert window.contains(_local(2026, 10, 6, *wall), WARSAW) is quiet


def test_a_moment_outside_is_not_moved() -> None:
    moment = _local(2026, 10, 6, 21, 59)
    assert NIGHT.postpone(moment, WARSAW) is moment


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (_local(2026, 10, 6, 22, 0), _local(2026, 10, 7, 6, 0)),
        (_local(2026, 10, 6, 23, 30), _local(2026, 10, 7, 6, 0)),
        (_local(2026, 10, 7, 3, 0), _local(2026, 10, 7, 6, 0)),
    ],
)
def test_a_night_window_ends_the_next_morning(moment: datetime, expected: datetime) -> None:
    assert NIGHT.postpone(moment, WARSAW) == expected


def test_a_day_window_ends_the_same_day() -> None:
    assert DAY.postpone(_local(2026, 10, 6, 12, 0), WARSAW) == _local(2026, 10, 6, 17, 0)


def test_times_are_compared_in_the_given_zone_not_in_utc() -> None:
    late_evening = datetime(2026, 10, 6, 21, 0, tzinfo=UTC)  # 23:00 in Warsaw (CEST)

    assert NIGHT.contains(late_evening, WARSAW)
    assert not NIGHT.contains(late_evening, UTC)
    assert NIGHT.postpone(late_evening, WARSAW) == datetime(2026, 10, 7, 4, 0, tzinfo=UTC)


def test_a_night_across_the_autumn_change_ends_at_wall_clock_six() -> None:
    """25 Oct 2026: 03:00 CEST becomes 02:00 CET; the night is an hour longer."""
    moment = _local(2026, 10, 24, 23, 0)  # CEST
    assert NIGHT.postpone(moment, WARSAW) == datetime(2026, 10, 25, 5, 0, tzinfo=UTC)


def test_an_end_that_does_not_exist_in_spring_still_lies_ahead() -> None:
    """28 Mar 2027: 02:00 CET becomes 03:00 CEST, so 02:30 never shows on a clock."""
    window = QuietHours(time(1, 0), time(2, 30))
    moment = _local(2027, 3, 28, 1, 30)

    end = window.postpone(moment, WARSAW)

    assert end > moment
    assert end == datetime(2027, 3, 28, 1, 30, tzinfo=UTC)  # 03:30 CEST


def test_an_end_in_the_repeated_autumn_hour_is_the_one_still_ahead() -> None:
    window = QuietHours(time(1, 0), time(2, 30))
    first_pass = _local(2026, 10, 25, 2, 15)  # CEST, 00:15 UTC
    second_pass = _local(2026, 10, 25, 2, 15, fold=1)  # CET, 01:15 UTC

    assert window.postpone(first_pass, WARSAW) == datetime(2026, 10, 25, 0, 30, tzinfo=UTC)
    assert window.postpone(second_pass, WARSAW) == datetime(2026, 10, 25, 1, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    "window",
    [NIGHT, DAY, QuietHours(time(1, 0), time(2, 30)), QuietHours(time(2, 30), time(1, 0))],
    ids=["night", "day", "inside-change-hour", "almost-all-day"],
)
@pytest.mark.parametrize(
    "week", [datetime(2026, 10, 21, tzinfo=UTC), datetime(2027, 3, 24, tzinfo=UTC)]
)
def test_a_postponed_moment_is_never_quiet_and_never_earlier(
    window: QuietHours, week: datetime
) -> None:
    """Every 5 minutes of the weeks around both clock changes."""
    for step in range(7 * 24 * 12):
        moment = week + timedelta(minutes=5 * step)
        postponed = window.postpone(moment, WARSAW)
        assert postponed >= moment
        assert not window.contains(postponed, WARSAW)
        assert (postponed == moment) is not window.contains(moment, WARSAW)
        assert postponed - moment < timedelta(hours=25)


def test_parse_reads_hours_and_minutes() -> None:
    assert parse("22:00", "06:30") == QuietHours(time(22, 0), time(6, 30))
    assert parse("22:00:00", "06:30:00") == QuietHours(time(22, 0), time(6, 30))


def test_parse_refuses_an_empty_window() -> None:
    assert parse("22:00", "22:00") is None
