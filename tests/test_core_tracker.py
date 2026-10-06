"""New-invoice detection: unit behaviour, then whole sequences of cycles and restarts.

The simulation at the end is the tracker's acceptance test: a scripted KSeF with the
high-water-mark guarantee, invoices that become visible late, incomplete queries, missing
HWMs, deferrals and restarts in the middle of a cycle — and no invoice is notified twice or
missed in any of them.
"""

from __future__ import annotations

import json
import random
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pytest

from custom_components.ksef_notification.const import (
    BASELINE_WINDOW,
    HWM_FALLBACK,
    MAX_CATCH_UP,
    MAX_DEFERRALS,
    OVERLAP,
    SEEN_MAX,
)
from custom_components.ksef_notification.core.model import Invoice
from custom_components.ksef_notification.core.tracker import (
    TrackerState,
    finish_baseline,
    finish_cycle,
    ksef_hash,
    may_defer,
    plan_cycle,
    record_deferred,
    record_handled,
    select_new,
)

from .core_factory import T0, invoice

MINUTE = timedelta(minutes=1)


def state_after(*handled: Invoice, cursor: datetime = T0) -> TrackerState:
    state = TrackerState(cursor=cursor)
    for item in handled:
        state = record_handled(state, item)
    return state


# --- hashing and persistence -------------------------------------------------------------


def test_the_hash_is_16_hex_characters_and_stable() -> None:
    first = ksef_hash("2222222222-20261006-000000000001-00")

    assert len(first) == 16
    assert int(first, 16) >= 0
    assert first == ksef_hash("2222222222-20261006-000000000001-00")
    assert first != ksef_hash("2222222222-20261006-000000000002-00")


def test_round_trip_keeps_cursor_and_seen_but_not_deferrals() -> None:
    state = record_deferred(state_after(invoice(1, MINUTE)), invoice(2))

    restored = TrackerState.from_dict(json.loads(json.dumps(state.to_dict())))

    assert restored == TrackerState(cursor=state.cursor, seen=state.seen)
    assert restored.deferrals == {}


def test_the_persisted_form_holds_no_invoice_content() -> None:
    item = invoice("3333333333-20261006-0000000000B2-02", seller_name="Fikcyjny Dostawca B")

    text = json.dumps(state_after(item).to_dict())

    assert "3333333333" not in text
    assert "Fikcyjny" not in text
    assert set(state_after(item).to_dict()) == {"cursor", "seen"}


@pytest.mark.parametrize(
    "data",
    [
        None,
        [],
        "text",
        {},
        {"cursor": None, "seen": {}},
        {"cursor": "garbage", "seen": {}},
        {"cursor": "2026-10-06T07:00:00", "seen": {}},  # naive
        {"cursor": "2026-10-06T07:00:00+00:00"},
        {"cursor": "2026-10-06T07:00:00+00:00", "seen": []},
        {"cursor": "2026-10-06T07:00:00+00:00", "seen": {"short": "2026-10-06T07:00:00+00:00"}},
        {"cursor": "2026-10-06T07:00:00+00:00", "seen": {"0123456789abcdef": "x"}},
    ],
)
def test_an_unreadable_store_means_a_baseline(data: object) -> None:
    assert TrackerState.from_dict(data) == TrackerState()
    assert plan_cycle(TrackerState.from_dict(data), T0).baseline


# --- planning ----------------------------------------------------------------------------


def test_first_run_is_a_baseline_over_the_last_two_hours() -> None:
    plan = plan_cycle(TrackerState(), T0)

    assert (plan.date_from, plan.baseline, plan.catch_up_expired) == (
        T0 - BASELINE_WINDOW,
        True,
        False,
    )


def test_a_normal_cycle_starts_one_overlap_before_the_cursor() -> None:
    plan = plan_cycle(TrackerState(cursor=T0), T0 + 15 * MINUTE)

    assert (plan.date_from, plan.baseline) == (T0 - OVERLAP, False)


