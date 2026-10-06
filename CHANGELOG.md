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
