"""
Signalpost — Evaluation & Validation Tests
=========================================
Tests company profiling, evidence citation, non-guessing of missing data,
and merge/update integrity.
"""

import asyncio
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from models import CompanyProfile, EvidenceItem
from profile_store import merge_profile
from agent import run_pipeline


async def test_missing_financials_not_guessed():
    """Verify that government entities never get guessed/hallucinated financials."""
    print("\n[TEST] Verifying missing financials are not guessed for Riksrevisjonen (974760843)...")
    res = await run_pipeline("974760843", force_refresh=True)
    assert res.success, f"Pipeline failed: {res.error}"
    p = res.profile
    assert p.company_name == "RIKSREVISJONEN"
    assert p.revenue is None, f"Expected None for revenue, got {p.revenue}"
    assert p.net_income is None, f"Expected None for net_income, got {p.net_income}"
    assert p.total_assets is None, f"Expected None for total_assets, got {p.total_assets}"
    assert p.financials_status == "not_filed"
    print("  ✓ Missing financials remain strictly None (not guessed).")


async def test_evidence_citations():
    """Verify that every fact in evidence has source_url, source_name, and date."""
    print("\n[TEST] Verifying evidence citations for Equinor ASA (923609016)...")
    res = await run_pipeline("923609016", force_refresh=True)
    assert res.success, f"Pipeline failed: {res.error}"
    p = res.profile
    assert len(p.evidence) > 0, "No evidence items found"
    for ev in p.evidence:
        assert ev.fact_key, "Evidence item missing fact_key"
        assert ev.source_name, f"Evidence item {ev.fact_key} missing source_name"
        assert ev.source_url, f"Evidence item {ev.fact_key} missing source_url"
        assert ev.date_checked, f"Evidence item {ev.fact_key} missing date_checked"
        assert 0.0 <= ev.confidence <= 1.0, f"Invalid confidence {ev.confidence}"
    print(f"  ✓ All {len(p.evidence)} evidence items contain valid sources, URLs, and dates.")


async def test_merge_and_update_protection():
    """Verify that merge_profile protects core fields and does not overwrite valid data with None."""
    print("\n[TEST] Verifying smart merge & update protections...")
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
                source_url="https://brreg.no/999999999",
                date_checked="2026-10-01",
                confidence=1.0,
                method="brreg_api",
            )
        ]
    )

    # Attempt to overwrite with lower-confidence/unsupported data
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
            source_name="Web",
            source_url="https://example.com",
            date_checked="2026-10-11",
            confidence=0.8,
            method="web_search",
        )
    ]

    merged = merge_profile(original, updates, new_evidence)
    assert merged.company_name == "ORIGINAL AS", "Protected company_name was overwritten!"
    assert merged.revenue == 5000000.0, "Valid revenue was wiped out with None!"
    assert merged.employees == 15, "New valid employee count was not updated!"
    assert merged.website == "https://example.com", "New field was not set!"
    assert len(merged.evidence) == 2, "Evidence was not properly merged!"
    print("  ✓ Protected fields preserved, valid data not wiped, and evidence deduplicated.")


async def run_all():
    print("=" * 60)
    print("Starting Automated Evaluation & Verification Suite")
    print("=" * 60)
    await test_missing_financials_not_guessed()
    await test_evidence_citations()
    await test_merge_and_update_protection()
    print("\n" + "=" * 60)
    print("✅ All evaluation tests passed successfully!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(run_all())
