import asyncio
import json
import os
from pathlib import Path

import pytest

# Import modules from the project

# Import modules from the project

import sys
import pathlib
# Ensure the project root is on the import path
project_root = pathlib.Path(__file__).resolve().parents[1]
sys.path.append(str(project_root))

import agent
import profile_store
from models import CompanyProfile

from models import CompanyProfile

# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------

@pytest.fixture
def isolated_profiles(tmp_path, monkeypatch):
    """Redirect the profile storage directory to a temporary location.
    This ensures the test does not touch the real ``profiles`` folder.
    """
    profiles_dir = tmp_path / "profiles"
    profiles_dir.mkdir()
    monkeypatch.setattr(profile_store, "PROFILES_DIR", profiles_dir)
    return profiles_dir


@pytest.fixture(autouse=True)
def mock_external_calls(monkeypatch):
    """Replace all async network‑dependent functions with lightweight stubs.
    The stubs return minimal data sufficient for the pipeline to proceed.
    """

    async def _dummy_brreg_entity(org):
        return {
            "organization_number": org,
            "company_name": "DummyCo",
            "evidence": [],
        }

    async def _dummy_brreg_roles(org):
        return {"evidence": []}

    async def _dummy_regnskap(org):
        return {"evidence": []}

    async def _dummy_scrape_proff(org, name):
        return {"evidence": []}

    async def _dummy_discover_company_website(*args, **kwargs):
        return {"website": "https://example.com", "evidence": []}

    async def _dummy_extract_company_news(*args, **kwargs):
        return []

    async def _dummy_extract_company_jobs(*args, **kwargs):
        return []

    async def _dummy_extract_company_intelligence_facts(*args, **kwargs):
        return []

    async def _dummy_verify_company_match(*args, **kwargs):
        return {"is_match": True, "confidence": 1.0}

    async def _dummy_generate_description(*args, **kwargs):
        return "A short description."

    # Patch each function used by the pipeline
    monkeypatch.setattr(agent, "fetch_brreg_entity", _dummy_brreg_entity)
    monkeypatch.setattr(agent, "fetch_brreg_roles", _dummy_brreg_roles)
    monkeypatch.setattr(agent, "fetch_regnskap", _dummy_regnskap)
    monkeypatch.setattr(agent, "scrape_proff", _dummy_scrape_proff)
    monkeypatch.setattr(agent, "discover_company_website", _dummy_discover_company_website)
    monkeypatch.setattr(agent, "extract_company_news", _dummy_extract_company_news)
    monkeypatch.setattr(agent, "extract_company_jobs", _dummy_extract_company_jobs)
    monkeypatch.setattr(agent, "extract_company_intelligence_facts", _dummy_extract_company_intelligence_facts)
    monkeypatch.setattr(agent, "verify_company_match", _dummy_verify_company_match)
    monkeypatch.setattr(agent, "generate_description", _dummy_generate_description)

    # Disable optional OpenAI path
    monkeypatch.setattr(agent, "HAS_OPENAI", False)
    # Stub increment_request (no side effects)
    monkeypatch.setattr(agent, "increment_request", lambda *a, **k: None)
    # Stub any other async calls that may be imported but not directly patched
    monkeypatch.setattr(agent, "search_company_news", lambda *a, **k: asyncio.Future())
    monkeypatch.setattr(agent, "extract_facts_from_text", lambda *a, **k: asyncio.Future())


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cache_hit_with_enrichment(isolated_profiles):
    """A profile with ``enrichment_done=True`` should be returned via cache.
    The pipeline must not execute any further steps.
    """
    org = "111111111"
    # Create a profile with the enrichment marker set.
    profile = CompanyProfile(
        identity={"organization_number": org, "company_name": "TestCo"},
        meta={"enrichment_done": True},
    )
    profile_store.save_profile(profile)

    result = await agent.run_pipeline(org, force_refresh=False)
    assert result.success
    # The only step recorded must be the cache hit.
    assert result.steps_executed == ["cache_hit"]
    # The returned profile must carry the marker.
    assert result.profile.meta.enrichment_done is True


@pytest.mark.asyncio
async def test_cache_miss_enrichment_flow(isolated_profiles):
    """When ``enrichment_done`` is missing or False, the pipeline runs and
    sets the marker to True after successful enrichment.
    """
    org = "222222222"
    # Ensure no pre‑existing profile.
    path = profile_store._profile_path(org)
    if path.exists():
        path.unlink()

    # Run the pipeline – all external calls are mocked to return empty data.
    result = await agent.run_pipeline(org, force_refresh=False)
    assert result.success
    # The pipeline should NOT contain a cache hit.
    assert "cache_hit" not in result.steps_executed
    # The resulting profile must have the enrichment marker set.
    assert result.profile.meta.enrichment_done is True
    # Verify the profile was persisted with the marker.
    saved = profile_store.load_profile(org)
    assert saved is not None
    assert saved.meta.enrichment_done is True
