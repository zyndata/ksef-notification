# KSeF Notification

*Polish: „Powiadomienia KSeF"*

A [Home Assistant](https://www.home-assistant.io/) custom integration that watches
**KSeF** — the Polish national e-invoice system (Krajowy System e-Faktur) — for new **cost
invoices**, the invoices issued *to* your company, and sends a push notification with the
invoice details you chose to your phone.

It is a notifier, not an archive: an invoice is read, turned into a notification and
forgotten.

> **Unofficial.** This integration is not affiliated with, endorsed by or supported by the
> Ministry of Finance or the KSeF team. It uses KSeF's public API the way any accounting
> program does.

## Features

- **You choose what the notification says** — seller, seller NIP, invoice number, gross, net
  and VAT amounts, issue date, due date, payment form, bank account, line items, invoice type,
  KSeF number. The invoice itself is downloaded only when a field you picked needs it.
- **Nothing historical, nothing missed, nothing twice** — the first check after setup only takes
  note of what is already in KSeF. From then on every new invoice is notified exactly once,
  including the ones that arrived while Home Assistant was down.
- **Readable when many arrive** — up to three invoices in one check are notified one by one;
  from four on, one combined notification lists them.
- **Kind to KSeF's limits** — checks every 15 minutes by default (the shortest interval the
  official guidance allows), with a throttled *Check now* button, and backs off when KSeF asks
  it to.
- **Fits into automations** — a `ksef_notification_invoice` event for every new invoice, a
  sensor for the most recent invoice, a diagnostic sensor that tells "no new invoices" apart
  from "KSeF has not answered", and a switch to pause notifications.
- **No invoice archive** — no XML, PDF or database on disk. See [Privacy](#privacy).

## Requirements

- Home Assistant 2026.9 or newer.
- The **Home Assistant Companion app** on the phone to notify (a `notify.mobile_app_*`
  service).
- A **KSeF token** for the company, generated with the **InvoiceRead** permission only — see
  below.

## Getting a KSeF token

1. Log in to the KSeF taxpayer app (*Aplikacja Podatnika KSeF*) of the environment you want to
   watch, in the context of your company's NIP. For real invoices that is production:
   [ap.ksef.mf.gov.pl](https://ap.ksef.mf.gov.pl).
2. Open **Tokeny → Generuj token**, give it a name (for example "Home Assistant") and tick
   **only** the permission to read invoices (`InvoiceRead`).
3. Generate it and copy the value — it is shown only once.

The token never expires on its own; you can revoke it in the same place at any time. Anyone who
holds it can read your company's invoices, so treat it like a password. A token works only in
the environment and for the NIP it was generated for.

## Installation

### HACS (recommended)

1. In HACS, add this repository as a custom repository (category: *Integration*), or install
   it directly from the HACS store once it is included there.
2. Install **KSeF Notification** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration** and search for
   **KSeF Notification**.

### Manual

Copy `custom_components/ksef_notification/` into the `custom_components/` folder of your Home
Assistant configuration directory and restart Home Assistant.

### Development period: manual install

To try a version that has not been released yet, deploy a working copy into a local Home
Assistant instance — by copying the folder as above, by symlinking it
(`ln -s /path/to/repo/custom_components/ksef_notification /path/to/ha-config/custom_components/ksef_notification`),
or with the repository's deploy script (`python scripts/install.py`, target path configured in
`.env` — see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md)). Use the KSeF **test** environment
for this, never production data.

## Configuration

Everything is configured from the UI — a three-step wizard (KSeF access, notification,
behaviour) plus an options flow for later changes. The wizard checks the token against KSeF
before it accepts it. One entry watches one company in one KSeF environment; add more entries
for more companies.

See [docs/CONFIG.md](docs/CONFIG.md) for every option, every selectable field, the entities and
the event payload.

## Privacy

- **Stored by this integration:** one small file per entry in Home Assistant's own storage,
  holding a KSeF timestamp and short hashes of the invoices already handled — no amount, name,
  number or NIP. Access tokens are kept in memory only.
- **Stored by Home Assistant:** the KSeF token in the config entry, like any integration's
  credentials. Home Assistant's recorder also stores every event, including
  `ksef_notification_invoice` with the fields you selected, and the `call_service` event Home
  Assistant itself fires for every service call — for the notification, that one contains its
  title and text. To keep invoice data out of the database, exclude both:

  ```yaml
  recorder:
    exclude:
      event_types:
        - ksef_notification_invoice
        - call_service
  ```

  Excluding `call_service` drops the record of every service call, not only this
  integration's; Home Assistant offers no narrower way.

- **The notification itself** travels through the Companion app's push relay and Google's or
  Apple's push service, and stays in your phone's notification history — it is invoice data by
  definition, so select only the fields you are comfortable sending that way.

## How it works

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the design and
[docs/KSEF_API.md](docs/KSEF_API.md) for the part of the KSeF API it uses, its limits and the
request budget.

## Development

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md). One command sets up the environment:
`python scripts/setup.py`.

## License

[MIT](LICENSE)
