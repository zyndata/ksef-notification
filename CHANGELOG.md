# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Quiet hours.** In the *Behaviour* step (and later under *Configure*) you can set a daily
  window, for example 22:00 to 06:00, in which KSeF is not asked at all and so no notification
  arrives. Invoices that came in meanwhile are notified by the first check when the window ends.
  Off by default; *Check now* still works during quiet hours.

## [1.0.0] - 2026-10-06

The first release. KSeF Notification watches KSeF, the Polish national e-invoice system, for new
cost invoices of your company and sends each one to your phone.

### Added

- **New cost invoices on your phone.** Every 15 minutes (or the interval you choose, up to once
  a day) the integration asks KSeF for invoices issued to your company and sends each new one to
  a phone with the Home Assistant Companion app: one notification per invoice, or one summary
  when four or more arrive at once. Corrections get their own title.
- **You choose what the notification says**, from 13 fields: seller, seller NIP, invoice
  number, gross, net and VAT amounts, issue date, due date, payment form, bank account, items,
  invoice type and KSeF number. Seller, invoice number, gross amount and due date are
  preselected. The invoice itself is downloaded only when a field you picked needs it.
- **Nothing historical, nothing missed, nothing twice.** Adding the integration, or switching
  notifications back on, only takes note of what is already in KSeF. Invoices that arrive while
  Home Assistant is down are notified once it is back. Only a power cut in the instant between a
  notification and its record can repeat that one notification.
- **A setup wizard** in three steps: KSeF access (production, demo or test environment, the
  company's NIP and a KSeF token, checked against KSeF before it is saved, with a clear message
  for a wrong token, a token without the InvoiceRead permission, a blocked account, KSeF asking
  to slow down and KSeF being unreachable), the notification (phone and fields) and the check
  interval. Options change the phone, the fields and the interval later. When KSeF stops
  accepting the token, Home Assistant asks for a new one, and nothing already notified is
  notified again. Several companies, and one company in several environments, can be added.
- **Entities:** a *Notifications* switch (off means no contact with KSeF at all), a *Last
  invoice* sensor with the selected fields as attributes (kept out of Home Assistant's
  history), a diagnostic *Last check* sensor that tells "no new invoices" apart from "KSeF has
  not answered", and a *Check now* button usable once every 10 minutes.
- **The `ksef_notification_invoice` event** for every new invoice, with the selected fields,
  for your own automations.
- **Repair notices** when the phone's notify service is missing or a push fails, and when KSeF
  blocks access for the company. A diagnostics download with the token, the NIP, the phone and
  every invoice value hidden.
- **Kind to KSeF's limits.** Measured at the default interval with every field selected: at
  most 4 of the 20 invoice queries and 12 of the 64 downloads KSeF allows per hour. When KSeF
  asks to slow down, the next check waits as long as it says; an outage only delays checks.
- **No invoice archive.** No invoice XML, PDF or database on disk; Home Assistant's storage
  holds only a timestamp and short hashes of the invoices already handled, and KSeF session
  tokens stay in memory. Invoice XML is read with a hardened parser.
- **Polish and English** throughout: the wizard, the entities, the repair notices and the
  notifications, with Polish amounts and dates when Home Assistant is in Polish. In Polish the
  integration is called "Powiadomienia KSeF".
- An icon and logo of its own, shipped with the integration. They deliberately do not use the
  KSeF or Ministry of Finance logo.
- Documentation: installation, getting a KSeF token, screenshots, privacy (including how to keep
  invoice data out of Home Assistant's database) and troubleshooting in the README; every option
  and the event payload in `docs/CONFIG.md`; the design in `docs/ARCHITECTURE.md`; the KSeF API
  as verified on KSeF's test environment, with its limits and failure behaviour, in
  `docs/KSEF_API.md`.

## [0.1.0] - 2026-10-06

Never released (no tag, no GitHub release). The development baseline: the design is complete and the integration installs, but it does not
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

[Unreleased]: https://github.com/zyndata/ksef-notification/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/zyndata/ksef-notification/releases/tag/v1.0.0
