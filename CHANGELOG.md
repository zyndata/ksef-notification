# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `docs/KSEF_API.md`: the part of the KSeF API 2.0 this integration uses, checked against the
  official documentation, the production/TEST/DEMO OpenAPI specifications and a live run on the
  TEST environment (2026-10-06) — environments, KSeF-token authentication, token lifetimes
  (access 15 minutes, refresh 7 days, KSeF token without expiry), the invoice metadata query
  and its high-water mark, the single-invoice XML fetch and the XML paths of every
  notification field, rate limits and penalties, the absence of any push mechanism, and the
  request budget with a minimum poll interval of 15 minutes.
- `docs/CONFIG.md`: every candidate notification field assigned to the invoice metadata or the
  invoice XML, with its exact key or path and whether it can be missing.
- `docs/ARCHITECTURE.md`: the complete design — module layout, KSeF client interface and error
  types, data flow, new-invoice detection on KSeF's high-water mark (never missed, never
  repeated, across restarts and clock differences), no historical notifications on first run,
  the small state kept in Home Assistant's storage (no invoice content), token lifecycle and
  re-authentication, polling every 15 minutes by default with a throttled manual check,
  individual notifications for up to 3 invoices and a combined one beyond, message formatting,
  hardened XML parsing with the standard library, and the request budget.

### Changed

- `docs/CONFIG.md` finalized: every option with its type, default and bounds, the 13 selectable
  invoice fields with labels, sources, default selection and rendering, the notification, the
  entities (including a *Check now* button) and the `ksef_notification_invoice` event payload.
