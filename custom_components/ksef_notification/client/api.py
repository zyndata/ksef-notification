"""KsefClient: the metadata query with paging and the invoice XML fetch.

Contract in docs/ARCHITECTURE.md § Client interface; the API facts in docs/KSEF_API.md
§ Listing invoices and § Fetching one invoice.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import aiohttp

from ..const import (
    API_BASE_URLS,
    MAX_PAGES,
    MAX_QUERY_RANGE,
    MAX_XML_BYTES,
    PAGE_SIZE,
    VALIDATE_PAGE_SIZE,
    VALIDATE_WINDOW,
)
from .auth import Authenticator
from .errors import (
    AuthErrorReason,
    KsefAuthError,
    KsefInvoiceNotFoundError,
    KsefInvoiceNotReadyError,
    KsefMalformedResponseError,
    KsefTemporaryError,
    RateLimitGroup,
)
from .http import Clock, Response, Transport, format_timestamp, parse_timestamp, unexpected

_LOGGER = logging.getLogger(__name__)

_CODE_INVOICE_NOT_FOUND = 21164
_CODE_INVOICE_NOT_READY = 21165
_SECURITY_BLOCKED = "security-service-blocked"

_HTTP_OK = 200
_HTTP_BAD_REQUEST = 400
_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403

#: The value goes into a URL path; KSeF numbers are digits, capitals and hyphens
#: (35 characters, 36 for KSeF 1.0).
_KSEF_NUMBER = re.compile(r"[0-9A-Z]+(?:-[0-9A-Z]+){1,6}")
_KSEF_NUMBER_MAX = 36


@dataclass(frozen=True)
class MetadataResult:
    """One metadata query, all pages together."""

    invoices: tuple[dict[str, Any], ...]  # sorted by permanentStorageDate ascending
    hwm: datetime | None  # permanentStorageHwmDate
    complete: bool  # False when stopped by max_pages


class KsefClient:
    """Everything the integration asks KSeF, for one (environment, NIP, KSeF token)."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        environment: str,
        nip: str,
        ksef_token: str,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._http = Transport(session, API_BASE_URLS[environment], clock or Clock())
        self._auth = Authenticator(self._http, nip, ksef_token)

    def requests_last_hour(self, group: RateLimitGroup) -> int:
        """Requests sent to one limit group over the last 60 minutes (diagnostics)."""
        return self._http.requests_last_hour(group)

    async def query_metadata(
        self, date_from: datetime, *, max_pages: int = MAX_PAGES, page_size: int = PAGE_SIZE
    ) -> MetadataResult:
        """Cost invoices stored permanently since `date_from`, up to now, ascending.

        Pages by moving `from` to the last record's `permanentStorageDate` with `pageOffset`
        0, which works whatever `pageOffset` means and also covers `isTruncated`. Records are
        deduplicated by `ksefNumber` across pages (the boundary record comes back).
        """
        clock = self._http.clock
        if date_from.tzinfo is None:
            raise ValueError("date_from must be timezone-aware")
        if clock.utcnow() - date_from > MAX_QUERY_RANGE:
            raise ValueError("date_from is beyond KSeF's maximum date range")

        records: dict[str, tuple[datetime, dict[str, Any]]] = {}
        hwm: datetime | None = None
        complete = False
        window_from = date_from
        page_offset = 0
        for _ in range(max_pages):
            data = await self._query_page(window_from, page_offset, page_size)
            if hwm is None and data.get("permanentStorageHwmDate") is not None:
                hwm = parse_timestamp(data["permanentStorageHwmDate"])
            page = _page_records(data)
            for stored, record in page:
                records.setdefault(record["ksefNumber"], (stored, record))
            if not data["hasMore"]:
                complete = True
                break
            last_stored = page[-1][0] if page else window_from
            if last_stored > window_from:
                window_from, page_offset = last_stored, 0
            else:
                # A full page sharing one timestamp: moving `from` would not advance.
                page_offset += 1

        ordered = sorted(records.values(), key=lambda item: item[0])
        return MetadataResult(tuple(record for _, record in ordered), hwm, complete)

    async def fetch_invoice_xml(self, ksef_number: str) -> bytes:
        """One invoice's XML exactly as KSeF stores it; at most MAX_XML_BYTES."""
        if len(ksef_number) > _KSEF_NUMBER_MAX or not _KSEF_NUMBER.fullmatch(ksef_number):
            raise KsefMalformedResponseError("not a KSeF number")
        self._http.check_gate(RateLimitGroup.DOWNLOAD)
        response = await self._protected(
            "GET",
            f"/invoices/ksef/{ksef_number}",
            group=RateLimitGroup.DOWNLOAD,
            label="invoice xml",
            accept="application/xml",
            max_bytes=MAX_XML_BYTES,
        )
        if response.status == _HTTP_BAD_REQUEST:
            codes = response.error_codes()
            if _CODE_INVOICE_NOT_READY in codes:
                raise KsefInvoiceNotReadyError
            if _CODE_INVOICE_NOT_FOUND in codes:
                raise KsefInvoiceNotFoundError
        if response.status != _HTTP_OK:
            raise unexpected(response, "invoice xml")
        return response.body

    async def validate(self) -> None:
        """Prove the token works and carries InvoiceRead, then end the session.

        Authentication alone is not proof: a token without InvoiceRead authenticates fine
        and is refused only by a protected call.
        """
        try:
            await self.query_metadata(
                self._http.clock.utcnow() - VALIDATE_WINDOW,
                max_pages=1,
                page_size=VALIDATE_PAGE_SIZE,
            )
        finally:
            await self.async_close()

    async def async_close(self) -> None:
        """Best effort session revocation; the tokens are forgotten either way."""
        await self._auth.revoke()

    # --- internals -------------------------------------------------------------------------

    async def _query_page(
        self, window_from: datetime, page_offset: int, page_size: int
    ) -> dict[str, Any]:
        self._http.check_gate(RateLimitGroup.METADATA)
        response = await self._protected(
            "POST",
            "/invoices/query/metadata",
            group=RateLimitGroup.METADATA,
            label="metadata",
            params={"sortOrder": "Asc", "pageOffset": page_offset, "pageSize": page_size},
            json_body={
                "subjectType": "Subject2",
                "dateRange": {
                    "dateType": "PermanentStorage",
                    "from": format_timestamp(window_from),
                    "restrictToPermanentStorageHwmDate": False,
                },
            },
        )
        if response.status != _HTTP_OK:
            raise unexpected(response, "metadata")
        data = response.json_object()
        if not isinstance(data.get("invoices"), list) or not isinstance(data.get("hasMore"), bool):
            raise KsefMalformedResponseError("metadata: invoices/hasMore missing")
        return data

    async def _protected(self, method: str, path: str, **kwargs: Any) -> Response:
        """A call with the access token; one retry with a new token after a 401."""
        label = kwargs["label"]
        token = await self._auth.ensure_access_token()
        response = await self._http.request(method, path, bearer=token, **kwargs)
        if response.status == _HTTP_UNAUTHORIZED:
            _LOGGER.debug("%s: access token refused, getting a new one", label)
            self._auth.invalidate_access_token()
            token = await self._auth.ensure_access_token()
            response = await self._http.request(method, path, bearer=token, **kwargs)
            if response.status == _HTTP_UNAUTHORIZED:
                self._auth.invalidate_access_token()
                raise KsefTemporaryError(f"{label}: HTTP 401 with a fresh token")
        if response.status == _HTTP_FORBIDDEN:
            if response.reason_code() == _SECURITY_BLOCKED:
                raise KsefAuthError(AuthErrorReason.BLOCKED, _HTTP_FORBIDDEN)
            raise KsefAuthError(AuthErrorReason.NO_PERMISSION, _HTTP_FORBIDDEN)
        return response


def _page_records(data: dict[str, Any]) -> list[tuple[datetime, dict[str, Any]]]:
    """The page's records with their parsed storage date; the two keys the tracker relies on
    are checked here, everything else is the core's business."""
    page = []
    for record in data["invoices"]:
        if not isinstance(record, dict) or not isinstance(record.get("ksefNumber"), str):
            raise KsefMalformedResponseError("metadata: record without ksefNumber")
        page.append((parse_timestamp(record.get("permanentStorageDate")), record))
    return page
