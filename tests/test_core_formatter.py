"""The formatter: every field present and absent, locale rules, the combined message."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from custom_components.ksef_notification.const import (
    BODY_MAX,
    COMBINE_THRESHOLD,
    COMBINED_MAX_LINES,
    DEFAULT_FIELDS,
)
from custom_components.ksef_notification.core.fields import FIELD_KEYS
from custom_components.ksef_notification.core.formatter import (
    ABSENT,
    MESSAGE_KEYS,
    Line,
    Message,
    Phrase,
    field_values,
    format_combined,
    format_invoice,
    is_combined,
    phrase,
    render,
)
from custom_components.ksef_notification.core.model import Invoice, InvoiceDetails
from custom_components.ksef_notification.core.xml_parser import parse

from .core_factory import fixture_bytes, full_invoice, invoice

STRINGS: dict[str, str] = json.loads(
    (
        Path(__file__).parents[1] / "custom_components" / "ksef_notification" / "strings.json"
    ).read_text(encoding="utf-8")
)["common"]
POLISH: dict[str, str] = json.loads(
    (
        Path(__file__).parents[1]
        / "custom_components"
        / "ksef_notification"
        / "translations"
        / "pl.json"
    ).read_text(encoding="utf-8")
)["common"]
NBSP = chr(0xA0)  # no-break space

FA2_DETAILS = parse(fixture_bytes("invoice_fa2.xml"))


def text(item: Invoice, selection: tuple[str, ...] = FIELD_KEYS, locale: str = "en") -> str:
    return render(format_invoice(item, selection, locale), STRINGS)[1]


def title(item: Invoice) -> str:
    return render(format_invoice(item, DEFAULT_FIELDS, "en"), STRINGS)[0]


# --- one invoice -------------------------------------------------------------------------


def test_the_default_selection() -> None:
    item = Invoice.from_metadata(
        {
            "ksefNumber": "3333333333-20261006-0000000000B2-02",
            "permanentStorageDate": "2026-10-06T07:41:12.5123456+00:00",
            "seller": {"nip": "3333333333", "name": "Fikcyjny Dostawca B"},
            "invoiceNumber": "FV/2026/10/0042",
            "grossAmount": 123.0,
            "currency": "PLN",
            "invoiceType": "Vat",
        }
    ).with_details(parse(fixture_bytes("invoice_fa3.xml")))

    assert render(format_invoice(item, DEFAULT_FIELDS, "en"), STRINGS) == (
        "New cost invoice",
        "Fikcyjny Dostawca B\n"
        "Invoice number: FV/2026/10/0042\n"
        "Gross: 123.00 PLN\n"
        "Due date: 2026-10-20",
    )


def test_every_field_present_in_english() -> None:
    item = full_invoice().with_details(FA2_DETAILS)

    assert text(item).split("\n") == [
        "Fikcyjny Dostawca B",
        "Seller NIP: 3333333333",
        "Invoice number: FV/2026/10/0042",
        "Gross: 1,234.56 PLN",
        "Net: 1,003.71 PLN",
        "VAT: 230.85 PLN",
        "Issue date: 2026-10-05",
        "Due date: 2026-10-15 (+2 more)",
        "Payment form: Kompensata",
        "Bank account: 00 0000 0000 0000 0000 0000 0000 (+1 more)",
        "Items: 5: Pozycja pierwsza; Pozycja druga; Pozycja czwarta; +2 more",
        "Invoice type: VAT invoice",
        f"KSeF number: {item.ksef_number}",
    ]


def test_polish_number_and_date_rules() -> None:
    item = full_invoice().with_details(FA2_DETAILS)

    lines = text(item, locale="pl").split("\n")

    assert lines[3] == f"Gross: 1{NBSP}234,56 PLN"
    assert lines[6] == "Issue date: 05.10.2026"
    assert lines[7] == "Due date: 15.10.2026 (+2 more)"


def test_every_field_present_in_polish() -> None:
    item = full_invoice().with_details(FA2_DETAILS)

    title_text, body = render(format_invoice(item, FIELD_KEYS, "pl"), POLISH)

    assert title_text == "Nowa faktura kosztowa"
    assert body.split("\n") == [
        "Fikcyjny Dostawca B",
        "NIP sprzedawcy: 3333333333",
        "Numer faktury: FV/2026/10/0042",
        f"Brutto: 1{NBSP}234,56 PLN",
        f"Netto: 1{NBSP}003,71 PLN",
        "VAT: 230,85 PLN",
        "Data wystawienia: 05.10.2026",
        "Termin płatności: 15.10.2026 (jeszcze 2)",
        "Forma płatności: Kompensata",
        "Nr rachunku: 00 0000 0000 0000 0000 0000 0000 (jeszcze 1)",
        "Pozycje: 5: Pozycja pierwsza; Pozycja druga; Pozycja czwarta; jeszcze 2",
        "Rodzaj faktury: Faktura VAT",
        f"Numer KSeF: {item.ksef_number}",
    ]


def test_a_combined_message_in_polish() -> None:
    """The count stands after a colon, so no Polish plural form is needed for any number."""
    items = [full_invoice(gross_amount=Decimal(n)) for n in range(COMBINED_MAX_LINES + 2)]

    title_text, body = render(format_combined(items, "pl"), POLISH)

    assert title_text == f"Nowe faktury kosztowe: {COMBINED_MAX_LINES + 2}"
    assert body.split("\n")[-1] == "… i jeszcze 2"


def test_every_emitted_key_has_a_polish_text() -> None:
    assert set(POLISH) == MESSAGE_KEYS


@pytest.mark.parametrize("locale", ["pl", "PL", "pl-PL", "pl_PL"])
def test_polish_locale_spellings(locale: str) -> None:
    assert "05.10.2026" in text(full_invoice(), ("issue_date",), locale)


@pytest.mark.parametrize("locale", ["en", "de", "en-GB", "", "plx"])
def test_other_locales_use_the_english_rules(locale: str) -> None:
    assert "2026-10-05" in text(full_invoice(), ("issue_date",), locale)


def test_every_field_absent() -> None:
    """A selected field without a value reads `Label: —`; nothing at all for the seller."""
    item = invoice()

    lines = text(item).split("\n")

    assert lines[0] == ABSENT
    assert lines[1:-1] == [f"{label}: {ABSENT}" for label in _labels(FIELD_KEYS[1:-1])]
    assert lines[-1] == f"KSeF number: {item.ksef_number}", "the one value always there"


def test_xml_fields_absent_when_the_xml_was_not_obtained() -> None:
    item = full_invoice().without_details()

    assert text(item, ("due_date", "payment_form", "bank_account", "line_items")) == (
        "Due date: —\nPayment form: —\nBank account: —\nItems: —"
    )


def test_xml_fields_absent_in_the_xml() -> None:
    item = full_invoice().with_details(InvoiceDetails())

    assert text(item, ("due_date", "payment_form", "bank_account", "line_items")) == (
        "Due date: —\nPayment form: —\nBank account: —\nItems: —"
    )


def test_unselected_fields_are_not_shown_and_order_is_fixed() -> None:
    item = full_invoice()

    assert text(item, ("ksef_number", "issue_date")) == (
        f"Issue date: 2026-10-05\nKSeF number: {item.ksef_number}"
    )


def test_the_seller_falls_back_to_the_xml_then_to_the_nip() -> None:
    details = InvoiceDetails(seller_name="Fikcyjny Dostawca A")

    assert text(full_invoice(seller_name=None).with_details(details), ("seller_name",)) == (
        "Fikcyjny Dostawca A"
    )
    assert text(full_invoice(seller_name=None), ("seller_name",)) == "NIP 3333333333"


def test_a_correction_has_its_own_title() -> None:
    assert title(full_invoice(invoice_type="KorZal")) == "New correction invoice"
    assert title(full_invoice()) == "New cost invoice"


def test_vat_is_always_in_pln() -> None:
    item = full_invoice(currency="EUR")

    assert text(item, ("gross_amount", "vat_amount")) == ("Gross: 1,234.56 EUR\nVAT: 230.85 PLN")


@pytest.mark.parametrize(
    ("amount", "english", "polish"),
    [
        (Decimal("0"), "0.00", "0,00"),
        (Decimal("0.005"), "0.01", "0,01"),
        (Decimal("999.999"), "1,000.00", f"1{NBSP}000,00"),
        (Decimal("1234567.8"), "1,234,567.80", f"1{NBSP}234{NBSP}567,80"),
        (Decimal("-1234.56"), "-1,234.56", f"-1{NBSP}234,56"),
    ],
)
def test_amounts(amount: Decimal, english: str, polish: str) -> None:
    item = full_invoice(gross_amount=amount)

    assert text(item, ("gross_amount",)) == f"Gross: {english} PLN"
    assert text(item, ("gross_amount",), "pl") == f"Gross: {polish} PLN"


def test_an_amount_without_a_currency() -> None:
    assert text(full_invoice(currency=None), ("gross_amount",)) == "Gross: 1,234.56"


@pytest.mark.parametrize(
    ("account", "shown"),
    [
        ("00000000000000000000000000", "00 0000 0000 0000 0000 0000 0000"),
        ("PL00000000000000000000000000", "PL00 0000 0000 0000 0000 0000 0000"),
        ("DE00000000000000000000", "DE00 0000 0000 0000 0000 00"),
        ("0000000000", "0000000000"),
        ("ACCOUNT-0001", "ACCOUNT-0001"),
    ],
)
def test_bank_accounts(account: str, shown: str) -> None:
    item = full_invoice().with_details(InvoiceDetails(bank_accounts=(account,)))

    assert text(item, ("bank_account",)) == f"Bank account: {shown}"


def test_a_due_description_when_there_is_no_date() -> None:
    details = parse(fixture_bytes("invoice_fa3_correction.xml"))

    assert text(full_invoice().with_details(details), ("due_date",)) == (
        "Due date: 14 dni — od dnia doręczenia"
    )


@pytest.mark.parametrize(("code", "label"), [("1", "Cash"), ("6", "Bank transfer")])
def test_payment_form_codes_are_translated(code: str, label: str) -> None:
    item = full_invoice().with_details(InvoiceDetails(payment_form=code))

    assert text(item, ("payment_form",)) == f"Payment form: {label}"


def test_line_items_without_descriptions_show_the_count() -> None:
    item = full_invoice().with_details(InvoiceDetails(line_item_count=7))

    assert text(item, ("line_items",)) == "Items: 7"


def test_line_items_all_described() -> None:
    details = InvoiceDetails(line_item_count=2, line_item_descriptions=("A", "B"))

    assert text(full_invoice().with_details(details), ("line_items",)) == "Items: 2: A; B"


@pytest.mark.parametrize(
    ("invoice_type", "shown"),
    [("Vat", "VAT invoice"), ("KorVatRr", "VAT RR correction invoice"), ("Nowy", "Nowy")],
)
def test_invoice_types(invoice_type: str, shown: str) -> None:
    assert text(full_invoice(invoice_type=invoice_type), ("invoice_type",)) == (
        f"Invoice type: {shown}"
    )


# --- text written by the seller ----------------------------------------------------------


def test_seller_text_is_cleaned_and_shortened() -> None:
    long_name = "Fikcyjny Dostawca " + "B" * 100
    item = full_invoice(seller_name=long_name, invoice_number="FV/1\n\x1b[31m/2026\u200b")

    lines = text(item, ("seller_name", "invoice_number")).split("\n")

    assert len(lines[0]) == 80
    assert lines[0].endswith("…")
    assert lines[1] == "Invoice number: FV/1 [31m/2026"


def test_descriptions_are_shortened_to_sixty() -> None:
    details = InvoiceDetails(
        line_item_count=1, line_item_descriptions=("x" * 100,), payment_description="y" * 100
    )

    lines = text(full_invoice().with_details(details), ("payment_form", "line_items")).split("\n")

    assert lines[0] == "Payment form: " + "y" * 59 + "…"
    assert lines[1] == "Items: 1: " + "x" * 59 + "…"


def test_placeholder_syntax_in_seller_text_is_shown_not_interpreted() -> None:
    item = full_invoice(seller_name="{count} {nip}")

    assert text(item, ("seller_name",)) == "{count} {nip}"


def test_the_body_is_capped() -> None:
    details = InvoiceDetails(line_item_count=3, line_item_descriptions=("x" * 60,) * 3)
    item = full_invoice(invoice_number="N" * 900).with_details(details)

    body = text(item)

    assert len(body) == BODY_MAX
    assert body.endswith("…")


# --- several invoices --------------------------------------------------------------------


def test_combine_threshold() -> None:
    assert not is_combined(COMBINE_THRESHOLD)
    assert is_combined(COMBINE_THRESHOLD + 1)


def test_a_combined_message() -> None:
    items = [
        full_invoice(),
        full_invoice(seller_name=None, currency="EUR", gross_amount=Decimal(100)),
        full_invoice(gross_amount=None).with_details(InvoiceDetails(seller_name="z XML")),
        full_invoice(seller_name=None, seller_nip=None),
    ]

    assert render(format_combined(items, "en"), STRINGS) == (
        "4 new cost invoices",
        "Fikcyjny Dostawca B — 1,234.56 PLN\n"
        "NIP 3333333333 — 100.00 EUR\n"
        "Fikcyjny Dostawca B — —\n"
        "— — 1,234.56 PLN",
    )


def test_a_combined_message_over_the_line_limit() -> None:
    items = [full_invoice(gross_amount=Decimal(n)) for n in range(COMBINED_MAX_LINES + 2)]

    title_text, body = render(format_combined(items, "pl"), STRINGS)
    lines = body.split("\n")

    assert title_text == f"{COMBINED_MAX_LINES + 2} new cost invoices"
    assert len(lines) == COMBINED_MAX_LINES + 1
    assert lines[0] == "Fikcyjny Dostawca B — 0,00 PLN"
    assert lines[-1] == "… and 2 more"


# --- rendering ---------------------------------------------------------------------------


def test_the_entry_title_is_appended() -> None:
    message = format_invoice(full_invoice(), DEFAULT_FIELDS, "en")

    assert render(message, STRINGS, "Firma testowa")[0] == "New cost invoice · Firma testowa"


def test_a_missing_string_shows_its_key() -> None:
    message = Message(phrase("notification_title_combined", count=4), (Line(None, ("x",)),))

    assert render(message, {}) == ("notification_title_combined", "x")


def test_every_emitted_key_has_an_english_text() -> None:
    assert set(STRINGS) == MESSAGE_KEYS


def test_no_raw_key_reaches_the_rendered_text() -> None:
    samples = [
        format_invoice(full_invoice().with_details(FA2_DETAILS), FIELD_KEYS, "en"),
        format_invoice(invoice(), FIELD_KEYS, "en"),
        format_combined([full_invoice(seller_name=None)] * 12, "en"),
    ]
    for message in samples:
        for part in _phrases(message):
            assert part.key in MESSAGE_KEYS
        rendered = "\n".join(render(message, STRINGS))
        assert "{" not in rendered
        assert not any(key in rendered for key in MESSAGE_KEYS)


# --- raw values --------------------------------------------------------------------------


def test_field_values_for_the_default_selection() -> None:
    item = full_invoice().with_details(FA2_DETAILS)

    assert field_values(item, DEFAULT_FIELDS) == {
        "seller_name": "Fikcyjny Dostawca B",
        "invoice_number": "FV/2026/10/0042",
        "gross_amount": 1234.56,
        "due_date": "2026-10-15",
        "currency": "PLN",
    }


def test_field_values_for_every_field() -> None:
    item = full_invoice().with_details(FA2_DETAILS)

    values = field_values(item, FIELD_KEYS)

    assert values == {
        "seller_name": "Fikcyjny Dostawca B",
        "seller_nip": "3333333333",
        "invoice_number": "FV/2026/10/0042",
        "gross_amount": 1234.56,
        "net_amount": 1003.71,
        "vat_amount": 230.85,
        "issue_date": "2026-10-05",
        "due_date": "2026-10-15",
        "payment_form": "Kompensata",
        "bank_account": "00000000000000000000000000",
        "line_items": {
            "count": 5,
            "descriptions": ["Pozycja pierwsza", "Pozycja druga", "Pozycja czwarta"],
        },
        "invoice_type": "Vat",
        "ksef_number": item.ksef_number,
        "currency": "PLN",
    }
    assert json.loads(json.dumps(values)) == values


def test_field_values_absent() -> None:
    item = invoice()

    values = field_values(item, FIELD_KEYS)

    assert values == {**dict.fromkeys([*FIELD_KEYS, "currency"]), "ksef_number": item.ksef_number}


def test_currency_comes_with_gross_or_net_only() -> None:
    item = full_invoice(currency="EUR")

    assert "currency" not in field_values(item, ("vat_amount",))
    assert field_values(item, ("net_amount",))["currency"] == "EUR"


def test_field_values_payment_code_and_seller_fallback() -> None:
    details = InvoiceDetails(seller_name="Fikcyjny Dostawca A", payment_form="6")
    item = full_invoice(seller_name=None).with_details(details)

    assert field_values(item, ("seller_name", "payment_form")) == {
        "seller_name": "Fikcyjny Dostawca A",
        "payment_form": "6",
    }


def test_field_values_issue_date_is_iso() -> None:
    item = full_invoice(issue_date=date(2026, 1, 2))

    assert field_values(item, ("issue_date",)) == {"issue_date": "2026-01-02"}


# --- helpers -----------------------------------------------------------------------------


def _labels(keys: tuple[str, ...]) -> list[str]:
    return [STRINGS[f"field_{key}"] for key in keys]


def _phrases(message: Message) -> list[Phrase]:
    found = [message.title]
    for line in message.lines:
        if line.label:
            found.append(line.label)
        found += [part for part in line.parts if isinstance(part, Phrase)]
    return found
