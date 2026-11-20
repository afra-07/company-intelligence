"""Regression tests for the website resolution logic in ``agent.run_pipeline``.

The pipeline used to look for a cached homepage only under the top‑level
``website`` field.  Cached profiles store the address under ``contact.website``.
A recent fix adds a fallback to ``merged_data["contact"]["website"]``.
These tests verify that the resolution works for the three relevant cases:

1. A top‑level ``website`` field is present.
2. Only a nested ``contact.website`` field is present.
3. Neither field is present.
4. A cached profile that contains ``contact.website`` (and is refreshed with
   ``force_refresh=True``) also triggers the news and job pipeline guards.
"""

import json
import pathlib
import pytest

from agent import run_pipeline
import profile_store

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_profiles(tmp_path, monkeypatch):
    """Redirect the profile store to a temporary directory for isolation."""
    monkeypatch.setattr('profile_store.PROFILES_DIR', tmp_path / "profiles")
    (tmp_path / "profiles").mkdir()
    yield

# ---------------------------------------------------------------------------
# Helper to apply common monkey‑patches
# ---------------------------------------------------------------------------

def _apply_monkeypatches(monkeypatch, brreg_entity_result):
    """Patch all external async functions used by ``run_pipeline``.

    ``brreg_entity_result`` is merged into the mocked ``fetch_brreg_entity``
    return value (it already contains any ``website`` / ``contact`` data).
    The function returns a dict ``captured`` that will hold the ``known_website``
    argument received by ``discover_company_website``.
    """
    async def mock_brreg_entity(_):
        # Merge the supplied result with an empty evidence list.
        return {"evidence": [], **brreg_entity_result}

    async def mock_brreg_roles(_):
        return {"evidence": []}

    async def mock_regnskap(_):
        return {"evidence": []}

    async def mock_scrape_proff(_org, _name):
        return {"evidence": []}

    captured = {}

    async def mock_discover_company_website(org_number, company_name, known_website, proff_hint=None):
        # Record the actual argument passed (the third positional argument).
        captured["known_website"] = known_website
        return {"evidence": []}

    async def mock_empty(*_args, **_kwargs):
        return []

    async def mock_verify_company_match(*_args, **_kwargs):
        return {"is_match": True, "confidence": 0.9}

    async def mock_generate_description(*_args, **_kwargs):
        return "Generated description"

    async def mock_extract_facts_from_text(*_args, **_kwargs):
        return {"evidence": []}

    # Apply patches.
    monkeypatch.setattr('agent.fetch_brreg_entity', mock_brreg_entity)
    monkeypatch.setattr('agent.fetch_brreg_roles', mock_brreg_roles)
    monkeypatch.setattr('agent.fetch_regnskap', mock_regnskap)
    monkeypatch.setattr('agent.scrape_proff', mock_scrape_proff)
    monkeypatch.setattr('agent.discover_company_website', mock_discover_company_website)
    monkeypatch.setattr('agent.extract_company_news', mock_empty)
    monkeypatch.setattr('agent.extract_company_jobs', mock_empty)
    monkeypatch.setattr('agent.extract_company_intelligence_facts', mock_empty)
    monkeypatch.setattr('agent.verify_company_match', mock_verify_company_match)
    monkeypatch.setattr('agent.generate_description', mock_generate_description)
    monkeypatch.setattr('agent.extract_facts_from_text', mock_extract_facts_from_text)
    # Increment request counters are no‑ops for the test suite.
    monkeypatch.setattr('agent.increment_request', lambda *_, **__: None)

    return captured

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_top_level_website_used(monkeypatch):
    org = "111111111"
    captured = _apply_monkeypatches(monkeypatch, {"website": "https://top.example.com"})
    result = await run_pipeline(org, force_refresh=True)
    assert captured["known_website"] == "https://top.example.com"
    assert "company_news_pipeline" in result.steps_executed
    assert "job_posting_pipeline" in result.steps_executed


@pytest.mark.asyncio
async def test_nested_contact_website_used(monkeypatch):
    org = "222222222"
    captured = _apply_monkeypatches(monkeypatch, {"contact": {"website": "https://nested.example.com"}})
    result = await run_pipeline(org, force_refresh=True)
    assert captured["known_website"] == "https://nested.example.com"
    assert "company_news_pipeline" in result.steps_executed
    assert "job_posting_pipeline" in result.steps_executed


@pytest.mark.asyncio
async def test_no_website_skips_pipelines(monkeypatch):
    org = "333333333"
    captured = _apply_monkeypatches(monkeypatch, {})
    result = await run_pipeline(org, force_refresh=True)
    assert captured["known_website"] is None
    assert "company_news_pipeline" not in result.steps_executed
    assert "job_posting_pipeline" not in result.steps_executed


@pytest.mark.asyncio
async def test_cached_profile_contact_website_triggers_pipelines(monkeypatch):
    org = "444444444"
    # Re‑use the helper from ``tests.test_refresh_behavior`` to create a minimal profile.
    from tests.test_refresh_behavior import make_profile
    _ = make_profile(org)
    # Write the cached website into the profile JSON (contact.website).
    profile_path = pathlib.Path(profile_store.PROFILES_DIR) / f"{org}.json"
    data = json.loads(profile_path.read_text(encoding="utf-8"))
    data.setdefault("contact", {})["website"] = "https://cached.example.com"
    profile_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    captured = _apply_monkeypatches(monkeypatch, {})
    result = await run_pipeline(org, force_refresh=True)
    assert captured["known_website"] == "https://cached.example.com"
    assert "company_news_pipeline" in result.steps_executed
    assert "job_posting_pipeline" in result.steps_executed
