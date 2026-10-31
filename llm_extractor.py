"""
Signalpost Company Intelligence Agent
=====================================
LLM Extractor — Parses unstructured text into structured facts.
Also handles company identity verification (the critical 30-point matching step).

Provider selection (first key found wins):
  1. OPENAI_API_KEY   → OpenAI  gpt-4o-mini
  2. ANTHROPIC_API_KEY → Anthropic claude-3-haiku-20240307
  3. GEMINI_API_KEY   → Google  gemini-1.5-flash

If none of the above keys are set, all LLM functions return graceful
no-op results so the pipeline continues in fully-keyless mode.
"""

from __future__ import annotations
import json
import os
from datetime import date
from typing import Optional, Dict, Any, List

TODAY = date.today().isoformat()

# ─── Provider detection ───────────────────────────────────────────────────────

_OPENAI_KEY    = os.getenv("OPENAI_API_KEY", "")
_ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "")
_GEMINI_KEY    = os.getenv("GEMINI_API_KEY", "")

def _key_valid(k: str) -> bool:
    return bool(k and not k.startswith("your_") and len(k) > 20)

if _key_valid(_OPENAI_KEY):
    _PROVIDER = "openai"
elif _key_valid(_ANTHROPIC_KEY):
    _PROVIDER = "anthropic"
elif _key_valid(_GEMINI_KEY):
    _PROVIDER = "gemini"
else:
    _PROVIDER = None  # Fully keyless — all LLM calls are no-ops

# Lazy-import only the needed client
_openai_client    = None
_anthropic_client = None
_genai_client     = None

if _PROVIDER == "openai":
    from openai import AsyncOpenAI
    _openai_client = AsyncOpenAI(api_key=_OPENAI_KEY)

elif _PROVIDER == "anthropic":
    import anthropic as _ant_module
    _anthropic_client = _ant_module.AsyncAnthropic(api_key=_ANTHROPIC_KEY)

elif _PROVIDER == "gemini":
    import google.generativeai as genai  # type: ignore
    genai.configure(api_key=_GEMINI_KEY)
    _genai_client = genai.GenerativeModel("gemini-1.5-flash")

from models import EvidenceItem, CompanyProfile


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


# ─── Provider-agnostic completion ────────────────────────────────────────────

async def _complete(system: str, user: str, json_mode: bool = True, max_tokens: int = 800) -> Optional[str]:
    """
    Send a chat completion to whichever provider is available.
    Returns the raw text of the model's reply, or None if no provider is set.
    """
    if _PROVIDER is None:
        return None

    if _PROVIDER == "openai":
        assert _openai_client is not None
        kwargs: Dict[str, Any] = dict(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            temperature=0,
            max_tokens=max_tokens,
        )
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = await _openai_client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content

    if _PROVIDER == "anthropic":
        assert _anthropic_client is not None
        resp = await _anthropic_client.messages.create(
            model="claude-3-haiku-20240307",
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return resp.content[0].text  # type: ignore[attr-defined]

    if _PROVIDER == "gemini":
        assert _genai_client is not None
        combined = f"{system}\n\n{user}"
        resp = await _genai_client.generate_content_async(combined)
        return resp.text

    return None


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
    Returns empty dict (no-op) when no LLM provider is available.
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
        raw = await _complete(EXTRACTION_SYSTEM, prompt, json_mode=True, max_tokens=800)
        if raw is None:
            return {"evidence": []}
        # Gemini/Anthropic may wrap JSON in markdown fences — strip them
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        extracted = json.loads(raw)
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
    Returns a permissive default when no LLM provider is available.
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
        raw = await _complete(VERIFICATION_SYSTEM, prompt, json_mode=True, max_tokens=300)
        if raw is None:
            # No LLM available — default to uncertain
            return {"is_match": True, "confidence": 0.5, "reason": "LLM verification unavailable"}
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(raw)
    except Exception:
        return {"is_match": True, "confidence": 0.5, "reason": "LLM verification unavailable"}


# ─── Company description generation ──────────────────────────────────────────

async def generate_description(profile_data: Dict[str, Any]) -> Optional[str]:
    """
    Generate a clean company description from available profile facts.
    Returns None when no LLM provider is available.
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
        raw = await _complete(DESCRIPTION_SYSTEM, prompt, json_mode=False, max_tokens=200)
        if raw is None:
            return None
        return raw.strip()
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
    Falls back to highest-confidence source when no LLM provider is available.
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
        raw = await _complete(
            "You are a data quality analyst. Return valid JSON only.",
            prompt,
            json_mode=True,
            max_tokens=200,
        )
        if raw is None:
            raise ValueError("no provider")
        raw = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(raw)
    except Exception:
        # Fall back to highest confidence
        best = max(values, key=lambda v: v["confidence"])
        return {"chosen_value": best["value"], "confidence": best["confidence"], "reason": "Fallback to highest confidence source"}
