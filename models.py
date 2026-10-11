"""
Signalpost Company Intelligence Agent
=====================================
Core data models for company profiles and evidence.
"""

from __future__ import annotations
from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    fact_key: str = Field(..., description="The profile field this evidence supports")
    fact_value: Any = Field(..., description="The value found")
    source_name: str = Field(..., description="Human-readable source name")
    source_url: str = Field(..., description="URL of the source")
    date_checked: str = Field(..., description="ISO date when checked")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence 0-1")
    method: str = Field(..., description="How this was found: brreg_api / regnskaps_api / serp_search / webpage_scrape / llm_extract")


class CompanyProfile(BaseModel):
    # ── Core identity (from Brreg) ──────────────────────────────────────────
    organization_number: str
    company_name: Optional[str] = None
    legal_form: Optional[str] = None          # AS, ASA, ENK, etc.
    status: Optional[str] = None              # Active / Under liquidation / Bankrupt
    registered_date: Optional[str] = None

    # ── Contact & location ───────────────────────────────────────────────────
    address: Optional[str] = None
    postal_address: Optional[str] = None
    municipality: Optional[str] = None
    website: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

    # ── Business details ─────────────────────────────────────────────────────
    industry_code: Optional[str] = None
    industry_description: Optional[str] = None
    employees: Optional[int] = None
    sector: Optional[str] = None

    # ── Financial data (from Regnskapsregisteret) ────────────────────────────
    revenue: Optional[float] = None           # Operating revenue NOK
    operating_profit: Optional[float] = None
    net_income: Optional[float] = None
    total_assets: Optional[float] = None
    equity: Optional[float] = None
    financial_year: Optional[int] = None
    # 'found' | 'not_filed' | 'no_data' | None
    financials_status: Optional[str] = None

    # ── People & enrichment (from search + LLM) ──────────────────────────────
    ceo: Optional[str] = None
    board_members: Optional[List[str]] = None
    description: Optional[str] = None         # Short company description

    # ── Meta ─────────────────────────────────────────────────────────────────
    profile_created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    profile_updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    evidence: List[EvidenceItem] = Field(default_factory=list)
    confidence_score: float = Field(default=0.0, description="Overall profile confidence 0-1")
    sources_used: List[str] = Field(default_factory=list)


class AgentRunResult(BaseModel):
    organization_number: str
    success: bool
    profile: Optional[CompanyProfile] = None
    error: Optional[str] = None
    duration_seconds: float = 0.0
    steps_executed: List[str] = Field(default_factory=list)
