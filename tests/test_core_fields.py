"""The field registry: order, sources, and when the XML is needed."""

from __future__ import annotations

import pytest

from custom_components.ksef_notification.const import DEFAULT_FIELDS
from custom_components.ksef_notification.core.fields import (
    FIELD_KEYS,
    XML_FIELD_KEYS,
    needs_xml,
    ordered,
)


def test_the_thirteen_fields_in_the_documented_order() -> None:
    """docs/CONFIG.md § Selectable invoice fields — the order is the message order."""
    assert FIELD_KEYS == (
        "seller_name",
        "seller_nip",
        "invoice_number",
        "gross_amount",
        "net_amount",
        "vat_amount",
        "issue_date",
        "due_date",
        "payment_form",
        "bank_account",
        "line_items",
        "invoice_type",
        "ksef_number",
    )


def test_exactly_four_fields_come_from_the_xml() -> None:
    assert {"due_date", "payment_form", "bank_account", "line_items"} == XML_FIELD_KEYS


def test_the_default_selection_is_valid_and_in_order() -> None:
    assert ordered(DEFAULT_FIELDS) == DEFAULT_FIELDS


@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        (DEFAULT_FIELDS, True),  # due_date
        (("seller_name", "gross_amount", "invoice_type", "ksef_number"), False),
        ((), False),
        (("line_items",), True),
        (iter(["bank_account"]), True),
    ],
)
def test_needs_xml(selection: tuple[str, ...], expected: bool) -> None:
    assert needs_xml(selection) is expected


def test_ordered_sorts_dedups_and_drops_unknown_keys() -> None:
    assert ordered(["ksef_number", "due_date", "nonsense", "seller_name", "due_date"]) == (
        "seller_name",
        "due_date",
        "ksef_number",
    )
