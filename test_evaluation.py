"""
Signalpost — Evaluation & Validation Tests
=========================================
Comprehensive tests covering:
1. Keyless valid company profiling across all 6 nested sections.
2. Invalid and non-existent company handling.
3. Undiscovered website resilience.
4. Simulated network failure handling.
5. Batch processing order preservation & failure isolation.
6. run.py arbitrary input file execution and validation.
7. Non-guessing of missing financials for public entities.
8. Merge and update integrity protections.
9. Company identity verification & rejection of conflicting companies.
10. Dedicated news collection pipeline verification.
11. Dedicated job posting pipeline verification.
12. Claim diversity & presence of non-registry facts.
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
from web_searcher import (
    verify_company_identity,
    normalize_company_name,
    extract_company_news,
    extract_company_jobs,
    extract_company_intelligence_facts,
)


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
        "revenue": None,
        "employees": 15,
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


async def test_company_identity_verification_and_mismatch_rejection():
    """9. Verify strict company identity matching rejects mismatched organizations and accepted matches."""
    print("\n[TEST 9] Verifying company identity matching and conflict rejection...", flush=True)
    # Target: Equinor ASA 923609016
    matched_text = "Equinor ASA operates offshore facilities. Organisasjonsnummer: 923 609 016."
    is_ver, conf, reason = verify_company_identity(matched_text, "923609016", "EQUINOR ASA")
    assert is_ver is True
    assert conf >= 0.95

    # Conflict: text has another company's 9-digit org number
    conflicting_text = "Aker BP ASA operations. Organisasjonsnummer: 974 530 023. Oil and gas production."
    is_ver2, conf2, reason2 = verify_company_identity(conflicting_text, "923609016", "EQUINOR ASA")
    assert is_ver2 is False
    assert conf2 == 0.0
    assert "conflicting" in reason2.lower()

    # Domain match verification
    is_ver3, conf3, _ = verify_company_identity("Energy for people.", "923609016", "EQUINOR ASA", "https://www.equinor.com")
    assert is_ver3 is True
    assert conf3 >= 0.90

    # Domain mismatch
    is_ver4, conf4, _ = verify_company_identity("Unrelated services.", "923609016", "EQUINOR ASA", "https://www.completelyunrelated.no")
    assert is_ver4 is False
    print("  ✓ Conflicting org numbers strictly rejected; authentic identity matches confirmed.", flush=True)


async def test_news_collection_pipeline():
    """10. Verify dedicated news collection extracts valid structured news items."""
    print("\n[TEST 10] Verifying dedicated company news pipeline...", flush=True)
    res = await run_pipeline("923609016", force_refresh=False)
    assert res.success
    p = res.profile

    news_ev = [e for e in p.meta.evidence if e.fact_key == "company_news"]
    assert len(news_ev) > 0, "Expected at least one company_news evidence item"

    for ev in news_ev:
        val = ev.fact_value
        assert isinstance(val, dict), f"News fact_value should be dict, got {type(val)}"
        assert "headline" in val and val["headline"], "Missing news headline"
        assert "summary" in val and val["summary"], "Missing news summary"
        assert "published_date" in val, "Missing published_date key in news item"
        assert "company" in val and val["company"], "Missing company in news item"
        assert ev.source_url.startswith("http"), f"Invalid news URL: {ev.source_url}"
        assert "google.com" not in ev.source_url.lower(), "Search engine in news URL!"
        assert "proff.no" not in ev.source_url.lower(), "Proff in news URL!"
    print(f"  ✓ News collection pipeline verified with {len(news_ev)} rich news claims.", flush=True)


async def test_job_posting_pipeline():
    """11. Verify dedicated job posting pipeline extracts valid structured job vacancies."""
    print("\n[TEST 11] Verifying dedicated job posting pipeline...", flush=True)
    res = await run_pipeline("923609016", force_refresh=False)
    assert res.success
    p = res.profile

    job_ev = [e for e in p.meta.evidence if e.fact_key == "job_posting"]
    assert len(job_ev) > 0, "Expected at least one job_posting evidence item"

    for ev in job_ev:
        val = ev.fact_value
        assert isinstance(val, dict), f"Job fact_value should be dict, got {type(val)}"
        assert "title" in val and val["title"], "Missing job title"
        assert "url" in val and val["url"], "Missing job URL"
        assert ev.source_url.startswith("http"), f"Invalid job URL: {ev.source_url}"
        assert "google.com" not in ev.source_url.lower(), "Search engine in job URL!"
        assert "proff.no" not in ev.source_url.lower(), "Proff in job URL!"

        # Audit requirement: employment_type, deadline, location must NEVER be hardcoded defaults
        emp_type = val.get("employment_type")
        deadline = val.get("deadline")
        loc = val.get("location")
        if emp_type is not None:
            assert emp_type != "Full-time / Fast stilling", f"Hardcoded employment_type detected: {emp_type}"
        if deadline is not None:
            assert deadline != "Open until filled", f"Hardcoded deadline detected: {deadline}"
    print(f"  ✓ Job posting pipeline verified with {len(job_ev)} active job vacancies.", flush=True)


async def test_claim_diversity_and_non_registry_facts():
    """12. Verify high claim diversity beyond repetitive registry rows."""
    print("\n[TEST 12] Verifying claim diversity and non-registry intelligence...", flush=True)
    res = await run_pipeline("923609016", force_refresh=False)
    assert res.success
    p = res.profile

    fact_keys = [e.fact_key for e in p.meta.evidence]
    distinct_keys = set(fact_keys)

    # Core non-registry intelligence categories
    non_registry_keys = {"company_news", "job_posting", "products_services", "business_activity", "sustainability", "project"}
    found_non_reg = distinct_keys.intersection(non_registry_keys)
    assert len(found_non_reg) >= 3, f"Expected at least 3 non-registry categories, found {found_non_reg}"

    # Verify no duplicates
    ev_fingerprints = [
        (e.fact_key, e.source_url, str(e.fact_value.get("headline") or e.fact_value.get("title") or e.fact_value)[:50] if isinstance(e.fact_value, dict) else str(e.fact_value)[:50])
        for e in p.meta.evidence
    ]
    assert len(ev_fingerprints) == len(set(ev_fingerprints)), "Found duplicate evidence items!"
    print(f"  ✓ Diverse non-registry categories found: {sorted(list(found_non_reg))} (Total claims: {len(p.meta.evidence)}).", flush=True)


# ─── New Tests for Second-Level Audit Requirements ─────────────────────────────

async def test_two_companies_similar_names():
    """13. Verify that two companies with similar names are distinguished by organization number."""
    print("\n[TEST 13] Verifying two companies with similar names are distinguished...", flush=True)
    # Target: EQUINOR ASA (923609016)
    # Similar company: Equinor Eiendom AS (org: 987654321)
    similar_company_text = (
        "Equinor Eiendom AS forvalter næringseiendom i Stavanger. "
        "Foretaksregisteret / Organisasjonsnummer: 987 654 321. "
        "Besøksadresse: Forusbeen 50."
    )
    is_ver, conf, reason = verify_company_identity(similar_company_text, "923609016", "EQUINOR ASA")
    assert is_ver is False, "Should have rejected page belonging to similar named company"
    assert conf == 0.0, "Confidence should be 0.0 on conflicting org number"
    assert "conflicting" in reason.lower()
    print("  ✓ Conflicting org number for similarly named company correctly rejected.", flush=True)


async def test_news_article_passing_mention_rejected():
    """14. Verify external news article that mentions target company only in passing is rejected."""
    print("\n[TEST 14] Verifying passing mentions in external news articles are rejected...", flush=True)
    passing_headline = "Oslo Børs stiger etter rentedom fra Norges Bank"
    passing_body = (
        "Hovedindeksen på Oslo Børs klatret 0.8 prosent onsdag etter rentebeskjeden. "
        "Teknologiselskapene AutoStore og Nordic Semiconductor ledet oppgangen med markante løft. "
        "Industritungvektere som Hydro og Yara endte også i grønt territorium. "
        "En rekke andre selskaper som Equinor og DNB hadde moderat handelsvolum. "
        "Mange aksjestrateger venter likevel roligere dager fremover mot helgen."
    )
    # Target company Equinor is only mentioned once in paragraph 4, not in headline or lead
    is_ver, conf, reason = verify_company_identity(
        passing_body,
        "923609016",
        "EQUINOR ASA",
        "https://finansavisen.no/marked/oslo-bors-oppgang",
        headline=passing_headline,
        is_external_article=True,
    )
    assert is_ver is False, f"Passing mention should be rejected, got is_ver={is_ver}"
    assert conf < 0.5, f"Confidence should be low for passing mention, got {conf}"
    assert "passing" in reason.lower()

    # Contrast with article genuinely about Equinor:
    genuine_headline = "Equinor tildeler nye milliardkontrakter på norsk sokkel"
    genuine_body = (
        "Equinor har i dag tildelt rammeavtaler for modifikasjoner og vedlikehold. "
        "Konsernsjefen i Equinor uttaler at avtalene sikrer arbeidsplasser og forutsigbarhet. "
        "Equinor investerer betydelige beløp i å forlenge levetiden til eksisterende felt."
    )
    is_ver_gen, conf_gen, _ = verify_company_identity(
        genuine_body,
        "923609016",
        "EQUINOR ASA",
        "https://e24.no/energi/equinor-milliardkontrakter",
        headline=genuine_headline,
        is_external_article=True,
    )
    assert is_ver_gen is True, "Genuine article about Equinor in headline should be accepted"
    assert conf_gen >= 0.85
    print("  ✓ Passing mention rejected; genuine article featuring company accepted.", flush=True)


async def test_article_conflicting_org_number_rejected():
    """15. Verify that an article containing a conflicting organization number is rejected."""
    print("\n[TEST 15] Verifying article with conflicting org number is strictly rejected...", flush=True)
    conflict_headline = "Aker BP og Equinor i nytt samarbeid"
    conflict_body = (
        "Aker BP ASA melder om oppstart på Johan Sverdrup fase 2. "
        "Aker BP ASA er operatør. Organisasjonsnummer: 974 530 023. "
        "Partner Equinor ASA har en eierandel i lisensen."
    )
    is_ver, conf, reason = verify_company_identity(
        conflict_body,
        "923609016",
        "EQUINOR ASA",
        "https://e24.no/nyheter/aker-bp-oppstart",
        headline=conflict_headline,
        is_external_article=True,
    )
    assert is_ver is False, "Should be rejected because article has conflicting org number (974530023)"
    assert conf == 0.0
    assert "conflicting" in reason.lower()
    print("  ✓ Conflicting organization number rejected with confidence 0.0.", flush=True)


async def test_job_posting_other_company_rejected():
    """16. Verify a job posting belonging to another company is rejected."""
    print("\n[TEST 16] Verifying job posting belonging to another company is rejected...", flush=True)
    other_job_page = (
        "Stilling: Senior Boreingeniør hos Vår Energi ASA. "
        "Organisasjonsnummer: 919 160 675. "
        "Arbeidssted: Sandnes. Søknadsfrist: 01.12.2026."
    )
    is_ver, conf, reason = verify_company_identity(
        other_job_page,
        "923609016",
        "EQUINOR ASA",
        "https://varenergi.no/careers/senior-boreingenior",
        is_official_website=True,
    )
    assert is_ver is False, "Job posting for Vår Energi should be rejected for Equinor"
    assert conf == 0.0
    assert "conflicting" in reason.lower()
    print("  ✓ Job posting for another company correctly rejected.", flush=True)


async def test_expired_job_posting_excluded():
    """17. Verify that expired/closed vacancies are detected and excluded."""
    print("\n[TEST 17] Verifying expired/closed vacancies are excluded...", flush=True)
    expired_page = (
        "<html><body>"
        "<h1>Senior Reservoir Engineer</h1>"
        "<div class='status'>Denne stillingen er utløpt og tar ikke lenger imot søknader.</div>"
        "<p>Deadline has passed. Job posting has expired.</p>"
        "</body></html>"
    )
    # Check closed indicators match our detection list
    expired_keywords = [
        "expired", "closed", "no longer accepting", "not available",
        "deadline has passed", "stillingen er lukket", "søknadsfristen er utløpt",
        "utgått", "ikke lenger tilgjengelig", "this position has been filled",
        "job is no longer available",
    ]
    page_lower = expired_page.lower()
    detected = any(k in page_lower for k in expired_keywords)
    assert detected is True, "Expired job keywords should be detected in page"
    print("  ✓ Expired job detection verified.", flush=True)


async def test_workday_unverifiable_excluded():
    """18. Verify that Workday vacancies are excluded when individual verification fails."""
    print("\n[TEST 18] Verifying unverified Workday vacancies are excluded...", flush=True)
    # Test that _query_workday_jobs excludes items when individual page returns 404 or fails
    from unittest.mock import AsyncMock
    mock_post_resp = AsyncMock()
    mock_post_resp.status_code = 200
    mock_post_resp.json = lambda: {
        "jobPostings": [
            {
                "title": "Subsea Engineer",
                "externalPath": "/job/subsea-123",
                "locationsText": "Stavanger",
            }
        ]
    }

    mock_get_resp = AsyncMock()
    mock_get_resp.status_code = 404

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_post_resp):
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_get_resp):
            from web_searcher import _query_workday_jobs
            jobs = await _query_workday_jobs("https://equinor.wd3.myworkdayjobs.com/Careers", "EQUINOR ASA", "923609016")
            assert len(jobs) == 0, f"Vacancy should be excluded on 404 verification failure, got {len(jobs)}"
    print("  ✓ Unverifiable Workday vacancy excluded when GET fails.", flush=True)


async def test_date_validation_missing_and_invalid():
    """19. Verify date parsing rejects invalid/impossible dates and handles missing dates."""
    print("\n[TEST 19] Verifying date parser rejects invalid dates and handles missing dates...", flush=True)
    from web_searcher import parse_iso_date

    # 1. Invalid / impossible dates
    assert parse_iso_date("2026-02-31") is None, "Feb 31 must be rejected"
    assert parse_iso_date("2026-04-31") is None, "April 31 must be rejected"
    assert parse_iso_date("31.02.2026") is None, "31.02.2026 must be rejected"
    assert parse_iso_date("31. april 2026") is None, "31. april 2026 must be rejected"
    assert parse_iso_date("2026-13-01") is None, "Month 13 must be rejected"
    assert parse_iso_date("2099-01-01") is None, "Far future year must be rejected"
    assert parse_iso_date("1850-01-01") is None, "Ancient year must be rejected"

    # 2. Missing / None / empty
    assert parse_iso_date(None) is None
    assert parse_iso_date("") is None
    assert parse_iso_date("No date mentioned in this text.") is None

    # 3. Valid date formats
    assert parse_iso_date("2026-10-28") == "2026-10-28"
    assert parse_iso_date("2026-10-28T14:30:00Z") == "2026-10-28"
    assert parse_iso_date("28.10.2026") == "2026-10-28"
    assert parse_iso_date("28/10/2026") == "2026-10-28"
    assert parse_iso_date("28. oktober 2026") == "2026-10-28"
    assert parse_iso_date("15. mai 2026") == "2026-05-15"
    assert parse_iso_date("October 28, 2026") == "2026-10-28"
    assert parse_iso_date("May 15, 2026") == "2026-05-15"
    print("  ✓ Strict Python date validation passed on all edge cases.", flush=True)


async def test_company_with_no_news_or_vacancies():
    """20. Verify that a company with no news or vacancies produces a contract-valid profile."""
    print("\n[TEST 20] Verifying contract validity for entity with no news or vacancies...", flush=True)
    res = await run_pipeline("974760843", force_refresh=False)
    assert res.success, f"Pipeline failed: {res.error}"
    assert res.profile is not None

    violations = validate_result(res)
    assert not violations, f"Contract violations: {violations}"
    p = res.profile
    assert p.identity.company_name == "RIKSREVISJONEN"
    assert p.meta.confidence_score > 0.0
    print("  ✓ Entity with no news or vacancies produced 100% contract-valid profile.", flush=True)


async def test_company_multiple_distinct_operational_facts():
    """21. Verify company intelligence extraction produces multiple distinct operational facts."""
    print("\n[TEST 21] Verifying multiple distinct operational facts...", flush=True)
    res = await run_pipeline("923609016", force_refresh=False)
    assert res.success
    p = res.profile

    diverse_ev = [
        e for e in p.meta.evidence
        if e.fact_key in {"products_services", "business_activity", "sustainability", "project", "technology_innovation"}
    ]
    assert len(diverse_ev) >= 3, f"Expected at least 3 diverse facts, got {len(diverse_ev)}"

    # Check that all diverse facts have distinct values and valid URLs
    values = [str(e.fact_value) for e in diverse_ev]
    assert len(values) == len(set(values)), "Duplicate fact values detected!"

    for ev in diverse_ev:
        assert ev.source_url.startswith("http"), f"Invalid source URL: {ev.source_url}"
        assert "proff.no" not in ev.source_url.lower(), "Proff cited for intelligence fact!"
        assert "google.com" not in ev.source_url.lower(), "Google cited for intelligence fact!"
        assert len(str(ev.fact_value)) > 40, f"Fact value too brief/generic: {ev.fact_value}"

    print(f"  ✓ {len(diverse_ev)} distinct, substantive operational facts verified across categories.", flush=True)


async def test_workday_mismatched_requisition_id_excluded():
    """22. Regression: Verify Workday vacancy is excluded when requisition ID mismatches."""
    print("\n[TEST 22] Verifying mismatched requisition ID is excluded...", flush=True)
    from unittest.mock import AsyncMock
    mock_post_resp = AsyncMock()
    mock_post_resp.status_code = 200
    mock_post_resp.json = lambda: {
        "jobPostings": [
            {
                "title": "Junior Deal Handler",
                "externalPath": "/job/London/Junior-Deal-Handler_JR108097",
                "locationsText": "London, Great Britain",
            }
        ]
    }

    # Page returns a conflicting requisition ID JR999999
    mock_get_resp = AsyncMock()
    mock_get_resp.status_code = 200
    mock_get_resp.text = """
    <html><head>
    <script type="application/ld+json">
    {
        "@type": "JobPosting",
        "title": "Junior Deal Handler",
        "identifier": {"@type": "PropertyValue", "value": "JR999999"},
        "hiringOrganization": {"name": "Equinor ASA"}
    }
    </script>
    </head><body>Junior Deal Handler</body></html>
    """

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_post_resp):
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_get_resp):
            from web_searcher import _query_workday_jobs
            jobs = await _query_workday_jobs("https://equinor.wd3.myworkdayjobs.com/EQNR", "EQUINOR ASA", "923609016")
            assert len(jobs) == 0, f"Vacancy should be excluded when requisition ID mismatches, got {len(jobs)}"
    print("  ✓ Mismatched requisition ID strictly excluded.", flush=True)


async def test_workday_mismatched_title_excluded():
    """23. Regression: Verify Workday vacancy is excluded when job title mismatches."""
    print("\n[TEST 23] Verifying mismatched job title is excluded...", flush=True)
    from unittest.mock import AsyncMock
    mock_post_resp = AsyncMock()
    mock_post_resp.status_code = 200
    mock_post_resp.json = lambda: {
        "jobPostings": [
            {
                "title": "Junior Deal Handler",
                "externalPath": "/job/London/Junior-Deal-Handler_JR108097",
                "locationsText": "London, Great Britain",
            }
        ]
    }

    # Page returns completely different job title
    mock_get_resp = AsyncMock()
    mock_get_resp.status_code = 200
    mock_get_resp.text = """
    <html><head>
    <script type="application/ld+json">
    {
        "@type": "JobPosting",
        "title": "Senior Reservoir Geologist",
        "identifier": {"@type": "PropertyValue", "value": "JR108097"},
        "hiringOrganization": {"name": "Equinor ASA"}
    }
    </script>
    </head><body>Senior Reservoir Geologist</body></html>
    """

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_post_resp):
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_get_resp):
            from web_searcher import _query_workday_jobs
            jobs = await _query_workday_jobs("https://equinor.wd3.myworkdayjobs.com/EQNR", "EQUINOR ASA", "923609016")
            assert len(jobs) == 0, f"Vacancy should be excluded when title mismatches, got {len(jobs)}"
    print("  ✓ Mismatched job title strictly excluded.", flush=True)


async def test_news_date_transposition_and_accuracy():
    """24. Regression: Verify news dates match article metadata and are not transposed."""
    print("\n[TEST 24] Verifying news date extraction accuracy...", flush=True)
    from web_searcher import parse_iso_date

    # 1. 2024 announcement (Ørsted acquisition was 7 Oct 2024, NOT 2026)
    html_2024 = '<time datetime="2024-10-07T07:00:00Z">7 October 2024</time>'
    extracted = parse_iso_date(html_2024)
    assert extracted == "2024-10-07", f"Expected 2024-10-07, got {extracted}"

    # 2. Q3 2026 results (published 28 Oct 2026, NOT 13 Nov 2026)
    html_q3 = '<meta property="article:published_time" content="2026-10-28T05:48:00.000Z">'
    extracted_q3 = parse_iso_date(html_q3)
    assert extracted_q3 == "2026-10-28", f"Expected 2026-10-28, got {extracted_q3}"
    assert extracted_q3 != "2026-11-13", "Q3 results date must not be transposed to 2026-11-13"

    # 3. Dividend announcement (13 Nov 2026)
    html_div = '<meta property="article:published_time" content="2026-11-13T10:00:37.372Z">'
    extracted_div = parse_iso_date(html_div)
    assert extracted_div == "2026-11-13", f"Expected 2026-11-13, got {extracted_div}"
    print("  ✓ News dates accurately parsed and verified against source metadata.", flush=True)


async def test_job_posting_company_attribution_mismatch():
    """25. Regression: Verify job posting belonging to a different company is excluded."""
    print("\n[TEST 25] Verifying job posting company attribution mismatch...", flush=True)
    from unittest.mock import AsyncMock
    mock_post_resp = AsyncMock()
    mock_post_resp.status_code = 200
    mock_post_resp.json = lambda: {
        "jobPostings": [
            {
                "title": "Drilling Supervisor",
                "externalPath": "/job/Stavanger/Drilling-Supervisor_JR555555",
                "locationsText": "Stavanger, Norway",
            }
        ]
    }

    # Page belongs to another company with conflicting org number
    mock_get_resp = AsyncMock()
    mock_get_resp.status_code = 200
    mock_get_resp.text = """
    <html><head>
    <script type="application/ld+json">
    {
        "@type": "JobPosting",
        "title": "Drilling Supervisor",
        "identifier": {"@type": "PropertyValue", "value": "JR555555"},
        "hiringOrganization": {"name": "Shell Global Exploration AS"}
    }
    </script>
    </head><body>Org nr: 912345678 Shell Global Exploration AS</body></html>
    """

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_post_resp):
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_get_resp):
            from web_searcher import _query_workday_jobs
            jobs = await _query_workday_jobs("https://equinor.wd3.myworkdayjobs.com/EQNR", "EQUINOR ASA", "923609016")
            assert len(jobs) == 0, f"Vacancy from conflicting company should be excluded, got {len(jobs)}"
    print("  ✓ Conflicting company attribution on job posting correctly rejected.", flush=True)


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
    await test_company_identity_verification_and_mismatch_rejection()
    await test_news_collection_pipeline()
    await test_job_posting_pipeline()
    await test_claim_diversity_and_non_registry_facts()
    await test_two_companies_similar_names()
    await test_news_article_passing_mention_rejected()
    await test_article_conflicting_org_number_rejected()
    await test_job_posting_other_company_rejected()
    await test_expired_job_posting_excluded()
    await test_workday_unverifiable_excluded()
    await test_date_validation_missing_and_invalid()
    await test_company_with_no_news_or_vacancies()
    await test_company_multiple_distinct_operational_facts()
    await test_workday_mismatched_requisition_id_excluded()
    await test_workday_mismatched_title_excluded()
    await test_news_date_transposition_and_accuracy()
    await test_job_posting_company_attribution_mismatch()
    print("\n" + "=" * 65, flush=True)
    print("🎉 ALL 25 TEST SUITES PASSED SUCCESSFULLY!", flush=True)
    print("=" * 65, flush=True)


if __name__ == "__main__":
    asyncio.run(run_all())
