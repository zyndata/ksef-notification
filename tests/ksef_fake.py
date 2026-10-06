"""A scripted KSeF behind Home Assistant's aiohttp mocker, and a clock the tests drive.

The client under test gets a real `aiohttp.ClientSession` whose transport is replaced by
`AiohttpClientMocker`; every request lands in `FakeKsef._dispatch`, which records it and
answers from a per-route script. A script is a list of replies consumed in order, the last
one repeating. A reply is a `Reply` or a callable that builds one from the recorded call.
"""

from __future__ import annotations

import base64
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = URL("https://api-test.ksef.mf.gov.pl/v2")

#: The fake clock's start; the fixtures' timestamps are written relative to it.
START = datetime(2026, 10, 6, 8, 0, tzinfo=UTC)

#: Invented, structurally valid NIP of the configured company (see test_fixtures.py).
NIP = "1111111111"
KSEF_TOKEN = "synthetic-ksef-token-0000"
KEY_ID = "c3ludGhldGljLWtleS1pZC0wMDAwMDAwMDAwMDAwMDA="

ROUTES: dict[str, tuple[str, re.Pattern[str]]] = {
    "keys": ("GET", re.compile(r"/security/public-key-certificates")),
    "challenge": ("POST", re.compile(r"/auth/challenge")),
    "init": ("POST", re.compile(r"/auth/ksef-token")),
    "redeem": ("POST", re.compile(r"/auth/token/redeem")),
    "refresh": ("POST", re.compile(r"/auth/token/refresh")),
    "status": ("GET", re.compile(r"/auth/[^/]+")),
    "metadata": ("POST", re.compile(r"/invoices/query/metadata")),
    "xml": ("GET", re.compile(r"/invoices/ksef/[^/]+")),
    "revoke": ("DELETE", re.compile(r"/auth/sessions/current")),
}

AUTH_ROUTES = ("keys", "challenge", "init", "status", "redeem")


def fixture(name: str) -> Any:
    """A JSON fixture, freshly loaded (tests may modify it)."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def stamp(value: datetime) -> str:
    """KSeF's own spelling: seven fractional digits and an offset."""
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f0+00:00")


class FakeClock:
    """utcnow/monotonic that move only when a test (or a client's sleep) moves them."""

    def __init__(self) -> None:
        self.offset = 0.0
        self.sleeps: list[float] = []

    def utcnow(self) -> datetime:
        return START + timedelta(seconds=self.offset)

    def monotonic(self) -> float:
        return 1000.0 + self.offset

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.offset += seconds

    def advance(self, delta: timedelta) -> None:
        self.offset += delta.total_seconds()


@dataclass(frozen=True)
class Reply:
    status: int = 200
    json: Any = None
    body: bytes | None = None
    headers: dict[str, str] | None = None
    exc: BaseException | None = None


@dataclass(frozen=True)
class Call:
    route: str
    method: str
    path: str
    query: dict[str, str]
    body: Any
    headers: dict[str, str] = field(repr=False)

    @property
    def bearer(self) -> str | None:
        value = self.headers.get("Authorization")
        return value.removeprefix("Bearer ") if value else None


ReplySpec = Reply | Callable[[Call], Reply]


