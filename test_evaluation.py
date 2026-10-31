"""
Signalpost — Evaluation & Validation Tests
=========================================
Tests company profiling, evidence citation, non-guessing of missing data,
and merge/update integrity.
"""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from models import CompanyProfile, EvidenceItem, AgentRunResult
from profile_store import merge_profile
from agent import run_pipeline, run_batch
from run import validate_result, run as run_eval_script


async def test_keyless_valid_company():
    """1. Test valid company with all API keys disabled, asserting all 6 nested sections."""
    print("\n[TEST 1] Valid company (Equinor ASA 923609016) with all API keys disabled...", flush=True)
    with patch.dict(os.environ, {"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": "", "GEMINI_API_KEY": "", "SERPAPI_KEY": ""}):
        res = await run_pipeline("923609016", force_refresh=True)
        assert res.success, f"Pipeline failed: {res.error}"
        assert res.profile is not None
        p = res.profile

        # Explicit verification of all six nested sections
        assert hasattr(p, "identity"), "Missing identity section"
        assert hasattr(p, "contact"), "Missing contact section"
        assert hasattr(p, "business"), "Missing business section"
        assert hasattr(p, "financials"), "Missing financials section"
        assert hasattr(p, "people"), "Missing people section"
        assert hasattr(p, "meta"), "Missing meta section"

        # 1. Identity section
        assert p.identity.company_name == "EQUINOR ASA"
        assert p.identity.organization_number == "923609016"
        assert p.identity.status in ("Active", "Bankrupt", "Under liquidation", "Forced liquidation")
        assert p.identity.registered_date is not None

        # 2. Contact section
        assert p.contact.address is not None
        assert p.contact.municipality is not None

        # 3. Business section
        assert p.business.industry_description is not None or p.business.industry_code is not None
        assert p.business.employees is not None and p.business.employees > 0

        # 4. Financials section
        assert p.financials.revenue is not None and p.financials.revenue > 0
        assert p.financials.financials_status == "found"

        # 5. People section
        assert p.people.ceo is not None or p.people.board_members is not None

        # 6. Meta section
        assert len(p.meta.evidence) > 0, "Expected evidence items in profile.meta"
        assert 0.0 <= p.meta.confidence_score <= 1.0
        assert len(p.meta.sources_used) > 0

        # Verify serialized JSON contains exact 6 top-level nested sections
        p_dict = p.model_dump()
        expected_sections = {"identity", "contact", "business", "financials", "people", "meta"}
        assert set(p_dict.keys()) == expected_sections, f"Serialized keys mismatch: {set(p_dict.keys())}"

        for ev in p.meta.evidence:
            if "proff.no" in ev.source_url.lower():
                assert ev.fact_key in ("employees", "_proff_raw"), f"Proff.no found for non-employee key: {ev.fact_key}"
            assert "google.com" not in ev.source_url.lower(), f"Google found in evidence URL: {ev.source_url}"

        violations = validate_result(res)
        assert not violations, f"Contract violations: {violations}"
        print("  ✓ Keyless run succeeded, verified all 6 nested sections and contract compliance.", flush=True)


async def test_invalid_company_number():
    """2. Test invalid / non-existent company numbers."""
    print("\n[TEST 2] Invalid organization numbers...", flush=True)
    # Malformed number (not 9 digits)
    res_malformed = await run_pipeline("12345", force_refresh=True)
    assert not res_malformed.success
    assert res_malformed.profile is None
    assert res_malformed.error
    assert not validate_result(res_malformed)

    # 9-digit non-existent company
    res_nonexistent = await run_pipeline("999999999", force_refresh=True)
    assert not res_nonexistent.success
    assert res_nonexistent.profile is None
    assert "not found" in res_nonexistent.error.lower()
    assert not validate_result(res_nonexistent)
    print("  ✓ Invalid and non-existent companies returned proper structured failure results.", flush=True)


async def test_undiscovered_website():
    """3. Test company whose website cannot be discovered."""
    print("\n[TEST 3] Company with no website discovered...", flush=True)
    # Mock website discovery to return empty
    with patch("agent.discover_company_website", return_value={"evidence": []}):
        with patch("agent.scrape_proff", return_value={"evidence": []}):
            res = await run_pipeline("974760843", force_refresh=True)
            assert res.success
            assert res.profile is not None
            violations = validate_result(res)
            assert not violations, f"Violations: {violations}"
    print("  ✓ Pipeline gracefully handles missing website without crashing.", flush=True)


async def test_simulated_network_failure():
    """4. Test simulated API/network failure."""
    print("\n[TEST 4] Simulated network failure in Brreg...", flush=True)
    with patch("agent.fetch_brreg_entity", side_effect=Exception("Connection reset by peer")):
        res = await run_pipeline("923609016", force_refresh=True)
        assert not res.success
        assert res.profile is None
        assert "Connection reset by peer" in res.error
        violations = validate_result(res)
        assert not violations, f"Violations: {violations}"
    print("  ✓ Simulated network failure handled gracefully and returned contract-compliant failure.", flush=True)


