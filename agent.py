"""
Signalpost Company Intelligence Agent
=====================================
The main orchestration pipeline.
Coordinates all sources, handles matching, and builds the final profile.

Pipeline:
  org_number
    → Brreg entity fetch   (authoritative identity)
    → Brreg roles fetch    (CEO, board)
    → Regnskapsregisteret  (financials)
    → Proff.no scrape      (enrichment)
    → Web search + scrape  (supplemental)
    → LLM extraction       (unstructured → structured)
    → Company verification (matching guard)
    → Description gen      (polish)
    → Profile merge/save   (update logic)
    → CompanyProfile
"""

from __future__ import annotations
import asyncio
import time
from datetime import datetime
from typing import List, Optional
from models import CompanyProfile, EvidenceItem, AgentRunResult
from brreg_fetcher import fetch_brreg_entity, fetch_brreg_roles, fetch_regnskap
from web_searcher import scrape_proff, discover_company_website, search_company_news
from llm_extractor import (
    extract_facts_from_text,
    verify_company_match,
    generate_description,
    resolve_conflicting_values,
)
from profile_store import load_profile, load_profile_any_age, save_profile, merge_profile, increment_request
import os

# Skip LLM steps gracefully when no valid OpenAI key is configured
_raw_key = os.getenv("OPENAI_API_KEY", "")
HAS_OPENAI = bool(_raw_key and not _raw_key.startswith("your_") and len(_raw_key) > 20)


def _compute_confidence(profile: CompanyProfile) -> float:
    """
    Compute overall profile confidence score based on:
    - Number of fields populated
    - Confidence of supporting evidence
    """
    key_fields = [
        "company_name", "status", "industry_description", "address",
        "employees", "revenue", "ceo", "website", "description",
    ]
    filled = sum(1 for f in key_fields if getattr(profile, f, None) is not None)
    field_score = filled / len(key_fields)

    if profile.evidence:
        avg_ev_confidence = sum(e.confidence for e in profile.evidence) / len(profile.evidence)
    else:
        avg_ev_confidence = 0.0

    return round(0.6 * field_score + 0.4 * avg_ev_confidence, 3)


def _dedupe_sources(sources: List[str]) -> List[str]:
    seen = set()
    out = []
    for s in sources:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


