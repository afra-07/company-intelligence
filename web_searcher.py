"""
Signalpost Company Intelligence Agent
=====================================
Source 2: Web Search via SerpAPI + intelligent scraping
Finds company websites, news, and supplemental facts.

Keyless operation (no SERPAPI_KEY):
  - serp_search() returns [] immediately — no external call.
  - discover_company_website() uses the Brreg-supplied hjemmeside first, then
    falls back to extracting a homepage link from the Proff.no page.
  - search_company_news() returns empty — no search key available.
  All core pipeline steps (Brreg + Regnskapsregisteret + Proff scrape) still
  run and a complete profile is produced for every company.
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
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True, headers=HEADERS) as client:
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
    Scrape proff.no for company website discovery and fallback employee count.
    Citation rule: Proff.no is permitted ONLY for employees and internal _proff_raw keys.
    Never attribute Proff-extracted facts to a company's website.
    """
    evidence: list[EvidenceItem] = []
    result: Dict[str, Any] = {}

    # Proff.no URL pattern: /firmaer/<slug>/<org_number>/
    slug = re.sub(r"[^a-z0-9]+", "-", company_name.lower()).strip("-")
    url = f"https://www.proff.no/firmaer/{slug}/{org_number}/"

    text = await scrape_page(url)
    if not text:
        # Try search on Proff
        search_url = f"https://www.proff.no/soek?q={org_number}"
        text = await scrape_page(search_url)
        if not text:
            return {"evidence": []}

    # Extract official company website URL from Proff
    company_website = _extract_company_website_from_proff(text, org_number)
    if company_website:
        result["_proff_website"] = company_website

    # Extract employee count if present on Proff
    emp_match = re.search(r"(\d[\d\s]*)\s+ansatte", text, re.IGNORECASE)
    if emp_match:
        emp_str = emp_match.group(1).replace(" ", "")
        try:
            emp_val = int(emp_str)
            result["employees"] = emp_val
            evidence.append(EvidenceItem(
                fact_key="employees",
                fact_value=emp_val,
                source_name="Proff.no",
                source_url=url,
                date_checked=TODAY,
                confidence=0.7,
                method="webpage_scrape",
            ))
        except ValueError:
            pass

    result["evidence"] = evidence
    return result


def _extract_company_website_from_proff(text: str, org_number: str) -> Optional[str]:
    """
    Parse the plain text of a Proff.no page to find the company's own homepage.
    Proff typically renders it as a labelled line like:
      Hjemmeside: https://www.example.no
    Returns the URL string if found, else None.
    """
    # Match labelled website line
    m = re.search(
        r"(?:Hjemmeside|Nettside|Website)[:\s]+((https?://|www\.)[^\s\n,;\"'<>]+)",
        text,
        re.IGNORECASE,
    )
    if m:
        site = m.group(1).rstrip("/.,;")
        if not site.startswith("http"):
            site = "https://" + site
        # Exclude aggregator sites
        if not any(skip in site for skip in ["proff.no", "brreg.no", "1881.no", "gulesider"]):
            return site
    return None


# ─── Company website discovery ────────────────────────────────────────────────

async def discover_company_website(
    org_number: str,
    company_name: str,
    known_website: Optional[str] = None,
    proff_website_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """
    If Brreg doesn't have a website, find one via:
      1. SerpAPI search (if SERPAPI_KEY is set), OR
      2. Homepage URL extracted from the Proff.no page (keyless fallback).
    Then scrapes the company's own homepage for a description.

    The discovered URL is always the company's own site, so facts scraped from
    it are cited with the company's domain as source_url — never proff.no.
    """
    evidence: list[EvidenceItem] = []
    result: Dict[str, Any] = {}

    website = known_website

    if not website:
        if SERPAPI_KEY:
            # Search for the company website via SerpAPI
            results = await serp_search(
                f'"{company_name}" organisasjonsnummer {org_number} site:no OR site:com', num=3
            )
            for r in results:
                link = r.get("link", "")
                # Skip aggregator sites
                if any(skip in link for skip in ["proff.no", "brreg.no", "1881.no", "gulesider"]):
                    continue
                website = link
                break

        # Keyless fallback: use homepage URL extracted from Proff.no page
        if not website and proff_website_hint:
            website = proff_website_hint

    if not website:
        return {"evidence": []}

    result["website"] = website
    evidence.append(EvidenceItem(
        fact_key="website",
        fact_value=website,
        source_name="Company website" if (not SERPAPI_KEY or proff_website_hint == website) else "Web search",
        source_url=website,
        date_checked=TODAY,
        confidence=0.65 if not SERPAPI_KEY else 0.6,
        method="webpage_scrape" if (not SERPAPI_KEY or proff_website_hint == website) else "serp_search",
    ))

    # Scrape homepage for description
    text = await scrape_page(website, max_chars=3000)
    if text:
        result["_website_text"] = text

    result["evidence"] = evidence
    return result


# ─── Company news search ──────────────────────────────────────────────────────

async def search_company_news(company_name: str, org_number: str) -> List[Dict[str, str]]:
    """
    Search for recent news about the company to find CEO, revenue, etc.
    Returns a list of articles: [{"title": ..., "link": ..., "snippet": ...}]
    Only includes items with valid, real article URLs (never google.com or aggregators).
    """
    query = f'"{company_name}" Norway CEO revenue employees 2024 OR 2025'
    results = await serp_search(query, num=5)

    articles: List[Dict[str, str]] = []

    for r in results:
        snippet = r.get("snippet", "")
        link = r.get("link", "")
        title = r.get("title", "News article")
        if not link or not snippet:
            continue
        if not (link.startswith("http://") or link.startswith("https://")):
            continue
        # Skip search engines, portals and aggregators
        if any(skip in link for skip in ["google.", "bing.", "yahoo.", "proff.no", "1881.no", "gulesider"]):
            continue

        articles.append({
            "title": title,
            "link": link,
            "snippet": snippet,
        })

    return articles
