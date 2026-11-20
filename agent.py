"""
Signalpost Company Intelligence Agent
=====================================
The main orchestration pipeline.
Coordinates all authoritative public sources, performs strict identity verification,
runs dedicated news and job posting pipelines, and produces high-diversity company intelligence.

Pipeline:
  org_number
    â†’ Brreg entity fetch                 (authoritative legal identity, address, status, employees)
    â†’ Brreg roles fetch                  (CEO, board of directors)
    â†’ Regnskapsregisteret                (annual accounts & financials)
    â†’ Proff.no scrape                    (website discovery bridge & employee fallback)
    â†’ Website Discovery & Verification   (strict domain and org number cross-checking)
    â†’ Dedicated News Pipeline            (headlines, summaries, publication dates, original URLs)
    â†’ Dedicated Job Posting Pipeline     (vacancies, locations, employment types, deadlines, URLs)
    â†’ Dedicated Intelligence Facts       (products/services, operations, sustainability, projects)
    â†’ LLM Extraction & Verification      (optional deep extraction if provider key set)
    â†’ Deduplication & Quality Filter     (removes duplicate/unsupported claims, validates citations)
    â†’ Profile Merge / Persist            (updates store and generates contract-compliant profile)
"""

from __future__ import annotations
import asyncio
import math
import os
import time
from urllib.parse import urlparse
from datetime import datetime, date
from typing import List, Optional, Dict, Any

from models import CompanyProfile, EvidenceItem, AgentRunResult
from brreg_fetcher import fetch_brreg_entity, fetch_brreg_roles, fetch_regnskap
from web_searcher import (
    scrape_proff,
    discover_company_website,
    extract_company_news,
    extract_company_jobs,
    extract_company_intelligence_facts,
    search_company_news,
)
from llm_extractor import (
    extract_facts_from_text,
    verify_company_match,
    generate_description,
    resolve_conflicting_values,
)
from profile_store import (
    load_profile,
    load_profile_any_age,
    save_profile,
    merge_profile,
    increment_request,
    filter_expired_jobs,
)
from llm_extractor import _PROVIDER as _LLM_PROVIDER

HAS_OPENAI = _LLM_PROVIDER is not None
TODAY = date.today().isoformat()


def _compute_confidence(profile: CompanyProfile) -> float:
    """
    Compute overall profile confidence score based on:
    - Number of core fields populated
    - Average confidence of supporting evidence
    - Diversity bonus for non-registry company intelligence (news, jobs, products)
    """
    key_fields = [
        "company_name", "status", "industry_description", "address",
        "employees", "revenue", "ceo", "website", "description",
    ]
    filled = sum(1 for f in key_fields if getattr(profile, f, None) is not None)
    field_score = filled / len(key_fields)

    # Check evidence diversity: reward independently verified intelligence
    fact_keys = {e.fact_key for e in profile.evidence}
    has_news = "company_news" in fact_keys
    has_jobs = "job_posting" in fact_keys
    has_diverse = any(k in fact_keys for k in ["products_services", "business_activity", "sustainability", "technology_innovation", "project"])
    diversity_bonus = 0.05 if (has_news or has_jobs or has_diverse) else 0.0

    if profile.evidence:
        avg_ev_confidence = sum(e.confidence for e in profile.evidence) / len(profile.evidence)
    else:
        avg_ev_confidence = 0.0

    score = 0.5 * field_score + 0.45 * avg_ev_confidence + diversity_bonus
    return min(1.0, round(score, 3))


def _dedupe_sources(sources: List[str]) -> List[str]:
    seen = set()
    out = []
    for s in sources:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _ev_fingerprint(ev: EvidenceItem) -> tuple:
    val = ev.fact_value
    if isinstance(val, dict):
        val_repr = str(val.get("headline") or val.get("title") or val)[:60]
    else:
        val_repr = str(val)[:60]
    return (ev.fact_key, ev.source_url, val_repr)


