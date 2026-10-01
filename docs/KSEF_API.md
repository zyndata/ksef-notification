# KSeF API

> **Stub — filled in during phase 0.** What is written below is what KSeF Manager's client
> *does*, read from its code on 2026-10-01. **None of it is verified.** Phase 0 replaces every
> "unverified" with a finding checked against the official documentation, dated and linked. The
> reference code is already known to be unreliable in places — its base URLs disagree with each
> other, and its limits exist only as comments.

## Environments

TODO (phase 0): base URL of production, and of the test and demo environments if they exist;
which a taxpayer's portal token belongs to.

Unverified, from the reference code — four different values appear:
`https://api.ksef.mf.gov.pl/v2`, `https://api-test.ksef.mf.gov.pl/v2`,
`https://ksef-test.mf.gov.pl/api`, `https://api-ksef-test.mf.gov.pl`.

## Authentication with a KSeF token

TODO (phase 0): the verified sequence, the encryption scheme, the certificate to use, the
permissions the token needs for reading invoices, how a user generates one.

Unverified, from the reference code:

1. `POST /auth/challenge` with `contextIdentifier: {type: "NIP", value}` → `challenge`, `timestampMs`.
2. `GET /security/public-key-certificates` → pick the certificate whose `usage` contains
   `KsefTokenEncryption`.
3. Encrypt `{token}|{timestampMs}` with RSA-OAEP (SHA-256, MGF1-SHA-256), Base64.
4. `POST /auth/ksef-token` → `authenticationToken.token`, `referenceNumber`.
5. Poll `GET /auth/{referenceNumber}` until `status.code == 200`.
6. `POST /auth/token/redeem` → `accessToken.token` (+ `validUntil`), `refreshToken.token`.

## Token lifetimes

TODO (phase 0): access token validity, refresh token validity, the refresh call, what happens
when the refresh token expires.

Unverified: refresh is `POST /auth/token/refresh` with the refresh token as Bearer. The
reference project observed a refresh token living about 24 hours against a documented 7 days.

## Listing invoices

TODO (phase 0): endpoint, request shape, every response field, result cap, `hasMore` /
`isTruncated` semantics, and **which `dateType` finds invoices by when they arrived in KSeF**.

Unverified: `POST /invoices/query/metadata?pageSize=&pageOffset=&sortOrder=` with body
`{subjectType: "Subject2", dateRange: {dateType, from, to}}`; the reference polls on
`PermanentStorage`. Response items under `invoices`, with `ksefNumber`, `invoiceNumber`,
`issueDate`, `seller{nip,name}`, `buyer{identifier{type,value},name}`, `netAmount`, `vatAmount`,
`grossAmount`, `currency`, `invoiceType`.

## Fetching one invoice

TODO (phase 0): endpoint, schema versions in circulation, XML path of every field the metadata
does not carry.

Unverified: `GET /invoices/ksef/{ksefNumber}` with `Accept: application/xml`. Paths used by the
reference parser: `Platnosc/TerminPlatnosci/Termin`, `Platnosc/FormaPlatnosci`,
`Platnosc/RachunekBankowy/NrRB`, `FaWiersz/P_7`.

## Rate limits

TODO (phase 0): documented limits per endpoint group, how exceeding them is signalled, penalties.

Unverified, from code comments only: 20 requests per hour for the metadata query, 64 per hour
for the single-invoice download.

## Push or webhook

TODO (phase 0): confirm that none exists, so that polling is the design and not an oversight.

## Request budget

TODO (phase 0): requests per poll cycle and per hour in the worst case; the shortest poll
interval that stays well inside the limits.
