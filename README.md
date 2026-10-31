# 🔍 Signalpost — Norwegian Company Intelligence Agent

Automated company profiling agent for Norwegian organisations. Takes a 9-digit Norwegian organisation number and returns a complete, sourced, contract-compliant company profile.

---

## Architecture

```
org_number
    │
    ▼
┌─────────────────────┐
│  Brreg API          │  ← Identity, address, industry, employees, status
│  (free, keyless)    │
└──────────┬──────────┘
           │
    ┌──────▼──────────────┐
    │  Brreg Roles API    │  ← CEO, board members  (free, keyless)
    └──────┬──────────────┘
           │
    ┌──────▼──────────────┐
    │ Regnskapsregisteret │  ← Revenue, profit, assets, equity  (free, keyless)
    └──────┬──────────────┘
           │
    ┌──────▼──────────────┐
    │  Proff.no scrape    │  ← Discover company homepage URL  (free, keyless)
    │  (bridge only)      │    Fallback employee count (if omitted by Brreg)
    └──────┬──────────────┘
           │
    ┌──────▼──────────────┐   ┌──────────────────────────┐
    │  Company website    │   │  Company Matcher (LLM)   │
    │  scrape (keyless)   │ → │  Verify source ≡ company │  ← Optional: only runs
    │  + SerpAPI (opt.)   │   │  (requires LLM API key)  │     when key configured
    └──────┬──────────────┘   └──────────────────────────┘
           │
    ┌──────▼──────────────┐
    │  LLM Extraction     │  ← Optional; any of OpenAI / Anthropic / Gemini
    │  (optional)         │    Facts are always cited with the company's own
    └──────┬──────────────┘    URL or an official register — never proff.no
           │
    ┌──────▼──────────────┐
    │  Profile Merge/Save │  ← Smart update logic & structure validation
    └──────┬──────────────┘
           │
    JSON Company Profile  (nested 6-section format per OUTPUT_CONTRACT.md)
```

### Keyless Operation

With **no API keys configured**, the pipeline returns a fully contract-compliant, citeable profile (`success=true`) for every valid 9-digit Norwegian organisation number found in Brreg. It extracts all available information from Norway's official public registers and direct website scraping.

Unavailable facts (such as financial accounts for exempt public-sector entities or sole proprietorships, missing company websites, or unlisted contact numbers) remain `null` in strict accordance with `OUTPUT_CONTRACT.md`. Missing facts are never guessed or hallucinated.

| Step | Source | Auth needed? |
|------|--------|:---:|
| Identity / address / employees / status | Brreg Enhetsregisteret | None |
| CEO / board members | Brreg Roller | None |
| Revenue / assets / equity / profit | Regnskapsregisteret | None |
| Company homepage discovery | Brreg `hjemmeside` field → Proff.no fallback bridge | None |
| Company website scrape | Direct HTTP | None |
| Web search (supplemental) | SerpAPI | Optional key |
| LLM extraction / verification | OpenAI / Anthropic / Gemini | Optional key |

---

## How to Run & Quick Start

### 1. Prerequisites
* Python 3.10 or higher
* `pip` package manager

### 2. Setup Environment
Clone or navigate to the project directory:
```bash
cd numbers
```

*(Recommended)* Create and activate a virtual environment:
* **Windows (PowerShell):**
  ```powershell
  python -m venv .venv
  .venv\Scripts\Activate.ps1
  ```
