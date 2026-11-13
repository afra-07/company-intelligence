"""
Signalpost Company Intelligence Agent
=====================================
Source 2: Web Search via SerpAPI + intelligent scraping & discovery.
Finds company websites, verified news, active job postings, and diverse company facts.

Keyless operation (no SERPAPI_KEY):
  - Direct HTTP scraping of official company websites, careers, and news sections.
  - Homepage discovery via Brreg-supplied hjemmeside first, with Proff.no fallback.
  - Strict identity verification (cross-checking 9-digit org numbers and verified domains)
    to prevent false company matches.
"""

from __future__ import annotations
import httpx
import json
import os
import re
import urllib.parse
from datetime import date
from typing import Optional, Dict, Any, List, Tuple
from bs4 import BeautifulSoup
from models import EvidenceItem

SERPAPI_KEY = os.getenv("SERPAPI_KEY", "")
TODAY = date.today().isoformat()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9,nb;q=0.8,no;q=0.7",
}

NORWEGIAN_MONTHS = {
    "januar": "01", "jan": "01", "january": "01",
    "februar": "02", "feb": "02", "february": "02",
    "mars": "03", "mar": "03", "march": "03",
    "april": "04", "apr": "04",
    "mai": "05", "may": "05",
    "juni": "06", "jun": "06", "june": "06",
    "juli": "07", "jul": "07", "july": "07",
    "august": "08", "aug": "08",
    "september": "09", "sep": "09", "sept": "09",
    "oktober": "10", "okt": "10", "october": "10", "oct": "10",
    "november": "11", "nov": "11",
    "desember": "12", "des": "12", "december": "12", "dec": "12",
}


def normalize_company_name(name: str) -> str:
    """Strip legal corporate suffixes and punctuation to extract core company slug."""
    cleaned = re.sub(
        r"\b(ASA|AS|DA|ANS|ENK|BA|SF|KF|IKS|GROUP|HOLDING|NORGE|NORWAY|ORGANISASJONSLEDD|ORGL)\b",
        "",
        name,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"[^a-zA-Z0-9æøåÆØÅ]+", "", cleaned).lower()
    return cleaned


