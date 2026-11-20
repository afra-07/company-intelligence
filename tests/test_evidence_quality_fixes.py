import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from models import EvidenceItem, CompanyProfile
from web_searcher import extract_company_news
from profile_store import is_job_expired, filter_expired_jobs, merge_profile, _get_norway_today, load_profile
from tests.test_refresh_behavior import make_profile


@pytest.mark.asyncio
async def test_follow_us_and_utility_pages_rejected_as_news():
    """Verify that utility/navigation pages like /follow-us, /contact, /about are rejected."""
    # Mock scrape_page_soup returning a homepage with a link to /follow-us and /contact
    homepage_html = """
    <html><body>
        <a href="/en/news">News</a>
    </body></html>
    """
    news_hub_html = """
    <html><body>
        <a href="/en/follow-us">Follow us on social media for regular updates!</a>
        <a href="/en/contact-us">Get in touch with our media and press contacts</a>
        <a href="/en/news/press-release-1">Major Company Wins Landmark Architecture Contract</a>
    </body></html>
    """
    article_html = """
    <html><body>
        <h1>Major Company Wins Landmark Architecture Contract</h1>
        <time datetime="2026-10-15">15 October 2026</time>
        <p>The company has secured an extraordinary architectural award for urban sustainability design.</p>
    </body></html>
    """
    follow_us_html = """
    <html><body>
        <h1>Follow us!</h1>
        <p>Follow us on LinkedIn, Instagram and Facebook to stay up to date on all things happening.</p>
    </body></html>
    """
    contact_html = """
    <html><body>
        <h1>Contact us</h1>
        <p>Reach out to our offices across Scandinavia.</p>
    </body></html>
    """

    async def mock_scrape_page_soup(url, max_chars=12000):
        from bs4 import BeautifulSoup
        if url.endswith("/follow-us"):
            return follow_us_html, BeautifulSoup(follow_us_html, "lxml"), url
        elif url.endswith("/contact-us"):
            return contact_html, BeautifulSoup(contact_html, "lxml"), url
        elif url.endswith("/press-release-1"):
            return article_html, BeautifulSoup(article_html, "lxml"), url
        elif url.endswith("/news"):
            return news_hub_html, BeautifulSoup(news_hub_html, "lxml"), url
        else:
            return homepage_html, BeautifulSoup(homepage_html, "lxml"), url

    with patch("web_searcher.scrape_page_soup", side_effect=mock_scrape_page_soup):
        news = await extract_company_news("https://linkarkitektur.com", "LINK ARKITEKTUR AB", "811551422")
        headlines = [n["headline"] for n in news]
        urls = [n["url"] for n in news]

        # /follow-us and /contact-us must be rejected
        assert "Follow us!" not in headlines
        assert "Contact us" not in headlines
        assert not any("follow-us" in u for u in urls)
        assert not any("contact-us" in u for u in urls)

        # Genuine news article must be accepted
        assert "Major Company Wins Landmark Architecture Contract" in headlines
        assert any("press-release-1" in u for u in urls)


def test_job_expiry_date_logic():
    today = _get_norway_today()
    yesterday = (datetime.fromisoformat(today) - timedelta(days=1)).date().isoformat()
    tomorrow = (datetime.fromisoformat(today) + timedelta(days=1)).date().isoformat()

    def make_job_ev(deadline):
        return EvidenceItem(
            fact_key="job_posting",
            fact_value={"title": "Engineer", "deadline": deadline},
            source_name="Careers",
            source_url="https://example.com/job/1",
            date_checked=today,
            confidence=0.9,
            method="webpage_scrape",
        )

    # 1. Earlier than today -> expired
    ev_past = make_job_ev(yesterday)
    assert is_job_expired(ev_past, today) is True

    # 2. Equal to today -> NOT expired
    ev_today = make_job_ev(today)
    assert is_job_expired(ev_today, today) is False

    # 3. Future date -> NOT expired
    ev_future = make_job_ev(tomorrow)
    assert is_job_expired(ev_future, today) is False

    # 4. Missing / None / unparseable deadline -> NOT expired
    assert is_job_expired(make_job_ev(None), today) is False
    assert is_job_expired(make_job_ev(""), today) is False
    assert is_job_expired(make_job_ev("Open until filled"), today) is False
    assert is_job_expired(make_job_ev("2026-99-99"), today) is False
    assert is_job_expired(make_job_ev("not-a-date"), today) is False

    # Regression checks for malformed deadline and invalid today
    assert is_job_expired(make_job_ev("2000-01-01garbage"), today) is False
    assert is_job_expired(make_job_ev("2000-01-01"), "not-a-date") is False

    # ISO Datetime format support (YYYY-MM-DDTHH:MM:SS)
    ev_iso_datetime_past = make_job_ev(f"{yesterday}T23:59:59Z")
    assert is_job_expired(ev_iso_datetime_past, today) is True

    ev_iso_datetime_future = make_job_ev(f"{tomorrow}T12:00:00+01:00")
    assert is_job_expired(ev_iso_datetime_future, today) is False

    # 5. Non-job evidence -> NOT expired
    ev_news = EvidenceItem(
        fact_key="company_news",
        fact_value={"headline": "News", "deadline": yesterday},
        source_name="News",
        source_url="https://example.com/news",
        date_checked=today,
        confidence=0.9,
        method="webpage_scrape",
    )
    assert is_job_expired(ev_news, today) is False


