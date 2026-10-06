# Security policy

## Supported versions

Only the latest released version is supported. Report against it whenever possible.

## Reporting a vulnerability

Use GitHub's private vulnerability reporting:
**Security → Advisories → Report a vulnerability** in this repository.

Please do not open a public issue for a security problem, and never include a real KSeF token,
NIP or invoice in a report — an invented one shows the problem just as well.

Expect an initial response within 14 days.

## Scope

This integration holds a KSeF token that can read a company's invoices, and it handles invoice
data written by third parties. The most likely security-relevant problems are therefore:

- the KSeF token, an access or refresh token, or the company's NIP leaking into logs,
  diagnostics, events, error messages or outbound requests other than to KSeF;
- invoice content reaching storage the integration documents as free of it;
- unsafe handling of an untrusted invoice XML or KSeF response (entity expansion, external
  entities, unbounded memory or recursion);
- seller-written text in a notification escaping into a context that treats it as markup or a
  command;
- requests to KSeF that could get the user's account rate-limited or blocked.

Bugs in Home Assistant itself belong to
[the Home Assistant security policy](https://github.com/home-assistant/core/security/policy).
Problems in KSeF itself belong to the Ministry of Finance.
