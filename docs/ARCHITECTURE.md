# Architecture

> **Stub — filled in during phase 1** (see [PLAN.md](../PLAN.md)). Prerequisite:
> [KSEF_API.md](KSEF_API.md) completed in phase 0. Every design decision here must respect two
> hard constraints: **no invoice content is written to disk**, and **the request budget in
> KSEF_API.md is never exceeded**. Runtime dependencies are limited to what Home Assistant core
> already ships.

## Module layout

TODO (phase 1): modules inside `custom_components/ksef_notification/` and their responsibilities.

## Data flow

TODO (phase 1): poll → list metadata since the high-water mark → drop already-notified → fetch
XML only where a selected field needs it → invoice model → format → notify → record as notified.

## New-invoice detection

TODO (phase 1): the high-water mark, the set of notified KSeF numbers, how both are bounded, and
how an invoice is never notified twice and never missed — across restarts, mid-cycle failures
and clock differences.

## First run

TODO (phase 1): what happens when the integration is added to a company that already has
invoices in KSeF. Proposed at bootstrap: start from "now", notify nothing historical.

## What is persisted

TODO (phase 1): exactly what is written to Home Assistant's storage, its maximum size, and the
statement that it contains no invoice content.

## Token lifecycle

TODO (phase 1): when to authenticate, when to refresh, what happens when both fail, how it
reaches the user (re-authentication flow).

## Coordinator scheduling

TODO (phase 1): poll interval (default, minimum, maximum), behaviour while the enable switch is
off, after HTTP 429, after an outage; whether a manual "check now" exists.

## Several invoices at once

TODO (phase 1): one notification per invoice versus a combined one; the threshold.

## Message formatting

TODO (phase 1): field order, formatting of amounts, dates and bank accounts, what is shown when
a selected value is absent.

## XML safety

TODO (phase 1): how third-party XML is parsed safely with the allowed dependency set.

## Outputs

TODO (phase 1): entities, the notification's title/body/data, the event payload — public
contracts from 1.0.0 on.

## Resource and request budget

TODO (phase 1): per cycle and per hour, consistent with [KSEF_API.md](KSEF_API.md). Phase 8
replaces estimates with measurements.
