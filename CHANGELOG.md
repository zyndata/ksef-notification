# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- The setup wizard in three steps — KSeF access, notification, behaviour. The first step
  checks the NIP (including its check digit) and then the KSeF token against KSeF itself, with
  one login and one small invoice query, and tells a wrong token, a token without the
  InvoiceRead permission, a blocked account, KSeF asking to slow down and an unreachable KSeF
  apart. The same company can be added once per KSeF environment; a duplicate is refused before
  anything is sent to KSeF. The second step offers the registered Companion-app phones and the
  13 invoice fields (four preselected); the third the check interval (15–1 440 minutes).
- An options flow to change the phone, the fields and the interval later; the integration
  reloads only when something actually changed.
- A re-authentication flow for entering a new KSeF token when KSeF stops accepting the old one;
  nothing already notified is notified again.
- English texts for every step, field, error and choice of the flows.
- Tests for all three flows against the scripted KSeF, including a check that the token never
  appears in the log or on a form.

- The decision core (`core/`), pure Python without Home Assistant: an invoice model built from
  KSeF's metadata in which every field may be missing; a hardened reader for the invoice XML
  (FA(2) and FA(3)) that refuses DTDs and entities, bounds size, depth and text, and picks
  only the due dates, payment form, bank account and line items the notification can show —
  never a factor's account, a partial payment's form or a correction's "before" rows; the
  new-invoice tracker that decides what to notify from KSeF's high-water mark, keeps only
  hashes and timestamps, and never notifies an invoice twice or misses one across restarts;
  and the message formatter: the selected fields in a fixed order, Polish and English number
  and date rules, grouped bank accounts, seller-written text cleaned and shortened, absent
  values shown as "—", and one combined message for more than three invoices.
- English texts for notification titles, field labels, payment forms and invoice types.
- Tests for all of it, including a simulation of three days of checks with late invoices,
  partial queries, deferrals and restarts, and hostile XML documents.

- The KSeF client (`client/`): logs in with a KSeF token (RSA-OAEP encryption with the current
  public key, status polling, redeem), keeps the 15-minute access token alive with the refresh
  token and falls back to a fresh login when KSeF refuses the refresh, and tells a revoked or
  under-privileged KSeF token apart from a KSeF outage. It lists cost invoices by the date they
  were stored in KSeF, page by page, with KSeF's completeness marker, and downloads one
  invoice's XML. It honours KSeF's `Retry-After` and refuses further calls to a blocked group
  until it has passed, spaces downloads 250 ms apart, caps response sizes, and counts its own
  requests per limit group. Tokens are kept in memory only and never logged.
- Tests for the client against a scripted, offline KSeF, with hand-written synthetic fixtures
  and a test that rejects any fixture value not on the list of invented NIPs, names, account
  numbers and amounts.

## [0.1.0] - 2026-10-06

The development baseline: the design is complete and the integration installs, but it does not
check KSeF or notify anything yet — adding it ends with *"The setup wizard is not implemented
yet"*.

### Added

- The integration skeleton: `custom_components/ksef_notification/` with its manifest (version
  0.1.0, no extra Python requirements), a placeholder setup wizard, and the module layout of
  the design.
- Packaging for HACS (`hacs.json`, `info.md`), a README, the MIT license and a security policy
  with private vulnerability reporting.
- Continuous integration: lint and tests, Home Assistant's `hassfest` and HACS validation on
  every push and weekly, and a release workflow that publishes a GitHub release from this file
  when a `vX.Y.Z` tag is pushed. Dependabot for the workflow actions, and issue forms that ask
  reporters to remove tokens, NIPs and invoice data.
- A one-command development environment (`python scripts/setup.py`), task scripts for lint,
  format, tests, deploying into a local Home Assistant and releasing, and
  `docs/DEVELOPMENT.md`. On Windows the tests run in a local Docker container without network
  access.
- `docs/KSEF_API.md`: the part of the KSeF API 2.0 this integration uses, checked against the
  official documentation, the production/TEST/DEMO OpenAPI specifications and a live run on the
  TEST environment (2026-10-06) — environments, KSeF-token authentication, token lifetimes
  (access 15 minutes, refresh 7 days, KSeF token without expiry), the invoice metadata query
  and its high-water mark, the single-invoice XML fetch and the XML paths of every
  notification field, rate limits and penalties, the absence of any push mechanism, and the
  request budget with a minimum poll interval of 15 minutes.
- `docs/ARCHITECTURE.md`: the complete design — module layout, KSeF client interface and error
  types, data flow, new-invoice detection on KSeF's high-water mark (never missed, never
  repeated, across restarts and clock differences), no historical notifications on first run,
  the small state kept in Home Assistant's storage (no invoice content), token lifecycle and
  re-authentication, polling every 15 minutes by default with a throttled manual check,
  individual notifications for up to 3 invoices and a combined one beyond, message formatting,
  hardened XML parsing with the standard library, and the request budget.
- `docs/CONFIG.md`: every option with its type, default and bounds, the 13 selectable invoice
  fields with labels, sources (invoice metadata or invoice XML, with the exact key or path),
  default selection and rendering, the notification, the entities (including a *Check now*
  button) and the `ksef_notification_invoice` event payload.

[Unreleased]: https://github.com/zyndata/ksef-notification/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/zyndata/ksef-notification/releases/tag/v0.1.0
