"""The XML parser: both schema versions, missing nodes, the reference's traps, hostile input.

Reference defects 8 and 23 to 26 (the local docs/REFERENCE.md) each have a test here: a
hardened parser (8), the factor's account (23), several and description-only due dates
(24), amounts mistaken for descriptions and correction "before" rows (25), and payment-form
codes instead of Polish labels (26).
"""

from __future__ import annotations

from datetime import date

import pytest

from custom_components.ksef_notification.const import (
    MAX_XML_BYTES,
    XML_CHUNK_BYTES,
    XML_MAX_DEPTH,
    XML_MAX_TEXT,
)
from custom_components.ksef_notification.core.model import InvoiceDetails
from custom_components.ksef_notification.core.xml_parser import InvoiceXmlError, parse

from .core_factory import fixture_bytes

FA3 = "http://crd.gov.pl/wzor/2025/06/25/13775/"
FA2 = "http://crd.gov.pl/wzor/2023/06/29/12648/"


def invoice_xml(fa: str = "", namespace: str = FA3, top: str = "") -> bytes:
    """A minimal FA document with `fa` inside <Fa> and `top` next to it."""
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n<Faktura xmlns="{namespace}">'
        f"{top}<Fa>{fa}</Fa></Faktura>"
    ).encode()


def test_fa3() -> None:
    assert parse(fixture_bytes("invoice_fa3.xml")) == InvoiceDetails(
        seller_name="Fikcyjny Dostawca B",
        due_dates=(date(2026, 10, 20),),
        payment_form="6",
        bank_accounts=("00000000000000000000000000",),
        line_item_count=1,
        line_item_descriptions=("Usługa przykładowa",),
    )


def test_fa2_with_instalments_another_payment_form_and_two_accounts() -> None:
    assert parse(fixture_bytes("invoice_fa2.xml")) == InvoiceDetails(
        seller_name="Fikcyjny Dostawca A",
        due_dates=(date(2026, 11, 15), date(2026, 10, 15), date(2026, 12, 15)),
        payment_description="Kompensata",
        bank_accounts=("00000000000000000000000000", "11111111111111111111111111"),
        line_item_count=5,
        # Whitespace collapsed; row 3 has no P_7 (counted, not described); only 3 kept.
        line_item_descriptions=("Pozycja pierwsza", "Pozycja druga", "Pozycja czwarta"),
    )


def test_fa3_correction_with_prefixes_and_every_trap() -> None:
    """Prefixed namespace; ZaplataCzesciowa/FormaPlatnosci, the factor's NrRB and the
    StanPrzed row are all ignored; a structured TerminOpis is the only due date."""
    assert parse(fixture_bytes("invoice_fa3_correction.xml")) == InvoiceDetails(
        seller_name="Fikcyjny Dostawca B",
        due_description="14 dni — od dnia doręczenia",
        line_item_count=1,
        line_item_descriptions=("Usługa po korekcie",),
    )


def test_fa2_free_text_due_description() -> None:
    xml = invoice_xml(
        "<Platnosc><TerminPlatnosci><Termin>2026-10-20</Termin>"
        "<TerminOpis>lub  przy odbiorze</TerminOpis></TerminPlatnosci>"
        "<TerminPlatnosci><TerminOpis>drugi opis</TerminOpis></TerminPlatnosci></Platnosc>",
        FA2,
    )

    details = parse(xml)

    assert details.due_dates == (date(2026, 10, 20),)
    assert details.due_description == "lub przy odbiorze"  # the first one


def test_an_incomplete_structured_due_description_keeps_what_is_there() -> None:
    xml = invoice_xml(
        "<Platnosc><TerminPlatnosci><TerminOpis><Ilosc>30</Ilosc><Jednostka>dni</Jednostka>"
        "</TerminOpis></TerminPlatnosci></Platnosc>"
    )

    assert parse(xml).due_description == "30 dni"


def test_nothing_but_the_root() -> None:
    """An advance invoice may carry no rows and no payment section at all."""
    assert parse(invoice_xml()) == InvoiceDetails()


def test_payment_form_wins_over_other_and_other_needs_its_flag() -> None:
    both = invoice_xml(
        "<Platnosc><FormaPlatnosci>1</FormaPlatnosci><PlatnoscInna>1</PlatnoscInna>"
        "<OpisPlatnosci>Barter</OpisPlatnosci></Platnosc>"
    )
    unflagged = invoice_xml("<Platnosc><OpisPlatnosci>Barter</OpisPlatnosci></Platnosc>")
    flag_only = invoice_xml("<Platnosc><PlatnoscInna>1</PlatnoscInna></Platnosc>")

    assert (parse(both).payment_form, parse(both).payment_description) == ("1", "Barter")
    assert parse(unflagged).payment_description is None
    assert parse(flag_only) == InvoiceDetails()


@pytest.mark.parametrize("code", ["0", "8", "6 7", "przelew", ""])
def test_an_unknown_payment_form_code_is_absent(code: str) -> None:
    xml = invoice_xml(f"<Platnosc><FormaPlatnosci>{code}</FormaPlatnosci></Platnosc>")

    assert parse(xml).payment_form is None


def test_a_payment_form_code_with_whitespace() -> None:
    xml = invoice_xml("<Platnosc><FormaPlatnosci>\n  6\n</FormaPlatnosci></Platnosc>")

    assert parse(xml).payment_form == "6"


def test_an_invalid_due_date_is_skipped() -> None:
    xml = invoice_xml(
        "<Platnosc><TerminPlatnosci><Termin>2026-13-01</Termin></TerminPlatnosci>"
        "<TerminPlatnosci><Termin>2026-10-20</Termin></TerminPlatnosci></Platnosc>"
    )

    assert parse(xml).due_dates == (date(2026, 10, 20),)


