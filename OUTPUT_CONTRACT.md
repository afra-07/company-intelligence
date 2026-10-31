# OUTPUT_CONTRACT — Signalpost Company Profile

Every JSONL line written by `run.py` (or returned by the API) must satisfy
all of the constraints listed here.  The evaluation harness checks each line.

---

## Required top-level fields

| Field | Type | Notes |
|-------|------|-------|
| `organization_number` | `string` | Exactly 9 digits |
| `success` | `bool` | `true` if a profile was produced |
| `profile` | `object \| null` | `null` only when `success` is `false` |
| `error` | `string \| null` | Non-null only when `success` is `false` |
| `duration_seconds` | `number` | ≥ 0 |
| `steps_executed` | `array[string]` | Non-empty list of pipeline steps |

---

## Profile object constraints (when `success == true`)

The `profile` object must contain exactly six nested objects:
- `identity`: Authoritative legal identity
- `contact`: Contact and address details
- `business`: Industry, workforce and sector classifications
- `financials`: Annual accounts from Regnskapsregisteret
- `people`: Executive leadership and company overview
- `meta`: Audit trail, timestamps, confidence scoring and evidence list

---

### 1. `profile.identity` (authoritative — sourced from Brreg)

| Field | Type | Nullable? |
|-------|------|-----------|
| `organization_number` | `string` | No |
| `company_name` | `string` | No |
| `legal_form` | `string` | Yes |
| `status` | `string` | No — one of `Active`, `Bankrupt`, `Under liquidation`, `Forced liquidation` |
| `registered_date` | `string` (ISO 8601 date) | Yes |

### 2. `profile.contact` (contact & location)

| Field | Type | Nullable? |
|-------|------|-----------|
| `address` | `string` | Yes |
| `postal_address` | `string` | Yes |
| `municipality` | `string` | Yes |
| `website` | `string` (URL) | Yes |
| `email` | `string` | Yes |
| `phone` | `string` | Yes |

### 3. `profile.business` (business details)

| Field | Type | Nullable? |
|-------|------|-----------|
| `industry_code` | `string` | Yes |
| `industry_description` | `string` | Yes |
| `employees` | `integer` | Yes |
| `sector` | `string` | Yes |

### 4. `profile.financials` (from Regnskapsregisteret)

| Field | Type | Nullable? | Notes |
|-------|------|-----------|-------|
| `revenue` | `number` | Yes | NOK; `null` for exempt/non-filing entities |
| `operating_profit` | `number` | Yes | |
| `net_income` | `number` | Yes | |
| `total_assets` | `number` | Yes | |
| `equity` | `number` | Yes | |
| `financial_year` | `integer` | Yes | |
| `financials_status` | `string` | Yes | `found` \| `not_filed` \| `no_data` |

### 5. `profile.people` (people & descriptions)

| Field | Type | Nullable? |
|-------|------|-----------|
| `ceo` | `string` | Yes |
| `board_members` | `array[string]` | Yes |
| `description` | `string` | Yes |

### 6. `profile.meta` (metadata & evidence audit trail)

| Field | Type | Notes |
|-------|------|-------|
| `confidence_score` | `number` | 0.0 – 1.0 |
| `sources_used` | `array[string]` | ≥ 1 element when `success == true` |
| `evidence` | `array[EvidenceItem]` | ≥ 1 element when `success == true` |
| `profile_created_at` | `string` | ISO 8601 datetime |
| `profile_updated_at` | `string` | ISO 8601 datetime |

---

## EvidenceItem constraints

Every item in the `evidence` array must have:

| Field | Type | Must be non-empty? |
|-------|------|--------------------|
| `fact_key` | `string` | Yes |
| `fact_value` | `any` | Yes |
| `source_name` | `string` | Yes |
| `source_url` | `string` | Yes — must NOT be `proff.no` for published facts |
| `date_checked` | `string` | Yes (ISO 8601 date) |
| `confidence` | `number` | Yes — 0.0 ≤ value ≤ 1.0 |
| `method` | `string` | Yes — one of `brreg_api`, `regnskaps_api`, `serp_search`, `webpage_scrape`, `llm_extract` |

### Citation rules

- Facts sourced from **official Norwegian registers** (Brønnøysundregistrene,
  Regnskapsregisteret) must cite `data.brreg.no` URLs.
- Facts that originate from a **company's own website** must cite that website's
  domain, not an aggregator.
- `proff.no` is permitted **ONLY** as `source_url` for the `employees` fact (since
  Proff.no mirrors the official Brreg headcount) and internal `_proff_raw` keys.
  Never attribute Proff-extracted facts to a company's website.
- Search engine URLs (e.g. `google.com`, `bing.com`) must **NEVER** appear as
  `source_url` for any fact. News facts must cite the actual article URL.

---

## Non-guessing rule

Fields for which no authoritative data exists **must be `null`**, not guessed
or hallucinated.  In particular:

- `revenue`, `net_income`, `total_assets` must be `null` for public-sector
  entities (ORGL, STAT, KF, …) that are legally exempt from commercial filing.
- No financial field may be populated unless it appears in the
  Regnskapsregisteret API response for that org number.

---

## Keyless operation guarantee

When neither `OPENAI_API_KEY` nor `SERPAPI_KEY` is set, the pipeline **must**
still return a valid profile (with `success == true`) for every valid 9-digit
Norwegian org number found in Brønnøysundregistrene.  The profile will contain
at minimum: `company_name`, `status`, `legal_form`, `address`, `industry_*`,
`employees`, and all financial data available in Regnskapsregisteret.

