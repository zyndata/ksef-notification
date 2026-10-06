# KSeF API

The part of the KSeF API 2.0 that this integration uses, and nothing else. Every claim carries the
date it was checked and its source. Where the documentation and the live service were compared,
the observation is marked **observed** and was made on the TEST environment.

## Sources

| Key | Source | Version checked |
|---|---|---|
| **DOCS** | Official integration documentation by the Ministry of Finance — [github.com/CIRFMF/ksef-api](https://github.com/CIRFMF/ksef-api) (formerly `ksef-docs`) | commit `c50f855`, 2026-09-22 |
| **OAS-PROD** | OpenAPI of production — [api.ksef.mf.gov.pl/docs/v2](https://api.ksef.mf.gov.pl/docs/v2/index.html) (`openapi.json`) | downloaded 2026-10-06 (title "KSeF API PR v2") |
| **OAS-TEST** | OpenAPI of TEST — [api-test.ksef.mf.gov.pl/docs/v2](https://api-test.ksef.mf.gov.pl/docs/v2/index.html) | downloaded 2026-10-06 |
| **OAS-DEMO** | OpenAPI of DEMO — [api-demo.ksef.mf.gov.pl/docs/v2](https://api-demo.ksef.mf.gov.pl/docs/v2/index.html) | downloaded 2026-10-06 |
| **CHANGELOG** | [api-changelog.md](https://github.com/CIRFMF/ksef-api/blob/main/api-changelog.md) | up to version 2.8.1 (PROD 2026-09-23) |
| **HANDBOOK** | "Podręcznik KSeF 2.0, cz. I — Rozpoczęcie korzystania z KSeF", Ministry of Finance, [PDF](https://ksef.podatki.gov.pl/media/dmrfdixs/podrecznik-ksef-20-cz-i-rozpoczecie-korzystania-z-ksef-06082026.pdf) | edition of 2026-08-06 |
| **XSD** | Invoice schemas FA(2) and FA(3) in [faktury/schemy/FA](https://github.com/CIRFMF/ksef-api/tree/main/faktury/schemy/FA) | as in DOCS |
| **PROBE** | A live run against TEST with a test-environment KSeF token: two authentications, one refresh, one metadata query, session revocation, invalid-token calls | 2026-10-06 |

All three OpenAPI documents agree on every endpoint, field and limit used below; where this
document quotes OpenAPI it means all three.

---

## Environments

Checked 2026-10-06 — DOCS [srodowiska.md](https://github.com/CIRFMF/ksef-api/blob/main/srodowiska.md) (dated 2026-03-16), `servers` of OAS-PROD/TEST/DEMO.

| Environment | API base URL | Taxpayer app (generates tokens) | Purpose |
|---|---|---|---|
| **PROD** | `https://api.ksef.mf.gov.pl/v2` | `https://ap.ksef.mf.gov.pl` | Real invoices with legal effect |
| **DEMO** (pre-production) | `https://api-demo.ksef.mf.gov.pl/v2` | `https://ap-demo.ksef.mf.gov.pl` | Mirrors the production configuration and limits; final validation |
| **TEST** | `https://api-test.ksef.mf.gov.pl/v2` | `https://ap-test.ksef.mf.gov.pl` | Release candidates; fictional data only; self-signed certificates allowed |

- The `/v2` path segment is part of the base URL; every path below is relative to it.
- **A token belongs to exactly one environment** — the one whose taxpayer app or API generated
  it. Each environment is a separate system with its own tokens, permissions and invoices. A
  token from the production taxpayer app works only against PROD.
- TEST and DEMO must never receive real invoices or real company data (DOCS). TEST data is
  shared between integrators because anyone can authenticate as any NIP there with a
  self-signed certificate; random NIPs are expected.
- TEST may be down for maintenance **16:00–18:00** on any day (DOCS, since 2025-10-01).
- Formats accepted for **new** invoices: PROD and DEMO — FA(3), PEF(3), PEF_KOR(3) (FA_RR(1)
  since 2026-04-01 per OpenAPI `FormCode`); TEST also FA(2).
- Taxpayer app hosts checked to answer HTTP 200 on 2026-10-06; TEST app URL from
  [ksef.podatki.gov.pl](https://ksef.podatki.gov.pl/aplikacja-podatnika-ksef-20/aplikacja-podatnika-ksef-20-wersja-testowa/).

---

## Authentication with a KSeF token

### Is the KSeF token here to stay?

Yes. Checked 2026-10-06 — HANDBOOK §2.4 (p. 34–36) and its change log (p. 101). The original plan
withdrew tokens on 2026-12-31 in favour of KSeF certificates; the Ministry reversed this and
**keeps token authentication indefinitely**. A token has **no expiry**: it stays valid until the
user revokes it. Secondary press reports of the 2026 withdrawal are outdated.

### How a user generates a token

Checked 2026-10-06 — HANDBOOK §2.4.6–2.4.7, DOCS [tokeny-ksef.md](https://github.com/CIRFMF/ksef-api/blob/main/tokeny-ksef.md).

1. Log in to the taxpayer app of the right environment (table above) with the national login
   node (Węzeł krajowy), a qualified signature/seal, or a KSeF certificate — in the context of
   the company's NIP.
2. Menu **Tokeny → Generuj token**; enter a name and tick the permissions.
3. Press **Generuj token** and copy the value. **It is shown only once.**

Rules that matter here:

- **Permission needed: `InvoiceRead`** — both endpoints this integration calls
  (`POST /invoices/query/metadata`, `GET /invoices/ksef/{ksefNumber}`) declare
  `x-required-permissions: [InvoiceRead]` (OpenAPI). A read-only token with `InvoiceRead` alone
  is sufficient; nothing else should be granted. All token permissions:
  `InvoiceRead`, `InvoiceWrite`, `CredentialsRead`, `CredentialsManage`, `SubunitManage`,
  `EnforcementOperations`, `Introspection`, `CollectiveIdentifierManage` (OpenAPI
  `TokenPermissionType`).
- A token's permissions are fixed at generation; changing them means generating a new token.
- A token works **only in the context (NIP) it was generated in**.
- Revoking the permissions behind a token does not revoke the token, it only makes
  authentication fail (status 415, below). Restoring them makes the token work again.
- No limit on the number of tokens.
- The KSeF token is a secret: anyone holding it can read the company's invoices.

### The sequence

Checked 2026-10-06 — DOCS [uwierzytelnianie.md](https://github.com/CIRFMF/ksef-api/blob/main/uwierzytelnianie.md) §1, §2.2, §3, §4; OpenAPI; **observed** in PROBE.

| # | Call | Auth | Request | Response |
|---|---|---|---|---|
| 0 | `GET /security/public-key-certificates` | none | — | Array of `{certificate, certificateId, publicKeyId, validFrom, validTo, usage[]}` |
| 1 | `POST /auth/challenge` | none | no body | `200` `{challenge, timestamp, timestampMs, clientIp}` |
| 2 | `POST /auth/ksef-token` | none | `{challenge, contextIdentifier: {type: "Nip", value: <NIP>}, encryptedToken, publicKeyId}` | `202` `{referenceNumber, authenticationToken: {token, validUntil}}` |
| 3 | `GET /auth/{referenceNumber}` | `Bearer <authenticationToken>` | — | `200` `{startDate, authenticationMethodInfo, status: {code, description, details?}, isTokenRedeemed?, refreshTokenValidUntil?, …}` |
| 4 | `POST /auth/token/redeem` | `Bearer <authenticationToken>` | no body | `200` `{accessToken: {token, validUntil}, refreshToken: {token, validUntil}}` |

Then every protected call sends `Authorization: Bearer <accessToken>`.

**Step 0 — which certificate.** Choose the entry whose `usage` contains `KsefTokenEncryption`
and which is valid now; if several are, the one with the **latest `validFrom`**. Send its
`publicKeyId` in step 2. If step 2 answers `400` with code **21470** (key unknown or withdrawn),
re-download the list and retry once with the new key. Keys can rotate — planned with an overlap
period, or in an emergency without one. Certificates are X.509 DER, Base64, issued to
"Ministerstwo Finansów". Checked 2026-10-06 — DOCS
[klucze-publiczne-do-szyfrowania.md](https://github.com/CIRFMF/ksef-api/blob/main/bezpieczenstwo/klucze-publiczne-do-szyfrowania.md)
(dated 2026-05-05), CHANGELOG 2.5.0. **Observed:** PROD and TEST each publish one
`KsefTokenEncryption` certificate, RSA 2048-bit, valid 2025-09-29 → 2027-09-29.

**Step 2 — encryption.** Plaintext `"{ksefToken}|{timestampMs}"` (UTF-8), where `timestampMs` is
the integer from step 1. Encrypt with **RSA-OAEP, SHA-256 as the hash and MGF1-SHA-256, empty
label**; send Base64. Confirmed in the official clients (C#: `RSAEncryptionPadding.OaepSHA256`;
Java: `OAEPParameterSpec("SHA-256", "MGF1", MGF1ParameterSpec.SHA256, PSource.PSpecified.DEFAULT)`).
The timestamp acts as a nonce; a challenge lives **10 minutes**. `authorizationPolicy` (IP
allow-list) is optional and not used — Home Assistant's public IP can change.
`contextIdentifier.type` is the enum `Nip | InternalId | NipVatUe | PeppolId` — spelled `Nip`.
**Observed:** `"NIP"` was also accepted on 2026-10-06, but only `Nip` is the contract.

**Step 3 — status codes** (`status.code` in the body, the HTTP status is 200):

| Code | Meaning | What it means for us |
|---|---|---|
| 100 | In progress | Poll again |
| 200 | Success | Redeem |
| 415 | Failed — no permissions assigned | Token valid but its permissions were taken away → user action |
| 425 | Authentication revoked (session and its refresh tokens revoked by the user) | Start over |
| 450 | Failed — bad token. `details`: invalid challenge, invalid token, invalid token time, **token revoked**, **token inactive**, invalid encryption, invalid encoding, token cannot be used in context {NIP} | **The KSeF token itself is the problem → re-authentication flow** (except "invalid challenge"/"invalid token time", which are retryable once) |
| 460 | Certificate error | Not applicable to token auth |
| 470 | Authorisation methods of a deceased person | User action |
| 480 | Blocked — suspected security incident; contact the Ministry | User action, stop trying |
| 500 | Unknown error | Temporary failure |
| 550 | Cancelled by the system, retry | Temporary failure |

`POST /auth/ksef-token` can answer `400` with **21111** (invalid challenge), **21470** (key
unknown or withdrawn) or **21405** (input validation) — OpenAPI, checked 2026-10-06. The exact
status-450 detail strings are listed in the OpenAPI description of `status`; the two a new
challenge cures are `Nieprawidłowe wyzwanie autoryzacyjne` and `Nieprawidłowy czas tokena`.

Status retention: the operation is queryable for **7 days**, then `410 Gone` (CHANGELOG 2.4.0).
HTTP `400` code 21304 = unknown reference number. **Observed:** with a KSeF token the status was
200 on the first poll, about 0.5 s after step 2, in both runs; on 2026-10-06 in phase 3 (the
integration's own client) it was still 100 at 0.5 s and 200 at 1.5 s. (XAdES authentication can stay at
100 for a long time while certificate revocation is checked; token authentication does not
involve that.)

**Step 4 — redeem** returns the pair **once**. A second call answers `400` code **21301**
"tokens … already retrieved" (**observed**). Other 400/21301 details: status does not permit
redeeming, **KSeF token revoked**; 21308 deceased person.

The `authenticationToken` from step 2 is valid **45 minutes** (**observed**; `validUntil` in the
response).

## Token lifetimes

Checked 2026-10-06 — DOCS uwierzytelnianie.md §4–5, OpenAPI, **observed** in PROBE.

| Token | Documented | Observed on TEST, 2026-10-06 |
|---|---|---|
| Access token (JWT) | "a dozen or so minutes", see `exp` | **15 min 00 s** (`validUntil` and JWT `exp − iat`) |
| Refresh token (JWT) | "up to 7 days", reusable for many refreshes | **7 days 00:00:00** (`validUntil`, JWT `exp − iat`, and `refreshTokenValidUntil` from `GET /auth/{ref}` all agree) |
| Authentication operation token | — | 45 min |
| KSeF token | No expiry; valid until revoked (HANDBOOK §2.4.2) | — |

**Never assume a lifetime: use the `validUntil` returned with each token.** The documentation
says "up to" 7 days; PROD was not measured (it needs a real token and is not done in this
project's tests).

**Refresh.** `POST /auth/token/refresh`, header `Authorization: Bearer <refreshToken>`, no body →
`200 {accessToken: {token, validUntil}}`. **The response contains only a new access token**
(OpenAPI `AuthenticationTokenRefreshResponse`, **observed**): the refresh token is not rotated and
keeps its original expiry. So after at most 7 days a full authentication is unavoidable.

**When the refresh fails:**

| Response | When | **Observed** |
|---|---|---|
| `400` code **21301**, detail "authentication status (425) does not permit refreshing" | The session was revoked | yes — after `DELETE /auth/sessions/current` |
| `400` code 21301, "KSeF token revoked" | The KSeF token was revoked | documented |
| `400` code 21304 | Authentication operation not found | documented |
| `401` | Refresh token malformed, or (by JWT semantics) expired | yes — malformed |

In every case the fallback is the full sequence above with the KSeF token; only a step-3 status
of 415/450 (token-related)/470/480, or step-4 21301 "KSeF token revoked", means the user must
act.

**Other facts.** A protected call refused with `403` carries a `reasonCode` (problem details):
`missing-permissions` (with `requiredAnyOfPermissions` / `presentPermissions`),
`security-service-blocked`, `ip-not-allowed`, `insufficient-resource-access`,
`auth-method-not-allowed`, `context-type-not-allowed` (OpenAPI `ForbiddenProblemDetails`,
checked 2026-10-06). An access token stays valid until its `exp` even if permissions change or the
session is revoked (DOCS). A protected call with a bad or expired access token answers `401`
(**observed** with a malformed token). `DELETE /auth/sessions/current` revokes the session's
refresh token (204, **observed**).

---

## Listing invoices

Checked 2026-10-06 — OpenAPI `POST /invoices/query/metadata`; DOCS
[pobieranie-faktur.md](https://github.com/CIRFMF/ksef-api/blob/main/pobieranie-faktur/pobieranie-faktur.md),
[hwm.md](https://github.com/CIRFMF/ksef-api/blob/main/pobieranie-faktur/hwm.md) (2025-11-25),
[przyrostowe-pobieranie-faktur.md](https://github.com/CIRFMF/ksef-api/blob/main/pobieranie-faktur/przyrostowe-pobieranie-faktur.md) (2025-11-21);
**observed** once on TEST (an empty result for the test NIP).

### Request

`POST /invoices/query/metadata?pageSize=…&pageOffset=…&sortOrder=…` — **paging is in the query
string, the filter is in the body.** Required permission `InvoiceRead`.

| Query parameter | Type | Default | Bounds |
|---|---|---|---|
| `sortOrder` | `Asc` \| `Desc` | `Asc` | — sorts by the date type chosen in the filter |
| `pageOffset` | int | 0 | ≥ 0 — **a page index**, not a record offset ("index of the first page of results, 0 = first page"; the same parameter on other endpoints is called "page number" and the official clients increment it by 1) |
| `pageSize` | int | 10 | **10–250** |

Body (`InvoiceQueryFilters`), the fields used here:

```json
{
  "subjectType": "Subject2",
  "dateRange": {
    "dateType": "PermanentStorage",
    "from": "2026-10-06T07:30:00.000+00:00",
    "to": null,
    "restrictToPermanentStorageHwmDate": false
  }
}
```

- `subjectType`: `Subject1` seller, **`Subject2` buyer** (cost invoices), `Subject3` third party
  (e.g. additional buyer, recipient unit), `SubjectAuthorized`.
- `dateRange.dateType`: `Issue` (issue date), `Invoicing` (accepted by KSeF for processing),
  `PermanentStorage` (permanently stored in the repository).
- `from` required; `to` optional — **omitted means "now" (UTC)**.
- **Always send an explicit offset or `Z`.** A value without one is interpreted as
  Europe/Warsaw local time (CHANGELOG 2.1.2).
- **The maximum range is 100 days** in UTC (CHANGELOG 2.7.1; was 3 months, before that 2 years).
- `restrictToPermanentStorageHwmDate: true` caps `to` at the high-water mark (only with
  `PermanentStorage`). With it, a `from` later than the HWM is `400` code **21183**.
- Optional exact-match filters exist (`ksefNumber`, `invoiceNumber`, `sellerNip`, `amount`,
  `currencyCodes`, `invoiceTypes`, `formType`, `hasAttachment`, …) and are not used.
- Send `X-Error-Format: problem-details` to get errors as `application/problem+json`; without it
  400 and 429 bodies use a deprecated legacy shape.

### Response

`200` `QueryInvoicesMetadataResponse`:

| Field | Required | Meaning |
|---|---|---|
| `invoices` | yes | Array of `InvoiceMetadata`, below |
| `hasMore` | yes | More results exist after this page |
| `isTruncated` | yes | The **10 000-record cap per filter set** was reached |
| `permanentStorageHwmDate` | no | Only for `PermanentStorage` queries: the high-water mark, the same on every page of one query |

Stop conditions (OpenAPI description):

- `hasMore = false` → done.
- `hasMore = true`, `isTruncated = false` → next page (`pageOffset + 1`).
- `hasMore = true`, `isTruncated = true` → set `dateRange.from` to the date of the last returned
  record, reset `pageOffset` to 0, continue (expect the boundary record again).

`InvoiceMetadata` — every field, with whether OpenAPI marks it required:

| Key | Type | Required | Notes |
|---|---|---|---|
| `ksefNumber` | string | yes | 35 chars in KSeF 2.0 (36 accepted for KSeF 1.0 numbers). Pattern `NIP-YYYYMMDD-XXXXXX-XXXXXX-XX` |
| `invoiceNumber` | string ≤ 256 | yes | Number given by the seller |
| `issueDate` | date `YYYY-MM-DD` | yes | |
| `invoicingDate` | date-time | yes | Accepted by KSeF for processing |
| `acquisitionDate` | date-time | yes | **The KSeF number was assigned — legally the date the buyer received the invoice** |
| `permanentStorageDate` | date-time | yes | Permanently stored; up to 7 fractional digits in examples |
| `seller.nip` | string, 10 digits | yes | |
| `seller.name` | string ≤ 512 | **no, nullable** | |
| `buyer.identifier.type` | `Nip` \| `VatUe` \| `Other` \| `None` | yes | |
| `buyer.identifier.value` | string ≤ 50 | no, nullable | |
| `buyer.name` | string ≤ 512 | no, nullable | |
| `netAmount` | number | yes | Invoice currency |
| `grossAmount` | number | yes | Invoice currency |
| `vatAmount` | number | yes | **Expressed in PLN** ("Łączna kwota VAT wyrażona w PLN"), even when `currency` is not PLN |
| `currency` | ISO 4217, 3 chars | yes | |
| `invoicingMode` | `Online` \| `Offline` | yes | |
| `invoiceType` | enum | yes | `Vat` basic, `Zal` advance, `Kor` correction, `Roz` settlement, `Upr` simplified, `KorZal`, `KorRoz`, `VatPef`, `VatPefSp`, `KorPef`, `VatRr`, `KorVatRr` |
| `formCode` | `{systemCode, schemaVersion, value}` | yes | e.g. `{"FA (3)", "1-0E", "FA"}`; also `FA (2)`, `PEF (3)`, `PEF_KOR (3)` (`2-1`, `PEF`), `FA_RR (1)` (`1-1E`, `FA_RR`) |
| `isSelfInvoicing` | bool | yes | |
| `hasAttachment` | bool | yes | |
| `invoiceHash` | Base64 SHA-256 | yes | |
| `hashOfCorrectedInvoice` | Base64 SHA-256 | no, nullable | Corrections only |
| `thirdSubjects[]` | `{identifier{type,value?}, name?, role}` | no, nullable | |
| `authorizedSubject` | `{nip, name?, role}` | no, nullable | |

Clients must ignore unknown properties: new ones may be added without notice (CHANGELOG 2.1.2).

### Which date finds newly arrived invoices

**`PermanentStorage`**, sorted **`Asc`**. Checked 2026-10-06 — OpenAPI ("for the incremental
scenario use the PermanentStorage date and Asc order"), DOCS przyrostowe-pobieranie-faktur.md
("**necessary** … other date types may lead to unpredictable behaviour in incremental
synchronisation"), hwm.md.

- `Issue` is the seller's date and can be days or weeks before arrival — unusable.
- `Invoicing` is set before processing finishes and is not covered by the completeness
  guarantee.
- `PermanentStorage` comes with the **high-water mark**: KSeF guarantees that no invoice with a
  `permanentStorageDate` ≤ HWM will ever appear later. Invoices in (HWM, now] may still be
  arriving.

Two documented polling strategies (hwm.md):

1. **Up to the HWM only** (`restrictToPermanentStorageHwmDate: true`): the next `from` is the
   returned `permanentStorageHwmDate`. No overlaps, but an invoice is seen only once the HWM has
   passed it.
2. **Up to now** (`to` omitted, restrict false): fresher, but the next query must start no later
   than the previous HWM, and duplicates must be removed by `ksefNumber`.

Either way, windows must be **contiguous** (end of one = start of the next) and
**deduplicated by `ksefNumber`**; whether `from` is inclusive is not documented, so the window
should overlap rather than risk a gap. **Observed:** the HWM was 1 min 59.9 s behind "now" on
2026-10-06, and 1 min 57 s in a second run the same day. Whether `from` is inclusive and how
`pageOffset` behaves past the first page could not be observed: the test NIP has no incoming
invoices. The design depends on neither. Choosing between the strategies is a phase 1 decision.

### Paging in practice

`pageSize = 250` makes paging very unlikely for one company's cost invoices in one cycle. If
`hasMore` is ever `true`, the variant that does not depend on `pageOffset` semantics is: set
`from` to the last record's `permanentStorageDate`, reset `pageOffset`, query again, deduplicate.
Each extra page is one more request against the metadata limit.

---

## Fetching one invoice

Checked 2026-10-06 — OpenAPI `GET /invoices/ksef/{ksefNumber}`, DOCS pobieranie-faktur.md, XSD.
Not observed live (the test NIP had no incoming invoices).

`GET /invoices/ksef/{ksefNumber}` with `Accept: application/xml`, `Bearer <accessToken>`,
permission `InvoiceRead`.

- `200` body: the invoice XML exactly as submitted. Header `x-ms-meta-hash`: Base64 SHA-256 of
  the invoice.
- `400` code **21164** — no invoice with that number; **21165** — processed but **not yet
  available for download, retry later**. A number freshly seen in the metadata can hit 21165:
  retry in a later cycle rather than immediately.
- Size: up to **1 MB** without attachment, **3 MB** with (DOCS limity.md, per-context defaults).

### Schema versions in circulation

| `formCode.systemCode` | Root namespace | Notes |
|---|---|---|
| `FA (3)` `1-0E` | `http://crd.gov.pl/wzor/2025/06/25/13775/` | **The only FA version accepted for new invoices on PROD since KSeF 2.0 (2026-02-01)** |
| `FA (2)` `1-0E` | `http://crd.gov.pl/wzor/2023/06/29/12648/` | KSeF 1.0 era; still accepted on TEST; still returned for old invoices |
| `PEF (3)`, `PEF_KOR (3)` | UBL 2.1 (Peppol) | Different structure entirely; none of the paths below apply |
| `FA_RR (1)` `1-1E` | — | VAT RR (flat-rate farmer) invoices; none of the paths below apply |

The root element is `Faktura`; `Naglowek/KodFormularza` carries the attributes
`kodSystemowy="FA (3)"` and `wersjaSchemy="1-0E"`. Match elements by **local name**, since the
namespace differs between versions. For PEF and FA_RR the XML-sourced fields below are treated
as absent.

### XML paths of the fields the metadata does not carry

Paths are relative to the root `Faktura`; identical in FA(2) and FA(3) unless noted.

| Field | Path | Cardinality | Notes |
|---|---|---|---|
| **Payment due date** | `Fa/Platnosc/TerminPlatnosci/Termin` | `TerminPlatnosci` 0..100; `Termin` 1 in FA(2), **0..1 in FA(3)** | Date `YYYY-MM-DD`. Several due dates (instalments) are possible; the earliest is the natural one to show. FA(3) may carry only `TerminOpis` — structured `{Ilosc, Jednostka, ZdarzeniePoczatkowe}` ("14 days from delivery"); FA(2) `TerminOpis` is free text |
| **Payment form** | `Fa/Platnosc/FormaPlatnosci` (direct child of `Platnosc`) | 0..1 | Codes: `1` cash, `2` card, `3` voucher, `4` cheque, `5` credit, `6` transfer, `7` mobile (identical tables in both schemas). Alternatively `Fa/Platnosc/PlatnoscInna = 1` + `Fa/Platnosc/OpisPlatnosci` (free text ≤ 256). **FA(3) also has `FormaPlatnosci` inside `Platnosc/ZaplataCzesciowa`** (form of an already-made partial payment) — a descendant search would pick the wrong one |
| **Bank account** | `Fa/Platnosc/RachunekBankowy/NrRB` | `RachunekBankowy` 0..100 | 10–34 characters (Polish NRB or IBAN). Optional siblings `SWIFT`, `NazwaBanku`. `Fa/Platnosc/RachunekBankowyFaktora/NrRB` (0..20) is a factor's account — a descendant search for `NrRB` would match it too |
| **Line items** | `Fa/FaWiersz` | 0..10 000 | Name/description in `FaWiersz/P_7`, optional, ≤ 256 chars (FA(2)) / ≤ 512 (FA(3)). In corrections, rows with `FaWiersz/StanPrzed = 1` are "before correction" rows. `FaWiersz` is optional for advance invoices and some corrections |
| Seller name (fallback) | `Podmiot1/DaneIdentyfikacyjne/Nazwa` | 1 | Only when the metadata `seller.name` is missing |
| Already paid | `Fa/Platnosc/Zaplacono = 1` + `Fa/Platnosc/DataZaplaty` | 0..1 | Not a candidate field yet; noted because it changes the meaning of "due date" |

Invoice XML is written by third parties and must be parsed with protection against hostile
documents (entity expansion, external entities). KSeF itself rejects processing instructions in
submitted XML since 2026-07-16 (CHANGELOG 2.4.0), but that is not a guarantee to rely on.

---

## Rate limits

Checked 2026-10-06 — DOCS [limity/limity-api.md](https://github.com/CIRFMF/ksef-api/blob/main/limity/limity-api.md) (dated 2026-09-14), OpenAPI `x-rate-limits` and `GET /rate-limits` schema, CHANGELOG.

### How they are counted

- Per **pair (context NIP, client IP address)**. Traffic in the same NIP from another IP counts
  separately; **traffic from the same IP in the same NIP is shared** — e.g. the company's
  accounting software running on the same internet connection as Home Assistant eats into the
  same budget.
- **Sliding windows**: per second over the last 1 s, per minute over the last 60 s, per hour
  over the last 60 min, all three at once; not aligned to the clock.
- Each endpoint group has its own counter.

### The limits that apply here (PROD, DEMO; TEST equalised with PROD since 2.5.0)

| Group | Endpoint | req/s | req/min | req/h |
|---|---|---|---|---|
| `invoiceMetadata` | `POST /invoices/query/metadata` | 8 | 16 | **20** |
| `invoiceDownload` | `GET /invoices/ksef/{ksefNumber}` | 8 | 16 | **64** |
| `anonymous` / auth | `/auth/challenge`, `/auth/ksef-token`, `/auth/{ref}`, `/auth/token/redeem`, `/auth/token/refresh`, `/security/public-key-certificates` | 60 per IP | — | — |
| `other` | everything else, e.g. `DELETE /auth/sessions/current`, `GET /rate-limits` | 10 | 30 | 120 |

`GET /rate-limits` (group `other`) returns the effective values for the current context, so
the limits can be read at runtime instead of hard-coded.

Conflict in the documentation: limity-api.md still says TEST limits are ten times higher than
PROD, but CHANGELOG 2.5.0 (TEST 2026-05-06) says they were made equal. Assume PROD values on
TEST. TEST also offers `POST /testdata/rate-limits/production`, `POST /testdata/rate-limits`
(custom values) and `DELETE /testdata/rate-limits` (reset) — useful for provoking 429 in tests.

Higher limits are announced for **20:00–06:00**, but their values have not been published
(limity-api.md §4). Do not rely on them.

### Usage guidance from the documentation

- **Production polling interval: not shorter than 15 minutes** per subject type (limity-api.md
  "Częstotliwość odpytywania"; przyrostowe-pobieranie-faktur.md "Minimalny interwał").
- Fetching single invoices synchronously is acceptable **for low volumes only**; high volumes
  should use the asynchronous export (`/invoices/exports`), which this integration does not use.
- The API is not meant to serve end users in real time (no "open invoice" on demand); any
  user-triggered sync must be deliberate.
- Limits are dynamic and may be lowered for inefficient integrations.

### When a limit is exceeded

- HTTP **429**, header **`Retry-After` in seconds**, body
  `{"status": {"code": 429, "description": "Too Many Requests", "details": [...]}}` (or problem
  details with `X-Error-Format: problem-details`).
- Further requests are blocked for a **dynamic** period that **grows with repeated violations**.
- Violations are logged and analysed; repeated violations or patterns that look like evasion
  (many IPs for one context) can lead to **blocking the context or an IP range** and to lowering
  that context's limits.
- Therefore: honour `Retry-After` exactly, never retry before it elapses, never retry in a loop.

---

## Push or webhook

**None exists.** Checked 2026-10-06:

- Neither the PROD nor the TEST OpenAPI has a `webhooks` or `callbacks` section, and no path or
  description mentions webhooks, callbacks, subscriptions or notifications.
- The DOCS repository mentions no such mechanism; it describes retrieval only as on-demand,
  scheduled (cyclic) or mixed synchronisation (limity-api.md "Tryby synchronizacji").

Polling is the only way to learn about a new invoice; it is the design, not an oversight.

---

## Request budget

Derived from the limits and lifetimes above. Assumptions: one config entry (one NIP) per Home
Assistant instance and public IP; poll interval `T`; cost invoices only (`Subject2`).

### Requests per poll cycle

| Request | When | Count | Limit group |
|---|---|---|---|
| `POST /invoices/query/metadata` | every cycle | 1 (+1 per extra page; with `pageSize` 250 only if > 250 new invoices) | invoiceMetadata |
| `POST /auth/token/refresh` | when the access token has < ~1 min left; with `T` ≥ 15 min that is every cycle | 0–1 | auth (60/s only) |
| Full authentication | first start, refresh failure, or refresh token near its 7-day expiry | 0 or 4–5 (`challenge`, `ksef-token`, ≥ 1 status poll, `redeem`, plus the key list unless cached until its `validTo`) | auth |
| `GET /invoices/ksef/{n}` | per new invoice, **only if a selected field comes from the XML** | 0..N | invoiceDownload |

### Worst case per hour, at the minimum interval T = 15 min

| Group | Used | Limit | Share |
|---|---|---|---|
| invoiceMetadata | 4 | 20 / h, 16 / min | 20 % |
| auth | 4 refreshes (+ ≤ 5 for a full authentication about once a week) | 60 / s | negligible |
| invoiceDownload | ≤ 4 × *k*, with *k* the per-cycle cap on XML fetches | 64 / h, 16 / min, 8 / s | *k* = 10 → 40 / h = 63 % |

Consequences for the design (decided in phase 1, constrained here):

1. **Minimum poll interval: 15 minutes.** The arithmetic would allow one metadata query every
   3 minutes (20 / h), but the official production guidance is 15 minutes, the budget is shared
   with any other software polling the same NIP from the same IP, and the documented penalty for
   inefficient integrations is a lower limit or a block. At 15 minutes the metadata limit keeps
   16 / h spare for paging, retries after an outage and a manual "check now".
2. **XML fetches need a per-cycle cap.** Several new invoices at once with an XML field selected
   would otherwise burst past 16 / min or 64 / h. With a cap *k* ≤ 12 the hourly worst case at
   T = 15 min stays ≤ 48 / h (75 %); invoices beyond the cap must be deferred to the next cycle
   or notified without their XML fields.
3. **XML fetches must be paced** at ≥ 125 ms apart (8 / s). Observed response times were
   40–100 ms, so back-to-back sequential requests would exceed 8 / s.
4. **A token lasts a week, an access token 15 minutes**: at T ≥ 15 min, expect one refresh per
   cycle and one full authentication per 7 days. Neither touches the invoice limits.
5. **After an outage** longer than 100 days the `from` date must be clamped to the 100-day
   maximum range.
6. A **manual check** costs one metadata request; it must be throttled so that it cannot eat
   the spare 16 / h.
