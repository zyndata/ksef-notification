"""Invoice + selection + locale → notification title and body parts.

Rules in docs/ARCHITECTURE.md § Message formatting.

`format_invoice` and `format_combined` return a `Message`: translation keys (under `common`
in strings.json) plus the values that go into them, in order. `render` turns it into the
title and body text with the strings of one language, which the caller loads. `field_values`
gives the same selection as raw values, for the event and the last-invoice sensor.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ..const import (
    BODY_MAX,
    COMBINE_THRESHOLD,
    COMBINED_MAX_LINES,
    LINE_ITEMS_SHOWN,
    SELLER_NAME_MAX,
    SELLER_TEXT_MAX,
)
from .fields import FIELD_KEYS, ordered
from .model import Invoice

ABSENT = "—"
ELLIPSIS = "…"
_VAT_CURRENCY = "PLN"
_CENT = Decimal("0.01")
_NBSP = chr(0xA0)  # no-break space
_POLISH_DIGITS = str.maketrans({",": _NBSP, ".": ","})

_NRB = re.compile(r"\d{26}")
_IBAN = re.compile(r"[A-Z]{2}\d{2}[A-Z0-9]{1,30}")

TITLE_INVOICE = "notification_title_invoice"
TITLE_CORRECTION = "notification_title_correction"
TITLE_COMBINED = "notification_title_combined"
MORE = "notification_more"
COMBINED_MORE = "notification_combined_more"
SELLER_NIP = "notification_seller_nip"
FIELD_PREFIX = "field_"
PAYMENT_FORM_PREFIX = "payment_form_"
INVOICE_TYPE_PREFIX = "invoice_type_"

PAYMENT_FORMS = ("1", "2", "3", "4", "5", "6", "7")
INVOICE_TYPES = (
    "Vat",
    "Zal",
    "Kor",
    "Roz",
    "Upr",
    "KorZal",
    "KorRoz",
    "VatPef",
    "VatPefSp",
    "KorPef",
    "VatRr",
    "KorVatRr",
)

#: Every key a Message can contain — each must have a text in strings.json `common`.
MESSAGE_KEYS: frozenset[str] = frozenset(
    {
        TITLE_INVOICE,
        TITLE_CORRECTION,
        TITLE_COMBINED,
        MORE,
        COMBINED_MORE,
        SELLER_NIP,
        *(FIELD_PREFIX + key for key in FIELD_KEYS),
        *(PAYMENT_FORM_PREFIX + code for code in PAYMENT_FORMS),
        *(INVOICE_TYPE_PREFIX + value.lower() for value in INVOICE_TYPES),
    }
)


@dataclass(frozen=True)
class Phrase:
    """A translatable text: a key, and the values of its `{placeholders}`."""

    key: str
    values: tuple[tuple[str, str], ...] = ()


def phrase(key: str, **values: object) -> Phrase:
    return Phrase(key, tuple((name, str(value)) for name, value in values.items()))


Part = str | Phrase


@dataclass(frozen=True)
class Line:
    """`label: parts…`, or just the parts when there is no label (the seller headline)."""

    label: Phrase | None
    parts: tuple[Part, ...]


@dataclass(frozen=True)
class Message:
    title: Phrase
    lines: tuple[Line, ...]


def is_combined(count: int) -> bool:
    """More new invoices in one cycle than COMBINE_THRESHOLD get one combined message."""
    return count > COMBINE_THRESHOLD


def format_invoice(invoice: Invoice, selection: Iterable[str], locale: str) -> Message:
    """One invoice: the selected fields in the fixed order, absent ones as `Label: —`."""
    polish = _is_polish(locale)
    lines = []
    for key in ordered(selection):
        if key == "seller_name":
            lines.append(Line(None, (_seller(invoice, use_details=True),)))
        else:
            lines.append(Line(phrase(FIELD_PREFIX + key), _value(invoice, key, polish)))
    title = TITLE_CORRECTION if invoice.is_correction else TITLE_INVOICE
    return Message(phrase(title), tuple(lines))


def format_combined(invoices: Sequence[Invoice], locale: str) -> Message:
    """Several invoices: `Seller — gross` per invoice, oldest first, metadata only."""
    polish = _is_polish(locale)
    shown = invoices[:COMBINED_MAX_LINES]
    lines = [
        Line(None, (_seller(invoice, use_details=False), " — ", *_gross(invoice, polish)))
        for invoice in shown
    ]
    if len(invoices) > len(shown):
        lines.append(Line(None, (phrase(COMBINED_MORE, count=len(invoices) - len(shown)),)))
    return Message(phrase(TITLE_COMBINED, count=len(invoices)), tuple(lines))


def render(
    message: Message, strings: Mapping[str, str], entry_title: str | None = None
) -> tuple[str, str]:
    """(title, body) in one language; `strings` maps the `common` keys to their texts.

    A key missing from `strings` is shown as itself rather than failing the notification.
    """
    title = _resolve(message.title, strings)
    if entry_title:
        title = f"{title} · {entry_title}"
    rendered = []
    for line in message.lines:
        text = "".join(_resolve(part, strings) for part in line.parts)
        rendered.append(f"{_resolve(line.label, strings)}: {text}" if line.label else text)
    body = "\n".join(rendered)
    if len(body) > BODY_MAX:
        body = body[: BODY_MAX - 1] + ELLIPSIS
    return title, body


def field_values(invoice: Invoice, selection: Iterable[str]) -> dict[str, Any]:
    """The selected fields as raw values, None when absent (docs/CONFIG.md § Event payload).

    Amounts are floats, dates ISO strings. `currency` is added with the gross or net amount;
    the VAT amount is always in PLN.
    """
    details = invoice.details
    keys = ordered(selection)
    values: dict[str, Any] = {}
    for key in keys:
        match key:
            case "seller_name":
                values[key] = invoice.seller_name or (details.seller_name if details else None)
            case "gross_amount" | "net_amount" | "vat_amount":
                amount = getattr(invoice, key)
                values[key] = float(amount) if amount is not None else None
            case "issue_date":
                values[key] = invoice.issue_date.isoformat() if invoice.issue_date else None
            case "due_date":
                values[key] = (
                    min(details.due_dates).isoformat() if details and details.due_dates else None
                )
            case "payment_form":
                values[key] = (
                    (details.payment_form or details.payment_description) if details else None
                )
            case "bank_account":
                values[key] = (
                    details.bank_accounts[0] if details and details.bank_accounts else None
                )
            case "line_items":
                values[key] = (
                    {
                        "count": details.line_item_count,
                        "descriptions": list(details.line_item_descriptions),
                    }
                    if details and details.line_item_count
                    else None
                )
            case _:
                values[key] = getattr(invoice, key)
    if {"gross_amount", "net_amount"} & set(keys):
        values["currency"] = invoice.currency
    return values


# --- values ------------------------------------------------------------------------------


def _value(invoice: Invoice, key: str, polish: bool) -> tuple[Part, ...]:  # noqa: PLR0911, PLR0912
    details = invoice.details
    match key:
        case "seller_nip" | "ksef_number":
            return _or_absent(getattr(invoice, key))
        case "invoice_number":
            return _or_absent(_clean(invoice.invoice_number))
        case "gross_amount":
            return _gross(invoice, polish)
        case "net_amount":
            return _or_absent(_money(invoice.net_amount, invoice.currency, polish))
        case "vat_amount":
            return _or_absent(_money(invoice.vat_amount, _VAT_CURRENCY, polish))
        case "issue_date":
            return _or_absent(_date(invoice.issue_date, polish))
        case "invoice_type":
            if invoice.invoice_type in INVOICE_TYPES:
                return (phrase(INVOICE_TYPE_PREFIX + invoice.invoice_type.lower()),)
            return _or_absent(_clean(invoice.invoice_type))
    if details is None:
        return (ABSENT,)
    match key:
        case "due_date" if details.due_dates:
            return _with_more(_date(min(details.due_dates), polish), len(details.due_dates) - 1)
        case "due_date":
            return _or_absent(_clean(details.due_description, SELLER_TEXT_MAX))
        case "payment_form" if details.payment_form in PAYMENT_FORMS:
            return (phrase(PAYMENT_FORM_PREFIX + details.payment_form),)
        case "payment_form":
            return _or_absent(_clean(details.payment_description, SELLER_TEXT_MAX))
        case "bank_account" if details.bank_accounts:
            return _with_more(_account(details.bank_accounts[0]), len(details.bank_accounts) - 1)
        case "line_items" if details.line_item_count:
            return _line_items(details.line_item_count, details.line_item_descriptions)
    return (ABSENT,)


def _seller(invoice: Invoice, *, use_details: bool) -> Part:
    name = invoice.seller_name
    if not name and use_details and invoice.details:
        name = invoice.details.seller_name
    if cleaned := _clean(name, SELLER_NAME_MAX):
        return cleaned
    if invoice.seller_nip:
        return phrase(SELLER_NIP, nip=invoice.seller_nip)
    return ABSENT


def _gross(invoice: Invoice, polish: bool) -> tuple[Part, ...]:
    return _or_absent(_money(invoice.gross_amount, invoice.currency, polish))


def _line_items(count: int, descriptions: Sequence[str]) -> tuple[Part, ...]:
    shown = [text for text in (_clean(d, SELLER_TEXT_MAX) for d in descriptions) if text]
    shown = shown[:LINE_ITEMS_SHOWN]
    if not shown:
        return (str(count),)
    parts: list[Part] = [f"{count}: ", "; ".join(shown)]
    if count > len(shown):
        parts += ["; ", phrase(MORE, count=count - len(shown))]
    return tuple(parts)


def _with_more(text: str, more: int) -> tuple[Part, ...]:
    return (text, " (", phrase(MORE, count=more), ")") if more > 0 else (text,)


def _or_absent(text: str | None) -> tuple[Part, ...]:
    return (text or ABSENT,)


def _money(amount: Decimal | None, currency: str | None, polish: bool) -> str | None:
    if amount is None:
        return None
    text = f"{amount.quantize(_CENT, rounding=ROUND_HALF_UP):,.2f}"
    if polish:
        text = text.translate(_POLISH_DIGITS)
    return f"{text} {currency}" if currency else text


def _date(value: date | None, polish: bool) -> str | None:
    if value is None:
        return None
    return value.strftime("%d.%m.%Y") if polish else value.isoformat()


def _account(account: str) -> str:
    """26-digit NRB → `NN NNNN …`; an IBAN → groups of four; anything else verbatim."""
    if _NRB.fullmatch(account):
        return " ".join([account[:2], *_fours(account[2:])])
    if _IBAN.fullmatch(account):
        return " ".join(_fours(account))
    return account


def _fours(text: str) -> list[str]:
    return [text[index : index + 4] for index in range(0, len(text), 4)]


def _clean(text: str | None, limit: int | None = None) -> str | None:
    """Seller-written text: control characters out, whitespace collapsed, shortened with …"""
    if not text:
        return None
    # Whitespace first, so a newline separates words instead of vanishing with the controls.
    text = " ".join(text.split())
    text = "".join(char for char in text if not unicodedata.category(char).startswith("C"))
    text = " ".join(text.split())
    if limit is not None and len(text) > limit:
        text = text[: limit - 1].rstrip() + ELLIPSIS
    return text or None


def _resolve(part: Part, strings: Mapping[str, str]) -> str:
    if isinstance(part, str):
        return part
    text = strings.get(part.key, part.key)
    for name, value in part.values:
        text = text.replace(f"{{{name}}}", value)
    return text


def _is_polish(locale: str) -> bool:
    return locale.lower().split("-")[0].split("_")[0] == "pl"
