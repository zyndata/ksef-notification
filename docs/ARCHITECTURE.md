# Architecture

The design of the integration, as implemented in 1.1.0. Every decision is stated with its
reason, so a change can be judged against it. Inputs: [KSEF_API.md](KSEF_API.md) (verified
API facts, limits and the request budget) and [CONFIG.md](CONFIG.md) (options, selectable fields,
outputs).

Two hard constraints shape everything below:

- **No invoice content is written to disk** by this integration — no XML, PDF, database or
  invoice value in its own storage. What it must persist is listed in
  [What is persisted](#what-is-persisted) and contains no invoice content.
- **The request budget in [KSEF_API.md](KSEF_API.md#request-budget) is never exceeded.** Every
  number here is checked against it in [Resource and request budget](#resource-and-request-budget).

Runtime dependencies: only what Home Assistant core ships (`aiohttp`, `cryptography`) plus the
Python standard library. `manifest.json` `requirements` stays empty.

Constants named in `CAPITALS` below live in `const.py`; their values are fixed here.

---

## Module layout

```
custom_components/ksef_notification/
├── __init__.py          # entry setup/unload: client, store, coordinator, platforms; revokes the
│                        #   KSeF session on unload; deletes the store on entry removal
├── const.py             # domain, config/option keys, environments, defaults, bounds, constants
├── manifest.json        # domain, version, config_flow, iot_class cloud_polling, no requirements
├── strings.json         # base texts; translations/en.json (= strings.json), translations/pl.json
├── brand/               # icon and logo served by Home Assistant (docs/BRANDING.md)
├── config_flow.py       # wizard (access → notification → behaviour), options flow, reauth flow
├── coordinator.py       # KsefCoordinator(DataUpdateCoordinator): one cycle = the data flow below
├── storage.py           # TrackerStore: TrackerState ⇄ HA Store (JSON), save after every push
├── notifier.py          # push via notify.mobile_app_*, the ksef_notification_invoice event
├── entity.py            # shared base: the one service device per entry
├── switch.py            # notifications enabled (RestoreEntity)
├── sensor.py            # last invoice; last check (diagnostic)
├── button.py            # check now (throttled)
├── diagnostics.py       # config entry diagnostics, every secret and invoice value redacted
├── client/              # I/O to KSeF; no Home Assistant imports except the session type
│   ├── __init__.py
│   ├── errors.py        # exception hierarchy, no user-facing text
│   ├── http.py          # one request: headers, body cap, error bodies, rate gates, pacing,
│   │                    #   request counters, timestamps; the injectable Clock
│   ├── auth.py          # public key, challenge, RSA-OAEP, status poll, redeem, refresh; tokens
│   └── api.py           # KsefClient: metadata query with paging, invoice XML fetch
└── core/                # PURE: no I/O, no homeassistant imports, no clock reads (now is a parameter)
    ├── __init__.py
    ├── fields.py        # the field registry: key, source, order; needs_xml(selection)
    ├── model.py         # Invoice, InvoiceDetails, DetailsStatus; from_metadata(), with_details()
    ├── xml_parser.py    # bytes → InvoiceDetails, hardened streaming expat parser
    ├── tracker.py       # TrackerState + plan_cycle() / select_new() / record_handled() /
    │                    #   may_defer() / record_deferred() / finish_baseline() / finish_cycle()
    ├── quiet_hours.py   # QuietHours: contains() / postpone() a scheduled check; parse()
    └── formatter.py     # Invoice + selection + locale → Message (translation keys + values);
                         #   render(message, strings); field_values() for the event and sensor
```

Layering rules (`tests/test_purity.py` enforces the first one, as Walk the dog does):

- `core/*` is **pure**: no I/O, no `homeassistant` imports, no `datetime.now()`. It takes
  already-fetched data and returns values. This is what the core and budget tests exercise exhaustively.
- `client/*` does all KSeF I/O through Home Assistant's shared `aiohttp` session (passed in) and
  never imports `core`. It raises typed errors and carries no translated text.
- Only `coordinator.py` wires client → core → storage → notifier. Entities read coordinator data
  only. `config_flow.py` uses the client directly for validation.
- `notifier.py`, not `notify.py`: a module named after a platform *is* that platform to Home
  Assistant (lesson from Walk the dog).

### Client interface

```python
class KsefClient:
    def __init__(self, session: aiohttp.ClientSession, environment: Environment,
                 nip: str, ksef_token: str) -> None: ...

    async def query_metadata(self, date_from: datetime, *, max_pages: int = MAX_PAGES
                             ) -> MetadataResult: ...
        # Subject2, PermanentStorage, Asc, pageSize 250, `to` omitted (up to now),
        # restrictToPermanentStorageHwmDate false. Pages by moving `from` to the last record's
        # permanentStorageDate and resetting pageOffset (independent of pageOffset semantics);
        # only if a full page shares one timestamp, so that `from` cannot move, pageOffset + 1.
        # Returns raw metadata dicts (deduplicated by ksefNumber across pages), the HWM if
        # KSeF sent one, and `complete` = False when max_pages stopped it early.

    async def fetch_invoice_xml(self, ksef_number: str) -> bytes: ...
        # GET /invoices/ksef/{n}, Accept: application/xml. Body capped at MAX_XML_BYTES.

    async def validate(self) -> None: ...
        # Config flow: authenticate, then one metadata query (pageSize 10, last hour) to prove
        # InvoiceRead. Then revoke the session.

    async def async_close(self) -> None: ...
        # Best effort DELETE /auth/sessions/current (timeout 5 s); drops tokens from memory.
        # Refreshes an expired access token for it, but never authenticates just to revoke.

    def requests_last_hour(self, group: RateLimitGroup) -> int: ...
        # Requests sent to one limit group in the last 60 min — the last-check sensor's
        # metadata_requests_last_hour / download_requests_last_hour.

@dataclass(frozen=True)
class MetadataResult:
    invoices: tuple[dict[str, Any], ...]   # sorted by permanentStorageDate ascending
    hwm: datetime | None                   # permanentStorageHwmDate
    complete: bool                         # False when stopped by max_pages
```

Token handling is internal to the client (`auth.py`): every protected call first ensures a valid
access token (see [Token lifecycle](#token-lifecycle)). The constructor also takes an optional
`clock` (`utcnow`, `monotonic`, `sleep`) so tests drive time; the integration passes none.

`client/http.py` holds what every call shares. A call to a group that KSeF answered with 429 is
refused locally until `Retry-After` has passed (seconds, or an HTTP date; missing or unusable →
`DEFAULT_RETRY_AFTER` = 60 s; capped at 24 h). Downloads are paced `XML_FETCH_GAP` apart at the
moment they are sent, so a download repeated after a 401 is paced too. Request and error bodies
are logged by a label (`metadata`, `invoice xml`, …), never by path, because a path can carry a
KSeF number. JSON bodies are capped at `MAX_JSON_BYTES` = 4 MB, XML at `MAX_XML_BYTES`.

### Error hierarchy

| Exception | Raised for | Coordinator reaction |
|---|---|---|
| `KsefError` | base class | — |
| `KsefAuthError(reason)` | `reason` ∈ `token_invalid` (450 token-related, 21301 "token revoked" at redeem, 400 21405 at `ksef-token`), `no_permission` (415, or 403 on a protected call), `blocked` (470, 480, 400 21308, 403 with `reasonCode` `security-service-blocked`) | `token_invalid`/`no_permission` → re-authentication started, checks stop until the entry is reloaded; `blocked` → repair issue, checks stop until reloaded |
| `KsefRateLimitError(retry_after_s, group)` | HTTP 429, or a call attempted while that group is still blocked | Next cycle no sooner than `retry_after_s` |
| `KsefTemporaryError` | 5xx, timeouts, connection errors, redirects, auth status 425/460/500/550 or an unknown one, status poll not finished in time, any 400 not named in this table (e.g. 21405 on the metadata query) | The check fails (`outcome` `unavailable`), next one at the normal interval |
| `KsefMalformedResponseError` | unparsable JSON, missing required keys, oversize body | As temporary |
| `KsefInvoiceNotReadyError` | 400 code 21165 on the XML fetch | That invoice is deferred |
| `KsefInvoiceNotFoundError` | 400 code 21164 on the XML fetch | That invoice is notified without XML fields |

---

## Data flow

One update cycle of `KsefCoordinator`, entered only while the switch is on:

```
                   switch on? ──no──► nothing (no timer, no request)
                       │ yes
                       ▼
   tracker.plan_cycle(state, now)  → date_from (cursor − OVERLAP, or baseline window)
                       │
                       ▼
   client.query_metadata(date_from)   [ensures token: refresh or full auth first if needed]
                       │  invoices[] + HWM
                       ▼
   tracker: drop invoices already in `seen`; baseline cycle → all go to `seen`, none notified
                       │  new invoices (ascending permanentStorageDate)
                       ▼
   N ≤ COMBINE_THRESHOLD ?──no──► one combined notification, metadata fields only
                       │ yes
                       ▼
   per invoice: needs_xml(selection) and formCode is FA (2)/FA (3)?
                       │ yes                               │ no
                       ▼                                   │
   client.fetch_invoice_xml (≥ 250 ms apart)               │
   → executor: xml_parser.parse(bytes) → InvoiceDetails    │
     (bytes dropped right after)                           │
                       ▼                                   ▼
                 model.Invoice (from metadata [+ details]) in memory
                       │
                       ▼
   formatter → title/body (translation keys + values) → notifier: push + event
                       │
                       ▼
   tracker.record_handled(invoice) → store.save()   (after every push)
                       │
                       ▼
   tracker.finish_cycle(invoices, HWM, complete) → cursor advances → store.save()
                       │
                       ▼
   coordinator data → last-invoice sensor, last-check sensor
```

Invoice objects, XML bytes and parsed details exist **only inside one cycle**. The coordinator
keeps exactly one `Invoice` between cycles — the most recent one, for the last-invoice sensor —
and never writes it anywhere.

---

## New-invoice detection

Built on KSeF's own completeness guarantee, the **high-water mark** (HWM): no invoice with a
`permanentStorageDate` ≤ HWM will ever appear later ([KSEF_API.md](KSEF_API.md#which-date-finds-newly-arrived-invoices)).

### Polling strategy: "up to now", deduplicated

Of the two documented strategies the integration uses **"up to now"**: `to` is omitted, so an
invoice is seen as soon as it is queryable rather than once the HWM has passed it (observed
≈ 2 minutes later, which at a 15-minute interval costs a whole cycle). The price — overlapping
windows — is paid by the `seen` set below.

### State (`TrackerState`)

| Field | Type | Meaning |
|---|---|---|
| `cursor` | aware UTC datetime \| `None` | Everything with `permanentStorageDate` ≤ `cursor` has been handled. Always a value KSeF reported (an HWM or a record's date), never Home Assistant's clock — except in the two fallbacks named below. `None` = baseline needed |
| `seen` | map `hash → permanentStorageDate` | Invoices already handled whose date is not yet safely below the window. `hash` = first 16 hex characters of SHA-256 of the `ksefNumber` (see [What is persisted](#what-is-persisted)) |
| `deferrals` | map `hash → int`, **memory only** | How many cycles an invoice's XML has been deferred |

### One cycle

1. **Window.** `date_from = cursor − OVERLAP` (`OVERLAP` = 60 s). Whether KSeF treats `from` as
   inclusive is undocumented; the overlap makes it irrelevant, and `seen` removes the repeats.
2. **Query** up to now, ascending, at most `MAX_PAGES` = 3 pages of 250.
3. **Filter.** An invoice whose hash is in `seen` is skipped. The rest are *new*, in ascending
   `permanentStorageDate`.
4. **Handle** each new invoice (see [Several invoices at once](#several-invoices-at-once)). An
   invoice is *handled* when it was notified, or when it was given up on (below). Handled →
   added to `seen` immediately and the store saved.
5. **Deferred** invoices (XML not ready, XML fetch failed temporarily, download rate limit) are
   **not** added to `seen`, so the next query returns them again. After `MAX_DEFERRALS` = 2
   deferrals an invoice is notified on the third cycle without its XML fields.
6. **Advance the cursor:**
   `cursor' = max(cursor, min(HWM, earliest deferred date, last returned date if the query was
   incomplete))`. With no HWM in the response (it is optional in OpenAPI), `HWM` is replaced by
   `now − HWM_FALLBACK` (`HWM_FALLBACK` = 1 h, Home Assistant's clock).
7. **Prune** `seen`: drop entries with date < `cursor' − OVERLAP`. They can no longer be
   returned, because the next window starts at exactly that point.
8. **Save** the state.

### Why nothing is notified twice or missed

- **Not missed:** windows are contiguous by construction (each starts `OVERLAP` before where the
  previous one's guarantee ends), the cursor never passes an unhandled invoice (step 6), and
  invoices that are still arriving — dated after the HWM — are re-queried every cycle until the
  HWM passes them.
- **Not twice:** every invoice inside the overlapping part of a window is in `seen`, and an
  entry leaves `seen` only once its date is below every future window.
- **An invoice arriving during a poll** either is in this cycle's response or is dated after the
  HWM the response returned; in both cases the next window covers it.
- **Clock differences** do not enter the window at all: the cursor and the pruning bound come
  from KSeF's own timestamps. Home Assistant's clock is used only for the baseline window, the
  HWM fallback, token expiry margins, scheduling and the 100-day guard — each of which tolerates
  minutes of skew (the baseline window is 2 h wide; a token used too late answers 401, which is
  handled).
- **Restart in the middle of a cycle.** The store is saved after *every* push, so a graceful
  restart (Home Assistant cancels the cycle; `Store` flushes on shutdown) loses nothing and
  repeats nothing: invoices not yet pushed are not in `seen` and the cursor has not moved past
  them. The only window for a duplicate is a **hard crash between one push and its file write**
  (milliseconds); the design is deliberately at-least-once there — a repeated notification is a
  smaller harm than a missing one.
- **Paging cut short** (`MAX_PAGES`): the cursor stops at the last returned record; the rest
  arrives next cycle.

### Bounds

- `seen` holds the invoices dated within roughly one cycle plus the HWM lag — normally a handful.
  Hard cap `SEEN_MAX` = 1 000 entries; above it the oldest by date are dropped and a warning is
  logged (reachable only with > 1 000 cost invoices inside one window, far beyond the use case).
- `deferrals` holds only currently deferred invoices; entries are removed when handled.
- **Paging assumption:** a window restarts `OVERLAP` before the cursor, so a query cut short by
  `MAX_PAGES` makes progress only if fewer than `MAX_PAGES` × `PAGE_SIZE` = 750 invoices were
  stored within any 60 seconds. If that were ever broken, every check would return the same
  750 already-handled invoices and the cursor would not move: no duplicate, but nothing newer
  until notifications are switched off and on again (a new baseline). Measured against KSeF's own limits (TEST,
  2026-10-06, equal to production): a seller sending online can store at most 30 invoices a
  minute per (seller, IP) (`invoiceSend` 10 / s, 30 / min, 180 / h), so it takes 25 sellers
  sending at full rate to the same buyer in the same minute; only a **batch session** (up to
  thousands of invoices in one package) from a seller billing this one company 750 times at
  once could reach it. Accepted for 1.0: no plausible cost-invoice stream comes near it, and the
  tracker simulation and the burst test (300 invoices in one check) stay inside it.

### The 100-day limit

KSeF refuses date ranges longer than 100 days. If `cursor` is older than `MAX_CATCH_UP` =
90 days (Home Assistant off for three months), the cycle **re-baselines** instead of catching up,
and logs a warning. 90 leaves margin for clock skew and for the time the cycle itself takes.

---

## First run

**Decision: notify nothing historical.** Adding the integration to a company with years of
invoices must never produce a flood.

The first cycle of a new entry (`cursor = None`) is a **baseline cycle**:

1. Query with `date_from = now − BASELINE_WINDOW` (`BASELINE_WINDOW` = 2 h, Home Assistant clock;
   wide enough to absorb any realistic clock skew, small enough to fit one page).
2. Put every returned invoice in `seen`, notify nothing, fire no event.
3. `cursor = HWM` (or the fallback), prune, save.

An invoice that is dated just before setup but becomes visible only after the baseline query is
dated after that HWM and is notified in the next cycle — correct, since it had not arrived when
the integration was set up.

The same baseline runs:

- when the **switch is turned back on** after being off (see
  [Coordinator scheduling](#coordinator-scheduling) — what arrived while notifications were off
  is not queued);
- after the 100-day guard above;
- when the store is missing or its version is unknown.

A restart of Home Assistant with the switch on is **not** a baseline: the cycle catches up from
the stored cursor, so invoices that arrived while Home Assistant was down are notified (combined,
if there are many).

---

## What is persisted

Exactly one file per config entry, through Home Assistant's `Store` helper:
`.storage/ksef_notification.<entry_id>`, `STORAGE_VERSION` = 1. Deleted when the entry is
removed (`async_remove_entry`).

```json
{
  "version": 1,
  "minor_version": 1,
  "key": "ksef_notification.01JAXXXXXXXXXXXXXXXXXXXXXX",
  "data": {
    "cursor": "2026-10-06T07:28:00.123456+00:00",
    "seen": {
      "3f9a1c0b7d2e4a65": "2026-10-06T07:31:12.512345+00:00"
    }
  }
}
```

- **No invoice content.** No amount, name, number, NIP, date of issue or XML. The KSeF number is
  not stored either — it begins with the *seller's* NIP — only a truncated SHA-256 of it, which
  is enough to recognise an invoice already handled and useless for anything else (64 bits:
  a collision among ≤ 1 000 entries has a probability below 10⁻¹³). The two timestamps are KSeF
  storage times, not invoice data.
- **Size:** about 55 bytes per `seen` entry. Typical: under 1 KB. Maximum: `SEEN_MAX` × 55 B
  ≈ **55 KB**.
- **Written** after every push and at the end of every cycle that changed something
  (`Store.async_save`, atomic replace). No delayed save: the write after a push is what keeps
  duplicates out after a restart.
- **Not persisted:** KSeF access and refresh tokens (memory only — a restart costs one full
  authentication, five requests in a group with no hourly limit, and keeps a 7-day bearer secret
  off the disk), `deferrals`, the last invoice, request counters.
- **Elsewhere, by Home Assistant itself:** the KSeF token in the config entry (Home Assistant's
  standard place for credentials); the switch position (`RestoreEntity`).

### What Home Assistant may record on its own

The integration controls its own storage, but two outputs pass through Home Assistant machinery
that writes to its database. Both are handled deliberately:

- **Last-invoice sensor attributes** carry the selected fields. They are declared
  `_unrecorded_attributes`, so the recorder never stores them; the sensor is **not** a
  `RestoreEntity`, so they never reach `core.restore_state` either. Its state is only a
  timestamp.
- **The `ksef_notification_invoice` event** is recorded by the recorder like every event, and an
  integration cannot opt its events out. Its payload is limited to the fields the user selected
  (see [CONFIG.md](CONFIG.md#event-payload)), and the README documents the recorder exclusion
  (`recorder: exclude: event_types: [ksef_notification_invoice, call_service]`) for users who
  want nothing in the database.
- **The `call_service` event** that Home Assistant fires for every service call is recorded as
  well, and for the push it carries the notification's title and message (found in the
  smoke test, 2026-10-06). It is Home Assistant's own event; the same exclusion covers it, at
  the price of not recording any service call.
- The **push itself** leaves Home Assistant through the companion app's push relay to Google or
  Apple and is kept in the phone's notification history. The README says so, because the
  notification is invoice data by definition.

---

## Token lifecycle

All of it inside `client/auth.py`; the coordinator only sees the errors.

**Held in memory:** the access token and refresh token with their `validUntil`, and the chosen
public-key certificate until its `validTo`.

**Before every protected call** (`ensure_access_token`):

1. Access token valid for ≥ `TOKEN_MARGIN` (60 s) → use it.
2. Else refresh token valid for ≥ `TOKEN_MARGIN` → `POST /auth/token/refresh`. On `400`
   21301/21304, `401` or `403` → drop both tokens, go to 3 (400 21308 → `blocked`).
3. Else **full authentication**: public key (cached) → challenge → `ksef-token` → status poll →
   redeem.

With the poll interval ≥ 15 minutes and a 15-minute access token, expect one refresh per cycle
and one full authentication per 7 days (and one per Home Assistant start).

**Status poll:** after 0.5 s, then 1, 2, 4, 4, 4 … s, for at most `AUTH_POLL_TIMEOUT` = 30 s
(observed on TEST: done on the first or the second poll). Status 100 past the
timeout → `KsefTemporaryError`. Concurrent callers share one authentication (a lock).

**Outcomes of a full authentication:**

| Result | Error | User sees |
|---|---|---|
| 200 → redeemed | — | — |
| 450 with "invalid challenge" or "invalid token time" (`Nieprawidłowe wyzwanie autoryzacyjne`, `Nieprawidłowy czas tokena`), or 400 21111 at `ksef-token` | retried once immediately with a new challenge; again → `KsefTemporaryError` | nothing (a failed check on the diagnostic sensor) |
| 450 other details, redeem 21301 "KSeF token revoked" | `KsefAuthError("token_invalid")` | Home Assistant's re-authentication flow |
| 415 | `KsefAuthError("no_permission")` | Re-authentication flow; its description says the token needs `InvoiceRead` |
| 470, 480; 400 21308 at redeem or refresh | `KsefAuthError("blocked")` | A repair issue; polling stops until the entry is reloaded |
| 400 21405 at `ksef-token` (the token fails KSeF's input validation) | `KsefAuthError("token_invalid")` | Re-authentication flow |
| 400 21470 at `ksef-token` | reload the key list, retry once | nothing |
| 500, 550, 5xx, timeout | `KsefTemporaryError` | a failed check on the diagnostic sensor |

**A protected call answering `401`:** drop the access token, ensure a token again (refresh or
full authentication), repeat the call **once**. A second 401 → `KsefTemporaryError`. **`403`** →
`KsefAuthError("no_permission")`.

**Re-authentication**: the reauth step asks for a new KSeF token, validates it with
`client.validate()`, updates `entry.data` and reloads the entry. The cursor and `seen` are kept —
a new token for the same company does not reset what was already notified.

**On unload** (reload, removal, Home Assistant stop): `client.async_close()` revokes the session
best-effort, so the refresh token does not outlive the entry.

---

## Coordinator scheduling

| | Value | Reason |
|---|---|---|
| Default interval | **15 min** | The official production guidance is "not shorter than 15 minutes". At 15 minutes the metadata limit is 20 % used (4 / 20 per hour), leaving room for another program on the same connection doing the same. Freshness is the product, so the default is the floor |
| Minimum | **15 min** | Same guidance; the shortest interval the request budget supports with margin |
| Maximum | **1 440 min** (24 h) | Allows a once-a-day digest. Any interval stays far inside the 100-day range |
| Step | 5 min | |

- **One timer.** The coordinator's `update_interval` stays `None`; it arms a single
  point-in-time timer after every check, at `now + check_interval_min` (or later after a 429,
  below). The switch, the back-off and a manual check all move that one timer. The entry's
  "disable polling" system option is respected: no timer, but the button still works.
- **Interval** is the `check_interval_min` option. The first cycle runs right after setup (or
  after the switch turns on).
- **Switch off:** the timer and any running check cancelled — **no timer, no request, no token
  refresh.** The coordinator starts in the off position and the restored switch
  turns it on, so a Home Assistant started with notifications off makes no request at all.
  **Switch on again:** a baseline cycle runs at once. This works because turning the switch
  *off* clears the stored cursor; restoring the switch to *on* at start-up clears nothing, so a
  restart with notifications on catches up instead of re-baselining.
- **Quiet hours** (optional `quiet_start`/`quiet_end`): every scheduled moment that falls inside
  the daily window — the timer after a check, the 429 back-off, the first check when the switch
  turns on or Home Assistant starts — is moved to the window's end (`core/quiet_hours.py`,
  wall-clock time in Home Assistant's zone, compared in UTC so daylight-saving changes neither
  skip nor repeat a check). Inside the window no request reaches KSeF, not even a token refresh.
  The check at the end is an ordinary one: the cursor makes it catch up on the whole night, so
  nothing is lost and the combine threshold applies as usual. *Check now* and
  `homeassistant.update_entity` are explicit requests and run at any hour (still within
  `MIN_QUERY_GAP`); the timer they re-arm is moved like any other.
- **Cycles never overlap.** A refresh requested while a cycle runs is dropped.
- **A failed check is not an error to Home Assistant.** It never raises `UpdateFailed`: its
  result is the last-check sensor's `outcome` (`ok`, `rate_limited`, `unavailable`,
  `auth_failed`, `blocked`), and every entity stays available.
- **Token refused** (`token_invalid`, `no_permission`): Home Assistant's re-authentication is
  started and checks stop until the entry is reloaded — which re-authentication does. Polling a
  refused token would only repeat a full authentication every interval. **Account blocked:**
  the `account_blocked` repair issue; checks stop until the entry is reloaded.
- **`MIN_QUERY_GAP` = 10 min between metadata queries that the timer did not start.** Applies to
  the *Check now* button, to Home Assistant's own `homeassistant.update_entity` on any of the
  entities, and to turning the switch on (the baseline then runs once the gap has passed, so
  off/on cannot get around it). The button raises a translated error naming when the next check is possible; other
  early requests are ignored and logged at debug level. A manual check resets the timer, so the
  worst case is one metadata query per 10 minutes (6 / h, 30 % of the limit).
- **HTTP 429 on the metadata query:** no retry. The next cycle is scheduled at
  `now + max(interval, Retry-After + 5 s)`; afterwards the normal interval is restored. The
  client refuses any call to that group before `Retry-After` has elapsed, so neither the button
  nor the timer can break it.
- **HTTP 429 on an XML fetch:** downloads stop for the rest of the cycle; remaining invoices are
  deferred (they count as a deferral).
- **KSeF outage** (5xx, timeouts, auth 500/550): the cycle fails, the next one runs at the normal
  interval — no extra retries, so an outage never increases the request rate. No catch-up
  burst afterwards either: one query covers the whole gap. The diagnostic sensor shows when the
  last successful check was; TEST's daily 16:00–18:00 maintenance looks exactly like this.
- **Entity availability during an outage:** the switch, the last-invoice sensor and the button
  stay available; only the diagnostic sensor reports the failure (as its `outcome`). A failed
  check is not "no invoice".

### Failure paths, measured

Measured 2026-10-06. KSeF's side of each failure was provoked on TEST where possible
([KSEF_API.md](KSEF_API.md#failure-behaviour-observed)); the integration's reaction was run end to
end in Home Assistant's test harness with the replies TEST gave (`tests/test_resilience.py`).
"Once" means: every invoice that arrived before, during or after the failure produced exactly
one push and one event.

| Failure | Source of KSeF's behaviour | Requests while it lasts | Recovery | User asked for | Once |
|---|---|---|---|---|---|
| Access token refused as expired (401) | TEST: 401 for a bad token; an expired one is still accepted for a while | refresh, the query again — once | same check | nothing | yes |
| Refresh token refused (401, or 21301 after the session was revoked) | TEST | one full authentication (4 requests; key cached) | same check | nothing | yes |
| Refresh token at its 7-day end | simulated | no refresh attempted; one full authentication | same check | nothing | — |
| KSeF token revoked | TEST: access token keeps working, then 21301 / 450 | until the access token runs out checks succeed; then refresh + 3 authentication requests, **then none** | after a new token (re-authentication reloads the entry): catches up from the cursor, not a baseline | **re-authentication** | yes |
| Context blocked | TEST: 21301 "(480)", then status 480 | refresh + 3, then none | after a reload, once MF lifts the block | **repair issue** (`account_blocked`), no re-authentication | yes |
| HTTP 429 (metadata or refresh) | TEST: `Retry-After` 3565 s | the one refused request, **then none** until `Retry-After` + 5 s | first check after that | nothing | yes |
| Outage, 6 h (503, timeouts, connection errors in turn) | simulated | **one per check** (24 in 6 h, checked minute by minute) | first check after it: one refresh + one query covering the whole gap, five invoices in one combined push | nothing; `outcome` `unavailable`, every entity available, no issue | yes |
| Outage inside authentication (status 550, 500, challenge 503) | simulated | one authentication attempt per check | next check | nothing | — |
| Restart while the 2nd of 3 pushes is on its way | simulated (entry reload) | — | the reloaded entry checks at once and sends pushes 2 and 3 | nothing | yes |
| Hard crash right after a push, before its store write | simulated (store image taken at that push) | — | that one push is repeated; nothing before it, nothing missed | nothing | **at least once** — the documented corner |

---

## Several invoices at once

**Decision: one notification per invoice up to `COMBINE_THRESHOLD` = 3 new invoices in a cycle;
from 4 on, one combined notification.**

- Three phones buzzing in a row is still readable; five is a nuisance and hides which one
  matters. Normal operation (a few cost invoices a day, 15-minute cycles) almost never reaches
  four in one cycle — it happens after an outage, after a Home Assistant restart that lasted
  hours, or on a company's busiest mornings, which is exactly when a summary is better.
- **The combined message uses metadata fields only**, so a combined cycle fetches **no XML**.
  This also bounds XML fetches to **≤ 3 per cycle** without a separate cap or a deferral queue
  for over-cap invoices.
- The companion app's push relay allows 500 notifications per device per day. At most 3 pushes
  per cycle and one cycle per 10 minutes keeps the worst case at 432 / day; at the default
  interval, 288.
- Every invoice still gets its own `ksef_notification_invoice` event, with `combined: true` and
  XML fields `null`.

Combined message: title "*N* new cost invoices"; one line per invoice, oldest first,
`Seller name — gross amount currency` (seller NIP if the name is missing), at most
`COMBINED_MAX_LINES` = 10 lines, then "… and *M* more".

---

## Message formatting

The selectable fields, their sources, labels and defaults are listed in
[CONFIG.md](CONFIG.md#selectable-invoice-fields); this section fixes how they are rendered. The
formatter is pure: it returns translation keys plus values, the notifier resolves them in Home
Assistant's configured language (`hass.config.language`), English as the fallback.

**Title.** "New cost invoice"; "New correction invoice" when `invoiceType` is a correction
(`Kor`, `KorZal`, `KorRoz`, `KorPef`, `KorVatRr`). When more than one KSeF Notification entry
exists, the entry's title is appended (" · *entry title*") so two companies can be told apart.

**Body.** One line per selected field, in the fixed order of the field table. The seller name, if
selected, is the first line without a label (it is the headline); every other line is
`Label: value`. The body is capped at `BODY_MAX` = 1 000 characters.

**Values:**

| Kind | Rule | pl | en |
|---|---|---|---|
| Amount | Two decimals, grouped, ISO currency code after | `1 234,56 PLN` | `1,234.56 PLN` |
| VAT | As amount, **always `PLN`** (the metadata gives VAT in PLN for every currency) | | |
| Date | Locale date | `20.10.2026` | `2026-10-20` |
| Bank account | 26 digits → `NN NNNN NNNN NNNN NNNN NNNN NNNN`; `PL` + 26 digits and other IBANs → groups of four after the country code; anything else verbatim | | |
| NIP | 10 digits, ungrouped | | |
| Payment form | Code 1–7 → translated label; `PlatnoscInna` → `OpisPlatnosci` verbatim | | |
| Invoice type | Translated label per enum value | | |
| Due date | The earliest `Termin`, plus " (+*n* more)" when there are several; if there is no `Termin`, the description: FA(3) `TerminOpis` as "*Ilosc* *Jednostka* — *ZdarzeniePoczatkowe*", FA(2) free text | | |
| Bank account (several) | The first, plus " (+*n* more)" | | |
| Line items | `Count: description; description; description` — up to 3 descriptions (`P_7`), each ≤ 60 characters, "; +*n* more" after. Rows with `StanPrzed = 1` are not counted | | |

Locales other than Polish use the English rules.

**Text written by the seller** (seller name, `P_7`, `OpisPlatnosci`, `TerminOpis`): control
characters removed, whitespace collapsed, truncated with "…" (seller name 80 characters, others
60). It is shown, never interpreted.

**Absent values.** A selected field without a value is still shown, as `Label: —`, so the user
can tell "the invoice has no due date" from "the field was not selected". The exception is the
seller name, which falls back to the XML `Podmiot1/DaneIdentyfikacyjne/Nazwa` when the XML was
fetched anyway, and otherwise to `NIP <seller.nip>`. XML fields of PEF and FA_RR invoices, of an
invoice given up on after `MAX_DEFERRALS`, and of a combined cycle are absent in the same way.

---

## XML safety

**Decision: the standard library's `xml.parsers.expat`, driven directly as a streaming parser
with every DTD feature refused. No `defusedxml`, no `lxml`** — neither is shipped with Home
Assistant core.

- **No DOCTYPE at all.** `StartDoctypeDeclHandler`, `EntityDeclHandler` and
  `ExternalEntityRefHandler` raise; `SetParamEntityParsing(XML_PARAM_ENTITY_PARSING_NEVER)`. A
  schema-valid FA(2)/FA(3) invoice never has a DOCTYPE, so refusing it loses nothing and removes
  entity expansion, external entities and DTD retrieval as a class. (Verified 2026-10-06 with
  Python 3.14 / expat 2.8.0: a nested-entity bomb is rejected at the DOCTYPE; plain
  `ElementTree` also refuses it, by expat's amplification limit, and refuses an external entity
  as undefined.)
- **No tree.** Handlers track the path of local names (namespace-independent, since FA(2) and
  FA(3) differ in namespace) and collect only the target values defined in
  [KSEF_API.md](KSEF_API.md#xml-paths-of-the-fields-the-metadata-does-not-carry) — matched as
  exact paths, so `ZaplataCzesciowa/FormaPlatnosci` and `RachunekBankowyFaktora/NrRB` are never
  picked up.
- **Bounds:** body ≤ `MAX_XML_BYTES` = 4 MB (KSeF's own cap is 3 MB with an attachment; the
  client stops reading above it); depth ≤ 64; text collected per element ≤ 4 KB; line items:
  the count plus the first 3 descriptions only. Exceeding a bound → the parser raises, the
  invoice is notified without XML fields.
- **Only FA(2) and FA(3)** are fetched at all (from the metadata `formCode.systemCode`). PEF and
  FA_RR are never downloaded: their XML fields are absent anyway, and skipping them saves budget.
- **Off the event loop:** parsing runs in the executor (`hass.async_add_executor_job`). A 3 MB
  invoice with thousands of rows means tens of thousands of Python callbacks — too long to run on
  the loop.
- Bytes are fed in 64 KB chunks and dropped as soon as the parser returns; only the small
  `InvoiceDetails` value survives.

---

## Outputs

Public contracts from 1.0.0 on. Exact names, states and payload schemas are in
[CONFIG.md](CONFIG.md#entities); the rules behind them are here.

- **Device.** One service device per config entry, named after the entry title; all entities sit
  on it.
- **Switch — notifications.** `RestoreEntity`, default on. Off = no requests at all.
- **Sensor — last invoice.** State: the `acquisitionDate` (device class `timestamp`) of the most
  recently notified invoice — the moment KSeF assigned its number, legally the moment the company
  received it. Attributes: `ksef_number` and the selected fields as raw values, all
  `_unrecorded_attributes`. Not restored: after a restart the state is `unknown` until the next
  invoice. Baseline cycles do not set it.
- **Sensor — last check** (diagnostic). State: time of the last *successful* metadata query.
  Attributes `outcome`, `last_attempt`, `next_check`, `new_invoices`, `metadata_requests_last_hour`,
  `download_requests_last_hour` — so the user can tell "no new invoices" from "KSeF has not
  answered since yesterday", and see what the integration costs KSeF.
- **Button — check now.** Throttled by `MIN_QUERY_GAP`.
- **Notification.** `notify.<notify_service>` with `title`, `message` and `data`:
  `tag` = `ksef_<hash>` (one per invoice, so a repeat replaces rather than stacks; the combined
  message uses `ksef_combined_<entry_id>`), `group` = `ksef_notification` (Android groups them),
  `channel` = `KSeF` (Android lets the user give it its own sound), and
  `clickAction` / `url` = `entityId:<last-invoice sensor>` so a tap opens the sensor, as in Walk
  the dog. Sent with `blocking=True`.
- **A notify service that is missing or raises:** logged, a repair issue raised
  (`notify_service_missing`), the event still fires with `notified: false`, and the invoice
  **counts as handled** — a broken phone setup must not pin the cursor or produce a burst once it
  is fixed. The issue is cleared on the next successful push.
- **Event `ksef_notification_invoice`.** Fired once per new invoice (never in a baseline), after
  the push attempt.
- **Repair issues:** `account_blocked` (auth 470/480), `notify_service_missing`. Token problems
  use Home Assistant's built-in re-authentication instead of an issue.
- **Diagnostics:** entry options, environment, `cursor`, the size of `seen`, the last outcome and
  request counters. Redacted: the KSeF token, the NIP, the entry title (it defaults to
  `KSeF <nip>`), access and refresh tokens, every hash, every invoice value.
- **Texts** (titles, labels, payment forms, invoice types, combined message) live under the
  `common` key of `strings.json` with prefixes `notification_`, `field_`, `payment_form_`,
  `invoice_type_` — `common` is the top-level key hassfest accepts for prose that belongs to no
  form and no entity (verified in Walk the dog). The notifier reads them in Home Assistant's
  server language (`hass.config.language`) over English, so a missing translation falls back
  key by key. Which texts follow which language: [CONFIG.md](CONFIG.md#languages).

---

## Resource and request budget

Consistent with [KSEF_API.md](KSEF_API.md#request-budget). **Measured 2026-10-06**
— the tables below were first design estimates; each number is now what `tests/test_budget.py`
counts over a simulated day (time stepped by the minute, every field selected) and asserts, so a
change that costs more requests fails the suite. Every estimate held.

### Per cycle

| Request | Group | Count |
|---|---|---|
| `POST /invoices/query/metadata` | invoiceMetadata | 1; ≤ `MAX_PAGES` = 3 only with > 250 new invoices |
| `POST /auth/token/refresh` | auth | 0–1 (practically 1) |
| Full authentication | auth | 0, or 5–6 (key list only when its certificate expired) — weekly and at start |
| `GET /invoices/ksef/{n}` | invoiceDownload | 0–3 (only individual notifications, only FA(2)/FA(3), only when an XML field is selected), ≥ 250 ms apart |

### Per hour, worst case

| Group | Default interval (15 min) | With *Check now* every 10 min | Limit |
|---|---|---|---|
| invoiceMetadata | 4 (20 %) | 6 (30 %) | 20 / h, 16 / min |
| invoiceDownload | 12 (19 %) | 18 (28 %) | 64 / h, 16 / min, 8 / s |
| auth | ~4 | ~6 | 60 / s |
| other (`DELETE /auth/sessions/current`) | 0 (only on unload) | 0 | 120 / h |

Paging (only with > 250 new invoices) adds at most 2 metadata queries to one cycle: still
≤ 8 / h. Per minute, no group exceeds 4 requests. Because limits are per (NIP, IP), the remaining
70–80 % is what another program polling the same company from the same connection can use.

**Measured** (worst case: three new FA(3) invoices before every check):

| Group | Peak per s / min / h, timer only | Peak per h, *Check now* every 10 min | Per day, timer only |
|---|---|---|---|
| invoiceMetadata | 1 / 1 / 4 | 6 | 97 |
| invoiceDownload | 3 / 3 / 12 | 18 | 288 |
| auth | 5 / s once (the setup's authentication) | — | 1 authentication + 96 refreshes |

Over a simulated week: 2 full authentications (setup and day 7), 1 key-list download, one
refresh per check. **Live in a real Home Assistant 2026.9.4 on TEST** (2026-10-06, all 13 fields):
the baseline cost 5 authentication requests and 1 query; a check with 2 new invoices cost 1
refresh, 1 query and 2 downloads, and the last-check sensor read 2 / 2 for the hour; a check with
5 new invoices cost 1 refresh, 1 query and no download.

### Local resources

| Quantity | Bound |
|---|---|
| Persisted state | typical < 1 KB, maximum ≈ 55 KB per entry |
| Memory between cycles | `seen` (≤ 1 000 small entries, normally a handful), tokens, one `Invoice` |
| Transient per cycle | one metadata response (≤ 3 × 250 records), one XML body (≤ 4 MB) at a time |
| Event-loop time | JSON decode and formatting only; XML parsing in the executor |
| Pushes per device per day | ≤ 288 at the default interval (relay limit 500) |

**Measured** (2026-10-06):

- **Memory does not grow.** Over a simulated week with three invoices an hour, the memory
  allocated by the integration's own code and still held was 23.1 KB on day two and 23.6 KB on
  day seven (+0.5 KB after 360 more invoices, `tracemalloc`). Day one is warm-up: aiohttp's
  URL parser (yarl) caches the last 128 URLs, and each download has its own URL. Timers
  scheduled on the event loop: the same number on day two and day seven. `seen` held ≤ 3
  entries, the request counters one hour of requests (11).
- **Nothing blocks the event loop.** A full life cycle (setup, individual and combined checks
  with every field, switch off, reload, removal) ran with Home Assistant's blocking-call
  detection switched on (`block_async_io`, as in a real instance) without a detection, and the
  XML was parsed on an executor thread both times. A real Home Assistant 2026.9.4 started with
  `--debug` and Python's development mode (`-X dev`, asyncio debug: slow callbacks over 100 ms
  are logged) ran the integration against TEST for several checks without a blocking-call
  report or a slow-callback warning from it.
