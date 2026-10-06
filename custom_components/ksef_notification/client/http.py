"""One request to KSeF: headers, body cap, error bodies, rate gates and request counters.

Shared by `auth.py` and `api.py`. Anything that is not an answer the caller can interpret
becomes a typed error here: connection errors and timeouts, 5xx, 429 (which also closes the
group's gate until `Retry-After` has passed), and bodies over their cap.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import aiohttp

from ..const import (
    DEFAULT_RETRY_AFTER,
    MAX_JSON_BYTES,
    REQUEST_TIMEOUT,
    XML_CHUNK_BYTES,
    XML_FETCH_GAP,
)
from .errors import (
    KsefMalformedResponseError,
    KsefRateLimitError,
    KsefTemporaryError,
    RateLimitGroup,
)

_LOGGER = logging.getLogger(__name__)

_HOUR_S = 3600.0
_MAX_RETRY_AFTER_S = 24 * _HOUR_S
_HTTP_SERVER_ERROR = 500
_HTTP_TOO_MANY_REQUESTS = 429


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Clock:
    """Every way the client reads time or waits, replaceable as a whole in tests."""

    utcnow: Callable[[], datetime] = _utcnow
    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep


def parse_timestamp(value: object) -> datetime:
    """A KSeF date-time (up to 7 fractional digits, always with an offset) as aware UTC."""
    if not isinstance(value, str):
        raise KsefMalformedResponseError("timestamp is not a string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as err:
        raise KsefMalformedResponseError("unparsable timestamp") from err
    if parsed.tzinfo is None:
        raise KsefMalformedResponseError("timestamp without an offset")
    return parsed.astimezone(UTC)


def format_timestamp(value: datetime) -> str:
    """UTC with an explicit offset: KSeF reads a timestamp without one as Warsaw time."""
    if value.tzinfo is None:
        raise ValueError("naive datetime")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class ApiError:
    """One entry of a 400 body: a KSeF exception code and its detail strings."""

    code: int
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class Response:
    """A complete answer below 500 other than 429; interpreting it is the caller's job."""

    status: int
    headers: Mapping[str, str]
    body: bytes = field(repr=False)

    def json(self) -> Any:
        try:
            return json.loads(self.body)
        except ValueError as err:
            raise KsefMalformedResponseError(f"HTTP {self.status}: body is not JSON") from err

    def json_object(self) -> dict[str, Any]:
        data = self.json()
        if not isinstance(data, dict):
            raise KsefMalformedResponseError(f"HTTP {self.status}: body is not an object")
        return data

    def errors(self) -> tuple[ApiError, ...]:
        """The KSeF exception codes of an error body, in either documented shape.

        Problem details (`X-Error-Format: problem-details`): `errors[].code/details`. The
        deprecated legacy shape: `exception.exceptionDetailList[].exceptionCode/details`.
        """
        try:
            data = json.loads(self.body)
        except ValueError:
            return ()
        if not isinstance(data, dict):
            return ()
        entries = data.get("errors")
        code_key = "code"
        if not isinstance(entries, list):
            exception = data.get("exception")
            entries = exception.get("exceptionDetailList") if isinstance(exception, dict) else None
            code_key = "exceptionCode"
        if not isinstance(entries, list):
            return ()
        found = []
        for entry in entries:
            if not isinstance(entry, dict) or not isinstance(entry.get(code_key), int):
                continue
            details = entry.get("details")
            found.append(
                ApiError(
                    entry[code_key],
                    tuple(d for d in details if isinstance(d, str))
                    if isinstance(details, list)
                    else (),
                )
            )
        return tuple(found)

    def error_codes(self) -> set[int]:
        return {error.code for error in self.errors()}

    def reason_code(self) -> str | None:
        """`reasonCode` of a 403 problem-details body."""
        try:
            data = json.loads(self.body)
        except ValueError:
            return None
        reason = data.get("reasonCode") if isinstance(data, dict) else None
        return reason if isinstance(reason, str) else None


def unexpected(response: Response, label: str) -> KsefTemporaryError:
    """An answer outside the documented ones for this call, KSeF codes included for the log."""
    codes = ",".join(str(code) for code in sorted(response.error_codes())) or "-"
    return KsefTemporaryError(f"{label}: unexpected HTTP {response.status} (codes {codes})")