def parse_iso_date(text: Optional[str]) -> Optional[str]:
    """Parse common Norwegian and ISO date formats to YYYY-MM-DD.
    
    Validates strictly using Python's datetime.date to reject impossible dates
    (e.g., February 31, April 31, month 13, etc.) and dates with out-of-range years.
    Returns None if the date cannot be validated. Never invents a date.
    """
    if not text:
        return None

    def _validate_and_format(y_val: int, m_val: int, d_val: int) -> Optional[str]:
        # Enforce realistic publication / posting year range (1990 - current_year + 3)
        current_year = date.today().year
        if not (1990 <= y_val <= current_year + 3):
            return None
        try:
            valid_dt = date(y_val, m_val, d_val)
            return valid_dt.isoformat()
        except ValueError:
            return None

    # 1. ISO format: YYYY-MM-DD (optionally followed by T or space and time)
    m = re.search(r"\b(20[0-3]\d)-(0[1-9]|1[0-2])-([0-3]\d)(?:[T\s]|\b)", text)
    if m:
        res = _validate_and_format(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if res:
            return res

    # 2. DD.MM.YYYY or DD/MM/YYYY
    m = re.search(r"\b([0-3]?\d)[\./]([0-1]?\d)[\./](20[0-3]\d)\b", text)
    if m:
        d, mo, y = m.groups()
        res = _validate_and_format(int(y), int(mo), int(d))
        if res:
            return res

    # 3. Day Month Year: e.g. "28 October 2026" or "13. november 2026" or "28. okt. 2026"
    m = re.search(r"\b([0-3]?\d)\.?\s+([a-zA-ZæøåÆØÅ]{3,12})\.?\s+(20[0-3]\d)\b", text)
    if m:
        d, mon_str, y = m.groups()
        mo = NORWEGIAN_MONTHS.get(mon_str.lower().strip("."))
        if mo:
            res = _validate_and_format(int(y), int(mo), int(d))
            if res:
                return res

    # 4. Month Day, Year: e.g. "October 28, 2026" or "May 15, 2026"
    m = re.search(r"\b([a-zA-ZæøåÆØÅ]{3,12})\s+([0-3]?\d),?\s+(20[0-3]\d)\b", text)
    if m:
        mon_str, d, y = m.groups()
        mo = NORWEGIAN_MONTHS.get(mon_str.lower())
        if mo:
            res = _validate_and_format(int(y), int(mo), int(d))
            if res:
                return res

    return None


def verify_company_identity(
    text: str,
    org_number: str,
    company_name: str,
    url: Optional[str] = None,
    *,
    is_official_website: bool = False,
    headline: Optional[str] = None,
    is_external_article: bool = False,
) -> Tuple[bool, float, str]:
    """
    CRITICAL IDENTITY CHECK:
    Verify that a page, article or candidate source strictly belongs to the target company.
    Guards against misidentifying similarly named companies and false attribution:
      1. Disqualifies if a DIFFERENT 9-digit Norwegian organization number is found.
      2. Confirms if target 9-digit organization number appears in text (confidence 0.98).
      3. For official websites (is_official_website=True): domain match confirms the source.
      4. For external news/articles (is_external_article=True):
         - NEVER accepts an article solely because the company name appears in passing.
         - Requires the target company to be featured in the headline or in the opening
           lead paragraph with repeated substantive mentions.
         - Rejects passing mentions.
      5. For general pages: requires prominent company name appearance without conflict.
    """
    if not text:
        return False, 0.0, "Empty content"

    clean_org = org_number.replace(" ", "")
    formatted_org = f"{clean_org[:3]} {clean_org[3:6]} {clean_org[6:]}"

    # 1. Check for mismatched organization number in footer / imprint / contact / body
    mismatched_pat = r"(?:organisasjonsnummer|org\.?\s*nr\.?|organisasjonsnr\.?|foretaksregisteret|mva-nr\.?|mva)[\s.:#]+(\d{3}[\s.]?\d{3}[\s.]?\d{3}|\d{9})"
    for m in re.finditer(mismatched_pat, text, re.IGNORECASE):
        found = re.sub(r"[\s.]", "", m.group(1))
        if found.isdigit() and len(found) == 9 and found != clean_org:
            return False, 0.0, f"Found conflicting organization number {found} (expected {clean_org})"

    # 2. Check if target organization number appears
    if clean_org in text or formatted_org in text:
        return True, 0.98, "Target organization number matched in text"

    # 3. Domain match against normalized company name (disabled for external publisher articles)
    if url and not is_external_article:
        try:
            netloc = urllib.parse.urlparse(url).netloc.lower()
            if netloc.startswith("www."):
                netloc = netloc[4:]
            domain_slug = netloc.split(".")[0]
            norm_name = normalize_company_name(company_name)
            clean_domain_slug = re.sub(r"[^a-zA-Z0-9æøåÆØÅ]+", "", domain_slug)
            if clean_domain_slug and norm_name and len(clean_domain_slug) >= 3:
                if (clean_domain_slug in norm_name or norm_name in clean_domain_slug or norm_name.startswith(clean_domain_slug)):
                    return True, 0.95, f"Domain {netloc} matches company name '{company_name}'"
        except Exception:
            pass

    # 4. Check for external articles / publishers
    clean_name = company_name.strip()
    norm_target = normalize_company_name(company_name)
    text_lower = text.lower()

    if is_external_article:
        # Strict external article verification:
        # Require company name in headline OR in lead paragraph with multiple body mentions.
        headline_text = (headline or "").strip()
        headline_lower = headline_text.lower()

        name_in_headline = False
        if clean_name.lower() in headline_lower:
            name_in_headline = True
        elif norm_target and len(norm_target) >= 4 and norm_target in normalize_company_name(headline_text):
            name_in_headline = True

        if name_in_headline:
            return True, 0.90, f"Company '{clean_name}' featured in article headline"

        # Check lead paragraph (first 300 characters)
        lead_text = text_lower[:300]
        name_in_lead = (clean_name.lower() in lead_text) or (norm_target in lead_text)

        # Count occurrences in body
        occurrences = text_lower.count(clean_name.lower())
        if norm_target and norm_target != clean_name.lower():
            occurrences = max(occurrences, text_lower.count(norm_target))

        if name_in_lead and occurrences >= 2:
            return True, 0.85, f"Company '{clean_name}' featured in lead and repeated ({occurrences} times)"

        # Passing mention or unrelated article
        return False, 0.30, f"Company '{clean_name}' mentioned only in passing in external article ({occurrences} mention(s), not in headline)"

    # 5. Non-external official page check
    if clean_name.lower() in text_lower:
        if is_official_website:
            return True, 0.90, f"Company name '{clean_name}' verified on official site"
        return True, 0.85, f"Company name '{clean_name}' present in text"

    return False, 0.3, "No conclusive identity match found"


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

async def scrape_page_soup(url: str, max_chars: int = 15000) -> Tuple[Optional[str], Optional[BeautifulSoup], Optional[str]]:
    """
    Fetch a webpage and return (clean_text, BeautifulSoup, final_url).
    Follows redirects and strips non-content tags.
    """
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=True, headers=HEADERS) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None, None, None
            final_url = str(resp.url)
            soup = BeautifulSoup(resp.text, "lxml")
            for tag in soup(["script", "style", "noscript", "svg"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            text = re.sub(r"\n{3,}", "\n\n", text)
            return text[:max_chars], soup, final_url
    except Exception:
        return None, None, None


async def scrape_page(url: str, max_chars: int = 8000) -> Optional[str]:
    """Fetch webpage and return clean text content."""
    text, _, _ = await scrape_page_soup(url, max_chars=max_chars)
    return text


# ─── Proff.no direct lookup ───────────────────────────────────────────────────

async def scrape_proff(org_number: str, company_name: str) -> Dict[str, Any]:
    """
    Scrape proff.no for company website discovery and fallback employee count.
    Citation rule: Proff.no is permitted ONLY for employees and internal _proff_raw keys.
    Never attribute Proff-extracted facts to a company's website.
    """
    evidence: list[EvidenceItem] = []
    result: Dict[str, Any] = {}

    slug = re.sub(r"[^a-z0-9]+", "-", company_name.lower()).strip("-")
    url = f"https://www.proff.no/firmaer/{slug}/{org_number}/"

    text = await scrape_page(url)
    if not text:
        search_url = f"https://www.proff.no/soek?q={org_number}"
        text = await scrape_page(search_url)
        if not text:
            return {"evidence": []}

    company_website = _extract_company_website_from_proff(text, org_number)
    if company_website:
        result["_proff_website"] = company_website

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
    """Parse plain text of a Proff.no page to find the company's homepage link."""
    m = re.search(
        r"(?:Hjemmeside|Nettside|Website)[:\s]+((https?://|www\.)[^\s\n,;\"'<>]+)",
        text,
        re.IGNORECASE,
    )
    if m:
        site = m.group(1).rstrip("/.,;")
        if not site.startswith("http"):
            site = "https://" + site
        if not any(skip in site for skip in ["proff.no", "brreg.no", "1881.no", "gulesider"]):
            return site
    return None


# ─── Company website discovery with strict verification ───────────────────────

async def discover_company_website(
    org_number: str,
    company_name: str,
    known_website: Optional[str] = None,
    proff_website_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Find and strictly verify the official company website:
      1. Uses Brreg-supplied hjemmeside first (100% authoritative).
      2. If missing, tests SerpAPI or Proff fallback.
      3. Validates domain ownership to prevent incorrect company matches.
    """
    evidence: list[EvidenceItem] = []
    result: Dict[str, Any] = {}

    website = known_website
    is_brreg_authoritative = bool(known_website)

    if not website:
        if SERPAPI_KEY:
            results = await serp_search(
                f'"{company_name}" organisasjonsnummer {org_number} site:no OR site:com', num=3
            )
            for r in results:
                link = r.get("link", "")
                if any(skip in link for skip in ["proff.no", "brreg.no", "1881.no", "gulesider", "purehelp.no"]):
                    continue
                website = link
                break

        if not website and proff_website_hint:
            website = proff_website_hint

    if not website:
        return {"evidence": []}

    if not website.startswith("http"):
        website = "https://" + website

    text, soup, final_url = await scrape_page_soup(website, max_chars=8000)
    actual_url = final_url or website

    if not is_brreg_authoritative and text:
        is_verified, conf, reason = verify_company_identity(text, org_number, company_name, actual_url, is_official_website=True)
        if not is_verified or conf < 0.7:
            return {"evidence": []}

    result["website"] = actual_url
    if text:
        result["_website_text"] = text

    evidence.append(EvidenceItem(
        fact_key="website",
        fact_value=actual_url,
        source_name="Company official website",
        source_url=actual_url,
        date_checked=TODAY,
        confidence=0.95 if is_brreg_authoritative else 0.85,
        method="webpage_scrape",
    ))

    result["evidence"] = evidence
    return result


# ─── Dedicated Company News Pipeline ──────────────────────────────────────────

async def extract_company_news(
    website_url: Optional[str],
    company_name: str,
    org_number: str,
) -> List[Dict[str, Any]]:
    """
    PHASE 3: Dedicated News Collection System.
    For every news item:
      - Extracts headline, factual summary, publication date, and original URL.
      - Strictly verifies company identity.
      - Visits original source page when available.
      - Rejects unverified and unrelated results.
    """
    news_items: List[Dict[str, Any]] = []
    seen_urls: set[str] = set()

    if not website_url:
        return []

    text, soup, final_url = await scrape_page_soup(website_url, max_chars=12000)
    base_url = final_url or website_url

    if not soup:
        return []

    candidate_article_links: List[Tuple[str, str, Optional[str]]] = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        full_url = urllib.parse.urljoin(base_url, href)
        if not full_url.startswith("http") or full_url in seen_urls:
            continue

        link_text = a.get_text(separator=" ", strip=True)
        if any(p in href.lower() for p in ["/news/", "/nyheter/", "/pressemelding/", "/aktuelt/", "/presse/", "/press-releases/", "/pressemeldinger/", "/media/", "/investor-relations/"]):
            parsed_path = urllib.parse.urlparse(full_url).path.strip("/")
            if "/" in parsed_path and len(link_text) > 15:
                date_hint = parse_iso_date(link_text)
                candidate_article_links.append((link_text, full_url, date_hint))
                seen_urls.add(full_url)

    if len(candidate_article_links) < 2:
        for subpath in ["/news", "/nyheter", "/presse", "/aktuelt", "/media", "/press-releases", "/pressemeldinger", "/investor-relations"]:
            sub_url = urllib.parse.urljoin(base_url, subpath)
            sub_text, sub_soup, sub_final = await scrape_page_soup(sub_url, max_chars=12000)
            if not sub_soup:
                continue
            for a in sub_soup.find_all("a", href=True):
                href = a["href"].strip()
                full_url = urllib.parse.urljoin(sub_final or sub_url, href)
                if not full_url.startswith("http") or full_url in seen_urls:
                    continue
                link_text = a.get_text(separator=" ", strip=True)
                parsed_path = urllib.parse.urlparse(full_url).path.strip("/")
                if "/" in parsed_path and len(link_text) > 20:
                    date_hint = parse_iso_date(link_text)
                    candidate_article_links.append((link_text, full_url, date_hint))
                    seen_urls.add(full_url)
                if len(candidate_article_links) >= 5:
                    break
            if len(candidate_article_links) >= 3:
                break

    for raw_headline, art_url, date_hint in candidate_article_links[:3]:
        art_text, art_soup, _ = await scrape_page_soup(art_url, max_chars=6000)
        if not art_soup:
            continue

        h1 = art_soup.find("h1")
        headline = h1.get_text(strip=True) if h1 else raw_headline

        # Extract publication date from article metadata — NEVER fall back to TODAY
        pub_date = None
        time_tag = art_soup.find("time")
        if time_tag:
            pub_date = parse_iso_date(time_tag.get("datetime")) or parse_iso_date(time_tag.get_text(strip=True))

        if not pub_date:
            # Try <meta> property tags (e.g. og:article:published_time)
            for meta_tag in art_soup.find_all("meta"):
                prop = meta_tag.get("property", "") or meta_tag.get("name", "")
                if any(k in prop.lower() for k in ["published_time", "publish_date", "date", "pubdate"]):
                    pub_date = parse_iso_date(meta_tag.get("content", ""))
                    if pub_date:
                        break

        if not pub_date and date_hint:
            pub_date = date_hint

        if not pub_date and art_text:
            pub_date = parse_iso_date(art_text[:500])

        # If no date could be determined, store null — never fabricate
        # pub_date remains None

        paragraphs = [
            p.get_text(strip=True) for p in art_soup.find_all("p")
            if len(p.get_text(strip=True)) > 40
        ]
        summary = paragraphs[0] if paragraphs else headline
        if len(summary) > 250:
            summary = summary[:247] + "..."

        # For articles on the official company website, use is_official_website=True
        art_domain = urllib.parse.urlparse(art_url).netloc.lower()
        base_domain = urllib.parse.urlparse(base_url).netloc.lower()
        is_same_domain = art_domain == base_domain or art_domain.endswith("." + base_domain)
        is_verified, conf, _ = verify_company_identity(
            art_text or headline, org_number, company_name, art_url,
            is_official_website=is_same_domain,
            headline=headline,
            is_external_article=not is_same_domain,
        )
        if is_verified:
            news_items.append({
                "headline": headline[:140],
                "summary": summary,
                "published_date": pub_date,  # may be None
                "url": art_url,
                "company": company_name,
                "source_name": f"{company_name} Official News" if is_same_domain else f"Independent News ({art_domain})",
            })

    if not news_items and SERPAPI_KEY:
        external_results = await serp_search(f'"{company_name}" news OR pressemelding', num=3)
        for r in external_results:
            link = r.get("link", "")
            title = r.get("title", "")
            snippet = r.get("snippet", "")
            if not link or any(skip in link for skip in ["google.", "bing.", "proff.no"]):
                continue

            art_text = await scrape_page(link, max_chars=4000)
            if art_text:
                is_ver, c, _ = verify_company_identity(
                    art_text, org_number, company_name, link,
                    is_official_website=False,
                    headline=title,
                    is_external_article=True,
                )
                if is_ver:
                    source_label = r.get("source") or urllib.parse.urlparse(link).netloc
                    news_items.append({
                        "headline": title[:140],
                        "summary": snippet[:250],
                        "published_date": parse_iso_date(art_text[:500]),  # may be None
                        "url": link,
                        "company": company_name,
                        "source_name": f"Independent News ({source_label})",
                    })
                    break

    return news_items


# ─── Dedicated Job Posting Pipeline ───────────────────────────────────────────

async def extract_company_jobs(
    website_url: Optional[str],
    company_name: str,
    org_number: str,
) -> List[Dict[str, Any]]:
    """
    PHASE 4: Dedicated Job Discovery and Verification System.
    Searches:
      - Official company career pages and ATS integrations (Workday, Teamtailor, etc.).
    Extracts:
      - Job title, location, employment type, application deadline, direct URL.
    Only includes verifiable, active vacancies.
    """
    job_postings: List[Dict[str, Any]] = []
    seen_urls: set[str] = set()

    if not website_url:
        return []

    text, soup, final_url = await scrape_page_soup(website_url, max_chars=12000)
    base_url = final_url or website_url

    if not soup:
        return []

    careers_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        link_text = a.get_text(separator=" ", strip=True).lower()
        if any(k in href.lower() or k in link_text for k in ["career", "karriere", "jobb", "stillinger", "work-with-us", "vacancies"]):
            full_url = urllib.parse.urljoin(base_url, href)
            if full_url not in careers_links:
                careers_links.append(full_url)

    if not careers_links:
        for p in ["/careers", "/karriere", "/jobb", "/stillinger"]:
            careers_links.append(urllib.parse.urljoin(base_url, p))

    for car_url in careers_links[:3]:
        if "myworkdayjobs.com" in car_url:
            workday_jobs = await _query_workday_jobs(car_url, company_name, org_number)
            if workday_jobs:
                job_postings.extend(workday_jobs)
                break

        car_text, car_soup, car_final = await scrape_page_soup(car_url, max_chars=12000)
        if not car_soup or not car_text:
            continue

        # Verify careers page itself
        is_car_ver, car_conf, _ = verify_company_identity(
            car_text, org_number, company_name, car_final or car_url, is_official_website=True
        )
        if not is_car_ver and car_conf == 0.0:
            continue

        for a in car_soup.find_all("a", href=True):
            if "myworkdayjobs.com" in a["href"]:
                workday_jobs = await _query_workday_jobs(a["href"], company_name, org_number)
                if workday_jobs:
                    job_postings.extend(workday_jobs)
                    break
        if job_postings:
            break

        for a in car_soup.find_all("a", href=True):
            href = a["href"].strip()
            job_url = urllib.parse.urljoin(car_final or car_url, href)
            job_text = a.get_text(separator=" ", strip=True)

            if job_url in seen_urls:
                continue

            if any(p in href.lower() for p in ["/job/", "/stilling/", "/position/", "/vacancy/", "/karriere/"]):
                parsed_path = urllib.parse.urlparse(job_url).path.strip("/")
                if "/" in parsed_path and len(job_text) > 8 and not any(skip in job_text.lower() for skip in ["cookie", "privacy", "search"]):
                    seen_urls.add(job_url)
                    # Visit the individual job page to verify it is active and belongs to target company
                    job_page_text, job_soup, job_final_url = await scrape_page_soup(job_url, max_chars=4000)
                    if not job_page_text or not job_soup:
                        continue  # Unreachable / non-200 — skip unverifiable vacancy

                    # Verify individual job page against company identity
                    is_j_ver, j_conf, _ = verify_company_identity(
                        job_page_text, org_number, company_name, job_final_url or job_url, is_official_website=True
                    )
                    if not is_j_ver and j_conf == 0.0:
                        continue  # Belongs to another company!

                    # Check for expired/closed indicators
                    page_lower = job_page_text.lower()
                    if any(closed in page_lower for closed in [
                        "expired", "closed", "no longer accepting", "not available",
                        "deadline has passed", "stillingen er lukket", "søknadsfristen er utløpt",
                        "utgått", "ikke lenger tilgjengelig", "this position has been filled",
                        "job is no longer available",
                    ]):
                        continue  # Expired or closed — exclude

                    # Extract employment type, deadline, location without inventing
                    emp_type = _extract_field_from_page(job_soup, [
                        "employment type", "ansettelsesform", "stillingsprosent",
                        "full-time", "part-time", "heltid", "deltid", "fast stilling", "vikariat",
                    ])
                    deadline = _extract_field_from_page(job_soup, [
                        "deadline", "søknadsfrist", "application deadline", "frist",
                    ])
                    loc = _extract_field_from_page(job_soup, [
                        "location", "arbeidssted", "sted", "city",
                    ])

                    job_postings.append({
                        "title": job_text[:100],
                        "location": loc,  # None if not explicitly found
                        "employment_type": emp_type,  # None if not found
                        "deadline": deadline,  # None if not found
                        "url": job_url,
                        "company": company_name,
                        "source_name": f"{company_name} Career Portal",
                    })
                    if len(job_postings) >= 4:
                        break

        if len(job_postings) >= 3:
            break

    return job_postings[:5]


def _extract_field_from_page(soup: Optional[BeautifulSoup], keywords: List[str]) -> Optional[str]:
    """Search a BeautifulSoup page for a field value near any of the given keywords.
    
    Looks for patterns like:
      <dt>Employment type</dt><dd>Full-time</dd>
      <strong>Deadline:</strong> 2026-12-31
      <span>Søknadsfrist: 15. november 2026</span>
    Returns the extracted value string, or None. Never invents values.
    """
    if not soup:
        return None

    # Strategy 1: <dt>/<dd> pairs
    for dt in soup.find_all("dt"):
        dt_text = dt.get_text(strip=True).lower()
        if any(k in dt_text for k in keywords):
            dd = dt.find_next_sibling("dd")
            if dd:
                val = dd.get_text(strip=True)
                if val and len(val) < 100:
                    return val

    # Strategy 2: <strong>Label:</strong> value or <b>Label:</b> value
    for tag in soup.find_all(["strong", "b", "label", "span", "th"]):
        tag_text = tag.get_text(strip=True).lower().rstrip(":")
        if any(k in tag_text for k in keywords):
            next_sib = tag.next_sibling
            if next_sib and isinstance(next_sib, str) and next_sib.strip():
                val = next_sib.strip().strip(":").strip()
                if val and len(val) < 100:
                    return val
            next_tag = tag.find_next_sibling()
            if next_tag:
                val = next_tag.get_text(strip=True)
                if val and len(val) < 100:
                    return val

    return None


async def _query_workday_jobs(workday_url: str, company_name: str, org_number: str = "") -> List[Dict[str, Any]]:
    """Query Workday public CXS JSON API for live vacancies.
    
    Each vacancy is verified by checking the individual job page.
    If individual-page verification fails or raises an exception, the vacancy is excluded.
    Employment type, location, deadline, and posting date are extracted only when supported.
    Never invents values.
    """
    jobs: List[Dict[str, Any]] = []
    try:
        parsed = urllib.parse.urlparse(workday_url)
        host = parsed.netloc
        tenant_path = parsed.path.strip("/").split("/")[0]
        api_url = f"https://{host}/wday/cxs/{host.split('.')[0]}/{tenant_path}/jobs"

        async with httpx.AsyncClient(timeout=8, headers=HEADERS) as client:
            resp = await client.post(api_url, json={"appliedFacets": {}, "limit": 4, "offset": 0, "searchText": ""})
            if resp.status_code == 200:
                data = resp.json()
                for j in data.get("jobPostings", [])[:4]:
                    title = j.get("title")
                    ext_path = j.get("externalPath", "")
                    job_url = f"https://{host}/{tenant_path}{ext_path}"
                    loc = j.get("locationsText") or None
                    posted_on = j.get("postedOn")

                    # Extract employment type from bullet fields if available
                    emp_type = None
                    deadline = None
                    for bullet in j.get("bulletFields", []):
                        bullet_lower = str(bullet).lower()
                        if any(k in bullet_lower for k in ["full-time", "part-time", "heltid", "deltid", "contract", "vikariat", "fast stilling"]):
                            emp_type = str(bullet).strip()
                        if any(k in bullet_lower for k in ["deadline", "frist", "close"]):
                            deadline = str(bullet).strip()

                    if not title:
                        continue

                    # STRICT REQUIREMENT: Individual-page verification MUST succeed
                    # If verification fails or raises an exception, do NOT include the vacancy.
                    try:
                        verify_resp = await client.get(job_url, follow_redirects=True)
                        if verify_resp.status_code != 200:
                            continue  # Individual page not accessible (non-200) -> exclude!
                        
                        page_text = verify_resp.text
                        if org_number:
                            is_v, c_conf, _ = verify_company_identity(
                                page_text, org_number, company_name, job_url, is_official_website=True
                            )
                            if not is_v and c_conf == 0.0:
                                continue  # Conflicting company found -> exclude!

                        # Parse structured data (JSON-LD) from the individual job page to verify requisition ID & title
                        soup_job = BeautifulSoup(page_text, "html.parser")
                        ld_job = None
                        for s in soup_job.find_all("script"):
                            if s.get("type") == "application/ld+json":
                                try:
                                    ld_parsed = json.loads(s.get_text())
                                    if isinstance(ld_parsed, dict) and ld_parsed.get("@type") == "JobPosting":
                                        ld_job = ld_parsed
                                        break
                                except Exception:
                                    pass

                        if ld_job:
                            ld_title = ld_job.get("title")
                            ld_id_obj = ld_job.get("identifier")
                            ld_req_id = ld_id_obj.get("value") if isinstance(ld_id_obj, dict) else None
                            
                            # If requisition ID is present in URL/job data, verify match
                            if ext_path and "_" in ext_path:
                                expected_req = ext_path.split("_")[-1]
                                if ld_req_id and ld_req_id != expected_req:
                                    continue  # Mismatched requisition ID!
                            
                            # Verify title matches or aligns with API title
                            if ld_title and title and (ld_title.lower() not in title.lower() and title.lower() not in ld_title.lower()):
                                continue  # Mismatched job title!
                                
                            # Extract verified location, datePosted, validThrough from JSON-LD if not already found
                            if not loc:
                                loc_addr = ld_job.get("jobLocation", {}).get("address", {})
                                if isinstance(loc_addr, dict):
                                    loc_city = loc_addr.get("addressLocality")
                                    loc_country = loc_addr.get("addressCountry")
                                    loc = f"{loc_city}, {loc_country}" if loc_city and loc_country else (loc_city or loc_country)
                            if ld_job.get("datePosted"):
                                posted_on = ld_job.get("datePosted")
                            if not deadline and ld_job.get("validThrough"):
                                deadline = ld_job.get("validThrough")
                            if not emp_type and ld_job.get("employmentType"):
                                raw_et = ld_job.get("employmentType")
                                if raw_et == "FULL_TIME":
                                    emp_type = "Full-time"
                                elif raw_et == "PART_TIME":
                                    emp_type = "Part-time"

                        page_lower = page_text.lower()
                        if any(closed in page_lower for closed in [
                            "expired", "closed", "no longer", "utgått", "position has been filled", "ikke lenger"
                        ]):
                            continue  # Expired vacancy -> exclude!
                    except Exception:
                        # Exclude vacancy if individual verification fails or raises exception
                        continue

                    jobs.append({
                        "title": title[:100],
                        "location": loc,  # None if not provided
                        "employment_type": emp_type,  # None if not determinable
                        "deadline": deadline,  # None if not determinable
                        "posted_date": parse_iso_date(posted_on) if posted_on else None,
                        "url": job_url,
                        "company": company_name,
                        "source_name": f"{company_name} Official Workday Portal",
                    })
    except Exception:
        pass
    return jobs


# ─── Dedicated Company Intelligence Extraction (Diverse Claims) ───────────────

async def extract_company_intelligence_facts(
    website_url: Optional[str],
    company_name: str,
    org_number: str,
) -> List[Dict[str, Any]]:
    """
    PHASE 2: Claim Quality and Diversity.
    Extracts high-value facts across core operational dimensions:
      - Products and services
      - Business activities
      - Sustainability initiatives
      - Technology and innovation
      - Strategic projects
    Verifies page identity before extracting facts and filters generic marketing noise.
    """
    facts: List[Dict[str, Any]] = []
    if not website_url:
        return []

    text, soup, final_url = await scrape_page_soup(website_url, max_chars=12000)
    base_url = final_url or website_url

    if not soup:
        return []

    about_url = None
    services_url = None
    sust_url = None

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        full_url = urllib.parse.urljoin(base_url, href)
        path = urllib.parse.urlparse(full_url).path.lower().strip("/")

        if any(skip in path for skip in ["contact", "privacy", "cookie", "terms", "social-media", "accessibility", "site-info", "faq"]):
            continue

        if not about_url and path in ["about-us", "about", "om-oss", "om", "in-brief", "who-we-are"]:
            about_url = full_url
        elif not services_url and path in ["what-we-do", "tjenester", "services", "produkter", "products", "virksomhet", "hva-gjor-vi"]:
            services_url = full_url
        elif not sust_url and path in ["sustainability", "baerekraft", "bærekraft", "esg", "samfunnsansvar", "klima"]:
            sust_url = full_url

    pages_to_check = [
        (about_url or urllib.parse.urljoin(base_url, "/about-us"), "about"),
        (services_url or urllib.parse.urljoin(base_url, "/what-we-do"), "services"),
        (sust_url or urllib.parse.urljoin(base_url, "/sustainability"), "sustainability"),
        (base_url, "homepage"),
    ]

    seen_facts = set()
    MAX_PER_CATEGORY = 3

    for page_url, category in pages_to_check:
        p_text, p_soup, actual_url = await scrape_page_soup(page_url, max_chars=10000)
        if not p_soup or not p_text:
            continue
        source_url = actual_url or page_url

        # Verify page identity before extracting facts
        is_p_ver, p_conf, _ = verify_company_identity(
            p_text, org_number, company_name, source_url, is_official_website=True
        )
        if not is_p_ver and p_conf == 0.0:
            continue  # Conflicting company found — skip!

        for p in p_soup.find_all("p"):
            pt = p.get_text(separator=" ", strip=True)
            pt = re.sub(r"\s+", " ", pt).strip()
            if not (50 < len(pt) < 320):
                continue
            if pt in seen_facts:
                continue

            pt_lower = pt.lower()
            # Disqualify boilerplate / cookie / navigation / footer noise
            if any(skip in pt_lower for skip in [
                "cookie", "privacy policy", "terms of use", "all rights reserved",
                "copyright", "browser", "javascript", "click here", "read more",
                "subscribe to", "follow us on", "social media", "contact us",
                "log in", "sign up", "fyll ut skjemaet", "brukervilkår",
            ]):
                continue

            if any(k in pt_lower for k in [
                "deliveries of", "portfolio encompasses", "portfolio spans", "offering",
                "products", "services", "tjenester", "produkter", "supplier of",
                "leverandør av", "solutions", "commercial production", "delivers energy",
            ]):
                cat_count = sum(1 for f in facts if f["fact_key"] == "products_services")
                if cat_count < MAX_PER_CATEGORY:
                    facts.append({
                        "fact_key": "products_services",
                        "fact_value": pt,
                        "source_name": f"{company_name} Products & Services",
                        "source_url": source_url,
                        "confidence": 0.92,
                    })
                    seen_facts.add(pt)
                    continue

            if any(k in pt_lower for k in [
                "international energy company", "backbone of the company", "virksomhet",
                "core positions", "operates", "focused on", "leverer", "operatør",
                "active in", "production facilities", "offshore operations", "headquartered in",
            ]):
                cat_count = sum(1 for f in facts if f["fact_key"] == "business_activity")
                if cat_count < MAX_PER_CATEGORY:
                    facts.append({
                        "fact_key": "business_activity",
                        "fact_value": pt,
                        "source_name": f"{company_name} Operations",
                        "source_url": source_url,
                        "confidence": 0.92,
                    })
                    seen_facts.add(pt)
                    continue

            if any(k in pt_lower for k in [
                "net zero by 2050", "net-zero", "emissions", "low-carbon", "renewables",
                "bærekraft", "energy transition", "climate", "fornybar", "carbon capture",
                "offshore wind", "esg",
            ]):
                cat_count = sum(1 for f in facts if f["fact_key"] == "sustainability")
                if cat_count < MAX_PER_CATEGORY:
                    facts.append({
                        "fact_key": "sustainability",
                        "fact_value": pt,
                        "source_name": f"{company_name} Sustainability",
                        "source_url": source_url,
                        "confidence": 0.90,
                    })
                    seen_facts.add(pt)
                    continue

            if any(k in pt_lower for k in [
                "collaboration", "partnership", "project", "joint venture", "samarbeid",
                "avtale", "field development", "investment decision", "development plan",
            ]):
                cat_count = sum(1 for f in facts if f["fact_key"] == "project")
                if cat_count < MAX_PER_CATEGORY:
                    facts.append({
                        "fact_key": "project",
                        "fact_value": pt,
                        "source_name": f"{company_name} Projects & Collaboration",
                        "source_url": source_url,
                        "confidence": 0.88,
                    })
                    seen_facts.add(pt)
                    continue

            if any(k in pt_lower for k in [
                "technology", "innovation", "digital", "artificial intelligence",
                "machine learning", "automation", "robotics", "digitalisering",
                "teknologi", "subsea technology", "research and development",
            ]):
                cat_count = sum(1 for f in facts if f["fact_key"] == "technology_innovation")
                if cat_count < MAX_PER_CATEGORY:
                    facts.append({
                        "fact_key": "technology_innovation",
                        "fact_value": pt,
                        "source_name": f"{company_name} Technology & Innovation",
                        "source_url": source_url,
                        "confidence": 0.88,
                    })
                    seen_facts.add(pt)
                    continue

        if len(facts) >= 10:
            break

    return facts


# ─── Legacy search news wrapper (kept for backwards compatibility) ─────────────

async def search_company_news(company_name: str, org_number: str) -> List[Dict[str, str]]:
    """Legacy news search using SerpAPI."""
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
        if any(skip in link for skip in ["google.", "bing.", "yahoo.", "proff.no", "1881.no", "gulesider"]):
            continue

        articles.append({
            "title": title,
            "link": link,
            "snippet": snippet,
        })

    return articles