def test_catch_up_up_to_ninety_days_then_a_new_baseline() -> None:
    state = TrackerState(cursor=T0)

    assert not plan_cycle(state, T0 + MAX_CATCH_UP).baseline
    late = plan_cycle(state, T0 + MAX_CATCH_UP + timedelta(seconds=1))
    assert (late.baseline, late.catch_up_expired) == (True, True)
    assert late.date_from == T0 + MAX_CATCH_UP + timedelta(seconds=1) - BASELINE_WINDOW


# --- selecting and recording -------------------------------------------------------------


def test_select_new_skips_seen_sorts_and_dedups() -> None:
    old, late, early = invoice(1), invoice(2, 5 * MINUTE), invoice(3, 2 * MINUTE)
    state = state_after(old)

    assert select_new(state, [late, old, early, late]) == [early, late]


def test_an_invoice_seen_twice_is_notified_once() -> None:
    item = invoice(1, MINUTE)
    state = TrackerState(cursor=T0)

    first = select_new(state, [item])
    state = record_handled(state, first[0])

    assert first == [item]
    assert select_new(state, [item]) == []


def test_functions_leave_their_argument_unchanged() -> None:
    state = TrackerState(cursor=T0)

    record_handled(state, invoice(1))
    record_deferred(state, invoice(1))
    finish_cycle(state, [invoice(1)], T0 + MINUTE, True, T0)

    assert state == TrackerState(cursor=T0)


def test_deferral_limit() -> None:
    item = invoice(1, MINUTE)
    state = TrackerState(cursor=T0)
    for _ in range(MAX_DEFERRALS):
        assert may_defer(state, item)
        state = record_deferred(state, item)

    assert not may_defer(state, item)
    assert may_defer(state, invoice(2)), "the count is per invoice"
    handled = record_handled(state, item)
    assert handled.deferrals == {}


# --- finishing a cycle -------------------------------------------------------------------


def test_baseline_marks_everything_and_moves_to_the_hwm() -> None:
    items = [invoice(1, -90 * MINUTE), invoice(2, -OVERLAP / 2), invoice(3, MINUTE)]
    hwm = T0

    state = finish_baseline(items, hwm, T0 + 2 * MINUTE)

    assert state.cursor == hwm
    # The first is below the next window and pruned; the others stay to filter repeats.
    assert set(state.seen) == {ksef_hash(items[1].ksef_number), ksef_hash(items[2].ksef_number)}
    assert select_new(state, items[1:]) == []


def test_baseline_without_an_hwm_uses_the_fallback() -> None:
    assert finish_baseline([], None, T0).cursor == T0 - HWM_FALLBACK


def test_the_cursor_moves_to_the_hwm_when_everything_was_handled() -> None:
    item = invoice(1, MINUTE)
    state = record_handled(TrackerState(cursor=T0), item)

    state, dropped = finish_cycle(state, [item], T0 + 5 * MINUTE, True, T0 + 7 * MINUTE)

    assert state.cursor == T0 + 5 * MINUTE
    assert dropped == 0


def test_an_empty_cycle_still_advances_and_prunes() -> None:
    state = state_after(invoice(1, -5 * MINUTE))

    state, _ = finish_cycle(state, [], T0 + 15 * MINUTE, True, T0 + 17 * MINUTE)

    assert state.cursor == T0 + 15 * MINUTE
    assert state.seen == {}


def test_a_deferred_invoice_holds_the_cursor() -> None:
    deferred, later = invoice(1, MINUTE), invoice(2, 3 * MINUTE)
    state = record_handled(record_deferred(TrackerState(cursor=T0), deferred), later)

    state, _ = finish_cycle(state, [deferred, later], T0 + 10 * MINUTE, True, T0 + 12 * MINUTE)

    assert state.cursor == T0 + MINUTE
    assert select_new(state, [deferred, later]) == [deferred]


