"""Every fixture is synthetic — checked, not promised.

CLAUDE.md's secrets rule: fixtures are written by hand to the shape of a real response and
contain no real NIP, company or person name, token, bank account or invoice. A scanner cannot
prove a value is invented, so the rule is turned around: every identifying value in a fixture
must come from the allow-lists below, which list values invented for this suite. Adding a
fixture with a new value means adding the value here, deliberately.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from .ksef_fake import KSEF_TOKEN, NIP

FIXTURES = Path(__file__).parent / "fixtures"
FILES = sorted(path for path in FIXTURES.iterdir() if path.is_file())

#: Repdigit NIPs: their checksum is valid by construction, and no tax office issues them.
INVENTED_NIPS = {"0000000000", NIP, "2222222222", "3333333333"}
INVENTED_NAMES = {"Fikcyjny Dostawca B", "Testowa Spółka Odbiorca"}
INVENTED_ACCOUNTS = {"00000000000000000000000000"}
#: Monetary amounts are invented too; listed so that a pasted real invoice stands out.
INVENTED_AMOUNTS = {"100.00", "123.00", 100.0, 123.0, 23.0, 1000.0, 1230.0, 980.5}

NIP_WEIGHTS = (6, 5, 7, 2, 3, 4, 5, 6, 7)


def nip_checksum_ok(nip: str) -> bool:
    total = sum(int(digit) * weight for digit, weight in zip(nip, NIP_WEIGHTS, strict=False))
    return total % 11 == int(nip[9])


def walk(value: Any, key: str = "") -> Iterator[tuple[str, Any]]:
    if isinstance(value, dict):
        for child_key, child in value.items():
            yield from walk(child, child_key)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child, key)
    else:
        yield key, value


def test_fixtures_exist_and_are_only_json_or_xml() -> None:
    assert FILES
    assert {path.suffix for path in FILES} <= {".json", ".xml"}


@pytest.mark.parametrize("nip", sorted(INVENTED_NIPS))
def test_invented_nips_are_structurally_valid(nip: str) -> None:
    """The client and the config flow must accept them, so they must pass the checksum."""
    assert re.fullmatch(r"\d{10}", nip)
    assert nip_checksum_ok(nip)


@pytest.mark.parametrize("path", FILES, ids=lambda path: path.name)
def test_every_ten_digit_run_is_an_invented_nip(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    runs = set(re.findall(r"(?<!\d)\d{10}(?!\d)", text))
    # Timestamps in milliseconds are 13 digits, KSeF numbers start with the seller NIP.
    assert runs <= INVENTED_NIPS, sorted(runs - INVENTED_NIPS)


@pytest.mark.parametrize("path", FILES, ids=lambda path: path.name)
def test_no_account_number_except_invented_ones(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    accounts = set(re.findall(r"(?<![\dA-Z])(?:PL)?\d{26}(?!\d)", text))
    assert accounts <= INVENTED_ACCOUNTS, sorted(accounts - INVENTED_ACCOUNTS)


@pytest.mark.parametrize("path", FILES, ids=lambda path: path.name)
def test_no_real_looking_token(path: Path) -> None:
    """KSeF access, refresh and operation tokens are JWTs; a JWT's header starts `eyJ`."""
    text = path.read_text(encoding="utf-8")
    assert "eyJ" not in text
    assert KSEF_TOKEN not in text
    for match in re.findall(r'"token"\s*:\s*"([^"]*)"', text):
        assert match.startswith("synthetic-"), match


@pytest.mark.parametrize("path", [p for p in FILES if p.suffix == ".json"], ids=lambda p: p.name)
def test_json_names_nips_and_amounts_are_invented(path: Path) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    for key, value in walk(data):
        if value is None:
            continue
        if key == "name":
            assert value in INVENTED_NAMES, value
        if key in {"nip", "value"} and isinstance(value, str) and value.isdigit():
            assert value in INVENTED_NIPS, value
        if key.endswith("Amount"):
            assert value in INVENTED_AMOUNTS, value


@pytest.mark.parametrize("path", [p for p in FILES if p.suffix == ".xml"], ids=lambda p: p.name)
def test_xml_names_and_amounts_are_invented(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    for name in re.findall(r"<(?:\w+:)?Nazwa>([^<]*)<", text):
        assert name in INVENTED_NAMES, name
    for amount in re.findall(r"<(?:\w+:)?P_1[1-5]\w*>([^<]*)<", text):
        assert amount in INVENTED_AMOUNTS, amount
