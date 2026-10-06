"""The metadata query, the XML fetch, transport failures, rate gates and the request budget.

docs/KSEF_API.md § Listing invoices, § Fetching one invoice, § Rate limits;
docs/ARCHITECTURE.md § Client interface, § Resource and request budget.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone

import aiohttp
import pytest

from custom_components.ksef_notification.client import (
    AuthErrorReason,
    KsefAuthError,
    KsefClient,
    KsefInvoiceNotFoundError,
    KsefInvoiceNotReadyError,
    KsefMalformedResponseError,
    KsefRateLimitError,
    KsefTemporaryError,
    RateLimitGroup,
)
from custom_components.ksef_notification.const import (
    API_BASE_URLS,
    ENV_TEST,
    ENVIRONMENTS,
    MAX_PAGES,
    MAX_XML_BYTES,
)

from .ksef_fake import (
    AUTH_ROUTES,
    BASE_URL,
    FIXTURES,
    START,
    FakeClock,
    FakeKsef,
    Reply,
    error_reply,
    fixture,
    invoice,
    metadata_reply,
    stamp,
)

SINCE = START - timedelta(minutes=16)
XML = (FIXTURES / "invoice_fa3.xml").read_bytes()
NUMBER = fixture("metadata_page.json")["invoices"][0]["ksefNumber"]


def test_test_environment_url_matches_the_fake() -> None:
    assert API_BASE_URLS[ENV_TEST] == str(BASE_URL)
    assert set(API_BASE_URLS) == set(ENVIRONMENTS)
    assert all(url.startswith("https://") and url.endswith("/v2") for url in API_BASE_URLS.values())


# --- the metadata query: request shape --------------------------------------------------------


async def test_request_shape(client: KsefClient, ksef: FakeKsef) -> None:
    warsaw_summer = timezone(timedelta(hours=2))
    await client.query_metadata(datetime(2026, 10, 6, 9, 44, 0, 123456, tzinfo=warsaw_summer))

    call = ksef.last("metadata")
    assert call.query == {"sortOrder": "Asc", "pageOffset": "0", "pageSize": "250"}
    assert call.body == {
        "subjectType": "Subject2",
        "dateRange": {
            "dateType": "PermanentStorage",
            "from": "2026-10-06T07:44:00.123456+00:00",
            "restrictToPermanentStorageHwmDate": False,
        },
    }
    assert call.headers["Accept"] == "application/json"


async def test_naive_or_too_old_date_from_is_refused_before_any_request(
    client: KsefClient, ksef: FakeKsef
) -> None:
    with pytest.raises(ValueError, match="aware"):
        await client.query_metadata(datetime(2026, 10, 6, 8, 0))  # noqa: DTZ001 — the point
    with pytest.raises(ValueError, match="range"):
        await client.query_metadata(START - timedelta(days=100, seconds=1))
    assert ksef.calls == []


# --- the metadata query: response -------------------------------------------------------------


async def test_result_sorted_ascending_with_the_hwm(client: KsefClient, ksef: FakeKsef) -> None:
    ksef.script("metadata", Reply(json=fixture("metadata_page.json")))

    result = await client.query_metadata(SINCE)

    page = fixture("metadata_page.json")["invoices"]
    assert result.invoices == (page[1], page[0])  # 07:31:00 before 07:41:12
    assert result.hwm == datetime(2026, 10, 6, 7, 58, tzinfo=UTC)
    assert result.complete is True
    assert ksef.count("metadata") == 1


async def test_unknown_properties_are_ignored_and_passed_through(
    client: KsefClient, ksef: FakeKsef
) -> None:
    body = fixture("metadata_page.json")
    body["someFutureField"] = {"x": 1}
    body["invoices"][0]["someFutureField"] = 1
    ksef.script("metadata", Reply(json=body))

    result = await client.query_metadata(SINCE)

    assert result.invoices[1]["someFutureField"] == 1


async def test_missing_hwm_is_none(client: KsefClient, ksef: FakeKsef) -> None:
    ksef.script("metadata", metadata_reply([], hwm=None))

    result = await client.query_metadata(SINCE)

    assert result.hwm is None
    assert result.invoices == ()


@pytest.mark.parametrize(
    "reply",
    [
        Reply(body=b"<html>gateway</html>"),
        Reply(json=[]),
        Reply(json={"invoices": []}),
        Reply(json={"invoices": {}, "hasMore": False}),
        Reply(json={"invoices": [{"permanentStorageDate": stamp(START)}], "hasMore": False}),
        Reply(
            json={
                "invoices": [{"ksefNumber": "1-2", "permanentStorageDate": "x"}],
                "hasMore": False,
            }
        ),
        Reply(
            json={
                "invoices": [{"ksefNumber": "1-2", "permanentStorageDate": "2026-10-06T07:00:00"}],
                "hasMore": False,
            }
        ),
        Reply(json={"invoices": [], "hasMore": False, "permanentStorageHwmDate": 5}),
    ],
)
async def test_malformed_metadata(client: KsefClient, ksef: FakeKsef, reply: Reply) -> None:
    ksef.script("metadata", reply)

    with pytest.raises(KsefMalformedResponseError):
        await client.query_metadata(SINCE)


async def test_rejected_query_is_temporary(client: KsefClient, ksef: FakeKsef) -> None:
    ksef.script("metadata", error_reply(21405, "synthetic validation message"))

    with pytest.raises(KsefTemporaryError, match="21405"):
        await client.query_metadata(SINCE)


# --- paging -----------------------------------------------------------------------------------


def _records(first: int, count: int, start: datetime) -> list[dict]:
    return [invoice(first + i, start + timedelta(seconds=i)) for i in range(count)]


async def test_paging_moves_from_and_deduplicates_the_boundary(
    client: KsefClient, ksef: FakeKsef
) -> None:
    t0 = START - timedelta(minutes=10)
    page1 = _records(1, 250, t0)
    page2 = [page1[-1], *_records(251, 10, t0 + timedelta(seconds=250))]
    ksef.script("metadata", metadata_reply(page1, has_more=True), metadata_reply(page2))

    result = await client.query_metadata(SINCE)

    first, second = (call for call in ksef.calls if call.route == "metadata")
    assert second.body["dateRange"]["from"] == page1[-1]["permanentStorageDate"][:26] + "+00:00"
    assert second.query["pageOffset"] == "0"
    assert first.body["dateRange"]["from"] != second.body["dateRange"]["from"]
    assert [r["ksefNumber"] for r in result.invoices] == [
        r["ksefNumber"] for r in [*page1, *page2[1:]]
    ]
    assert result.complete is True


async def test_truncated_result_continues_the_same_way(client: KsefClient, ksef: FakeKsef) -> None:
    t0 = START - timedelta(minutes=10)
    page1 = _records(1, 250, t0)
    ksef.script(
        "metadata",
        metadata_reply(page1, has_more=True, truncated=True),
        metadata_reply([page1[-1]]),
    )

    result = await client.query_metadata(SINCE)

    assert len(result.invoices) == 250
    assert ksef.last("metadata").query["pageOffset"] == "0"
    assert result.complete is True


async def test_max_pages_stops_early_and_says_so(client: KsefClient, ksef: FakeKsef) -> None:
    t0 = START - timedelta(minutes=10)
    ksef.script(
        "metadata",
        *(
            metadata_reply(
                _records(1 + 250 * n, 250, t0 + timedelta(seconds=250 * n)), has_more=True
            )
            for n in range(5)
        ),
    )

    result = await client.query_metadata(SINCE)

    assert ksef.count("metadata") == MAX_PAGES
    assert result.complete is False
    assert len(result.invoices) == 250 * MAX_PAGES


async def test_full_page_on_one_timestamp_advances_the_page_index(
    client: KsefClient, ksef: FakeKsef
) -> None:
    same = SINCE
    page1 = [invoice(n, same) for n in range(1, 251)]
    page2 = [invoice(n, same) for n in range(251, 255)]
    ksef.script("metadata", metadata_reply(page1, has_more=True), metadata_reply(page2))

    result = await client.query_metadata(SINCE)

    assert [call.query["pageOffset"] for call in ksef.calls if call.route == "metadata"] == [
        "0",
        "1",
    ]
    assert len(result.invoices) == 254


# --- the XML fetch ----------------------------------------------------------------------------


async def test_fetch_returns_the_bytes(client: KsefClient, ksef: FakeKsef) -> None:
    body = await client.fetch_invoice_xml(NUMBER)

    assert body == XML
    call = ksef.last("xml")
    assert call.path == f"/invoices/ksef/{NUMBER}"
    assert call.headers["Accept"] == "application/xml"
    assert call.bearer == "synthetic-access-token-1"


@pytest.mark.parametrize("legacy", [False, True])
async def test_not_ready(client: KsefClient, ksef: FakeKsef, legacy: bool) -> None:
    ksef.script("xml", error_reply(21165, legacy=legacy))

    with pytest.raises(KsefInvoiceNotReadyError):
        await client.fetch_invoice_xml(NUMBER)


@pytest.mark.parametrize("legacy", [False, True])
async def test_not_found(client: KsefClient, ksef: FakeKsef, legacy: bool) -> None:
    ksef.script("xml", error_reply(21164, legacy=legacy))

    with pytest.raises(KsefInvoiceNotFoundError):
        await client.fetch_invoice_xml(NUMBER)


async def test_fixture_error_bodies_carry_their_codes(client: KsefClient, ksef: FakeKsef) -> None:
    ksef.script("xml", Reply(status=400, json=fixture("error_400_problem.json")))
    with pytest.raises(KsefInvoiceNotReadyError):
        await client.fetch_invoice_xml(NUMBER)

    ksef.script("xml", Reply(status=400, json=fixture("error_400_legacy.json")))
    with pytest.raises(KsefInvoiceNotFoundError):
        await client.fetch_invoice_xml(NUMBER)


@pytest.mark.parametrize(
    "number", ["", "../auth/sessions/current", "2222222222-20261006-0000?x=1", "a" * 40, "ABC"]
)
async def test_suspicious_ksef_number_is_never_requested(
    client: KsefClient, ksef: FakeKsef, number: str
) -> None:
    with pytest.raises(KsefMalformedResponseError):
        await client.fetch_invoice_xml(number)
    assert ksef.calls == []


async def test_oversize_body_by_content_length(client: KsefClient, ksef: FakeKsef) -> None:
    ksef.script("xml", Reply(body=XML, headers={"Content-Length": str(MAX_XML_BYTES + 1)}))

    with pytest.raises(KsefMalformedResponseError):
        await client.fetch_invoice_xml(NUMBER)


async def test_oversize_body_while_streaming(client: KsefClient, ksef: FakeKsef) -> None:
    ksef.script("xml", Reply(body=b"<a>" + b"x" * MAX_XML_BYTES + b"</a>"))

    with pytest.raises(KsefMalformedResponseError):
        await client.fetch_invoice_xml(NUMBER)


async def test_body_at_the_cap_is_accepted(client: KsefClient, ksef: FakeKsef) -> None:
    ksef.script("xml", Reply(body=b"x" * MAX_XML_BYTES))

    assert len(await client.fetch_invoice_xml(NUMBER)) == MAX_XML_BYTES


async def test_downloads_are_paced(client: KsefClient, ksef: FakeKsef, clock: FakeClock) -> None:
    await client.fetch_invoice_xml(NUMBER)
    sleeps_after_auth = len(clock.sleeps)
    await client.fetch_invoice_xml(NUMBER)
    await client.fetch_invoice_xml(NUMBER)

    assert clock.sleeps[sleeps_after_auth:] == [pytest.approx(0.25), pytest.approx(0.25)]


async def test_a_download_repeated_after_401_is_paced_too(
    client: KsefClient, ksef: FakeKsef, clock: FakeClock
) -> None:
    await client.fetch_invoice_xml(NUMBER)
    ksef.script("xml", Reply(status=401), Reply(body=XML))
    sleeps = len(clock.sleeps)
    clock.advance(timedelta(seconds=1))

    await client.fetch_invoice_xml(NUMBER)

    assert ksef.routes()[-3:] == ["xml", "refresh", "xml"]
    assert clock.sleeps[sleeps:] == [pytest.approx(0.25)]


async def test_no_pause_when_downloads_are_already_apart(
    client: KsefClient, ksef: FakeKsef, clock: FakeClock
) -> None:
    await client.fetch_invoice_xml(NUMBER)
    sleeps = len(clock.sleeps)
    clock.advance(timedelta(seconds=1))
    await client.fetch_invoice_xml(NUMBER)

    assert len(clock.sleeps) == sleeps


# --- transport failures -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [
        Reply(status=500),
        Reply(status=502, body=b"<html>bad gateway</html>"),
        Reply(status=503),
        Reply(exc=TimeoutError()),
        Reply(exc=aiohttp.ClientConnectionError()),
        Reply(exc=aiohttp.ClientPayloadError()),
        Reply(status=302, headers={"Location": "https://example.invalid/"}),
    ],
)
async def test_outages_are_temporary(client: KsefClient, ksef: FakeKsef, failure: Reply) -> None:
    ksef.script("metadata", failure)

    with pytest.raises(KsefTemporaryError):
        await client.query_metadata(SINCE)
    assert ksef.count("metadata") == 1  # never retried in place


# --- rate limits ------------------------------------------------------------------------------


async def test_429_blocks_the_group_until_retry_after(
    client: KsefClient, ksef: FakeKsef, clock: FakeClock
) -> None:
    ksef.script(
        "metadata",
        Reply(status=429, json=fixture("error_429.json"), headers={"Retry-After": "120"}),
        metadata_reply([]),
    )

    with pytest.raises(KsefRateLimitError) as caught:
        await client.query_metadata(SINCE)
    assert caught.value.retry_after_s == 120
    assert caught.value.group is RateLimitGroup.METADATA

    clock.advance(timedelta(seconds=119))
    with pytest.raises(KsefRateLimitError) as caught:
        await client.query_metadata(SINCE)
    assert caught.value.retry_after_s == pytest.approx(1)
    assert ksef.count("metadata") == 1  # refused locally, KSeF never asked

    clock.advance(timedelta(seconds=1))
    await client.query_metadata(SINCE)
    assert ksef.count("metadata") == 2


async def test_a_blocked_group_refuses_before_touching_tokens(
    client: KsefClient, ksef: FakeKsef, clock: FakeClock
) -> None:
    ksef.script("metadata", Reply(status=429, headers={"Retry-After": "1800"}))
    with pytest.raises(KsefRateLimitError):
        await client.query_metadata(SINCE)
    clock.advance(timedelta(minutes=15))  # the access token has expired meanwhile
    calls = len(ksef.calls)

    with pytest.raises(KsefRateLimitError):
        await client.query_metadata(SINCE)
    assert len(ksef.calls) == calls


async def test_download_limit_leaves_metadata_alone(client: KsefClient, ksef: FakeKsef) -> None:
    ksef.script("xml", Reply(status=429, headers={"Retry-After": "30"}))

    with pytest.raises(KsefRateLimitError) as caught:
        await client.fetch_invoice_xml(NUMBER)
    assert caught.value.group is RateLimitGroup.DOWNLOAD
    with pytest.raises(KsefRateLimitError):
        await client.fetch_invoice_xml(NUMBER)
    assert ksef.count("xml") == 1

    await client.query_metadata(SINCE)
    assert ksef.count("metadata") == 1


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, 60),
        ("abc", 60),
        ("0", 1),
        ("-5", 1),
        ("2.5", 2.5),
        ("nan", 60),
        ("inf", 60),
        ("999999999", 86400),
        ("Tue, 06 Oct 2026 08:05:00 GMT", 300),
        ("Tue, 06 Oct 2026 08:05:00", 60),  # no zone: not a valid HTTP date
    ],
)
async def test_retry_after_parsing(
    client: KsefClient, ksef: FakeKsef, header: str | None, expected: float
) -> None:
    ksef.script("metadata", Reply(status=429, headers={"Retry-After": header} if header else {}))

    with pytest.raises(KsefRateLimitError) as caught:
        await client.query_metadata(SINCE)
    assert caught.value.retry_after_s == pytest.approx(expected, abs=1)


# --- the request budget -----------------------------------------------------------------------


async def test_one_cycle_stays_inside_the_budget(
    client: KsefClient, ksef: FakeKsef, clock: FakeClock
) -> None:
    """A first cycle at start-up: full authentication, one query, three downloads
    (docs/ARCHITECTURE.md § Resource and request budget, per cycle)."""
    ksef.script("metadata", Reply(json=fixture("metadata_page.json")))

    result = await client.query_metadata(SINCE)
    for record in result.invoices:
        await client.fetch_invoice_xml(record["ksefNumber"])
    await client.fetch_invoice_xml(NUMBER)

    assert ksef.count("metadata") == 1
    assert ksef.count("xml") == 3
    assert sum(ksef.count(route) for route in AUTH_ROUTES) == 5
    assert client.requests_last_hour(RateLimitGroup.METADATA) == 1
    assert client.requests_last_hour(RateLimitGroup.DOWNLOAD) == 3
    assert client.requests_last_hour(RateLimitGroup.AUTH) == 5
    xml_calls = [i for i, call in enumerate(ksef.calls) if call.route == "xml"]
    assert len(clock.sleeps) >= len(xml_calls) - 1


async def test_a_day_at_the_minimum_interval(
    client: KsefClient, ksef: FakeKsef, clock: FakeClock
) -> None:
    """96 cycles 15 minutes apart: one metadata query and one refresh each, a full
    authentication only at the start, never more than 4 metadata queries an hour."""
    peak = 0
    for _ in range(96):
        await client.query_metadata(clock.utcnow() - timedelta(minutes=16))
        peak = max(peak, client.requests_last_hour(RateLimitGroup.METADATA))
        clock.advance(timedelta(minutes=15))

    assert ksef.count("metadata") == 96
    assert ksef.count("redeem") == 1
    assert ksef.count("refresh") == 95
    assert peak <= 4


async def test_request_counters_forget_after_an_hour(
    client: KsefClient, ksef: FakeKsef, clock: FakeClock
) -> None:
    await client.query_metadata(SINCE)
    clock.advance(timedelta(minutes=59))
    assert client.requests_last_hour(RateLimitGroup.METADATA) == 1
    clock.advance(timedelta(minutes=1))
    assert client.requests_last_hour(RateLimitGroup.METADATA) == 0


# --- validate (the config flow's check) -------------------------------------------------------


async def test_validate_authenticates_queries_and_revokes(
    client: KsefClient, ksef: FakeKsef
) -> None:
    await client.validate()

    assert ksef.routes() == [*AUTH_ROUTES, "metadata", "revoke"]
    query = ksef.last("metadata")
    assert query.query["pageSize"] == "10"
    assert query.body["dateRange"]["from"] == "2026-10-06T07:00:00.000000+00:00"


async def test_validate_reports_a_token_without_invoice_read(
    client: KsefClient, ksef: FakeKsef
) -> None:
    ksef.script("metadata", Reply(status=403, json=fixture("error_403_missing_permissions.json")))

    with pytest.raises(KsefAuthError) as caught:
        await client.validate()

    assert caught.value.reason is AuthErrorReason.NO_PERMISSION
    assert ksef.routes()[-1] == "revoke"  # the session is still ended


async def test_validate_reports_a_bad_token_without_a_session_to_end(
    client: KsefClient, ksef: FakeKsef
) -> None:
    ksef.script("status", Reply(json=fixture("auth_status_token_revoked.json")))

    with pytest.raises(KsefAuthError) as caught:
        await client.validate()

    assert caught.value.reason is AuthErrorReason.TOKEN_INVALID
    assert "revoke" not in ksef.routes()


async def test_fixture_bodies_round_trip_as_json() -> None:
    """Guard against a fixture that is not valid JSON being skipped by the scanner."""
    for path in sorted(FIXTURES.glob("*.json")):
        json.loads(path.read_text(encoding="utf-8"))
