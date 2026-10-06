"""Invoice XML bytes → InvoiceDetails with a hardened streaming expat parser (phase 4).

Rules in docs/ARCHITECTURE.md § XML safety.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from xml.parsers import expat

from ..const import LINE_ITEMS_SHOWN, MAX_XML_BYTES, XML_CHUNK_BYTES, XML_MAX_DEPTH, XML_MAX_TEXT
from .model import InvoiceDetails

#: Paths of local names from the root. FA(2) and FA(3) differ in namespace only.
_ROOT = "Faktura"
_SELLER_NAME = (_ROOT, "Podmiot1", "DaneIdentyfikacyjne", "Nazwa")
_PAYMENT = (_ROOT, "Fa", "Platnosc")
_DUE = (*_PAYMENT, "TerminPlatnosci")
_DUE_DATE = (*_DUE, "Termin")
_DUE_TEXT = (*_DUE, "TerminOpis")
_DUE_PARTS = ("Ilosc", "Jednostka", "ZdarzeniePoczatkowe")  # FA(3) structured TerminOpis
_FORM = (*_PAYMENT, "FormaPlatnosci")
_OTHER_FORM = (*_PAYMENT, "PlatnoscInna")
_OTHER_FORM_TEXT = (*_PAYMENT, "OpisPlatnosci")
_ACCOUNT = (*_PAYMENT, "RachunekBankowy", "NrRB")
_ROW = (_ROOT, "Fa", "FaWiersz")
_ROW_TEXT = (*_ROW, "P_7")
_ROW_BEFORE = (*_ROW, "StanPrzed")

_COLLECTED = frozenset(
    {
        _SELLER_NAME,
        _DUE_DATE,
        _DUE_TEXT,
        *((*_DUE_TEXT, part) for part in _DUE_PARTS),
        _FORM,
        _OTHER_FORM,
        _OTHER_FORM_TEXT,
        _ACCOUNT,
        _ROW_TEXT,
        _ROW_BEFORE,
    }
)

_PAYMENT_FORM_CODES = frozenset("1234567")


class InvoiceXmlError(ValueError):
    """The document is not a readable invoice, or it broke a safety bound."""


def parse(data: bytes) -> InvoiceDetails:
    """Extract the notification fields from an FA(2)/FA(3) invoice.

    Refuses any DOCTYPE, and documents over MAX_XML_BYTES, deeper than XML_MAX_DEPTH, or with
    a collected text longer than XML_MAX_TEXT. Raises InvoiceXmlError.
    """
    if len(data) > MAX_XML_BYTES:
        raise InvoiceXmlError("document too large")
    collector = _Collector()
    parser = expat.ParserCreate(namespace_separator=" ")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.StartDoctypeDeclHandler = _refuse
    parser.EntityDeclHandler = _refuse
    parser.UnparsedEntityDeclHandler = _refuse
    parser.NotationDeclHandler = _refuse
    parser.ExternalEntityRefHandler = _refuse
    parser.StartElementHandler = collector.start
    parser.EndElementHandler = collector.end
    parser.CharacterDataHandler = collector.text
    view = memoryview(data)
    try:
        for offset in range(0, len(view), XML_CHUNK_BYTES):
            parser.Parse(bytes(view[offset : offset + XML_CHUNK_BYTES]), False)
        parser.Parse(b"", True)
    except expat.ExpatError as err:
        raise InvoiceXmlError(f"not well-formed: {expat.ErrorString(err.code)}") from None
    if not collector.root_seen:
        raise InvoiceXmlError("no root element")
    return collector.result()


def _iso_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _refuse(*_: Any) -> None:
    raise InvoiceXmlError("DTDs and entities are refused")


class _Collector:
    """Streaming handlers: tracks the path and keeps only the target values."""

    def __init__(self) -> None:
        self.path: list[str] = []
        #: Parallel to `path`: the text collected so far for a target element, else None.
        self.buffers: list[list[str] | None] = []
        self.root_seen = False
        self.seller_name: str | None = None
        self.due_dates: list[date] = []
        self.due_description: str | None = None
        self.due_parts: dict[str, str] = {}
        self.payment_form: str | None = None
        self.other_form = False
        self.payment_description: str | None = None
        self.accounts: list[str] = []
        self.rows = 0
        self.descriptions: list[str] = []
        self.row_text: str | None = None
        self.row_before = False

    def start(self, name: str, _attributes: dict[str, str]) -> None:
        local = name.rpartition(" ")[2]
        if not self.path:
            if local != _ROOT:
                raise InvoiceXmlError("root element is not Faktura")
            self.root_seen = True
        self.path.append(local)
        if len(self.path) > XML_MAX_DEPTH:
            raise InvoiceXmlError("document too deep")
        path = tuple(self.path)
        if path == _ROW:
            self.row_text = None
            self.row_before = False
        elif path == _DUE_TEXT:
            self.due_parts = {}
        self.buffers.append([] if path in _COLLECTED else None)

    def text(self, data: str) -> None:
        buffer = self.buffers[-1] if self.buffers else None
        if buffer is None:
            return
        buffer.append(data)
        if sum(map(len, buffer)) > XML_MAX_TEXT:
            raise InvoiceXmlError("element text too long")

    def end(self, _name: str) -> None:
        path = tuple(self.path)
        buffer = self.buffers.pop()
        if buffer is not None:
            self._store(path, " ".join("".join(buffer).split()))
        if path == _ROW:
            self._close_row()
        self.path.pop()

    def _store(self, path: tuple[str, ...], value: str) -> None:  # noqa: PLR0912
        if path == _SELLER_NAME:
            self.seller_name = value or None
        elif path == _DUE_DATE:
            if (due := _iso_date(value)) is not None:
                self.due_dates.append(due)
        elif path == _DUE_TEXT:
            # FA(2): free text. FA(3): the parts collected from its children.
            parts = [self.due_parts[part] for part in _DUE_PARTS if self.due_parts.get(part)]
            if len(parts) == len(_DUE_PARTS):
                description = f"{parts[0]} {parts[1]} — {parts[2]}"
            else:
                description = " ".join(parts) or value
            if self.due_description is None and description:
                self.due_description = description
        elif path[:-1] == _DUE_TEXT:
            self.due_parts[path[-1]] = value
        elif path == _FORM:
            if value in _PAYMENT_FORM_CODES:
                self.payment_form = value
        elif path == _OTHER_FORM:
            self.other_form = value == "1"
        elif path == _OTHER_FORM_TEXT:
            self.payment_description = value or None
        elif path == _ACCOUNT:
            if account := "".join(value.split()):
                self.accounts.append(account)
        elif path == _ROW_TEXT:
            self.row_text = value or None
        elif path == _ROW_BEFORE:
            self.row_before = value == "1"

    def _close_row(self) -> None:
        if self.row_before:
            return
        self.rows += 1
        if self.row_text and len(self.descriptions) < LINE_ITEMS_SHOWN:
            self.descriptions.append(self.row_text)

    def result(self) -> InvoiceDetails:
        return InvoiceDetails(
            seller_name=self.seller_name,
            due_dates=tuple(self.due_dates),
            due_description=self.due_description,
            payment_form=self.payment_form,
            payment_description=self.payment_description if self.other_form else None,
            bank_accounts=tuple(self.accounts),
            line_item_count=self.rows,
            line_item_descriptions=tuple(self.descriptions),
        )
