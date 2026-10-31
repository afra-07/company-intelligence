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
from llm_extractor import _PROVIDER as _LLM_PROVIDER

# LLM steps run when any supported provider key (OpenAI / Anthropic / Gemini) is available.
HAS_OPENAI = _LLM_PROVIDER is not None  # name kept for backwards-compat


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
    Guarantees:
      - Always returns a valid AgentRunResult matching OUTPUT_CONTRACT.md.
      - Never raises uncaught exceptions.
      - If org number is invalid or not in Brreg, returns success=False and profile=None.
      - Never fabricates evidence or guesses missing fields.
      - Proff.no is strictly used for website discovery; never as a published fact source.
    """
    start_time = time.time()
    steps: List[str] = []

    # ── Input validation ──────────────────────────────────────────────────────
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
        # ── STEP 0: Check cache ───────────────────────────────────────────────
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

        # ── STEP 1: Brreg — Authoritative identity ────────────────────────────
        steps.append("brreg_entity")
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
        sources_used.append("Brønnøysundregistrene (brreg.no)")

        company_name = merged_data.get("company_name", "")

        # ── STEP 2: Brreg — Management roles ─────────────────────────────────
        steps.append("brreg_roles")
        increment_request("brreg_http")
        roles_result = await fetch_brreg_roles(org_number)
        roles_evidence: List[EvidenceItem] = roles_result.pop("evidence", [])
        all_evidence.extend(roles_evidence)
        merged_data.update({k: v for k, v in roles_result.items() if not k.startswith("_")})

        # ── STEP 3: Regnskapsregisteret — Financials ──────────────────────────
        steps.append("regnskapsregisteret")
        regnskap_result = await fetch_regnskap(org_number)
        regnskap_evidence: List[EvidenceItem] = regnskap_result.pop("evidence", [])
        all_evidence.extend(regnskap_evidence)
        merged_data.update({k: v for k, v in regnskap_result.items() if not k.startswith("_")})
        if regnskap_evidence:
            sources_used.append("Regnskapsregisteret (brreg.no)")

        # ── STEP 4: Proff.no — Website discovery & employee fallback ─────────
        # Citation rule: Proff.no is permitted ONLY for employees and internal _proff_raw keys.
        # Never attribute Proff-extracted facts to a company's website.
        steps.append("proff_scrape")
        increment_request("proff_http")
        proff_result = await scrape_proff(org_number, company_name)
        proff_evidence: List[EvidenceItem] = proff_result.pop("evidence", [])
        proff_website_hint = proff_result.pop("_proff_website", None)
        if proff_website_hint:
            steps.append("proff_website_discovered")

        # Fallback to Proff employee count only if Brreg didn't already supply employees
        if merged_data.get("employees") is None and "employees" in proff_result:
            merged_data["employees"] = proff_result["employees"]
            for ev in proff_evidence:
                if ev.fact_key == "employees":
                    all_evidence.append(ev)
                    sources_used.append("Proff.no")

        # ── STEP 5: Company website discovery + scrape ────────────────────────
        known_website = merged_data.get("website")
        steps.append("website_discovery")
        increment_request("serpapi_http")
        web_result = await discover_company_website(org_number, company_name, known_website, proff_website_hint)
        web_evidence: List[EvidenceItem] = web_result.pop("evidence", [])
        website_text = web_result.pop("_website_text", None)
        discovered_website = web_result.get("website")

        if discovered_website and not merged_data.get("website"):
            merged_data["website"] = discovered_website
            # Add website evidence only if not already provided by Brreg
            if not any(e.fact_key == "website" for e in all_evidence):
                all_evidence.extend(web_evidence)

        target_website = discovered_website or known_website
        if website_text and target_website and HAS_OPENAI:
            # Verify this is really the company's website
            increment_request("openai_llm")
            web_verification = await verify_company_match(website_text, org_number, company_name)
            if web_verification.get("is_match", False) and web_verification.get("confidence", 0) > 0.4:
                steps.append("website_verified")
                # Extract facts directly from the actual company website
                increment_request("openai_llm")
                web_extracted = await extract_facts_from_text(
                    website_text, company_name, org_number,
                    source_url=target_website,
                    source_name="Company website",
                )
                web_extracted_ev: List[EvidenceItem] = web_extracted.pop("evidence", [])
                for field, value in web_extracted.items():
                    if not field.startswith("_") and merged_data.get(field) is None:
                        merged_data[field] = value
                all_evidence.extend(web_extracted_ev)
                sources_used.append(f"Company website ({target_website})")
        elif website_text and target_website:
            steps.append("website_no_llm_extract")
            sources_used.append(f"Company website ({target_website})")

        # ── STEP 6: News search (only if CEO or description still missing) ────
        if HAS_OPENAI and (not merged_data.get("ceo") or not merged_data.get("description")):
            steps.append("news_search")
            increment_request("serpapi_http")
            articles = await search_company_news(company_name, org_number)

            for art in articles:
                art_url = art.get("link")
                art_title = art.get("title") or "News article"
                snippet = art.get("snippet")
                if not art_url or not snippet:
                    continue

                increment_request("openai_llm")
                news_extracted = await extract_facts_from_text(
                    snippet, company_name, org_number,
                    source_url=art_url,
                    source_name=art_title,
                )
                news_extracted_ev: List[EvidenceItem] = news_extracted.pop("evidence", [])
                added_fields = 0
                for field, value in news_extracted.items():
                    if not field.startswith("_") and merged_data.get(field) is None:
                        merged_data[field] = value
                        added_fields += 1

                if added_fields > 0 and news_extracted_ev:
                    for ev in news_extracted_ev:
                        if merged_data.get(ev.fact_key) == ev.fact_value:
                            all_evidence.append(ev)
                    sources_used.append(f"{art_title} ({art_url})")

                if merged_data.get("ceo") and merged_data.get("description"):
                    break

        # ── STEP 7: Generate description if still missing ─────────────────────
        if HAS_OPENAI and not merged_data.get("description"):
            steps.append("generate_description")
            increment_request("openai_llm")
            desc = await generate_description(merged_data)
            if desc:
                merged_data["description"] = desc

        # ── STEP 8: Build final profile ───────────────────────────────────────
        steps.append("build_profile")

        profile_fields = {
            k: v for k, v in merged_data.items()
            if not k.startswith("_") and k != "evidence"
        }
        profile_fields["evidence"] = all_evidence
        profile_fields["sources_used"] = _dedupe_sources(sources_used)

        profile = CompanyProfile(**profile_fields)
        profile.confidence_score = _compute_confidence(profile)

        # ── STEP 9: Merge with existing or save fresh ─────────────────────────
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
