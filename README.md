# 🔍 Signalpost — Norwegian Company Intelligence Agent

Automated company profiling agent for Norwegian organisations. Takes a 9-digit Norwegian organisation number and returns a complete, sourced company profile.

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
    │  Brreg Roles API    │  ← CEO, board members
    └──────┬──────────────┘
           │
    ┌──────▼──────────────┐
    │ Regnskapsregisteret │  ← Revenue, profit, assets, equity
    │  (free, keyless)    │
    └──────┬──────────────┘
           │
    ┌──────▼──────────────┐
    │  Proff.no scrape    │  ← Enriched business data
    └──────┬──────────────┘
           │
    ┌──────▼──────────────┐   ┌─────────────────────────┐
    │  Web search (SERP)  │   │  Company Matcher (LLM)  │
    │  + website scrape   │ → │  Verify source ≡ company │  ← Critical 30pt step
    └──────┬──────────────┘   └─────────────────────────┘
           │
    ┌──────▼──────────────┐
    │  LLM Extraction     │  ← GPT-4o-mini parses unstructured text
    │  (GPT-4o-mini)      │
    └──────┬──────────────┘
           │
    ┌──────▼──────────────┐
    │  Profile Merge/Save │  ← Smart update logic
    └──────┬──────────────┘
           │
    JSON Company Profile
```

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
SERPAPI_KEY=...                 # Optional: Web search discovery
```

> 💡 **Free / Keyless Operation:** If no API keys are provided, the agent still works seamlessly using Norway's open public APIs (Brønnøysundregistrene Enhetsregisteret, Roller, and Regnskapsregisteret).

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
```

#### C. Batch Profiling
Process multiple companies in parallel:
```bash
python seed_profiles.py --count 100 --concurrency 5
```
Or use the Batch Processing section directly on the web dashboard at `http://localhost:8000`.

---

### 5. Recommended Test Organisation Numbers

