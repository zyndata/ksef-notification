"""I/O to KSeF. No Home Assistant imports except the session type; never imports core.

Interface contract in docs/ARCHITECTURE.md § Client interface.
"""

from __future__ import annotations

from .api import KsefClient, MetadataResult
from .errors import (
    AuthErrorReason,
    KsefAuthError,
    KsefError,
    KsefInvoiceNotFoundError,
    KsefInvoiceNotReadyError,
    KsefMalformedResponseError,
    KsefRateLimitError,
    KsefTemporaryError,
    RateLimitGroup,
)
from .http import Clock

__all__ = [
    "AuthErrorReason",
    "Clock",
    "KsefAuthError",
    "KsefClient",
    "KsefError",
    "KsefInvoiceNotFoundError",
    "KsefInvoiceNotReadyError",
    "KsefMalformedResponseError",
    "KsefRateLimitError",
    "KsefTemporaryError",
    "MetadataResult",
    "RateLimitGroup",
]
