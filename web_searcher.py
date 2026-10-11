"""
Signalpost Company Intelligence Agent
=====================================
Source 2: Web Search via SerpAPI + intelligent scraping
Finds company websites, news, and supplemental facts.
"""

from __future__ import annotations
import httpx
import os
import re
from datetime import date
from typing import Optional, Dict, Any, List
from bs4 import BeautifulSoup
from models import EvidenceItem

SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
TODAY = date.today().isoformat()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,nb;q=0.8",
}


# ─── SerpAPI search ───────────────────────────────────────────────────────────

async def serp_search(query: str, num: int = 5) -> List[Dict[str, Any]]:
    """
    Perform a Google search via SerpAPI.
    Returns list of {title, link, snippet} dicts.
    Falls back to empty list if no API key.
    """
    if not SERPAPI_KEY:
        return []

    params = {
        "q": query,
        "api_key": SERPAPI_KEY,
        "num": num,
        "gl": "no",   # Norway
        "hl": "no",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get("https://serpapi.com/search", params=params)
            data = resp.json()
            return data.get("organic_results", [])
        except Exception:
            return []


# ─── Webpage scraper ──────────────────────────────────────────────────────────

async def scrape_page(url: str, max_chars: int = 8000) -> Optional[str]:
    """
    Fetch a webpage and return clean text content.
    """
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers=HEADERS) as client:
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None
            soup = BeautifulSoup(resp.text, "lxml")
            # Remove noise
            for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            # Collapse whitespace
            text = re.sub(r"\n{3,}", "\n\n", text)
            return text[:max_chars]
        except Exception:
            return None


# ─── Proff.no direct lookup ───────────────────────────────────────────────────

async def scrape_proff(org_number: str, company_name: str) -> Dict[str, Any]:
    """
    Scrape proff.no for enriched company data.
    Proff.no has a predictable URL structure for Norwegian companies.
    """
    evidence: list[EvidenceItem] = []
    result: Dict[str, Any] = {}

    # Proff.no URL pattern: /firmaer/<slug>/<org_number>/
    # We try to build the slug from the company name
    slug = re.sub(r"[^a-z0-9]+", "-", company_name.lower()).strip("-")
    url = f"https://www.proff.no/firmaer/{slug}/{org_number}/"

    text = await scrape_page(url)
    if not text:
        # Try search on Proff
        search_url = f"https://www.proff.no/soek?q={org_number}"
        text = await scrape_page(search_url)
        if not text:
            return {"evidence": []}

    source_ev = EvidenceItem(
        fact_key="_proff_raw",
        fact_value=url,
        source_name="Proff.no",
        source_url=url,
        date_checked=TODAY,
        confidence=0.75,
        method="webpage_scrape",
    )

    # Extract employee count if missing
    emp_match = re.search(r"(\d[\d\s]*)\s+ansatte", text, re.IGNORECASE)
    if emp_match:
        emp_str = emp_match.group(1).replace(" ", "")
        try:
            result["employees_proff"] = int(emp_str)
            evidence.append(EvidenceItem(
                fact_key="employees",
                fact_value=int(emp_str),
                source_name="Proff.no",
                source_url=url,
                date_checked=TODAY,
                confidence=0.7,
                method="webpage_scrape",
            ))
        except ValueError:
            pass

    result["evidence"] = evidence
    result["_proff_text"] = text[:3000]  # Keep for LLM
    return result


# ─── Company website discovery ────────────────────────────────────────────────

async def discover_company_website(
    org_number: str,
    company_name: str,
    known_website: Optional[str] = None,
) -> Dict[str, Any]:
    """
    If Brreg doesn't have a website, search for it via SerpAPI.
    Also scrapes the homepage for a short description.
    """
    evidence: list[EvidenceItem] = []
    result: Dict[str, Any] = {}

    website = known_website
    if not website:
        # Search for the company website
        results = await serp_search(f'"{company_name}" organisasjonsnummer {org_number} site:no OR site:com', num=3)
        for r in results:
            link = r.get("link", "")
            # Skip aggregator sites
            if any(skip in link for skip in ["proff.no", "brreg.no", "1881.no", "gulesider"]):
                continue
            website = link
            break

    if not website:
        return {"evidence": []}

    result["website"] = website
    evidence.append(EvidenceItem(
        fact_key="website",
        fact_value=website,
        source_name="Web search",
        source_url=website,
        date_checked=TODAY,
        confidence=0.6,
        method="serp_search",
    ))

    # Scrape homepage for description
    text = await scrape_page(website, max_chars=3000)
    if text:
        result["_website_text"] = text

    result["evidence"] = evidence
    return result


# ─── Company news search ──────────────────────────────────────────────────────

async def search_company_news(company_name: str, org_number: str) -> Dict[str, Any]:
    """
    Search for recent news about the company to find CEO, revenue, etc.
    """
    query = f'"{company_name}" Norway CEO revenue employees 2024 OR 2025'
    results = await serp_search(query, num=5)

    evidence: list[EvidenceItem] = []
    snippets: list[str] = []

    for r in results:
        snippet = r.get("snippet", "")
        link = r.get("link", "")
        if snippet:
            snippets.append(f"[{link}]: {snippet}")
            evidence.append(EvidenceItem(
                fact_key="_news_snippet",
                fact_value=snippet,
                source_name=r.get("title", "News article"),
                source_url=link,
                date_checked=TODAY,
                confidence=0.5,
                method="serp_search",
            ))

    return {
        "news_snippets": snippets,
        "evidence": evidence,
    }