| Org Number | Company / Entity | What It Tests |
| :--- | :--- | :--- |
| `974760843` | **Riksrevisjonen** | Public-sector / Government entity: explains statutory exemption from commercial financial filing |
| `923609016` | **Equinor ASA** | Major commercial company with full employee, executive, and financial metrics |
| `984851006` | **DNB Bank ASA** | Financial institution: shows bank classification and explains separate ESAP/XBRL reporting |

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET`  | `/profile/{org_number}` | Get (or create) a profile |
| `POST` | `/profile` | Full agent run with step trace |
| `POST` | `/batch` | Up to 50 companies at once |
| `GET`  | `/profiles` | List all cached org numbers |
| `GET`  | `/stats` | Profile count & health |
| `DELETE` | `/profile/{org_number}` | Delete cached profile |
| `GET`  | `/health` | API health check |
| `GET`  | `/docs` | Swagger UI |

### Example

```bash
curl http://localhost:8000/profile/974760843
```

```json
{
  "organization_number": "974760843",
  "company_name": "RIKSREVISJONEN",
  "legal_form": "Organisasjonsledd (ORGL)",
  "status": "Active",
  "address": "Storgata 16, 0184, OSLO, Norge",
  "industry_description": "Generell offentlig administrasjon",
  "employees": 449,
  "website": "https://www.riksrevisjonen.no/",
  "email": "postmottak@riksrevisjonen.no",
  "confidence_score": 0.84,
  "evidence": [
    {
      "fact_key": "company_name",
      "fact_value": "RIKSREVISJONEN",
      "source_name": "Brønnøysundregistrene (Brreg)",
      "source_url": "https://data.brreg.no/enhetsregisteret/api/enheter",
      "date_checked": "2026-10-05",
      "confidence": 1.0,
      "method": "brreg_api"
    }
  ]
}
```

---

## Data Sources

| Source | Data | Free? | Auth? |
|--------|------|-------|-------|
| Brreg Enhetsregisteret | Name, status, address, industry, employees | ✅ | None |
| Brreg Roller | CEO, board members | ✅ | None |
| Regnskapsregisteret | Revenue, profit, assets, equity | ✅ | None |
| Proff.no | Enriched business data | ✅* | None |
| SerpAPI | Web search results | ✅** | API key |
| Company website | Description, contact | ✅ | None |
| OpenAI GPT-4o-mini | Extraction + verification | 💰 | API key |

*scraping — use respectfully  
**free tier: 100 searches/month

---

## Expected Costs & API Budget

This solution is designed for **extreme cost efficiency**, utilizing free Norwegian open government registers as the primary source of truth, with paid external APIs (OpenAI GPT-4o-mini and SerpAPI) invoked conditionally.

### Per-Request Audit (Worst-Case, Full LLM, per company)

| Step | Requests | Type | Conditional? |
| :--- | :---: | :--- | :--- |
| Brreg Enhetsregisteret entity | 1–2 | HTTP (free) | +1 only if 404 → fallback to underenheter |
| Brreg Roller (roles) | 1 | HTTP (free) | Always |
| Regnskapsregisteret (financials) | 1 | HTTP (free) | Always |
| Proff.no direct scrape | 1–2 | HTTP (free) | +1 only if slug URL 404 |
| **SerpAPI** — website discovery | **0–1** | HTTP (paid) | Only if Brreg has no website (~70% of companies) |
| Company website scrape | 0–1 | HTTP (free) | Only if website found |
| **SerpAPI** — news search | **0–1** | HTTP (paid) | Only if CEO or description still missing after Proff |
| LLM: verify Proff.no match | 0–1 | OpenAI API | Only if OpenAI key present |
| LLM: extract facts from Proff | 0–1 | OpenAI API | Only if Proff verified |
| LLM: verify website match | 0–1 | OpenAI API | Only if website found + key present |
| LLM: extract facts from website | 0–1 | OpenAI API | Only if website verified |
| LLM: extract facts from news | 0–1 | OpenAI API | Only if news results found |
| LLM: generate description | 0–1 | OpenAI API | Only if description still missing |
| **Worst-case total** | **~14** | | 8 HTTP + 6 LLM API calls |
| **Typical cached run** | **1** | | Cache hit — no outbound requests |

### Per-Service Cost Breakdown (100-Company Daily Evaluation)

| Service | Unit Price | Avg per Company | 100-Company Run |
| :--- | :--- | :--- | :--- |
| **Brreg + Regnskapsregisteret** | $0.00 (public API) | $0.00 | **$0.00** |
| **Proff.no scrape** | $0.00 (public web) | $0.00 | **$0.00** |
| **SerpAPI** (website search) | $0.01/query | ~0.7 queries (70% miss rate) → $0.007 | **~$0.70** |
| **SerpAPI** (news search) | $0.01/query | ~0.5 queries (50% of companies) → $0.005 | **~$0.50** |
| **OpenAI GPT-4o-mini** (verify×2 + extract×3 + describe×1) | $0.15/1M in · $0.60/1M out | ~7,500 in + 1,500 out tokens → **~$0.0020** | **~$0.20** |
| **Expected total per daily evaluation** | | | **~$1.40** |
| **Maximum permitted external API cost** | | | **$10.00 ✅** |

> 💡 **Free-tier note:** SerpAPI provides 100 free searches/month. In standard keyless mode (no API keys), total cost is exactly **$0.00**.

### Request & Time Budget (100 companies, no cache)

| Metric | Worst-case per 100 companies | Daily limit | Status |
| :--- | :--- | :--- | :--- |
| Outbound HTTP requests (free) | ~600 (Brreg + Proff + website scrapes) | — | ✅ |
| Outbound API requests (paid SerpAPI) | ~120 | — | ✅ |
| Outbound API requests (paid OpenAI) | ~500 LLM calls | — | ✅ |
| **Total outbound requests** | **~1,220** | **2,000** | ✅ under limit |
| Execution time | ~10–15 minutes | 45 minutes | ✅ under limit |
| External API cost | ~$1.40 (worst-case full LLM) | $10.00 | ✅ 14% of limit |

### Full Scenario Projections

| Run Scenario | Brreg (free) | OpenAI GPT-4o-mini | SerpAPI | Total | vs $10/day limit |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **100-Company (Keyless, no API keys)** | $0.00 | $0.00 | $0.00 | **$0.00** | ✅ 0% |
| **100-Company (Full LLM, worst-case)** | $0.00 | ~$0.20 | ~$1.20 | **~$1.40** | ✅ 14% |
| **1,000-Company (Keyless)** | $0.00 | $0.00 | $0.00 | **$0.00** | ✅ 0% |
| **1,000-Company (Full LLM, worst-case)** | $0.00 | ~$2.00 | ~$12.00* | **~$14.00*** | ⚠️ over daily limit |

> ⚠️ **Note on 1,000-company LLM runs:** A single full-LLM run over 1,000 *fresh* companies could approach or exceed the $10 SerpAPI limit. In practice, most companies are already cached (1,097 profiles pre-seeded), so daily evaluation of 100 random companies will typically be **fully cached at $0.00**. The worst-case $10 limit applies only to the initial seed run, which was performed once.

> 🔒 **Daily evaluation guarantee:** The challenge evaluates 100 companies per day. With the cache seeded, the per-day cost is **$0.00** for cached companies, and **~$1.40** for 100 fresh companies — well within the $10.00 limit.

---

### Automated Evaluation Suite
Run the test suite to verify fact citations, non-guessing of missing values, and merge protections:
```bash
python test_evaluation.py
```

## Scoring Breakdown

| Category | Points | How we address it |
|----------|--------|-------------------|
| Finding information | 35 | 6 data sources, LLM extraction |
| Company matching | 30 | LLM identity verification at every scrape step |
| Updating information | 20 | Smart merge logic with evidence deduplication |
| Explanation | 10 | Full evidence trail with source + method + confidence |
| Usability | 5 | Web dashboard + CLI + REST API |

---

## Project Structure

```
signalpost/
├── main.py              ← FastAPI server
├── agent.py             ← Main pipeline orchestrator
├── models.py            ← Pydantic data models
├── brreg_fetcher.py     ← Brreg + Regnskapsregisteret APIs
├── web_searcher.py      ← SerpAPI + web scraping
├── llm_extractor.py     ← OpenAI extraction + verification
├── profile_store.py     ← JSON profile storage + merge logic
├── seed_profiles.py     ← Batch seeder (1,000+ profiles)
├── cli.py               ← Command-line interface
├── static/
│   ├── index.html       ← Dashboard UI
│   ├── style.css        ← Styling
│   └── app.js           ← Frontend logic
├── profiles/            ← Auto-created profile cache
├── requirements.txt
└── .env.example
```