@cache
def signing_material() -> tuple[rsa.RSAPrivateKey, bytes]:
    """One RSA key for the whole run (generating one takes a noticeable moment)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Synthetic test key")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(1)
        .not_valid_before(START - timedelta(days=365))
        .not_valid_after(START + timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    return key, cert.public_bytes(serialization.Encoding.DER)


def key_entry(
    *,
    key_id: str = KEY_ID,
    valid_from: datetime = START - timedelta(days=30),
    valid_to: datetime = START + timedelta(days=30),
    usage: tuple[str, ...] = ("KsefTokenEncryption",),
) -> dict[str, Any]:
    _, der = signing_material()
    return {
        "certificate": base64.b64encode(der).decode(),
        "certificateId": "c3ludGhldGljLWNlcnQ=",
        "publicKeyId": key_id,
        "validFrom": stamp(valid_from),
        "validTo": stamp(valid_to),
        "usage": list(usage),
    }


def decrypt_token(encrypted_b64: str) -> str:
    key, _ = signing_material()
    return key.decrypt(
        base64.b64decode(encrypted_b64),
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    ).decode()


def status_reply(code: int, *details: str) -> Reply:
    body = fixture("auth_status_success.json")
    body["status"] = {"code": code, "description": "synthetic", "details": list(details)}
    return Reply(json=body)


def error_reply(code: int, *details: str, status: int = 400, legacy: bool = False) -> Reply:
    if legacy:
        body = fixture("error_400_legacy.json")
        body["exception"]["exceptionDetailList"] = [
            {"exceptionCode": code, "exceptionDescription": "synthetic", "details": list(details)}
        ]
    else:
        body = fixture("error_400_problem.json")
        body["errors"] = [{"code": code, "description": "synthetic", "details": list(details)}]
    return Reply(status=status, json=body)


def token_body(clock: FakeClock, n: int | str, *, refresh: bool = True) -> dict[str, Any]:
    now = clock.utcnow()
    body: dict[str, Any] = {
        "accessToken": {
            "token": f"synthetic-access-token-{n}",
            "validUntil": stamp(now + timedelta(minutes=15)),
        }
    }
    if refresh:
        body["refreshToken"] = {
            "token": f"synthetic-refresh-token-{n}",
            "validUntil": stamp(now + timedelta(days=7)),
        }
    return body


def metadata_reply(
    invoices: list[dict[str, Any]],
    *,
    has_more: bool = False,
    truncated: bool = False,
    hwm: str | None = "2026-10-06T07:58:00.0000000+00:00",
) -> Reply:
    body: dict[str, Any] = {"hasMore": has_more, "isTruncated": truncated, "invoices": invoices}
    if hwm is not None:
        body["permanentStorageHwmDate"] = hwm
    return Reply(json=body)


def invoice(number: int, stored: datetime) -> dict[str, Any]:
    """A metadata record in the fixture's shape with its own KSeF number and storage date."""
    record = fixture("metadata_page.json")["invoices"][0]
    record["ksefNumber"] = f"3333333333-20261006-{number:012X}-{number % 256:02X}"
    record["permanentStorageDate"] = stamp(stored)
    return record


class FakeKsef:
    """The scripted service. `calls` holds every request in order."""

    def __init__(self, mocker: AiohttpClientMocker, clock: FakeClock) -> None:
        self._mocker = mocker
        self.clock = clock
        self.calls: list[Call] = []
        self._scripts: dict[str, list[ReplySpec]] = {}
        self._issued = 0
        for method in ("get", "post", "delete"):
            mocker.request(method, re.compile(".*"), side_effect=self._dispatch)
        self.script("keys", Reply(json=[key_entry()]))
        self.script("challenge", self._challenge)
        self.script("init", Reply(status=202, json=fixture("auth_init.json")))
        self.script("status", Reply(json=fixture("auth_status_success.json")))
        self.script("redeem", self._redeem)
        self.script("refresh", self._refresh)
        self.script("metadata", metadata_reply([]))
        self.script("xml", Reply(body=(FIXTURES / "invoice_fa3.xml").read_bytes()))
        self.script("revoke", Reply(status=204))

    def script(self, route: str, *replies: ReplySpec) -> None:
        self._scripts[route] = list(replies)

    def routes(self) -> list[str]:
        return [call.route for call in self.calls]

    def count(self, route: str) -> int:
        return sum(1 for call in self.calls if call.route == route)

    def last(self, route: str) -> Call:
        return [call for call in self.calls if call.route == route][-1]

    def _challenge(self, _: Call) -> Reply:
        body = fixture("auth_challenge.json")
        body["timestampMs"] = int(self.clock.utcnow().timestamp() * 1000)
        return Reply(json=body)

    def _redeem(self, _: Call) -> Reply:
        self._issued += 1
        return Reply(json=token_body(self.clock, self._issued))

    def _refresh(self, _: Call) -> Reply:
        return Reply(json=token_body(self.clock, f"r{self.count('refresh')}", refresh=False))

    async def _dispatch(self, method: str, url: URL, data: Any) -> AiohttpClientMockResponse:
        headers = dict(self._mocker.mock_calls[-1][3] or {})
        assert str(url).startswith(str(BASE_URL)), url
        path = url.path.removeprefix(BASE_URL.path)
        route = next(
            name
            for name, (route_method, pattern) in ROUTES.items()
            if route_method == method.upper() and pattern.fullmatch(path)
        )
        call = Call(route, method.upper(), path, dict(url.query), data, headers)
        self.calls.append(call)
        script = self._scripts[route]
        spec = script.pop(0) if len(script) > 1 else script[0]
        reply = spec(call) if callable(spec) else spec
        return AiohttpClientMockResponse(
            method,
            url,
            status=reply.status,
            json=reply.json,
            response=reply.body,
            headers=reply.headers,
            exc=reply.exc,
        )
