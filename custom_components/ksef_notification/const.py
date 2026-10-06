"""Constants for the KSeF Notification integration.

Values fixed by the KSeF API research (docs/KSEF_API.md) and the architecture
(docs/ARCHITECTURE.md); option keys name the options documented in docs/CONFIG.md. Names
in capitals match the ones those documents use.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "ksef_notification"
INTEGRATION_NAME: Final = "KSeF Notification"

# Config entry data — fixed at setup (docs/CONFIG.md § Entry data)
CONF_ENVIRONMENT: Final = "environment"
CONF_NIP: Final = "nip"
CONF_TOKEN: Final = "token"

# Config entry options — editable later (docs/CONFIG.md § Entry options)
CONF_NOTIFY_SERVICE: Final = "notify_service"
CONF_FIELDS: Final = "fields"
CONF_CHECK_INTERVAL_MIN: Final = "check_interval_min"

# KSeF environments (docs/KSEF_API.md § Environments)
ENV_PROD: Final = "prod"
ENV_DEMO: Final = "demo"
ENV_TEST: Final = "test"
ENVIRONMENTS: Final = (ENV_PROD, ENV_DEMO, ENV_TEST)
DEFAULT_ENVIRONMENT: Final = ENV_PROD
API_BASE_URLS: Final[dict[str, str]] = {
    ENV_PROD: "https://api.ksef.mf.gov.pl/v2",
    ENV_DEMO: "https://api-demo.ksef.mf.gov.pl/v2",
    ENV_TEST: "https://api-test.ksef.mf.gov.pl/v2",
}

# Check interval, minutes (docs/ARCHITECTURE.md § Coordinator scheduling)
DEFAULT_CHECK_INTERVAL_MIN: Final = 15
MIN_CHECK_INTERVAL_MIN: Final = 15
MAX_CHECK_INTERVAL_MIN: Final = 1440
CHECK_INTERVAL_STEP_MIN: Final = 5

# Default field selection, in the fixed message order (docs/CONFIG.md § Selectable fields)
DEFAULT_FIELDS: Final = ("seller_name", "invoice_number", "gross_amount", "due_date")

# New-invoice detection (docs/ARCHITECTURE.md § New-invoice detection, § First run)
OVERLAP: Final = timedelta(seconds=60)
PAGE_SIZE: Final = 250
MAX_PAGES: Final = 3
MAX_DEFERRALS: Final = 2
HWM_FALLBACK: Final = timedelta(hours=1)
SEEN_MAX: Final = 1000
MAX_CATCH_UP: Final = timedelta(days=90)
BASELINE_WINDOW: Final = timedelta(hours=2)

# Persistence (docs/ARCHITECTURE.md § What is persisted)
STORAGE_VERSION: Final = 1
STORAGE_KEY_PREFIX: Final = DOMAIN

# Tokens (docs/ARCHITECTURE.md § Token lifecycle)
TOKEN_MARGIN: Final = timedelta(seconds=60)
AUTH_POLL_TIMEOUT: Final = timedelta(seconds=30)
AUTH_POLL_DELAYS: Final = (0.5, 1.0, 2.0, 4.0)  # seconds; the last one repeats

# HTTP to KSeF (docs/KSEF_API.md § Listing invoices, § Rate limits)
REQUEST_TIMEOUT: Final = timedelta(seconds=30)
CLOSE_TIMEOUT: Final = timedelta(seconds=5)
MAX_JSON_BYTES: Final = 4 * 1024 * 1024
MAX_QUERY_RANGE: Final = timedelta(days=100)
DEFAULT_RETRY_AFTER: Final = timedelta(seconds=60)  # a 429 without a usable Retry-After
VALIDATE_WINDOW: Final = timedelta(hours=1)
VALIDATE_PAGE_SIZE: Final = 10

# Scheduling and rate limits (docs/ARCHITECTURE.md § Coordinator scheduling)
MIN_QUERY_GAP: Final = timedelta(minutes=10)
RETRY_AFTER_MARGIN: Final = timedelta(seconds=5)
XML_FETCH_GAP: Final = timedelta(milliseconds=250)

# Several invoices and message shape (docs/ARCHITECTURE.md § Several invoices at once,
# § Message formatting)
COMBINE_THRESHOLD: Final = 3
COMBINED_MAX_LINES: Final = 10
BODY_MAX: Final = 1000
SELLER_NAME_MAX: Final = 80
SELLER_TEXT_MAX: Final = 60
LINE_ITEMS_SHOWN: Final = 3

# XML safety bounds (docs/ARCHITECTURE.md § XML safety)
MAX_XML_BYTES: Final = 4 * 1024 * 1024
XML_MAX_DEPTH: Final = 64
XML_MAX_TEXT: Final = 4 * 1024
XML_CHUNK_BYTES: Final = 64 * 1024

# Event fired once per new invoice (payload in docs/CONFIG.md § Event payload)
EVENT_INVOICE: Final = "ksef_notification_invoice"

# Entities: translation key = unique-id suffix (docs/CONFIG.md § Entities)
KEY_NOTIFICATIONS: Final = "notifications"
KEY_LAST_INVOICE: Final = "last_invoice"
KEY_LAST_CHECK: Final = "last_check"
KEY_CHECK_NOW: Final = "check_now"

# Push data (docs/CONFIG.md § Notification)
NOTIFY_DOMAIN: Final = "notify"
NOTIFICATION_GROUP: Final = "ksef_notification"
NOTIFICATION_CHANNEL: Final = "KSeF"
NOTIFICATION_TAG_PREFIX: Final = "ksef_"
CLICK_ENTITY_PREFIX: Final = "entityId:"

# Repair issues (docs/ARCHITECTURE.md § Outputs); the issue id adds the entry id
ISSUE_NOTIFY_SERVICE_MISSING: Final = "notify_service_missing"
ISSUE_ACCOUNT_BLOCKED: Final = "account_blocked"