def test_expired_jobs_removed_on_merge(tmp_path, monkeypatch):
    monkeypatch.setattr("profile_store.PROFILES_DIR", tmp_path / "profiles")
    (tmp_path / "profiles").mkdir()

    today = _get_norway_today()
    yesterday = (datetime.fromisoformat(today) - timedelta(days=1)).date().isoformat()
    tomorrow = (datetime.fromisoformat(today) + timedelta(days=1)).date().isoformat()

    expired_job = EvidenceItem(
        fact_key="job_posting",
        fact_value={"title": "Old Job", "deadline": yesterday, "company": "Co"},
        source_name="Careers",
        source_url="https://example.com/job/old",
        date_checked=today,
        confidence=0.9,
        method="webpage_scrape",
    )
    active_job = EvidenceItem(
        fact_key="job_posting",
        fact_value={"title": "Active Job", "deadline": tomorrow, "company": "Co"},
        source_name="Careers",
        source_url="https://example.com/job/active",
        date_checked=today,
        confidence=0.9,
        method="webpage_scrape",
    )

    existing_profile = make_profile("999999999", evidence=[expired_job, active_job])

    # Merge with empty or new evidence
    merged = merge_profile(existing_profile, {}, [])
    merged_jobs = [e for e in merged.meta.evidence if e.fact_key == "job_posting"]

    # Expired job must be gone, active job must remain
    assert len(merged_jobs) == 1
    assert merged_jobs[0].fact_value["title"] == "Active Job"


def test_cached_profile_does_not_return_expired_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr("profile_store.PROFILES_DIR", tmp_path / "profiles")
    (tmp_path / "profiles").mkdir()

    today = _get_norway_today()
    yesterday = (datetime.fromisoformat(today) - timedelta(days=1)).date().isoformat()

    expired_job = EvidenceItem(
        fact_key="job_posting",
        fact_value={"title": "Expired Job", "deadline": yesterday, "company": "Co"},
        source_name="Careers",
        source_url="https://example.com/job/expired",
        date_checked=today,
        confidence=0.9,
        method="webpage_scrape",
    )

    make_profile("888888888", evidence=[expired_job])

    # Load from cache via load_profile
    cached = load_profile("888888888")
    assert cached is not None
    job_evs = [e for e in cached.meta.evidence if e.fact_key == "job_posting"]
    assert len(job_evs) == 0


def test_norway_timezone_fallback(monkeypatch):
    """Verify that _get_norway_timezone correctly computes both winter (UTC+1) and summer (UTC+2) offsets."""
    import profile_store
    monkeypatch.setattr(profile_store, "NORWAY_TZ", None)

    winter_dt = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    summer_dt = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)

    winter_tz = profile_store._get_norway_timezone(winter_dt)
    summer_tz = profile_store._get_norway_timezone(summer_dt)

    # Winter is CET (UTC+1)
    assert winter_tz.utcoffset(winter_dt) == timedelta(hours=1)
    # Summer is CEST (UTC+2)
    assert summer_tz.utcoffset(summer_dt) == timedelta(hours=2)

    # General _get_norway_today returns valid ISO date string
    today = profile_store._get_norway_today()
    assert len(today) == 10
    assert datetime.fromisoformat(today) is not None