def test_an_account_with_spaces_is_compacted() -> None:
    xml = invoice_xml(
        "<Platnosc><RachunekBankowy><NrRB> 00 0000 0000 0000 0000 0000 0000 </NrRB>"
        "</RachunekBankowy></Platnosc>"
    )

    assert parse(xml).bank_accounts == ("00000000000000000000000000",)


def test_paths_are_exact_not_descendants() -> None:
    """Platnosc outside Fa, P_7 outside FaWiersz, a nested FaWiersz: none of them count."""
    xml = invoice_xml(
        "<Zamowienie><FaWiersz><P_7>zamówienie</P_7></FaWiersz></Zamowienie><P_7>luzem</P_7>",
        top="<Platnosc><FormaPlatnosci>6</FormaPlatnosci></Platnosc>",
    )

    assert parse(xml) == InvoiceDetails()


def test_amounts_are_never_descriptions() -> None:
    xml = invoice_xml("<FaWiersz><P_11>100.00</P_11><P_11A>123.00</P_11A></FaWiersz>")

    assert parse(xml).line_item_count == 1
    assert parse(xml).line_item_descriptions == ()


def test_ten_thousand_rows_across_many_chunks() -> None:
    rows = "".join(
        f"<FaWiersz><NrWierszaFa>{n}</NrWierszaFa><P_7>Pozycja ąę {n}</P_7></FaWiersz>"
        for n in range(1, 10_001)
    )
    xml = invoice_xml(rows)
    assert len(xml) > 10 * XML_CHUNK_BYTES  # multi-byte characters straddle chunk boundaries

    details = parse(xml)

    assert details.line_item_count == 10_000
    assert details.line_item_descriptions == ("Pozycja ąę 1", "Pozycja ąę 2", "Pozycja ąę 3")


# --- hostile and broken documents --------------------------------------------------------

BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE Faktura [
  <!ENTITY a "aaaaaaaaaa">
  <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
  <!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">
  <!ENTITY d "&c;&c;&c;&c;&c;&c;&c;&c;&c;&c;">
]>
<Faktura><Fa><FaWiersz><P_7>&d;</P_7></FaWiersz></Fa></Faktura>"""

EXTERNAL_ENTITY = b"""<?xml version="1.0"?>
<!DOCTYPE Faktura [<!ENTITY secret SYSTEM "file:///etc/passwd">]>
<Faktura><Fa><FaWiersz><P_7>&secret;</P_7></FaWiersz></Fa></Faktura>"""

PARAMETER_ENTITY = b"""<?xml version="1.0"?>
<!DOCTYPE Faktura [<!ENTITY % remote SYSTEM "http://example.invalid/x.dtd"> %remote;]>
<Faktura/>"""

EXTERNAL_DTD = b"""<?xml version="1.0"?>
<!DOCTYPE Faktura SYSTEM "http://example.invalid/faktura.dtd">
<Faktura/>"""

EMPTY_DOCTYPE = b"""<?xml version="1.0"?><!DOCTYPE Faktura><Faktura/>"""

UNDEFINED_ENTITY = b"""<?xml version="1.0"?><Faktura><Fa>&undefined;</Fa></Faktura>"""


@pytest.mark.parametrize(
    "document",
    [
        BILLION_LAUGHS,
        EXTERNAL_ENTITY,
        PARAMETER_ENTITY,
        EXTERNAL_DTD,
        EMPTY_DOCTYPE,
        UNDEFINED_ENTITY,
    ],
    ids=["billion-laughs", "external", "parameter", "external-dtd", "doctype", "undefined"],
)
def test_dtds_and_entities_are_refused(document: bytes) -> None:
    with pytest.raises(InvoiceXmlError):
        parse(document)


@pytest.mark.parametrize(
    "document",
    [
        b"",
        b"   ",
        b"not xml at all",
        b"{}",
        b"<Faktura><Fa></Faktura>",
        invoice_xml("<FaWiersz><P_7>urwane")[:-20],
        b"<Invoice><Fa/></Invoice>",
        b'<Faktura xmlns="x"><Fa>\xff\xfe</Fa></Faktura>',
    ],
    ids=[
        "empty",
        "blank",
        "text",
        "json",
        "mismatched",
        "truncated",
        "other-root",
        "bad-utf8",
    ],
)
def test_broken_documents_raise_one_error_type(document: bytes) -> None:
    with pytest.raises(InvoiceXmlError):
        parse(document)


def test_a_document_over_the_size_bound_is_refused_before_parsing() -> None:
    with pytest.raises(InvoiceXmlError, match="too large"):
        parse(invoice_xml(" " * MAX_XML_BYTES))


def test_depth_bound() -> None:
    def nested(depth: int) -> bytes:  # the root and Fa are two levels already
        return invoice_xml("<x>" * (depth - 2) + "</x>" * (depth - 2))

    assert parse(nested(XML_MAX_DEPTH)) == InvoiceDetails()
    with pytest.raises(InvoiceXmlError, match="too deep"):
        parse(nested(XML_MAX_DEPTH + 1))


def test_text_bound_applies_to_collected_values_only() -> None:
    long_text = "x" * (XML_MAX_TEXT + 1)

    assert parse(invoice_xml(f"<Uwagi>{long_text}</Uwagi>")) == InvoiceDetails()
    with pytest.raises(InvoiceXmlError, match="too long"):
        parse(invoice_xml(f"<FaWiersz><P_7>{long_text}</P_7></FaWiersz>"))