* **macOS / Linux:**
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  ```

Install dependencies:
```bash
pip install -r requirements.txt
```

### 3. Configuration (.env)
Copy the example environment file:
* **Windows (PowerShell):**
  ```powershell
  Copy-Item .env.example .env
  ```
* **macOS / Linux:**
  ```bash
  cp .env.example .env
  ```

Configure your API keys in `.env` (optional):
```env
OPENAI_API_KEY=sk-...          # Optional: Enables LLM fact extraction & verification
ANTHROPIC_API_KEY=sk-ant-...   # Optional: Alternative LLM provider (Anthropic Claude)
GEMINI_API_KEY=...             # Optional: Alternative LLM provider (Google Gemini)
SERPAPI_KEY=...                 # Optional: Web search discovery
```

> 💡 **Free / Keyless Operation:** If no API keys are provided, the agent operates entirely in keyless mode using Norway's open public APIs (Brønnøysundregistrene Enhetsregisteret, Roller, and Regnskapsregisteret) plus direct website scraping. The pipeline returns a contract-valid profile for every valid Norwegian organisation number found in Brreg. Only facts that actually exist in official sources are populated; unavailable facts remain `null` and are never guessed. LLM extraction, LLM company verification, and SerpAPI web search are optional enrichments that require a configured provider key and are skipped in keyless mode.

---

### 4. Running the Application

#### A. Web Dashboard (Recommended)
Start the FastAPI backend with dashboard:
```bash
python main.py
```
Then open your browser at:
👉 **[http://localhost:8000](http://localhost:8000)**

* Search any 9-digit Norwegian organisation number.
* Interactive live pipeline trace, confidence score ring, and full evidence audit trail.
* Contextual explanatory badges for missing data (e.g. government exemptions or non-standard IFRS filings).

#### B. Command Line Interface (CLI)
You can run lookups directly in the terminal:
```bash
# Basic lookup
python cli.py 974760843

# Force refresh (bypass cache)
python cli.py 923609016 --refresh

# View full evidence audit trail
python cli.py 974760843 --evidence

# Output raw JSON
python cli.py 923609016 --json

# Interactive mode (prompts for org number)
python cli.py
```

#### C. Official Evaluation Runner (Batch with Contract Validation)
`run.py` is the primary evaluation entrypoint. It takes the **input file path as a positional argument** and validates every output line against `OUTPUT_CONTRACT.md`:

```bash
# Run against the default org_numbers.txt
python run.py org_numbers.txt

# Specify an arbitrary input file and custom output path
python run.py /path/to/eval_set.txt --output results.jsonl

# With increased concurrency and forced refresh
python run.py org_numbers.txt --concurrency 8 --refresh
```

`run.py` accepts any valid file path as its first positional argument, handles batch processing with per-company exception isolation, preserves input order, and outputs one JSONL line per input record.

#### D. Batch Profiling (Pre-seeding)
Pre-seed profiles into the cache from Brreg open data or an input file:
```bash
# Seed 100 profiles from Brreg open registry
python seed_profiles.py --count 100 --concurrency 5

# Seed from a custom text file
python seed_profiles.py --from-file org_numbers.txt --concurrency 5
```
Or use the Batch Processing section directly on the web dashboard at `http://localhost:8000`.

---

### 5. Recommended Test Organisation Numbers