def _clean_and_dedupe_evidence(evidence_list: List[EvidenceItem]) -> List[EvidenceItem]:
    """
    Audit evidence trail to guarantee:
      1. No citation violations (no proff.no for published facts, no search engines).
      2. No duplicate claims.
      3. Valid confidence scores in [0.0, 1.0].
      4. Valid methods in OUTPUT_CONTRACT.
    """
    VALID_METHODS = {"brreg_api", "regnskaps_api", "serp_search", "webpage_scrape", "llm_extract"}
    BLOCKED_SEARCH_LABELS = {
        "google", "bing", "yahoo", "duckduckgo", "brave", "ecosia", "baidu", "yandex"
    }
    cleaned = []
    seen = set()

    for ev in evidence_list:
        # Confidence validation
        conf = ev.confidence
        if conf is None or not isinstance(conf, (int, float)) or isinstance(conf, bool):
            continue
        if math.isnan(conf) or math.isinf(conf) or not (0.0 <= conf <= 1.0):
            continue

        # URL validation
        url = ev.source_url
        if not url or not isinstance(url, str):
            continue
        try:
            parsed = urlparse(url.strip())
        except Exception:
            continue
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
            continue

        hostname = (parsed.hostname or "").lower()
        if not hostname:
            continue

        # Check search engine domain labels (matches exact labels like 'google', 'bing', 'brave' in hostname)
        host_labels = set(hostname.split("."))
        if host_labels & BLOCKED_SEARCH_LABELS:
            continue

        # Check Proff rules (only proff.no and its subdomains)
        is_proff = hostname == "proff.no" or hostname.endswith(".proff.no")
        if is_proff and ev.fact_key not in ("employees", "_proff_raw"):
            continue
        if ev.method not in VALID_METHODS:
            continue
        if ev.fact_value is None or ev.fact_value == "":
            continue

        fp = _ev_fingerprint(ev)
        if fp not in seen:
            seen.add(fp)
            cleaned.append(ev)

    return cleaned


