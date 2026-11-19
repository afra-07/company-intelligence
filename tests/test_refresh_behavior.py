import asyncio
import os
from datetime import datetime, timedelta
import pytest
from models import CompanyProfile, EvidenceItem
from agent import run_pipeline
from profile_store import save_profile, load_profile, delete_profile

# Helper to create a profile file
async def _noop(*args, **kwargs):
    return {}

def make_profile(org_number: str, created_at: str = None, updated_at: str = None, evidence=None, **fields):
    if evidence is None:
        evidence = []
    profile = CompanyProfile(
        identity=dict(
            organization_number=org_number,
            company_name=fields.get("company_name", "OldCo"),
            legal_form=fields.get("legal_form", "AS"),
            status=fields.get("status", "Active"),
            registered_date=fields.get("registered_date", "2020-01-01"),
        ),
        contact=dict(),
        business=dict(
            industry_description=fields.get("industry_description", "Manufacturing"),
            employees=fields.get("employees", 100),
        ),
        financials=dict(),
        people=dict(),
        meta=dict(
            profile_created_at=created_at or datetime.utcnow().isoformat(),
            profile_updated_at=updated_at or datetime.utcnow().isoformat(),
            evidence=evidence,
            sources_used=[],
        ),
    )
    save_profile(profile)
    return profile

# Fixtures to monkeypatch async fetch functions
@pytest.fixture(autouse=True)
def mock_async_functions(monkeypatch):
    # Brreg fetch returns minimal data
    async def mock_brreg_entity(org):
        return {
            "company_name": "NewCo",
            "organization_number": org,
            "evidence": [
                EvidenceItem(
                    fact_key="company_name",
                    fact_value="NewCo",
                    source_name="brreg",
                    source_url="https://brreg.no",
                    date_checked=datetime.utcnow().date().isoformat(),
                    confidence=1.0,
                    method="brreg_api",
                )
            ],
        }
    async def mock_brreg_roles(org):
        return {"evidence": []}
    async def mock_regnskap(org):
        return {"evidence": []}
    async def mock_scrape_proff(org, name):
        return {"evidence": []}
    async def mock_discover_company_website(*args, **kwargs):
        return {"evidence": []}
    async def mock_extract_company_news(*args, **kwargs):
        return []
    async def mock_extract_company_jobs(*args, **kwargs):
        return []
    async def mock_extract_company_intelligence_facts(*args, **kwargs):
        return []
    async def mock_verify_company_match(*args, **kwargs):
        return {"is_match": True, "confidence": 0.9}
    async def mock_generate_description(*args, **kwargs):
        return "Generated description"
    async def mock_extract_facts_from_text(*args, **kwargs):
        return {"evidence": []}

    # Apply patches
    monkeypatch.setattr('agent.fetch_brreg_entity', mock_brreg_entity)
    monkeypatch.setattr('agent.fetch_brreg_roles', mock_brreg_roles)
    monkeypatch.setattr('agent.fetch_regnskap', mock_regnskap)
    monkeypatch.setattr('agent.scrape_proff', mock_scrape_proff)
    monkeypatch.setattr('agent.discover_company_website', mock_discover_company_website)
    monkeypatch.setattr('agent.extract_company_news', mock_extract_company_news)
    monkeypatch.setattr('agent.extract_company_jobs', mock_extract_company_jobs)
    monkeypatch.setattr('agent.extract_company_intelligence_facts', mock_extract_company_intelligence_facts)
    monkeypatch.setattr('agent.verify_company_match', mock_verify_company_match)
    monkeypatch.setattr('agent.generate_description', mock_generate_description)
    monkeypatch.setattr('agent.extract_facts_from_text', mock_extract_facts_from_text)
    # No-op increment_request
    monkeypatch.setattr('agent.increment_request', lambda *args, **kwargs: None)

    yield

# Helper to clean profiles after each test
@pytest.fixture(autouse=True)
def clean_profiles(tmp_path, monkeypatch):
    # Redirect profiles directory to a temp location
    from profile_store import PROFILES_DIR
    monkeypatch.setattr('profile_store.PROFILES_DIR', tmp_path / "profiles")
    (tmp_path / "profiles").mkdir()
    yield
    # Ensure removal (tmp_path will be cleaned automatically)

@pytest.mark.asyncio
async def test_fresh_cached_profile_returns_without_network(monkeypatch):
    org = "111111111"
    # create fresh profile (updated now)
    profile = make_profile(org)
    # monitor that brreg fetch would raise if called
    def raise_brreg(*args, **kwargs):
        raise AssertionError("Brreg should not be called for fresh cache")
    monkeypatch.setattr('agent.fetch_brreg_entity', raise_brreg)
    result = await run_pipeline(org, force_refresh=False)
    assert result.success
    assert result.profile is not None
    assert result.profile.identity.company_name == "OldCo"
    assert "cache_hit" in result.steps_executed