async def test_batch_processing_isolation():
    """5. Test batch processing where one company fails but others succeed, preserving order."""
    print("\n[TEST 5] Batch processing with mixed success/failure...", flush=True)
    test_orgs = ["923609016", "000000000", "974760843"]
    results = await run_batch(test_orgs, concurrency=3, force_refresh=True)

    assert len(results) == 3, f"Expected 3 results, got {len(results)}"
    # Check input order preservation
    assert results[0].organization_number == "923609016"
    assert results[1].organization_number == "000000000"
    assert results[2].organization_number == "974760843"

    assert results[0].success is True
    assert results[1].success is False
    assert results[2].success is True

    for r in results:
        violations = validate_result(r)
        assert not violations, f"Violation for {r.organization_number}: {violations}"
    print("  ✓ Batch completed all items in input order, isolating failure cleanly.", flush=True)


async def test_run_py_multiple_companies():
    """6. Test run.py input file with multiple companies and output file generation."""
    print("\n[TEST 6] Multi-company file processing via run.py...", flush=True)
    with tempfile.TemporaryDirectory() as tmpdir:
        input_file = Path(tmpdir) / "test_orgs.txt"
        output_file = Path(tmpdir) / "test_results.jsonl"

        input_file.write_text("923609016\n# Comment line\n999999999\n\n974760843\n", encoding="utf-8")

        await run_eval_script(
            input_path=input_file,
            output_path=output_file,
            concurrency=2,
            force_refresh=True,
            validate=True,
        )

        assert output_file.exists()
        lines = output_file.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3, f"Expected 3 output lines for 3 input orgs, got {len(lines)}"

        data = [json.loads(line) for line in lines]
        assert data[0]["organization_number"] == "923609016"
        assert data[0]["success"] is True

        assert data[1]["organization_number"] == "999999999"
        assert data[1]["success"] is False

        assert data[2]["organization_number"] == "974760843"
        assert data[2]["success"] is True
    print("  ✓ run.py generated exactly one record per input org in preserved order.", flush=True)


async def test_missing_financials_not_guessed():
    """7. Verify government entities never get guessed/hallucinated financials across nested sections."""
    print("\n[TEST 7] Verifying missing financials are not guessed for Riksrevisjonen (974760843)...", flush=True)
    res = await run_pipeline("974760843", force_refresh=True)
    assert res.success, f"Pipeline failed: {res.error}"
    p = res.profile
    assert p.identity.company_name == "RIKSREVISJONEN"
    assert p.financials.revenue is None, f"Expected None for revenue, got {p.financials.revenue}"
    assert p.financials.net_income is None, f"Expected None for net_income, got {p.financials.net_income}"
    assert p.financials.total_assets is None, f"Expected None for total_assets, got {p.financials.total_assets}"
    assert p.financials.financials_status == "not_filed"
    print("  ✓ Missing financials remain strictly None (not guessed).", flush=True)


async def test_merge_and_update_protection():
    """8. Verify smart merge & update protections across nested sections."""
    print("\n[TEST 8] Verifying smart merge & update protections...", flush=True)
    original = CompanyProfile(
        organization_number="999999999",
        company_name="ORIGINAL AS",
        legal_form="Aksjeselskap (AS)",
        revenue=5000000.0,
        employees=10,
        ceo="Original Leader",
        evidence=[
            EvidenceItem(
                fact_key="company_name",
                fact_value="ORIGINAL AS",
                source_name="Brreg",
                source_url="https://data.brreg.no/enhetsregisteret/api/enheter/999999999",
                date_checked="2026-10-01",
                confidence=1.0,
                method="brreg_api",
            )
        ]
    )

    updates = {
        "company_name": "FAKE NAME FROM SCRAPING",
        "revenue": None,        # Should NOT overwrite existing 5000000.0
        "employees": 15,        # Valid new value should update
        "website": "https://example.com",
    }
    new_evidence = [
        EvidenceItem(
            fact_key="employees",
            fact_value=15,
            source_name="Company website",
            source_url="https://example.com",
            date_checked="2026-10-11",
            confidence=0.8,
            method="webpage_scrape",
        )
    ]

    merged = merge_profile(original, updates, new_evidence)
    assert merged.identity.company_name == "ORIGINAL AS", "Protected company_name was overwritten!"
    assert merged.financials.revenue == 5000000.0, "Valid revenue was wiped out with None!"
    assert merged.business.employees == 15, "New valid employee count was not updated!"
    assert merged.contact.website == "https://example.com", "New field was not set!"
    assert len(merged.meta.evidence) == 2, "Evidence was not properly merged!"
    print("  ✓ Protected fields preserved, valid data not wiped, and evidence deduplicated.", flush=True)


async def run_all():
    print("=" * 65, flush=True)
    print("Signalpost Comprehensive Evaluation & Verification Test Suite", flush=True)
    print("=" * 65, flush=True)
    await test_keyless_valid_company()
    await test_invalid_company_number()
    await test_undiscovered_website()
    await test_simulated_network_failure()
    await test_batch_processing_isolation()
    await test_run_py_multiple_companies()
    await test_missing_financials_not_guessed()
    await test_merge_and_update_protection()
    print("\n" + "=" * 65, flush=True)
    print("🎉 ALL TEST SUITES PASSED SUCCESSFULLY!", flush=True)
    print("=" * 65, flush=True)


if __name__ == "__main__":
    asyncio.run(run_all())