def test_an_invoice_after_the_hwm_is_kept_in_seen() -> None:
    """Visible before the HWM passed it: handled now, returned again next cycle, filtered."""
    item = invoice(1, 10 * MINUTE)
    state = record_handled(TrackerState(cursor=T0), item)

    state, _ = finish_cycle(state, [item], T0 + 8 * MINUTE, True, T0 + 10 * MINUTE)

    assert state.cursor == T0 + 8 * MINUTE
    assert select_new(state, [item]) == []


def test_an_invoice_exactly_at_the_hwm() -> None:
    item = invoice(1, T0 + 5 * MINUTE)
    state = record_handled(TrackerState(cursor=T0), item)

    state, _ = finish_cycle(state, [item], item.storage_date, True, T0 + 7 * MINUTE)
    plan = plan_cycle(state, T0 + 20 * MINUTE)

    assert state.cursor == item.storage_date
    assert plan.date_from <= item.storage_date, "the next window still covers it"
    assert select_new(state, [item]) == [], "and seen still filters it"


def test_an_incomplete_query_stops_at_the_last_returned_record() -> None:
    items = [invoice(n, n * MINUTE) for n in range(1, 4)]
    state = state_after(*items)

    state, _ = finish_cycle(state, items, T0 + 30 * MINUTE, False, T0 + 32 * MINUTE)

    assert state.cursor == T0 + 3 * MINUTE


def test_the_cursor_never_moves_back() -> None:
    state = TrackerState(cursor=T0)

    state, _ = finish_cycle(state, [], T0 - 10 * MINUTE, True, T0)
    assert state.cursor == T0
    state, _ = finish_cycle(state, [], None, True, T0 + 30 * MINUTE)  # fallback: T0 - 30 min
    assert state.cursor == T0


def test_seen_is_capped_oldest_first() -> None:
    items = [invoice(n, timedelta(seconds=n)) for n in range(SEEN_MAX + 5)]
    state = state_after(*items)

    state, dropped = finish_cycle(state, items, T0, True, T0)

    assert dropped == 5
    assert len(state.seen) == SEEN_MAX
    assert ksef_hash(items[0].ksef_number) not in state.seen
    assert ksef_hash(items[-1].ksef_number) in state.seen


# --- whole sequences ---------------------------------------------------------------------


@dataclass
class Arrival:
    number: str
    stored: datetime
    visible: datetime


@dataclass
class FakeKsef:
    """Metadata queries with KSeF's guarantee: everything stored at or before the HWM is
    visible. Invoices become visible up to `lag` after their storage date."""

    rng: random.Random
    inclusive: bool
    page_cap: int
    hwm_missing: float
    lag: timedelta = timedelta(minutes=2)
    arrivals: list[Arrival] = field(default_factory=list)

    def add(self, stored: datetime) -> None:
        number = f"2222222222-20261006-{len(self.arrivals):012X}-00"
        visible = stored + self.lag * self.rng.random()
        self.arrivals.append(Arrival(number, stored, visible))

    def query(
        self, date_from: datetime, now: datetime
    ) -> tuple[list[Invoice], datetime | None, bool]:
        hits = sorted(
            (
                arrival
                for arrival in self.arrivals
                if arrival.visible <= now
                and (arrival.stored >= date_from if self.inclusive else arrival.stored > date_from)
            ),
            key=lambda arrival: arrival.stored,
        )
        returned = [Invoice(a.number, a.stored) for a in hits[: self.page_cap]]
        hwm = None if self.rng.random() < self.hwm_missing else now - self.lag
        return returned, hwm, len(hits) <= self.page_cap


@dataclass
class Run:
    notified: Counter[str] = field(default_factory=Counter)
    baseline: set[str] = field(default_factory=set)
    max_seen: int = 0


