"""
Signalpost Company Intelligence Agent
=====================================
LLM Extractor — Uses OpenAI to parse unstructured text into structured facts.
Also handles company identity verification (the critical 30-point matching step).
"""

from __future__ import annotations
import json
import os
from datetime import date
from typing import Optional, Dict, Any, List
from openai import AsyncOpenAI
from models import EvidenceItem, CompanyProfile

client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
TODAY = date.today().isoformat()
MODEL = "gpt-4o-mini"


# ─── System prompts ───────────────────────────────────────────────────────────

EXTRACTION_SYSTEM = """You are a data extraction specialist for Norwegian company intelligence.
Given raw text from websites, news articles, or company pages, extract structured company facts.
ONLY extract facts that are clearly stated in the text. Do NOT invent or guess.
Return valid JSON only."""

VERIFICATION_SYSTEM = """You are a company identity verification specialist.
Your job is to confirm whether a piece of information belongs to a SPECIFIC company.
You must be strict: if the org number or full company name doesn't match, say so.
Return valid JSON only."""

DESCRIPTION_SYSTEM = """You are a business analyst. Given company data, write a concise 2-3 sentence
description of what the company does, based only on the facts provided. Write in English."""


# ─── Fact extraction from raw text ───────────────────────────────────────────

async def extract_facts_from_text(
    text: str,
    company_name: str,
    org_number: str,
    source_url: str,
    source_name: str,
) -> Dict[str, Any]:
    """
    Use LLM to extract structured facts from raw webpage/article text.
    Returns a dict of field->value plus evidence items.
    """
    prompt = f"""Extract company facts for: {company_name} (org: {org_number})

TEXT:
{text[:4000]}

Return JSON with these fields (omit fields not found):
{{
  "ceo": "full name or null",
  "employees": number or null,
  "revenue": number in NOK or null,
  "description": "what the company does" or null,
  "website": "URL" or null,
  "phone": "phone number" or null,
  "email": "email" or null,
  "board_members": ["name (role)", ...] or null
}}"""

    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=800,
        )
        extracted = json.loads(response.choices[0].message.content)
    except Exception as e:
        return {"evidence": [], "error": str(e)}

    evidence: list[EvidenceItem] = []
    result: Dict[str, Any] = {}

    for field, value in extracted.items():
        if value is None:
            continue
        result[field] = value
        evidence.append(EvidenceItem(
            fact_key=field,
            fact_value=value,
            source_name=source_name,
            source_url=source_url,
            date_checked=TODAY,
            confidence=0.7,  # LLM extraction is less certain than direct API
            method="llm_extract",
        ))

    result["evidence"] = evidence
    return result


# ─── Identity verification ────────────────────────────────────────────────────

async def verify_company_match(
    candidate_text: str,
    expected_org_number: str,
    expected_name: str,
) -> Dict[str, Any]:
    """
    CRITICAL: Verify that a webpage/source actually refers to the target company.
    This is the 30-point Company Matching component.
    Returns {is_match: bool, confidence: float, reason: str}
    """
    prompt = f"""We are looking for information about:
- Company name: {expected_name}
- Organization number: {expected_org_number}

Does this text refer to THAT SPECIFIC company? Check:
1. Does the org number appear? (strongest signal)
2. Is the full company name an exact match?
3. Is there any evidence this could be a different company with a similar name?

TEXT:
{candidate_text[:2000]}

Return JSON:
{{
  "is_match": true/false,
  "confidence": 0.0-1.0,
  "org_number_found": true/false,
  "name_matches": true/false,
  "reason": "brief explanation"
}}"""

    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": VERIFICATION_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=300,
        )
        result = json.loads(response.choices[0].message.content)
        return result
    except Exception:
        # Default to uncertain if LLM fails
        return {"is_match": True, "confidence": 0.5, "reason": "LLM verification unavailable"}


# ─── Company description generation ──────────────────────────────────────────

async def generate_description(profile_data: Dict[str, Any]) -> Optional[str]:
    """
    Generate a clean company description from available profile facts.
    """
    summary = json.dumps({
        k: v for k, v in profile_data.items()
        if k not in ("evidence", "_raw", "_website_text", "_proff_text")
        and v is not None
    }, ensure_ascii=False, indent=2)

    prompt = f"""Based on this Norwegian company data, write a concise 2-3 sentence description 
of what the company does. Focus on industry, size, and key activities. Write in English.

Company data:
{summary[:2000]}"""

    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": DESCRIPTION_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=200,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return None


# ─── Conflict resolution ──────────────────────────────────────────────────────

async def resolve_conflicting_values(
    field: str,
    values: List[Dict[str, Any]],
    company_name: str,
) -> Dict[str, Any]:
    """
    When multiple sources disagree on a value (e.g. different employee counts),
    use LLM to pick the most credible one and explain why.
    Returns {chosen_value, confidence, reason}
    """
    if len(values) == 1:
        return {"chosen_value": values[0]["value"], "confidence": values[0]["confidence"], "reason": "Only one source"}

    items_str = "\n".join([
        f"- Source: {v['source']} | Value: {v['value']} | Confidence: {v['confidence']}"
        for v in values
    ])

    prompt = f"""For company {company_name}, multiple sources report different values for '{field}':

{items_str}

Which value is most credible? Consider: official registries > government sources > news > scraped web.
Return JSON: {{"chosen_value": ..., "confidence": 0.0-1.0, "reason": "..."}}"""

    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "You are a data quality analyst. Return valid JSON only."},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0,
            max_tokens=200,
        )
        return json.loads(response.choices[0].message.content)
    except Exception:
        # Fall back to highest confidence
        best = max(values, key=lambda v: v["confidence"])
        return {"chosen_value": best["value"], "confidence": best["confidence"], "reason": "Fallback to highest confidence source"}
