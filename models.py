"""
Signalpost Company Intelligence Agent
=====================================
Core data models for company profiles and evidence matching OUTPUT_CONTRACT.md.
Structured into six nested sections: identity, contact, business, financials, people, meta.
"""

from __future__ import annotations
from datetime import datetime
from typing import Optional, List, Any, Dict
from pydantic import BaseModel, Field, model_validator


class EvidenceItem(BaseModel):
    fact_key: str = Field(..., description="The profile field this evidence supports")
    fact_value: Any = Field(..., description="The value found")
    source_name: str = Field(..., description="Human-readable source name")
    source_url: str = Field(..., description="URL of the source")
    date_checked: str = Field(..., description="ISO date when checked")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence 0-1")
    method: str = Field(..., description="How this was found: brreg_api / regnskaps_api / serp_search / webpage_scrape / llm_extract")


class ProfileIdentity(BaseModel):
    organization_number: str
    company_name: str
    legal_form: Optional[str] = None          # AS, ASA, ENK, etc.
    status: str = "Active"                    # Active / Bankrupt / Under liquidation / Forced liquidation
    registered_date: Optional[str] = None


class ProfileContact(BaseModel):
    address: Optional[str] = None
    postal_address: Optional[str] = None
    municipality: Optional[str] = None
    website: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


class ProfileBusiness(BaseModel):
    industry_code: Optional[str] = None
    industry_description: Optional[str] = None
    employees: Optional[int] = None
    sector: Optional[str] = None


class ProfileFinancials(BaseModel):
    revenue: Optional[float] = None           # Operating revenue NOK
    operating_profit: Optional[float] = None
    net_income: Optional[float] = None
    total_assets: Optional[float] = None
    equity: Optional[float] = None
    financial_year: Optional[int] = None
    financials_status: Optional[str] = None   # 'found' | 'not_filed' | 'no_data' | None


class ProfilePeople(BaseModel):
    ceo: Optional[str] = None
    board_members: Optional[List[str]] = None
    description: Optional[str] = None         # Short company description


class ProfileMeta(BaseModel):
    profile_created_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    profile_updated_at: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    evidence: List[EvidenceItem] = Field(default_factory=list)
    confidence_score: float = Field(default=0.0, ge=0.0, le=1.0, description="Overall profile confidence 0-1")
    sources_used: List[str] = Field(default_factory=list)


class CompanyProfile(BaseModel):
    identity: ProfileIdentity
    contact: ProfileContact = Field(default_factory=ProfileContact)
    business: ProfileBusiness = Field(default_factory=ProfileBusiness)
    financials: ProfileFinancials = Field(default_factory=ProfileFinancials)
    people: ProfilePeople = Field(default_factory=ProfilePeople)
    meta: ProfileMeta = Field(default_factory=ProfileMeta)

    @model_validator(mode="before")
    @classmethod
    def _migrate_flat_to_nested(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        # If already nested with 'identity', validate as-is
        if "identity" in data and isinstance(data["identity"], (dict, ProfileIdentity)):
            return data

        identity_keys = {"organization_number", "company_name", "legal_form", "status", "registered_date"}
        contact_keys = {"address", "postal_address", "municipality", "website", "email", "phone"}
        business_keys = {"industry_code", "industry_description", "employees", "sector"}
        financials_keys = {"revenue", "operating_profit", "net_income", "total_assets", "equity", "financial_year", "financials_status"}
        people_keys = {"ceo", "board_members", "description"}
        meta_keys = {"profile_created_at", "profile_updated_at", "evidence", "confidence_score", "sources_used"}

        identity_dict = {k: data[k] for k in identity_keys if k in data and data[k] is not None}
        contact_dict = {k: data[k] for k in contact_keys if k in data and data[k] is not None}
        business_dict = {k: data[k] for k in business_keys if k in data and data[k] is not None}
        financials_dict = {k: data[k] for k in financials_keys if k in data and data[k] is not None}
        people_dict = {k: data[k] for k in people_keys if k in data and data[k] is not None}
        meta_dict = {k: data[k] for k in meta_keys if k in data and data[k] is not None}

        if "organization_number" not in identity_dict and "organization_number" in data:
            identity_dict["organization_number"] = data["organization_number"]
        if "company_name" not in identity_dict:
            identity_dict["company_name"] = data.get("company_name", "Unknown")

        return {
            "identity": identity_dict,
            "contact": contact_dict,
            "business": business_dict,
            "financials": financials_dict,
            "people": people_dict,
            "meta": meta_dict,
        }

    # Backward compatibility properties
    @property
    def organization_number(self) -> str:
        return self.identity.organization_number

    @property
    def company_name(self) -> str:
        return self.identity.company_name

    @property
    def legal_form(self) -> Optional[str]:
        return self.identity.legal_form

    @property
    def status(self) -> str:
        return self.identity.status

    @property
    def registered_date(self) -> Optional[str]:
        return self.identity.registered_date

    @property
    def address(self) -> Optional[str]:
        return self.contact.address

    @property
    def postal_address(self) -> Optional[str]:
        return self.contact.postal_address

    @property
    def municipality(self) -> Optional[str]:
        return self.contact.municipality

    @property
    def website(self) -> Optional[str]:
        return self.contact.website

    @property
    def email(self) -> Optional[str]:
        return self.contact.email

    @property
    def phone(self) -> Optional[str]:
        return self.contact.phone

    @property
    def industry_code(self) -> Optional[str]:
        return self.business.industry_code

    @property
    def industry_description(self) -> Optional[str]:
        return self.business.industry_description

    @property
    def employees(self) -> Optional[int]:
        return self.business.employees

    @property
    def sector(self) -> Optional[str]:
        return self.business.sector

    @property
    def revenue(self) -> Optional[float]:
        return self.financials.revenue

    @property
    def operating_profit(self) -> Optional[float]:
        return self.financials.operating_profit

    @property
    def net_income(self) -> Optional[float]:
        return self.financials.net_income

    @property
    def total_assets(self) -> Optional[float]:
        return self.financials.total_assets

    @property
    def equity(self) -> Optional[float]:
        return self.financials.equity

    @property
    def financial_year(self) -> Optional[int]:
        return self.financials.financial_year

    @property
    def financials_status(self) -> Optional[str]:
        return self.financials.financials_status

    @property
    def ceo(self) -> Optional[str]:
        return self.people.ceo

    @property
    def board_members(self) -> Optional[List[str]]:
        return self.people.board_members

    @property
    def description(self) -> Optional[str]:
        return self.people.description

    @property
    def profile_created_at(self) -> str:
        return self.meta.profile_created_at

    @property
    def profile_updated_at(self) -> str:
        return self.meta.profile_updated_at

    @property
    def evidence(self) -> List[EvidenceItem]:
        return self.meta.evidence

    @property
    def confidence_score(self) -> float:
        return self.meta.confidence_score

    @confidence_score.setter
    def confidence_score(self, val: float) -> None:
        self.meta.confidence_score = val

    @property
    def sources_used(self) -> List[str]:
        return self.meta.sources_used


class AgentRunResult(BaseModel):
    organization_number: str
    success: bool
    profile: Optional[CompanyProfile] = None
    error: Optional[str] = None
    duration_seconds: float = 0.0
    steps_executed: List[str] = Field(default_factory=list)