async def run_pipeline(org_number: str, force_refresh: bool = False) -> AgentRunResult:
    """
    Main agent pipeline. Returns an AgentRunResult with the full CompanyProfile.
    Guarantees:
      - Always returns a valid AgentRunResult matching OUTPUT_CONTRACT.md.
      - Never raises uncaught exceptions.
      - If org number is invalid or not in Brreg, returns success=False and profile=None.
      - Never fabricates evidence or guesses missing fields.
      - Proff.no is strictly used for website discovery; never as a published fact source.
      - Gathers dedicated verified news and job postings.
      - Employs strict identity cross-checking to prevent false matches.
    """
    start_time = time.time()
    steps: List[str] = []

    # â”€â”€ Input validation â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    org_str = str(org_number).strip().replace(" ", "")
    if not (org_str.isdigit() and len(org_str) == 9):
        steps.append("input_validation_failed")
        return AgentRunResult(
            organization_number=str(org_number),
            success=False,
            profile=None,
            error=f"Invalid organization number '{org_number}': must be exactly 9 digits",
            duration_seconds=round(time.time() - start_time, 2),
            steps_executed=steps,
        )

    org_number = org_str
    all_evidence: List[EvidenceItem] = []
    merged_data: dict = {"organization_number": org_number}
    sources_used: List[str] = []

    try:
        # â”€â”€ STEP 0: Check cache â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        existing = load_profile_any_age(org_number)
        if not force_refresh:
            fresh_existing = load_profile(org_number)
            if fresh_existing:
                steps.append("cache_hit")
                return AgentRunResult(
                    organization_number=org_number,
                    success=True,
                    profile=fresh_existing,
                    duration_seconds=round(time.time() - start_time, 2),
                    steps_executed=steps,
                )

        # â”€â”€ STEP 1: Brreg â€” Authoritative legal identity â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        steps.append("brreg_entity")
        increment_request("brreg_http")
        brreg_result = await fetch_brreg_entity(org_number)

        if "error" in brreg_result and "company_name" not in brreg_result:
            return AgentRunResult(
                organization_number=org_number,
                success=False,
                profile=None,
                error=f"Company not found in Brreg: {brreg_result.get('error')}",
                duration_seconds=round(time.time() - start_time, 2),
                steps_executed=steps,
            )

        brreg_evidence: List[EvidenceItem] = brreg_result.pop("evidence", [])
        all_evidence.extend(brreg_evidence)
        merged_data.update({k: v for k, v in brreg_result.items() if not k.startswith("_")})
        sources_used.append("BrÃ¸nnÃ¸ysundregistrene (brreg.no)")

        company_name = merged_data.get("company_name", "")

        # â”€â”€ STEP 2: Brreg â€” Management roles & governance â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        steps.append("brreg_roles")
        increment_request("brreg_http")
        roles_result = await fetch_brreg_roles(org_number)
        roles_evidence: List[EvidenceItem] = roles_result.pop("evidence", [])
        all_evidence.extend(roles_evidence)
        merged_data.update({k: v for k, v in roles_result.items() if not k.startswith("_")})

        # â”€â”€ STEP 3: Regnskapsregisteret â€” Financial statements â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        steps.append("regnskapsregisteret")
        increment_request("regnskap_http")
        regnskap_result = await fetch_regnskap(org_number)
        regnskap_evidence: List[EvidenceItem] = regnskap_result.pop("evidence", [])
        all_evidence.extend(regnskap_evidence)
        merged_data.update({k: v for k, v in regnskap_result.items() if not k.startswith("_")})
        if regnskap_evidence:
            sources_used.append("Regnskapsregisteret (brreg.no)")

        # â”€â”€ STEP 4: Proff.no â€” Website discovery & employee fallback â”€â”€â”€â”€â”€â”€â”€â”€â”€
        steps.append("proff_scrape")
        increment_request("proff_http")
        proff_result = await scrape_proff(org_number, company_name)
        proff_evidence: List[EvidenceItem] = proff_result.pop("evidence", [])
        proff_website_hint = proff_result.pop("_proff_website", None)
        if proff_website_hint:
            steps.append("proff_website_discovered")

        if merged_data.get("employees") is None and "employees" in proff_result:
            merged_data["employees"] = proff_result["employees"]
            for ev in proff_evidence:
                if ev.fact_key == "employees":
                    all_evidence.append(ev)
                    sources_used.append("Proff.no")

        # â”€â”€ STEP 5: Official Website Discovery & Verification â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        contact_data = merged_data.get("contact")
        nested_website = contact_data.get("website") if isinstance(contact_data, dict) else None
        cached_website = existing.website if existing is not None else None
        known_website = merged_data.get("website") or nested_website or cached_website
        steps.append("website_discovery")
        increment_request("website_http")
        web_result = await discover_company_website(org_number, company_name, known_website, proff_website_hint)
        web_evidence: List[EvidenceItem] = web_result.pop("evidence", [])
        website_text = web_result.pop("_website_text", None)
        discovered_website = web_result.get("website")

        if discovered_website and not merged_data.get("website"):
            merged_data["website"] = discovered_website
            if not any(e.fact_key == "website" for e in all_evidence):
                all_evidence.extend(web_evidence)

        target_website = discovered_website or known_website

        # â”€â”€ STEP 6: PHASE 3 â€” Dedicated Company News Pipeline â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if target_website:
            steps.append("company_news_pipeline")
            try:
                news_items = await extract_company_news(target_website, company_name, org_number)
                for item in news_items:
                    all_evidence.append(EvidenceItem(
                        fact_key="company_news",
                        fact_value={
                            "headline": item["headline"],
                            "summary": item["summary"],
                            "published_date": item["published_date"],
                            "url": item["url"],
                            "company": company_name,
                        },
                        source_name=item.get("source_name", f"{company_name} Official News"),
                        source_url=item["url"],
                        date_checked=TODAY,
                        confidence=0.92,
                        method="webpage_scrape",
                    ))
                    sources_used.append(f"{item.get('source_name', 'Company News')} ({item['url']})")
                if news_items:
                    steps.append("news_extracted")
            except Exception:
                pass

        # â”€â”€ STEP 7: PHASE 4 â€” Dedicated Job Posting Pipeline â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if target_website:
            steps.append("job_posting_pipeline")
            try:
                jobs = await extract_company_jobs(target_website, company_name, org_number)
                for job in jobs:
                    all_evidence.append(EvidenceItem(
                        fact_key="job_posting",
                        fact_value={
                            "title": job["title"],
                            "location": job["location"],
                            "employment_type": job.get("employment_type"),  # None if not determinable
                            "published_date": job.get("published_date") or job.get("posted_date"),
                            "deadline": job.get("deadline"),  # None if not determinable
                            "url": job["url"],
                            "company": company_name,
                        },
                        source_name=job.get("source_name", f"{company_name} Careers"),
                        source_url=job["url"],
                        date_checked=TODAY,
                        confidence=0.90,
                        method="webpage_scrape",
                    ))
                    sources_used.append(f"{job.get('source_name', 'Careers')} ({job['url']})")
                if jobs:
                    steps.append("jobs_extracted")
            except Exception:
                pass

        # â”€â”€ STEP 8: PHASE 2 â€” Diverse Company Intelligence Facts â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if target_website:
            steps.append("diverse_facts_pipeline")
            try:
                diverse_facts = await extract_company_intelligence_facts(target_website, company_name, org_number)
                for df in diverse_facts:
                    all_evidence.append(EvidenceItem(
                        fact_key=df["fact_key"],
                        fact_value=df["fact_value"],
                        source_name=df["source_name"],
                        source_url=df["source_url"],
                        date_checked=TODAY,
                        confidence=df.get("confidence", 0.90),
                        method="webpage_scrape",
                    ))
                    sources_used.append(f"{df['source_name']} ({df['source_url']})")
                if diverse_facts:
                    steps.append("diverse_facts_extracted")
            except Exception:
                pass

        # â”€â”€ STEP 9: Optional LLM Extraction & Verification â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if website_text and target_website and HAS_OPENAI:
            increment_request("openai_llm")
            web_verification = await verify_company_match(website_text, org_number, company_name, target_website)
            if web_verification.get("is_match", False) and web_verification.get("confidence", 0) >= 0.8:
                steps.append("website_verified")
                increment_request("openai_llm")
                web_extracted = await extract_facts_from_text(
                    website_text, company_name, org_number,
                    source_url=target_website,
                    source_name=f"{company_name} Official Website",
                )
                web_extracted_ev: List[EvidenceItem] = web_extracted.pop("evidence", [])
                for field, value in web_extracted.items():
                    if not field.startswith("_") and merged_data.get(field) is None:
                        merged_data[field] = value
                all_evidence.extend(web_extracted_ev)
                sources_used.append(f"Company website ({target_website})")
        elif website_text and target_website:
            steps.append("website_scraped")
            sources_used.append(f"Company website ({target_website})")

        # â”€â”€ STEP 10: Generate description if still missing â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if not merged_data.get("description"):
            # Use verified business_activity from official website as factual description
            for ev in all_evidence:
                if ev.fact_key == "business_activity" and ev.fact_value:
                    desc_val = str(ev.fact_value).strip()
                    if len(desc_val) > 300:
                        desc_val = desc_val[:297] + "..."
                    merged_data["description"] = desc_val
                    break

        if HAS_OPENAI and not merged_data.get("description"):
            steps.append("generate_description")
            increment_request("openai_llm")
            desc = await generate_description(merged_data)
            if desc:
                merged_data["description"] = desc

        # â”€â”€ STEP 11: Deduplicate & Clean Evidence â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        steps.append("build_profile")
        cleaned_evidence = _clean_and_dedupe_evidence(all_evidence)

        profile_fields = {
            k: v for k, v in merged_data.items()
            if not k.startswith("_") and k != "evidence"
        }
        profile_fields["evidence"] = cleaned_evidence
        profile_fields["sources_used"] = _dedupe_sources(sources_used)

        profile = CompanyProfile(**profile_fields)
        profile.meta.enrichment_done = True
        profile.confidence_score = _compute_confidence(profile)

        # â”€â”€ STEP 12: Merge with existing or save fresh â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        if existing:
            profile = merge_profile(existing, profile.model_dump(), cleaned_evidence)
            steps.append("profile_merged")
        else:
            profile = filter_expired_jobs(profile)
            steps.append("profile_created")

        save_profile(profile)

        return AgentRunResult(
            organization_number=org_number,
            success=True,
            profile=profile,
            duration_seconds=round(time.time() - start_time, 2),
            steps_executed=steps,
        )

    except Exception as e:
        steps.append("unhandled_pipeline_error")
        return AgentRunResult(
            organization_number=org_number,
            success=False,
            profile=None,
            error=f"Pipeline error for org {org_number}: {str(e)}",
            duration_seconds=round(time.time() - start_time, 2),
            steps_executed=steps,
        )


async def run_batch(
    org_numbers: List[str],
    concurrency: int = 5,
    force_refresh: bool = False,
) -> List[AgentRunResult]:
    """
    Run the pipeline for multiple companies with controlled concurrency.
    Guarantees:
      - Processing error on one company does NOT terminate the batch.
      - Returns a valid failure AgentRunResult for any failing company.
      - Continues processing all remaining companies.
      - Preserves input order in the returned results.
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def _run_one(org: str) -> AgentRunResult:
        async with semaphore:
            try:
                return await run_pipeline(org, force_refresh=force_refresh)
            except Exception as e:
                return AgentRunResult(
                    organization_number=str(org),
                    success=False,
                    profile=None,
                    error=f"Batch execution exception for {org}: {str(e)}",
                    duration_seconds=0.0,
                    steps_executed=["batch_exception"],
                )

    tasks = [_run_one(org) for org in org_numbers]
    return await asyncio.gather(*tasks)

