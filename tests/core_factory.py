"""Building core values for the core tests. Every value is invented."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from custom_components.ksef_notification.core.model import Invoice

FIXTURES = Path(__file__).parent / "fixtures"
T0 = datetime(2026, 10, 6, 7, 0, tzinfo=UTC)


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def metadata_records() -> list[dict[str, Any]]:
    return json.loads((FIXTURES / "metadata_page.json").read_text(encoding="utf-8"))["invoices"]


def invoice(number: int | str = 1, stored: datetime | timedelta = T0, **fields: Any) -> Invoice:
    """An invoice with the KSeF number `number` and storage date `stored` (or T0 + offset)."""
    if isinstance(stored, timedelta):
        stored = T0 + stored
    ksef_number = number if isinstance(number, str) else f"2222222222-20261006-{number:012X}-00"
    return Invoice(ksef_number=ksef_number, storage_date=stored, **fields)


def full_invoice(**fields: Any) -> Invoice:
    """Every metadata field present."""
    values: dict[str, Any] = {
        "acquisition_date": T0,
        "form_code": "FA (3)",
        "seller_name": "Fikcyjny Dostawca B",
        "seller_nip": "3333333333",
        "invoice_number": "FV/2026/10/0042",
        "gross_amount": Decimal("1234.56"),
        "net_amount": Decimal("1003.71"),
        "vat_amount": Decimal("230.85"),
        "currency": "PLN",
        "issue_date": date(2026, 10, 5),
        "invoice_type": "Vat",
    }
    values.update(fields)
    return invoice(**values)
