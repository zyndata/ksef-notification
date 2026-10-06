"""The Invoice model, built from a metadata record and optionally the XML details."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from .fields import XML_FORM_CODES

#: `invoiceType` values of corrections; they get their own notification title.
CORRECTION_TYPES: frozenset[str] = frozenset({"Kor", "KorZal", "KorRoz", "KorPef", "KorVatRr"})


class DetailsStatus(StrEnum):
    """Whether the XML was read — the event's `details` value."""

    FETCHED = "fetched"
    NOT_NEEDED = "not_needed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class InvoiceDetails:
    """What the XML parser extracts. Every value may be missing."""

    seller_name: str | None = None
    #: Every `TerminPlatnosci/Termin`, in document order.
    due_dates: tuple[date, ...] = ()
    #: The first `TerminOpis`, used only when there is no `Termin`.
    due_description: str | None = None
    #: `FormaPlatnosci`, a code "1" to "7".
    payment_form: str | None = None
    #: `OpisPlatnosci` when `PlatnoscInna = 1`.
    payment_description: str | None = None
    bank_accounts: tuple[str, ...] = ()
    #: `FaWiersz` rows, correction "before" rows (`StanPrzed = 1`) not counted.
    line_item_count: int = 0
    #: `P_7` of the first counted rows that have one, at most LINE_ITEMS_SHOWN.
    line_item_descriptions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Invoice:
    """One cost invoice. Only the KSeF number and storage date are guaranteed."""

    ksef_number: str
    storage_date: datetime
    acquisition_date: datetime | None = None
    form_code: str | None = None
    seller_name: str | None = None
    seller_nip: str | None = None
    invoice_number: str | None = None
    gross_amount: Decimal | None = None
    net_amount: Decimal | None = None
    #: Always in PLN, whatever `currency` says.
    vat_amount: Decimal | None = None
    currency: str | None = None
    issue_date: date | None = None
    invoice_type: str | None = None
    details: InvoiceDetails | None = None
    details_status: DetailsStatus = DetailsStatus.NOT_NEEDED

    @classmethod
    def from_metadata(cls, record: Mapping[str, Any]) -> Invoice:
        """Build from one `InvoiceMetadata` record; a missing or mistyped value becomes None.

        Raises ValueError only without a KSeF number or a valid storage date — the client
        already refuses such records.
        """
        ksef_number = record.get("ksefNumber")
        storage_date = _timestamp(record.get("permanentStorageDate"))
        if not isinstance(ksef_number, str) or not ksef_number or storage_date is None:
            raise ValueError("metadata record without ksefNumber or permanentStorageDate")
        seller = record.get("seller")
        seller = seller if isinstance(seller, Mapping) else {}
        form_code = record.get("formCode")
        form_code = form_code if isinstance(form_code, Mapping) else {}
        return cls(
            ksef_number=ksef_number,
            storage_date=storage_date,
            acquisition_date=_timestamp(record.get("acquisitionDate")),
            form_code=_text(form_code.get("systemCode")),
            seller_name=_text(seller.get("name")),
            seller_nip=_text(seller.get("nip")),
            invoice_number=_text(record.get("invoiceNumber")),
            gross_amount=_amount(record.get("grossAmount")),
            net_amount=_amount(record.get("netAmount")),
            vat_amount=_amount(record.get("vatAmount")),
            currency=_text(record.get("currency")),
            issue_date=_date(record.get("issueDate")),
            invoice_type=_text(record.get("invoiceType")),
        )

    @property
    def is_correction(self) -> bool:
        return self.invoice_type in CORRECTION_TYPES

    @property
    def xml_supported(self) -> bool:
        """Only FA(2)/FA(3) XML has the paths the parser reads; nothing else is downloaded."""
        return self.form_code in XML_FORM_CODES

    def with_details(self, details: InvoiceDetails) -> Invoice:
        return replace(self, details=details, details_status=DetailsStatus.FETCHED)

    def without_details(self) -> Invoice:
        """The XML was needed but not obtained: its fields show as absent."""
        return replace(self, details=None, details_status=DetailsStatus.UNAVAILABLE)


def _text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _amount(value: object) -> Decimal | None:
    # bool is an int; a JSON `true` is not an amount.
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    # Through str, so 1230.1 stays 1230.1 and does not become its binary approximation.
    return Decimal(str(value))


def _date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _timestamp(value: object) -> datetime | None:
    """A KSeF date-time (up to 7 fractional digits, with an offset) as aware UTC."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)