async def run_pipeline(org_number: str, force_refresh: bool = False) -> AgentRunResult:
    """
    Main agent pipeline. Returns an AgentRunResult with the full CompanyProfile.
    """
    start_time = time.time()
    steps: List[str] = []
    all_evidence: List[EvidenceItem] = []
    merged_data: dict = {"organization_number": org_number}
    sources_used: List[str] = []

    # ── STEP 0: Check cache ───────────────────────────────────────────────────
    existing = load_profile(org_number)
    if existing and not force_refresh:
        steps.append("cache_hit")
        return AgentRunResult(
            organization_number=org_number,
            success=True,
            profile=existing,
            duration_seconds=round(time.time() - start_time, 2),
            steps_executed=steps,
        )

    # ── STEP 1: Brreg — Authoritative identity ────────────────────────────────
    steps.append("brreg_entity")
    brreg_result = await fetch_brreg_entity(org_number)

    if "error" in brreg_result and "company_name" not in brreg_result:
        return AgentRunResult(
            organization_number=org_number,
            success=False,
            error=f"Company not found in Brreg: {brreg_result.get('error')}",
            duration_seconds=round(time.time() - start_time, 2),
            steps_executed=steps,
        )

    brreg_evidence: List[EvidenceItem] = brreg_result.pop("evidence", [])
    all_evidence.extend(brreg_evidence)
    merged_data.update({k: v for k, v in brreg_result.items() if not k.startswith("_")})
    sources_used.append("Brønnøysundregistrene (brreg.no)")

    company_name = merged_data.get("company_name", "")

    # ── STEP 2: Brreg — Management roles ─────────────────────────────────────
    steps.append("brreg_roles")
    increment_request("brreg_http")
    roles_result = await fetch_brreg_roles(org_number)
    roles_evidence: List[EvidenceItem] = roles_result.pop("evidence", [])
    all_evidence.extend(roles_evidence)
    merged_data.update({k: v for k, v in roles_result.items() if not k.startswith("_")})

    # ── STEP 3: Regnskapsregisteret — Financials ──────────────────────────────
    steps.append("regnskapsregisteret")
    regnskap_result = await fetch_regnskap(org_number)
    regnskap_evidence: List[EvidenceItem] = regnskap_result.pop("evidence", [])
    all_evidence.extend(regnskap_evidence)
    merged_data.update({k: v for k, v in regnskap_result.items() if not k.startswith("_")})
    if regnskap_evidence:
        sources_used.append("Regnskapsregisteret (brreg.no)")

    # ── STEP 4: Proff.no — Enrichment ────────────────────────────────────────
    steps.append("proff_scrape")
    increment_request("proff_http")  # 1 req (+ 1 fallback if slug misses, counted in scrape_proff)
    proff_result = await scrape_proff(org_number, company_name)
    proff_evidence: List[EvidenceItem] = proff_result.pop("evidence", [])
    proff_text = proff_result.pop("_proff_text", None)

    # Company matching guard for Proff.no
    if proff_text and HAS_OPENAI:
        increment_request("openai_llm")  # verify_company_match
        verification = await verify_company_match(proff_text, org_number, company_name)
        if verification.get("is_match", False) and verification.get("confidence", 0) > 0.5:
            steps.append("proff_verified")
            all_evidence.extend(proff_evidence)
            # LLM extract from Proff text
            increment_request("openai_llm")  # extract_facts_from_text
            proff_extracted = await extract_facts_from_text(
                proff_text, company_name, org_number,
                source_url=f"https://www.proff.no/firmaer/{org_number}/",
                source_name="Proff.no",
            )
            proff_extracted_ev: List[EvidenceItem] = proff_extracted.pop("evidence", [])
            all_evidence.extend(proff_extracted_ev)
            merged_data.update({k: v for k, v in proff_extracted.items() if not k.startswith("_")})
            sources_used.append("Proff.no")
        else:
            steps.append("proff_rejected_mismatch")
    elif proff_text:
        # No OpenAI key — accept Proff data without LLM verification
        steps.append("proff_no_llm_verify")
        all_evidence.extend(proff_evidence)

    # ── STEP 5: Company website discovery + scrape ────────────────────────────
    known_website = merged_data.get("website")
    steps.append("website_discovery")
    increment_request("serpapi_http")  # discover_company_website (0 if already known, 1 if SerpAPI used)
    web_result = await discover_company_website(org_number, company_name, known_website)
    web_evidence: List[EvidenceItem] = web_result.pop("evidence", [])
    website_text = web_result.pop("_website_text", None)
    discovered_website = web_result.get("website")

    if discovered_website and not merged_data.get("website"):
        merged_data["website"] = discovered_website

    if website_text and HAS_OPENAI:
        # Verify this is really the company's website
        increment_request("openai_llm")  # verify_company_match
        web_verification = await verify_company_match(website_text, org_number, company_name)
        if web_verification.get("is_match", False) and web_verification.get("confidence", 0) > 0.4:
            steps.append("website_verified")
            all_evidence.extend(web_evidence)
            # LLM extract from website
            increment_request("openai_llm")  # extract_facts_from_text
            web_extracted = await extract_facts_from_text(
                website_text, company_name, org_number,
                source_url=discovered_website or known_website or "",
                source_name="Company website",
            )
            web_extracted_ev: List[EvidenceItem] = web_extracted.pop("evidence", [])
            all_evidence.extend(web_extracted_ev)
            # Only update fields not already filled by authoritative sources
            for field, value in web_extracted.items():
                if not field.startswith("_") and merged_data.get(field) is None:
                    merged_data[field] = value
            if discovered_website or known_website:
                sources_used.append(f"Company website ({discovered_website or known_website})")
    elif website_text:
        # No OpenAI key — skip LLM extract but record the website was found
        steps.append("website_no_llm_extract")
        all_evidence.extend(web_evidence)
        if discovered_website or known_website:
            sources_used.append(f"Company website ({discovered_website or known_website})")

    # ── STEP 6: News search (only if CEO or description still missing) ────────
    if HAS_OPENAI and (not merged_data.get("ceo") or not merged_data.get("description")):
        steps.append("news_search")
        increment_request("serpapi_http")  # search_company_news
        news_result = await search_company_news(company_name, org_number)
        news_snippets = news_result.get("news_snippets", [])
        news_evidence: List[EvidenceItem] = news_result.get("evidence", [])

        if news_snippets:
            combined_news = "\n".join(news_snippets)
            increment_request("openai_llm")  # extract_facts_from_text (news)
            news_extracted = await extract_facts_from_text(
                combined_news, company_name, org_number,
                source_url="https://www.google.com",
                source_name="News / Web search",
            )
            news_extracted_ev: List[EvidenceItem] = news_extracted.pop("evidence", [])
            # Add evidence but verify snippets
            for field, value in news_extracted.items():
                if not field.startswith("_") and merged_data.get(field) is None:
                    merged_data[field] = value
                    all_evidence.extend(news_extracted_ev)
            sources_used.append("Web search / News")

    # ── STEP 7: Generate description if still missing ─────────────────────────
    if HAS_OPENAI and not merged_data.get("description"):
        steps.append("generate_description")
        increment_request("openai_llm")  # generate_description
        desc = await generate_description(merged_data)
        if desc:
            merged_data["description"] = desc

    # ── STEP 8: Build final profile ───────────────────────────────────────────
    steps.append("build_profile")

    profile_fields = {
        k: v for k, v in merged_data.items()
        if not k.startswith("_") and k != "evidence"
    }
    profile_fields["evidence"] = all_evidence
    profile_fields["sources_used"] = _dedupe_sources(sources_used)

    profile = CompanyProfile(**profile_fields)
    profile.confidence_score = _compute_confidence(profile)

    # ── STEP 9: Merge with existing or save fresh ─────────────────────────────
    if existing:
        profile = merge_profile(existing, profile.model_dump(), all_evidence)
        steps.append("profile_merged")
    else:
        steps.append("profile_created")

    save_profile(profile)

    return AgentRunResult(
        organization_number=org_number,
        success=True,
        profile=profile,
        duration_seconds=round(time.time() - start_time, 2),
        steps_executed=steps,
    )


async def run_batch(org_numbers: List[str], concurrency: int = 5) -> List[AgentRunResult]:
    """
    Run the pipeline for multiple companies with controlled concurrency.
    """
    semaphore = asyncio.Semaphore(concurrency)

    async def _run_one(org: str) -> AgentRunResult:
        async with semaphore:
            return await run_pipeline(org)

    tasks = [_run_one(org) for org in org_numbers]
    return await asyncio.gather(*tasks, return_exceptions=False)
