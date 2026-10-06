"""The Invoice model: typed, every selectable field optional, built from a metadata record."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest

from custom_components.ksef_notification.core.model import (
    DetailsStatus,
    Invoice,
    InvoiceDetails,
)

from .core_factory import invoice, metadata_records


def test_a_complete_record() -> None:
    record = metadata_records()[0]

    built = Invoice.from_metadata(record)

    assert built == Invoice(
        ksef_number="3333333333-20261006-0000000000B2-02",
        storage_date=datetime(2026, 10, 6, 7, 41, 12, 512345, tzinfo=UTC),
        acquisition_date=datetime(2026, 10, 6, 7, 40, 2, 200000, tzinfo=UTC),
        form_code="FA (3)",
        seller_name="Fikcyjny Dostawca B",
        seller_nip="3333333333",
        invoice_number="FV/2026/10/0042",
        gross_amount=Decimal("123.0"),
        net_amount=Decimal("100.0"),
        vat_amount=Decimal("23.0"),
        currency="PLN",
        issue_date=date(2026, 10, 5),
        invoice_type="Vat",
    )
    assert built.details is None
    assert built.details_status is DetailsStatus.NOT_NEEDED
    assert not built.is_correction
    assert built.xml_supported


def test_a_correction_in_a_foreign_currency_without_a_seller_name() -> None:
    built = Invoice.from_metadata(metadata_records()[1])

    assert built.seller_name is None
    assert built.seller_nip == "2222222222"
    assert built.currency == "EUR"
    assert built.vat_amount == Decimal("980.5")
    assert built.is_correction
    assert built.form_code == "FA (2)"
    assert built.xml_supported


@pytest.mark.parametrize("missing", ["ksefNumber", "permanentStorageDate"])
def test_only_the_number_and_storage_date_are_required(missing: str) -> None:
    record = metadata_records()[0]
    del record[missing]

    with pytest.raises(ValueError, match="ksefNumber or permanentStorageDate"):
        Invoice.from_metadata(record)


def test_a_storage_date_without_an_offset_is_refused() -> None:
    record = metadata_records()[0]
    record["permanentStorageDate"] = "2026-10-06T07:41:12"

    with pytest.raises(ValueError, match="ksefNumber or permanentStorageDate"):
        Invoice.from_metadata(record)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("grossAmount", "123.00"),
        ("grossAmount", True),
        ("grossAmount", float("nan")),
        ("grossAmount", float("inf")),
        ("grossAmount", None),
        ("issueDate", "05.10.2026"),
        ("issueDate", 20261005),
        ("invoiceNumber", ""),
        ("invoiceNumber", "   "),
        ("invoiceNumber", 42),
        ("seller", "Fikcyjny Dostawca B"),
        ("formCode", "FA (3)"),
        ("acquisitionDate", "yesterday"),
    ],
)
def test_a_missing_or_mistyped_value_becomes_none(key: str, value: Any) -> None:
    record = metadata_records()[0]
    record[key] = value

    built = Invoice.from_metadata(record)

    attribute = {
        "grossAmount": "gross_amount",
        "issueDate": "issue_date",
        "invoiceNumber": "invoice_number",
        "seller": "seller_name",
        "formCode": "form_code",
        "acquisitionDate": "acquisition_date",
    }[key]
    assert getattr(built, attribute) is None


def test_an_empty_record_apart_from_the_two_required_keys() -> None:
    built = Invoice.from_metadata(
        {"ksefNumber": "X-1", "permanentStorageDate": "2026-10-06T07:00:00+02:00"}
    )

    assert built == Invoice("X-1", datetime(2026, 10, 6, 5, 0, tzinfo=UTC))
    assert not built.xml_supported


def test_amounts_keep_their_decimal_value() -> None:
    record = metadata_records()[0]
    record["grossAmount"] = 1230.1
    record["netAmount"] = 1000

    built = Invoice.from_metadata(record)

    assert built.gross_amount == Decimal("1230.1")
    assert built.net_amount == Decimal(1000)


def test_seven_fractional_digits_and_other_offsets_end_in_utc() -> None:
    record = metadata_records()[0]
    record["permanentStorageDate"] = "2026-10-06T09:41:12.5123456+02:00"

    assert Invoice.from_metadata(record).storage_date == datetime(
        2026, 10, 6, 7, 41, 12, 512345, tzinfo=UTC
    )


@pytest.mark.parametrize(
    ("form_code", "supported"),
    [("FA (3)", True), ("FA (2)", True), ("PEF (3)", False), ("FA_RR (1)", False), (None, False)],
)
def test_only_fa2_and_fa3_xml_is_read(form_code: str | None, supported: bool) -> None:
    assert invoice(form_code=form_code).xml_supported is supported


@pytest.mark.parametrize("invoice_type", ["Kor", "KorZal", "KorRoz", "KorPef", "KorVatRr"])
def test_corrections(invoice_type: str) -> None:
    assert invoice(invoice_type=invoice_type).is_correction


def test_details_status_follows_what_happened_to_the_xml() -> None:
    details = InvoiceDetails(payment_form="6")
    base = invoice()

    fetched = base.with_details(details)
    unavailable = fetched.without_details()

    assert (fetched.details, fetched.details_status) == (details, DetailsStatus.FETCHED)
    assert (unavailable.details, unavailable.details_status) == (None, DetailsStatus.UNAVAILABLE)
    assert base.details_status is DetailsStatus.NOT_NEEDED
