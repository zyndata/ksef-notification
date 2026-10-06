"""KSeF-token authentication, refresh and the token set held in memory.

Sequence in docs/KSEF_API.md § Authentication with a KSeF token; lifecycle in
docs/ARCHITECTURE.md § Token lifecycle. Tokens live only in this object, never on disk.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from ..const import AUTH_POLL_DELAYS, AUTH_POLL_TIMEOUT, CLOSE_TIMEOUT, TOKEN_MARGIN
from .errors import (
    AuthErrorReason,
    KsefAuthError,
    KsefError,
    KsefMalformedResponseError,
    KsefTemporaryError,
    RateLimitGroup,
)
from .http import Response, Transport, parse_timestamp, unexpected

_LOGGER = logging.getLogger(__name__)

_KEY_USAGE = "KsefTokenEncryption"

# Authentication status codes (`status.code` of GET /auth/{referenceNumber})
_STATUS_IN_PROGRESS = 100
_STATUS_SUCCESS = 200
_STATUS_NO_PERMISSION = 415
_STATUS_BAD_TOKEN = 450
_STATUS_BLOCKED = frozenset({470, 480})

# KSeF exception codes in 400 bodies
_CODE_INVALID_CHALLENGE = 21111
_CODE_UNAUTHORIZED = 21301
_CODE_NOT_FOUND = 21304
_CODE_DECEASED = 21308
_CODE_VALIDATION = 21405
_CODE_UNKNOWN_KEY = 21470

# Status-450 details that a fresh challenge cures (OpenAPI, 2026-10-06):
# "Nieprawidłowe wyzwanie autoryzacyjne", "Nieprawidłowy czas tokena"
_RETRYABLE_450 = ("wyzwanie", "czas tokena")
# 21301 detail at redeem/refresh when the KSeF token itself was revoked:
# "Token KSeF został unieważniony."
_TOKEN_REVOKED = "unieważniony"

_HTTP_OK = 200
_HTTP_ACCEPTED = 202
_HTTP_NO_CONTENT = 204
_HTTP_BAD_REQUEST = 400
_HTTP_UNAUTHORIZED = 401
_HTTP_FORBIDDEN = 403


@dataclass(frozen=True)
class Token:
    """A JWT and the moment KSeF says it stops working."""

    value: str = field(repr=False)
    valid_until: datetime

    def usable(self, now: datetime) -> bool:
        return self.valid_until - now >= TOKEN_MARGIN


@dataclass(frozen=True)
class _PublicKey:
    key_id: str
    key: rsa.RSAPublicKey = field(repr=False)
    valid_to: datetime


class _RetryWithNewChallenge(Exception):  # noqa: N818 — control flow, not an error
    """Status 450 "invalid challenge"/"invalid token time", or 400 21111: start over once."""


class _UnknownKeyError(Exception):
    """400 code 21470 at `ksef-token`: the key was rotated or withdrawn."""


def _token(data: object, name: str) -> Token:
    entry = data.get(name) if isinstance(data, dict) else None
    if not isinstance(entry, dict) or not isinstance(entry.get("token"), str):
        raise KsefMalformedResponseError(f"{name} missing")
    return Token(entry["token"], parse_timestamp(entry.get("validUntil")))


def _require_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise KsefMalformedResponseError(f"{key} missing")
    return value


def _mentions(response: Response, code: int, fragment: str) -> bool:
    return any(
        error.code == code and any(fragment in detail.casefold() for detail in error.details)
        for error in response.errors()
    )


class Authenticator:
    """Holds the access and refresh tokens and gets new ones when they run out."""

    def __init__(self, transport: Transport, nip: str, ksef_token: str) -> None:
        self._http = transport
        self._nip = nip
        self._ksef_token = ksef_token
        self._access: Token | None = None
        self._refresh: Token | None = None
        self._public_key: _PublicKey | None = None
        self._lock = asyncio.Lock()

    @property
    def has_session(self) -> bool:
        return self._refresh is not None

    def invalidate_access_token(self) -> None:
        """KSeF answered 401 to this access token: never send it again."""
        self._access = None

    async def ensure_access_token(self) -> str:
        """A valid access token: the held one, a refreshed one, or a full authentication."""
        async with self._lock:
            now = self._http.clock.utcnow()
            if self._access is not None and self._access.usable(now):
                return self._access.value
            if self._refresh is not None and self._refresh.usable(now):
                self._access = await self._refreshed_access(self._refresh)
                if self._access is not None:
                    return self._access.value
            self._access = self._refresh = None
            self._access, self._refresh = await self._authenticate()
            return self._access.value

    async def revoke(self) -> None:
        """Best effort: end the KSeF session so its refresh token dies with this entry.

        Never authenticates just to revoke; the tokens are dropped whatever happens.
        """
        try:
            async with asyncio.timeout(CLOSE_TIMEOUT.total_seconds()), self._lock:
                try:
                    await self._revoke_held_session()
                finally:
                    self._access = self._refresh = None
        except (KsefError, TimeoutError) as err:
            _LOGGER.debug("Session revocation failed: %s", err)

    async def _revoke_held_session(self) -> None:
        if self._refresh is None:
            return
        now = self._http.clock.utcnow()
        access = self._access
        if access is None or not access.usable(now):
            if not self._refresh.usable(now):
                return
            access = await self._refreshed_access(self._refresh)
            if access is None:
                return
        response = await self._http.request(
            "DELETE",
            "/auth/sessions/current",
            group=RateLimitGroup.OTHER,
            label="revoke session",
            bearer=access.value,
            time_limit=CLOSE_TIMEOUT,
        )
        if response.status != _HTTP_NO_CONTENT:
            _LOGGER.debug("Session revocation answered HTTP %s", response.status)

    # --- refresh -----------------------------------------------------------------------

    async def _refreshed_access(self, refresh: Token) -> Token | None:
        """A new access token, or None when KSeF wants a full authentication instead."""
        response = await self._http.request(
            "POST",
            "/auth/token/refresh",
            group=RateLimitGroup.AUTH,
            label="refresh",
            bearer=refresh.value,
        )
        if response.status == _HTTP_OK:
            # The answer carries only a new access token; the refresh token is not rotated.
            return _token(response.json(), "accessToken")
        if response.status == _HTTP_BAD_REQUEST and _CODE_DECEASED in response.error_codes():
            raise KsefAuthError(AuthErrorReason.BLOCKED, _CODE_DECEASED)
        if response.status in {_HTTP_BAD_REQUEST, _HTTP_UNAUTHORIZED, _HTTP_FORBIDDEN}:
            # 21301 (session revoked / KSeF token revoked), 21304, an expired JWT: the full
            # sequence with the KSeF token decides whether the user has to act.
            _LOGGER.debug("Refresh refused (HTTP %s); authenticating again", response.status)
            return None
        raise unexpected(response, "refresh")

    # --- full authentication -------------------------------------------------------------

    async def _authenticate(self) -> tuple[Token, Token]:
        """The full sequence: (access token, refresh token)."""
        reloaded_key = retried_challenge = False
        while True:
            key = await self._get_public_key(force=reloaded_key)
            try:
                reference, auth_token = await self._start(key)
                await self._wait_for_success(reference, auth_token)
            except _RetryWithNewChallenge:
                if retried_challenge:
                    raise KsefTemporaryError("challenge refused twice") from None
                retried_challenge = True
                continue
            except _UnknownKeyError:
                if reloaded_key:
                    raise KsefTemporaryError("public key refused after reloading") from None
                reloaded_key = True
                continue
            tokens = await self._redeem(auth_token)
            _LOGGER.debug("Authenticated with KSeF")
            return tokens

    async def _start(self, key: _PublicKey) -> tuple[str, str]:
        """Challenge and `POST /auth/ksef-token`: (reference number, operation token)."""
        challenge_response = await self._http.request(
            "POST", "/auth/challenge", group=RateLimitGroup.AUTH, label="challenge"
        )
        if challenge_response.status != _HTTP_OK:
            raise unexpected(challenge_response, "challenge")
        challenge_data = challenge_response.json_object()
        challenge = _require_str(challenge_data, "challenge")
        timestamp_ms = challenge_data.get("timestampMs")
        if not isinstance(timestamp_ms, int) or isinstance(timestamp_ms, bool):
            raise KsefMalformedResponseError("timestampMs missing")

        encrypted = key.key.encrypt(
            f"{self._ksef_token}|{timestamp_ms}".encode(),
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None
            ),
        )
        response = await self._http.request(
            "POST",
            "/auth/ksef-token",
            group=RateLimitGroup.AUTH,
            label="ksef-token",
            json_body={
                "challenge": challenge,
                "contextIdentifier": {"type": "Nip", "value": self._nip},
                "encryptedToken": base64.b64encode(encrypted).decode("ascii"),
                "publicKeyId": key.key_id,
            },
        )
        if response.status == _HTTP_BAD_REQUEST:
            codes = response.error_codes()
            if _CODE_UNKNOWN_KEY in codes:
                raise _UnknownKeyError
            if _CODE_INVALID_CHALLENGE in codes:
                raise _RetryWithNewChallenge
            if _CODE_VALIDATION in codes:
                raise KsefAuthError(AuthErrorReason.TOKEN_INVALID, _CODE_VALIDATION)
        if response.status not in {_HTTP_OK, _HTTP_ACCEPTED}:
            raise unexpected(response, "ksef-token")
        data = response.json_object()
        return _require_str(data, "referenceNumber"), _token(data, "authenticationToken").value

    async def _wait_for_success(self, reference: str, auth_token: str) -> None:
        clock = self._http.clock
        deadline = clock.monotonic() + AUTH_POLL_TIMEOUT.total_seconds()
        attempt = 0
        while True:
            await clock.sleep(AUTH_POLL_DELAYS[min(attempt, len(AUTH_POLL_DELAYS) - 1)])
            attempt += 1
            response = await self._http.request(
                "GET",
                f"/auth/{reference}",
                group=RateLimitGroup.AUTH,
                label="auth status",
                bearer=auth_token,
            )
            if response.status != _HTTP_OK:
                raise unexpected(response, "auth status")
            status = response.json_object().get("status")
            code = status.get("code") if isinstance(status, dict) else None
            if not isinstance(code, int):
                raise KsefMalformedResponseError("auth status code missing")
            if code == _STATUS_SUCCESS:
                return
            if code != _STATUS_IN_PROGRESS:
                _raise_for_status(code, status.get("details"))
            if clock.monotonic() >= deadline:
                raise KsefTemporaryError("authentication still in progress after the timeout")

    async def _redeem(self, auth_token: str) -> tuple[Token, Token]:
        response = await self._http.request(
            "POST",
            "/auth/token/redeem",
            group=RateLimitGroup.AUTH,
            label="redeem",
            bearer=auth_token,
        )
        if response.status == _HTTP_BAD_REQUEST:
            if _mentions(response, _CODE_UNAUTHORIZED, _TOKEN_REVOKED):
                raise KsefAuthError(AuthErrorReason.TOKEN_INVALID, _CODE_UNAUTHORIZED)
            if _CODE_DECEASED in response.error_codes():
                raise KsefAuthError(AuthErrorReason.BLOCKED, _CODE_DECEASED)
        if response.status != _HTTP_OK:
            raise unexpected(response, "redeem")
        data = response.json_object()
        return _token(data, "accessToken"), _token(data, "refreshToken")

    # --- public key ------------------------------------------------------------------------

    async def _get_public_key(self, *, force: bool) -> _PublicKey:
        now = self._http.clock.utcnow()
        if not force and self._public_key is not None and self._public_key.valid_to > now:
            return self._public_key
        response = await self._http.request(
            "GET",
            "/security/public-key-certificates",
            group=RateLimitGroup.AUTH,
            label="public keys",
        )
        if response.status != _HTTP_OK:
            raise unexpected(response, "public keys")
        entries = response.json()
        if not isinstance(entries, list):
            raise KsefMalformedResponseError("public keys: not a list")

        candidates: list[tuple[datetime, dict[str, Any]]] = []
        for entry in entries:
            if not isinstance(entry, dict) or _KEY_USAGE not in (entry.get("usage") or ()):
                continue
            valid_from = parse_timestamp(entry.get("validFrom"))
            if valid_from <= now < parse_timestamp(entry.get("validTo")):
                candidates.append((valid_from, entry))
        if not candidates:
            raise KsefMalformedResponseError("no valid KsefTokenEncryption certificate")
        # Overlapping certificates during a planned rotation: the newest one.
        entry = max(candidates, key=lambda candidate: candidate[0])[1]
        try:
            der = base64.b64decode(_require_str(entry, "certificate"), validate=True)
            key = x509.load_der_x509_certificate(der).public_key()
        except (binascii.Error, ValueError) as err:
            raise KsefMalformedResponseError("unreadable certificate") from err
        if not isinstance(key, rsa.RSAPublicKey):
            raise KsefMalformedResponseError("certificate key is not RSA")
        self._public_key = _PublicKey(
            _require_str(entry, "publicKeyId"), key, parse_timestamp(entry.get("validTo"))
        )
        return self._public_key


def _raise_for_status(code: int, details: object) -> None:
    """Map a failed authentication status (docs/ARCHITECTURE.md § Token lifecycle)."""
    texts = (
        [d.casefold() for d in details if isinstance(d, str)] if isinstance(details, list) else []
    )
    if code == _STATUS_BAD_TOKEN:
        if texts and all(any(f in text for f in _RETRYABLE_450) for text in texts):
            raise _RetryWithNewChallenge
        raise KsefAuthError(AuthErrorReason.TOKEN_INVALID, code)
    if code == _STATUS_NO_PERMISSION:
        raise KsefAuthError(AuthErrorReason.NO_PERMISSION, code)
    if code in _STATUS_BLOCKED:
        raise KsefAuthError(AuthErrorReason.BLOCKED, code)
    # 425 (revoked meanwhile), 460 (certificates — not used here), 500, 550, anything new
    raise KsefTemporaryError(f"authentication status {code}")