class Transport:
    """Sends requests to one KSeF environment through Home Assistant's shared session."""

    def __init__(self, session: aiohttp.ClientSession, base_url: str, clock: Clock) -> None:
        self._session = session
        self._base_url = base_url
        self.clock = clock
        self._blocked_until: dict[RateLimitGroup, float] = {}
        # KSeF allows 8 downloads per second; sequential ones would otherwise exceed it.
        self._min_gap = {RateLimitGroup.DOWNLOAD: XML_FETCH_GAP.total_seconds()}
        self._sent: dict[RateLimitGroup, deque[float]] = {
            group: deque() for group in RateLimitGroup
        }

    def check_gate(self, group: RateLimitGroup) -> None:
        """Refuse a call to a group KSeF has blocked, without asking KSeF."""
        until = self._blocked_until.get(group)
        if until is None:
            return
        remaining = until - self.clock.monotonic()
        if remaining > 0:
            raise KsefRateLimitError(remaining, group)
        del self._blocked_until[group]

    def requests_last_hour(self, group: RateLimitGroup) -> int:
        sent = self._sent[group]
        horizon = self.clock.monotonic() - _HOUR_S
        while sent and sent[0] <= horizon:
            sent.popleft()
        return len(sent)

    async def request(  # noqa: PLR0913 — keyword-only options of the one request helper
        self,
        method: str,
        path: str,
        *,
        group: RateLimitGroup,
        label: str,
        params: Mapping[str, str | int] | None = None,
        json_body: Any = None,
        bearer: str | None = None,
        accept: str = "application/json",
        max_bytes: int = MAX_JSON_BYTES,
        time_limit: timedelta = REQUEST_TIMEOUT,
    ) -> Response:
        """Send one request. `label` names the call in logs and errors instead of the path,
        which can contain a KSeF number."""
        self.check_gate(group)
        headers = {"Accept": accept, "X-Error-Format": "problem-details"}
        if bearer is not None:
            headers["Authorization"] = f"Bearer {bearer}"
        await self._pace(group)
        self._sent[group].append(self.clock.monotonic())
        self.requests_last_hour(group)
        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                params=params,
                json=json_body,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=time_limit.total_seconds()),
                allow_redirects=False,
            ) as resp:
                status = resp.status
                response_headers = {key.lower(): value for key, value in resp.headers.items()}
                body = await _read_capped(resp, max_bytes, label)
        except TimeoutError as err:
            raise KsefTemporaryError(f"{label}: timeout") from err
        except aiohttp.ClientError as err:
            raise KsefTemporaryError(f"{label}: {type(err).__name__}") from err

        _LOGGER.debug("%s %s: HTTP %s, %d bytes", method, label, status, len(body))
        if status == _HTTP_TOO_MANY_REQUESTS:
            retry_after = self._retry_after(response_headers.get("retry-after"))
            self._blocked_until[group] = self.clock.monotonic() + retry_after
            _LOGGER.warning("KSeF rate limit hit (%s); blocked for %.0f s", group, retry_after)
            raise KsefRateLimitError(retry_after, group)
        if status >= _HTTP_SERVER_ERROR:
            raise KsefTemporaryError(f"{label}: HTTP {status}")
        return Response(status, response_headers, body)

    async def _pace(self, group: RateLimitGroup) -> None:
        gap = self._min_gap.get(group)
        sent = self._sent[group]
        if gap is None or not sent:
            return
        wait = sent[-1] + gap - self.clock.monotonic()
        if wait > 0:
            await self.clock.sleep(wait)

    def _retry_after(self, value: str | None) -> float:
        """`Retry-After` in seconds (KSeF sends seconds; an HTTP date is accepted too)."""
        default = DEFAULT_RETRY_AFTER.total_seconds()
        if value is None:
            return default
        try:
            seconds = float(value)
        except ValueError:
            try:
                when = parsedate_to_datetime(value)
            except (TypeError, ValueError):
                return default
            if when.tzinfo is None:
                return default
            seconds = (when - self.clock.utcnow()).total_seconds()
        if not math.isfinite(seconds):
            return default
        return min(max(seconds, 1.0), _MAX_RETRY_AFTER_S)


async def _read_capped(resp: aiohttp.ClientResponse, max_bytes: int, label: str) -> bytes:
    declared = resp.headers.get("Content-Length")
    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
        raise KsefMalformedResponseError(f"{label}: body over {max_bytes} bytes")
    chunks: list[bytes] = []
    total = 0
    async for chunk in resp.content.iter_chunked(XML_CHUNK_BYTES):
        total += len(chunk)
        if total > max_bytes:
            raise KsefMalformedResponseError(f"{label}: body over {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)
