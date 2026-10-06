# KSeF Notification

**Know the moment a cost invoice arrives.** This integration watches KSeF — the Polish
national e-invoice system (Krajowy System e-Faktur) — for new invoices issued **to** your
company and sends a push notification to your phone with the details you chose: seller,
invoice number, amount, due date and more.

- Pick the fields the notification carries; the invoice itself is downloaded only when a
  field you picked needs it.
- Nothing historical on setup: the first check only takes note of what is already there.
  Invoices that arrive while Home Assistant is down are notified once it is back.
- Several invoices at once become one combined notification instead of a burst.
- Stays well inside KSeF's request limits: checks every 15 minutes by default, with a
  throttled *Check now* button.
- No invoice archive: no XML, PDF or database on disk. The only thing stored is what is needed
  not to notify the same invoice twice.
- An event for every new invoice, for your own automations.

Set up from the UI with a KSeF token that has only the **InvoiceRead** permission. Production,
demo and test environments are supported. Fully localized in Polish, where the integration is
called "Powiadomienia KSeF".

This is an unofficial integration, not affiliated with or endorsed by the Ministry of Finance.
