"""New-invoice detection: TrackerState, plan_cycle, record_notified, finish_cycle (phase 4).

Algorithm in docs/ARCHITECTURE.md § New-invoice detection.

One cycle, as the coordinator drives it:

    plan = plan_cycle(state, now)
    result = await client.query_metadata(plan.date_from)
    invoices = [Invoice.from_metadata(record) for record in result.invoices]
    if plan.baseline:
        state = finish_baseline(invoices, result.hwm, now)
    else:
        for invoice in select_new(state, invoices):
            ...                                     # notify, or:
            state = record_deferred(state, invoice)  # only while may_defer() says so
            state = record_handled(state, invoice)   # then save
        state, dropped = finish_cycle(state, invoices, result.hwm, result.complete, now)

Every function returns a new state and leaves its argument unchanged.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any

from ..const import BASELINE_WINDOW, HWM_FALLBACK, MAX_CATCH_UP, MAX_DEFERRALS, OVERLAP, SEEN_MAX
from .model import Invoice

_HASH_HEX = 16


def ksef_hash(ksef_number: str) -> str:
    """First 16 hex characters of SHA-256: enough to recognise an invoice, nothing more."""
    return hashlib.sha256(ksef_number.encode()).hexdigest()[:_HASH_HEX]


@dataclass(frozen=True)
class TrackerState:
    """`cursor`: everything stored at or before it is handled; None = a baseline is due.

    `seen`: hash → storage date of handled invoices not yet safely below the next window.
    `deferrals`: hash → how often the invoice was deferred; memory only, never persisted.
    """

    cursor: datetime | None = None
    seen: Mapping[str, datetime] = field(default_factory=dict)
    deferrals: Mapping[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """The persisted form (docs/ARCHITECTURE.md § What is persisted). No deferrals."""
        return {
            "cursor": self.cursor.isoformat() if self.cursor else None,
            "seen": {key: value.isoformat() for key, value in self.seen.items()},
        }

    @classmethod
    def from_dict(cls, data: object) -> TrackerState:
        """Read the persisted form; anything unreadable gives the empty state (a baseline)."""
        if not isinstance(data, Mapping):
            return cls()
        cursor = _timestamp(data.get("cursor"))
        seen_data = data.get("seen")
        if cursor is None or not isinstance(seen_data, Mapping):
            return cls()
        seen: dict[str, datetime] = {}
        for key, value in seen_data.items():
            stored = _timestamp(value)
            if not isinstance(key, str) or len(key) != _HASH_HEX or stored is None:
                return cls()
            seen[key] = stored
        return cls(cursor=cursor, seen=seen)


@dataclass(frozen=True)
class CyclePlan:
    date_from: datetime
    #: Take note of everything, notify nothing.
    baseline: bool
    #: The baseline is due because the cursor is older than MAX_CATCH_UP (worth a warning).
    catch_up_expired: bool = False


def plan_cycle(state: TrackerState, now: datetime) -> CyclePlan:
    """Where this cycle's query starts, and whether it is a baseline."""
    if state.cursor is None:
        return CyclePlan(now - BASELINE_WINDOW, baseline=True)
    if now - state.cursor > MAX_CATCH_UP:
        return CyclePlan(now - BASELINE_WINDOW, baseline=True, catch_up_expired=True)
    return CyclePlan(state.cursor - OVERLAP, baseline=False)


def select_new(state: TrackerState, invoices: Iterable[Invoice]) -> list[Invoice]:
    """Invoices not handled yet, oldest first, each once."""
    new: dict[str, Invoice] = {}
    for invoice in invoices:
        key = ksef_hash(invoice.ksef_number)
        if key not in state.seen:
            new.setdefault(key, invoice)
    return sorted(new.values(), key=lambda invoice: invoice.storage_date)


def record_handled(state: TrackerState, invoice: Invoice) -> TrackerState:
    """Notified, or given up on: never again. Persist the result before the next push."""
    key = ksef_hash(invoice.ksef_number)
    deferrals = {k: v for k, v in state.deferrals.items() if k != key}
    return replace(state, seen={**state.seen, key: invoice.storage_date}, deferrals=deferrals)


def may_defer(state: TrackerState, invoice: Invoice) -> bool:
    """False once the invoice was deferred MAX_DEFERRALS times: notify it without the XML."""
    return state.deferrals.get(ksef_hash(invoice.ksef_number), 0) < MAX_DEFERRALS


def record_deferred(state: TrackerState, invoice: Invoice) -> TrackerState:
    """Leave the invoice for the next cycle; the cursor will not pass it."""
    key = ksef_hash(invoice.ksef_number)
    return replace(state, deferrals={**state.deferrals, key: state.deferrals.get(key, 0) + 1})


def finish_baseline(
    invoices: Iterable[Invoice], hwm: datetime | None, now: datetime
) -> TrackerState:
    """A fresh state: everything returned is history, marked seen; the cursor at the HWM.

    An incomplete baseline needs no special case: whatever was not returned is either at or
    before the HWM (history too) or after it (queried again next cycle).
    """
    seen = {ksef_hash(invoice.ksef_number): invoice.storage_date for invoice in invoices}
    cursor = hwm if hwm is not None else now - HWM_FALLBACK
    return _pruned(TrackerState(seen=seen), cursor)[0]


def finish_cycle(
    state: TrackerState,
    invoices: Sequence[Invoice],
    hwm: datetime | None,
    complete: bool,
    now: datetime,
) -> tuple[TrackerState, int]:
    """Advance the cursor as far as is safe, prune `seen`; returns (state, entries dropped).

    `invoices` is everything the query returned. The cursor never passes one not handled yet
    (deferred, or not reached because the cycle stopped early), nor the last returned record
    of an incomplete query. A non-zero second value means `seen` overflowed SEEN_MAX.
    """
    bounds = [hwm if hwm is not None else now - HWM_FALLBACK]
    unhandled = [
        invoice.storage_date
        for invoice in invoices
        if ksef_hash(invoice.ksef_number) not in state.seen
    ]
    if unhandled:
        bounds.append(min(unhandled))
    if not complete and invoices:
        bounds.append(max(invoice.storage_date for invoice in invoices))
    cursor = min(bounds)
    if state.cursor is not None:
        cursor = max(state.cursor, cursor)
    return _pruned(state, cursor)


def _pruned(state: TrackerState, cursor: datetime) -> tuple[TrackerState, int]:
    """Set the cursor, drop what the next window cannot return, cap at SEEN_MAX oldest first."""
    floor = cursor - OVERLAP
    kept = sorted(
        ((key, stored) for key, stored in state.seen.items() if stored >= floor),
        key=lambda item: item[1],
    )
    dropped = max(0, len(kept) - SEEN_MAX)
    return replace(state, cursor=cursor, seen=dict(kept[dropped:])), dropped


def _timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