def simulate(seed: int, *, inclusive: bool) -> tuple[FakeKsef, Run]:
    rng = random.Random(seed)
    ksef = FakeKsef(rng, inclusive=inclusive, page_cap=8, hwm_missing=0.1)
    # A little history inside the baseline window (the last one maybe not visible yet at
    # setup), then three days of arrivals: quiet spells and bursts, never more than 5 within
    # 60 s (fewer than the page cap — see docs/ARCHITECTURE.md § Bounds).
    for minutes in (50, 20, 1):
        ksef.add(T0 - timedelta(minutes=minutes))
    moment = T0 + timedelta(minutes=rng.uniform(1, 20))
    while moment < T0 + timedelta(days=3):
        ksef.add(moment)
        burst = rng.random() < 0.1
        moment += timedelta(seconds=rng.uniform(15, 40) if burst else rng.expovariate(1 / 600))
        moment = max(moment, ksef.arrivals[-1].stored + timedelta(seconds=15))

    run = Run()
    state = TrackerState()
    now = T0
    end = T0 + timedelta(days=3, hours=2)
    while now < end:
        settling = now > T0 + timedelta(days=3)
        if not settling and rng.random() < 0.05:  # restart between cycles
            state = TrackerState.from_dict(json.loads(json.dumps(state.to_dict())))
        state = cycle(ksef, run, state, now, rng, chaos=not settling)
        run.max_seen = max(run.max_seen, len(state.seen))
        now += timedelta(minutes=15) + timedelta(seconds=rng.uniform(0, 30))
    return ksef, run


def cycle(
    ksef: FakeKsef, run: Run, state: TrackerState, now: datetime, rng: random.Random, *, chaos: bool
) -> TrackerState:
    plan = plan_cycle(state, now)
    invoices, hwm, complete = ksef.query(plan.date_from, now)
    if plan.baseline:
        run.baseline.update(item.ksef_number for item in invoices)
        return finish_baseline(invoices, hwm, now)
    new = select_new(state, invoices)
    for item in new:
        if chaos and rng.random() < 0.03:
            # Restart mid-cycle: the store holds what was saved after the last push.
            return TrackerState.from_dict(json.loads(json.dumps(state.to_dict())))
        if chaos and rng.random() < 0.2 and may_defer(state, item):
            state = record_deferred(state, item)
            continue
        run.notified[item.ksef_number] += 1
        state = record_handled(state, item)
    state, dropped = finish_cycle(state, invoices, hwm, complete, now)
    assert dropped == 0
    return state


@pytest.mark.parametrize("inclusive", [True, False], ids=["from-inclusive", "from-exclusive"])
@pytest.mark.parametrize("seed", range(12))
def test_no_invoice_is_notified_twice_or_missed(seed: int, inclusive: bool) -> None:
    ksef, run = simulate(seed, inclusive=inclusive)

    repeated = {number: count for number, count in run.notified.items() if count > 1}
    expected = {arrival.number for arrival in ksef.arrivals} - run.baseline
    assert not repeated
    assert set(run.notified) == expected
    assert run.baseline, "the first cycle took note of history"
    assert all(a.number in run.baseline for a in ksef.arrivals if a.stored <= T0 - ksef.lag)
    assert len(expected) > 300
    # A handful normally; bounded by what lands in one cycle plus the HWM lag and overlap.
    assert run.max_seen < 40


def test_a_restart_with_the_switch_on_catches_up_instead_of_re_baselining() -> None:
    """Invoices that arrived while Home Assistant was down are notified."""
    state = finish_baseline([], T0, T0 + 2 * MINUTE)
    restored = TrackerState.from_dict(state.to_dict())
    later = [invoice(n, timedelta(hours=n)) for n in range(1, 6)]

    plan = plan_cycle(restored, T0 + timedelta(hours=8))

    assert not plan.baseline
    assert select_new(restored, later) == later


def test_state_stays_small_over_a_week_of_cycles() -> None:
    state = finish_baseline([], T0, T0)
    now = T0
    for n in range(7 * 24 * 4):
        now += timedelta(minutes=15)
        items = [invoice(n * 2 + k, now - timedelta(minutes=3 + k)) for k in range(2)]
        for item in select_new(state, items):
            state = record_handled(state, item)
        state, _ = finish_cycle(state, items, now - 2 * MINUTE, True, now)
        assert len(state.seen) <= 4
    assert len(json.dumps(state.to_dict())) < 1024