@pytest.mark.asyncio
async def test_stale_profile_is_refreshed(monkeypatch):
    org = "222222222"
    stale_time = datetime.utcnow() - timedelta(days=2)
    profile = make_profile(org, updated_at=stale_time.isoformat())
    result = await run_pipeline(org, force_refresh=False)
    assert result.success
    # Should not be a cache hit
    assert "cache_hit" not in result.steps_executed
    # Updated_at should be newer than stale_time
    new_updated = datetime.fromisoformat(result.profile.meta.profile_updated_at)
    assert new_updated > stale_time
    # Company name should come from fresh brreg (NewCo)
    assert result.profile.identity.company_name == "NewCo"

@pytest.mark.asyncio
async def test_forced_refresh_preserves_old_evidence(monkeypatch):
    org = "333333333"
    old_ev = EvidenceItem(
        fact_key="employees",
        fact_value=100,
        source_name="old",
        source_url="url",
        date_checked=datetime.utcnow().date().isoformat(),
        confidence=0.9,
        method="brreg_api",
    )
    profile = make_profile(org, evidence=[old_ev])
    # modify mock_brreg_entity to return different evidence
    async def mock_brreg_entity(org):
        return {
            "company_name": "NewCo",
            "organization_number": org,
            "evidence": [
                EvidenceItem(
                    fact_key="employees",
                    fact_value=150,
                    source_name="new",
                    source_url="url2",
                    date_checked=datetime.utcnow().date().isoformat(),
                    confidence=0.95,
                    method="brreg_api",
                )
            ],
        }
    monkeypatch.setattr('agent.fetch_brreg_entity', mock_brreg_entity)
    result = await run_pipeline(org, force_refresh=True)
    assert result.success
    ev_keys = {(e.fact_key, e.fact_value) for e in result.profile.meta.evidence}
    # both old and new evidence should be present
    assert ("employees", 100) in ev_keys
    assert ("employees", 150) in ev_keys

@pytest.mark.asyncio
async def test_duplicate_evidence_not_duplicated(monkeypatch):
    org = "444444444"
    profile = make_profile(org)
    # Stub brreg to return same evidence twice across two runs
    ev_item = EvidenceItem(
        fact_key="company_name",
        fact_value="DupCo",
        source_name="brreg",
        source_url="https://brreg.no",
        date_checked=datetime.utcnow().date().isoformat(),
        confidence=1.0,
        method="brreg_api",
    )
    async def mock_brreg_entity(org):
        return {"company_name": "DupCo", "organization_number": org, "evidence": [ev_item]}
    monkeypatch.setattr('agent.fetch_brreg_entity', mock_brreg_entity)
    # first refresh
    await run_pipeline(org, force_refresh=True)
    # second refresh
    result = await run_pipeline(org, force_refresh=True)
    evs = result.profile.meta.evidence
    # there should be only one evidence item for the fingerprint
    assert len(evs) == 1

@pytest.mark.asyncio
async def test_none_does_not_erase_existing_value(monkeypatch):
    org = "555555555"
    profile = make_profile(org, industry_description="Manufacturing")
    async def mock_brreg_entity(org):
        # Return None for industry_description
        return {"company_name": "NewCo", "organization_number": org, "industry_description": None, "evidence": []}
    monkeypatch.setattr('agent.fetch_brreg_entity', mock_brreg_entity)
    result = await run_pipeline(org, force_refresh=True)
    assert result.profile.business.industry_description == "Manufacturing"

@pytest.mark.asyncio
async def test_protected_identity_fields_not_overwritten_without_brreg(monkeypatch):
    org = "666666666"
    profile = make_profile(org, company_name="OldCo")
    async def mock_brreg_entity(org):
        # No company_name in result, simulate other source trying to set it
        return {"organization_number": org, "evidence": []}
    async def mock_discover_company_website(*args, **kwargs):
        return {"company_name": "Malicious", "evidence": []}
    monkeypatch.setattr('agent.fetch_brreg_entity', mock_brreg_entity)
    monkeypatch.setattr('agent.discover_company_website', mock_discover_company_website)
    result = await run_pipeline(org, force_refresh=True)
    # company_name should stay OldCo because only brreg can override protected fields
    assert result.profile.identity.company_name == "OldCo"

@pytest.mark.asyncio
async def test_profile_created_at_preserved(monkeypatch):
    org = "777777777"
    created_at = datetime.utcnow() - timedelta(days=5)
    profile = make_profile(org, created_at=created_at.isoformat())
    result = await run_pipeline(org, force_refresh=True)
    print('PIPELINE ERROR:', result.error)
    print('PIPELINE STEPS:', result.steps_executed)
    assert result.profile.meta.profile_created_at == created_at.isoformat()

@pytest.mark.asyncio
async def test_brreg_lookup_failure_returns_failure(monkeypatch):
    org = "888888888"
    async def mock_brreg_entity(org):
        return {"error": "not found"}
    monkeypatch.setattr('agent.fetch_brreg_entity', mock_brreg_entity)
    result = await run_pipeline(org, force_refresh=False)
    assert not result.success
    assert result.profile is None
    # The pipeline should halt after brreg_entity without executing subsequent steps
    assert result.steps_executed == ["brreg_entity"]
    assert result.error is not None and "Company not found in Brreg" in result.error
