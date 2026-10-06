"""The KSeF client's exception hierarchy, carrying no user-facing text.

Contract in docs/ARCHITECTURE.md § Error hierarchy. Messages are for the log only: they name
HTTP statuses and KSeF codes, never a token, a NIP or anything KSeF wrote in a detail string
(details can quote the context NIP or a KSeF number).
"""

from __future__ import annotations

from enum import StrEnum


class AuthErrorReason(StrEnum):
    """Why KSeF refused the KSeF token — each needs a different action from the user."""

    TOKEN_INVALID = "token_invalid"
    NO_PERMISSION = "no_permission"
    BLOCKED = "blocked"


class RateLimitGroup(StrEnum):
    """KSeF's endpoint groups; each has its own limits and its own counter."""

    METADATA = "invoiceMetadata"
    DOWNLOAD = "invoiceDownload"
    AUTH = "auth"
    OTHER = "other"


class KsefError(Exception):
    """Base class of everything the client raises."""


class KsefAuthError(KsefError):
    """The KSeF token cannot be used; only the user can fix it."""

    def __init__(self, reason: AuthErrorReason, code: int | None = None) -> None:
        super().__init__(f"{reason} (KSeF code {code})" if code is not None else str(reason))
        self.reason = reason
        self.code = code


class KsefRateLimitError(KsefError):
    """HTTP 429, or a call refused locally because its group is still blocked."""

    def __init__(self, retry_after_s: float, group: RateLimitGroup) -> None:
        super().__init__(f"{group} rate limited for {retry_after_s:.0f} s")
        self.retry_after_s = retry_after_s
        self.group = group


class KsefTemporaryError(KsefError):
    """5xx, timeouts, connection errors, auth status 500/550, an unfinished status poll."""


class KsefMalformedResponseError(KsefError):
    """A response that does not have the documented shape, or a body over its size cap."""


class KsefInvoiceNotReadyError(KsefError):
    """400 code 21165: the invoice exists but cannot be downloaded yet."""


class KsefInvoiceNotFoundError(KsefError):
    """400 code 21164: no invoice with that KSeF number."""