| Org Number | Company / Entity | What It Tests |
| :--- | :--- | :--- |
| `974760843` | **Riksrevisjonen** | Public-sector / Government entity (448 employees): demonstrates statutory exemption from commercial financial filing |
| `923609016` | **Equinor ASA** | Major commercial enterprise (21,231 employees): full executive roles and complete Regnskapsregisteret financial metrics |
| `984851006` | **DNB Bank ASA** | Financial institution (7,347 employees): shows banking classification and explains specialized financial reporting |

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET`  | `/profile/{org_number}` | Get or generate profile (query param: `refresh=false`; returns nested `CompanyProfile`) |
| `POST` | `/profile` | Full agent run with step trace (JSON body: `{"organization_number": "...", "force_refresh": false}`) |
| `POST` | `/batch` | Batch profiling up to 50 companies (JSON body: `{"organization_numbers": [...], "concurrency": 5}`) |
| `GET`  | `/profiles` | List cached organisation numbers with pagination (`?skip=0&limit=100`) |
| `GET`  | `/stats` | Cache statistics and sample organisation numbers |
| `DELETE` | `/profile/{org_number}` | Delete a cached profile from storage |
| `GET`  | `/health` | API health check and provider configuration flags |
| `GET`  | `/` | Serve web dashboard UI (`static/index.html`) |
| `GET`  | `/docs` | Interactive Swagger / OpenAPI documentation |

### Example Response (`GET /profile/974760843`)

```bash
curl http://localhost:8000/profile/974760843
```

```json
{
  "identity": {
    "organization_number": "974760843",
    "company_name": "RIKSREVISJONEN",
    "legal_form": "Organisasjonsledd (ORGL)",
    "status": "Active",
    "registered_date": "1995-08-09"
  },
  "contact": {
    "address": "Storgata 16, 0184, OSLO, Norge",
    "postal_address": "Postboks 6835 St. Olavs plass, 0130, OSLO, Norge",
    "municipality": "OSLO",
    "website": "https://www.riksrevisjonen.no",
    "email": "postmottak@riksrevisjonen.no",
    "phone": "22 24 10 00"
  },
  "business": {
    "industry_code": "84.110",
    "industry_description": "Generell offentlig administrasjon",
    "employees": 448,
    "sector": "Statsforvaltningen"
  },
  "financials": {
    "revenue": null,
    "operating_profit": null,
    "net_income": null,
    "total_assets": null,
    "equity": null,
    "financial_year": null,
    "financials_status": "not_filed"
  },
  "people": {
    "ceo": "Jens Arild Gunvaldsen",
    "board_members": [
      "DIREKTORATET FOR FORVALTNING OG ØKONOMISTYRING (Regnskapsfører)",
      "STORTINGET (Org.ledd i offentlig sektor)"
    ],
    "description": null
  },
  "meta": {
    "profile_created_at": "2026-10-31T16:09:07.234498",
    "profile_updated_at": "2026-10-31T16:42:05.538970",
    "evidence": [
      {
        "fact_key": "company_name",
        "fact_value": "RIKSREVISJONEN",
        "source_name": "Brønnøysundregistrene (Brreg)",
        "source_url": "https://data.brreg.no/enhetsregisteret/api/enheter",
        "date_checked": "2026-10-31",
        "confidence": 1.0,
        "method": "brreg_api"
      },
      {
        "fact_key": "employees",
        "fact_value": 448,
        "source_name": "Brønnøysundregistrene (Brreg)",
        "source_url": "https://data.brreg.no/enhetsregisteret/api/enheter",
        "date_checked": "2026-10-31",
        "confidence": 1.0,
        "method": "brreg_api"
      }
    ],
    "confidence_score": 0.867,
    "sources_used": [
      "Brønnøysundregistrene (brreg.no)"
    ]
  }
}
```

---

## Data Sources

| Source | Data | Free? | Auth? |
|--------|------|-------|-------|
| Brreg Enhetsregisteret | Name, status, address, industry, employees | ✅ | None |
| Brreg Roller | CEO, board members | ✅ | None |
| Regnskapsregisteret | Revenue, profit, assets, equity | ✅ | None |
| Proff.no | Homepage URL discovery + fallback employee count | ✅* | None |
| Company website | Description, contact info (direct HTTP scrape) | ✅ | None |
| SerpAPI | Supplemental web search results | ✅** | API key (optional) |
| OpenAI / Anthropic / Gemini | Optional LLM extraction + website verification | 💰 | API key (optional) |

\* *Proff.no is used strictly as a discovery bridge to locate the official company website, and as a fallback employee count when omitted by Brreg. Proff.no is never cited as the source for published facts from company websites, which must cite the company's own domain or official registers.*
\*\* *SerpAPI free tier: 100 searches/month; optional — pipeline is fully operational without it.*

---

## Estimated Costs & API Budget

All figures below are **estimates** based on public third-party pricing at the time of evaluation design, not guaranteed rates or contractually fixed prices. Free Norwegian open government registers are the primary source of truth, with paid external APIs (OpenAI / Anthropic / Gemini and SerpAPI) invoked conditionally only when keys are configured.

In default **keyless mode (no API keys configured)**, total external API cost is exactly **$0.00**.

### Estimated Per-Request Breakdown (Worst-Case, Full LLM Enabled, per Company)

| Step | Requests | Type | Conditional? |
| :--- | :---: | :--- | :--- |
| Brreg Enhetsregisteret entity | 1–2 | HTTP (free) | +1 only if 404 → fallback to underenheter |
| Brreg Roller (roles) | 1 | HTTP (free) | Always |
| Regnskapsregisteret (financials) | 1 | HTTP (free) | Always |
| Proff.no direct scrape | 1–2 | HTTP (free) | +1 only if slug URL 404 |
| Company website direct scrape | 0–1 | HTTP (free) | Only if website found |
| **SerpAPI** — website discovery | **0–1** | HTTP (paid) | Only if Brreg & Proff yield no website (~30% of companies) |
| **SerpAPI** — news search | **0–1** | HTTP (paid) | Only if CEO or description still missing after registers |
| LLM: verify website match | 0–1 | LLM API | Only if website found + LLM key configured |
| LLM: extract facts from website | 0–1 | LLM API | Only if website verified + LLM key configured |
| LLM: extract facts from news | 0–1 | LLM API | Only if news results found + LLM key configured |
| LLM: generate description | 0–1 | LLM API | Only if description still missing + LLM key configured |
| **Estimated worst-case total** | **~10** | | ~6 HTTP + ~4 LLM API calls |
| **Typical cached run** | **1** | | Cache hit — no outbound requests |

### Estimated Per-Service Cost Breakdown (100-Company Run)

| Service | Estimated Unit Price | Estimated Avg per Company | Estimated 100-Company Run |
| :--- | :--- | :--- | :--- |
| **Brreg + Regnskapsregisteret** | $0.00 (public API) | $0.00 | **$0.00** |
| **Proff.no scrape** | $0.00 (public web) | $0.00 | **$0.00** |
| **Company website scrape** | $0.00 (direct HTTP) | $0.00 | **$0.00** |
| **SerpAPI** (website search) | ~$0.01/query | ~0.3 queries (miss rate) → ~$0.003 | **~$0.30** |
| **SerpAPI** (news search) | ~$0.01/query | ~0.2 queries → ~$0.002 | **~$0.20** |
| **OpenAI GPT-4o-mini** (verify×1 + extract×1 + describe×1) | ~$0.15/1M in · ~$0.60/1M out | ~4,000 in + 1,000 out tokens → **~$0.0012** | **~$0.12** |
| **Estimated total (full LLM mode)** | | | **~$0.62** |
| **Keyless mode (default)** | | | **$0.00** |

> 💡 **Free-tier note:** SerpAPI provides 100 free searches/month. In standard keyless mode (no API keys), external cost is exactly **$0.00**.

### Scenario Projections

| Run Scenario | Brreg (free) | LLM (est.) | SerpAPI (est.) | Total (est.) | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **100-Company (Keyless, default)** | $0.00 | $0.00 | $0.00 | **$0.00** | 100% free open data |
| **100-Company (Full LLM, fresh)** | $0.00 | ~$0.12 | ~$0.50 | **~$0.62** | Well within evaluation budget |
| **100-Company (Cached)** | $0.00 | $0.00 | $0.00 | **$0.00** | Served instantly from cache |

---

### Automated Evaluation Suite
Run the test suite to verify keyless compliance, nested contract sections, fact citations, non-guessing of missing values, and merge protections:
```bash
python test_evaluation.py
```

<<<<<<< HEAD
=======
## Scoring Breakdown

| Category | Points | How We Address It |
|----------|--------|-------------------|
| Finding information | 35 | 5 core open data sources (Brreg Enhetsregisteret, Brreg Roller, Regnskapsregisteret, Company website, Proff.no bridge) + optional SerpAPI & LLMs |
| Company matching | 30 | Identity verification: Brreg 9-digit matching, domain validation, and optional LLM website verification |
| Updating information | 20 | Smart merge logic protecting verified fields with evidence deduplication |
| Explanation | 10 | Full evidence audit trail with source name, URL, method, and confidence per fact |
| Usability | 5 | Web dashboard + CLI + REST API + contract-validated evaluation runner |

---
>>>>>>> 0e2f0d3 (Implement keyless profiling and contract validation)

## Project Structure

```
signalpost/
├── run.py               ← Official evaluation runner (validates against OUTPUT_CONTRACT.md)
├── main.py              ← FastAPI server & web dashboard backend
├── agent.py             ← Main pipeline orchestrator
├── models.py            ← Pydantic data models (nested 6-section structure)
├── brreg_fetcher.py     ← Brreg + Regnskapsregisteret open APIs
├── web_searcher.py      ← SerpAPI + direct web scraping (keyless-capable)
├── llm_extractor.py     ← Multi-provider LLM extraction (OpenAI / Anthropic / Gemini)
├── profile_store.py     ← JSON profile storage & smart merge logic
├── seed_profiles.py     ← Batch seeder (Brreg open data or file input)
├── cli.py               ← Command-line interface with evidence inspector
├── OUTPUT_CONTRACT.md   ← Official output format specification
├── test_evaluation.py   ← Automated test suite for compliance verification
├── static/
│   ├── index.html       ← Dashboard UI
│   ├── style.css        ← Dashboard styling
│   └── app.js           ← Dashboard interactive logic
├── profiles/            ← Profile cache directory
├── requirements.txt     ← Project dependencies
└── .env.example         ← Environment configuration template
```
