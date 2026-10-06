"""The selectable-field registry and needs_xml(selection).

Fields, sources and order in docs/CONFIG.md § Selectable invoice fields.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class Source(StrEnum):
    """Where a field's value comes from: the metadata record, or the invoice XML."""

    METADATA = "metadata"
    XML = "xml"


@dataclass(frozen=True)
class Field:
    key: str
    source: Source


#: Every selectable field, in the fixed order a message shows them.
FIELDS: tuple[Field, ...] = (
    Field("seller_name", Source.METADATA),
    Field("seller_nip", Source.METADATA),
    Field("invoice_number", Source.METADATA),
    Field("gross_amount", Source.METADATA),
    Field("net_amount", Source.METADATA),
    Field("vat_amount", Source.METADATA),
    Field("issue_date", Source.METADATA),
    Field("due_date", Source.XML),
    Field("payment_form", Source.XML),
    Field("bank_account", Source.XML),
    Field("line_items", Source.XML),
    Field("invoice_type", Source.METADATA),
    Field("ksef_number", Source.METADATA),
)

FIELD_KEYS: tuple[str, ...] = tuple(field.key for field in FIELDS)
XML_FIELD_KEYS: frozenset[str] = frozenset(
    field.key for field in FIELDS if field.source is Source.XML
)

#: `formCode.systemCode` values whose XML has the paths the parser reads. PEF and FA_RR
#: invoices are never downloaded.
XML_FORM_CODES: frozenset[str] = frozenset({"FA (2)", "FA (3)"})


def ordered(selection: Iterable[str]) -> tuple[str, ...]:
    """The selection in message order, without repeats; unknown keys are dropped."""
    chosen = set(selection)
    return tuple(key for key in FIELD_KEYS if key in chosen)


def needs_xml(selection: Iterable[str]) -> bool:
    """Whether any selected field is read from the invoice XML."""
    return not XML_FIELD_KEYS.isdisjoint(selection)
