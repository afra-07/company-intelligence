import math
import pytest
from models import EvidenceItem
from agent import _clean_and_dedupe_evidence


def make_item(
    fact_key="company_name",
    fact_value="Test Corp",
    source_name="Test Source",
    source_url="https://example.com/news/article-1",
    confidence=0.9,
    method="webpage_scrape",
):
    return EvidenceItem(
        fact_key=fact_key,
        fact_value=fact_value,
        source_name=source_name,
        source_url=source_url,
        date_checked="2026-11-19",
        confidence=confidence,
        method=method,
    )


def test_search_engine_urls_rejected():
    engines = [
        "https://www.google.com/search?q=test",
        "https://google.no/search?q=test",
        "https://sub.google.com/results",
        "https://www.bing.com/search?q=test",
        "https://cn.bing.com/search",
        "https://search.yahoo.com/search?p=test",
        "https://duckduckgo.com/?q=test",
        "https://search.brave.com/search?q=test",
        "https://www.ecosia.org/search?q=test",
        "https://www.baidu.com/s?wd=test",
        "https://yandex.com/search/?text=test",
    ]
    for url in engines:
        ev = make_item(source_url=url)
        cleaned = _clean_and_dedupe_evidence([ev])
        assert len(cleaned) == 0, f"Expected {url} to be rejected"


def test_legitimate_news_and_register_urls_accepted():
    legit_urls = [
        "https://e24.no/boers-og-finans/i/example-article?utm_source=twitter&utm_medium=social",
        "https://www.dn.no/marked/article-slug?ref=rss",
        "https://company.no/news/press-release-1",
        "https://data.brreg.no/enhetsregisteret/api/enheter/123456789",
        "https://data.brreg.no/regnskapsregisteret/regnskap/123456789",
    ]
    for url in legit_urls:
        ev = make_item(source_url=url)
        cleaned = _clean_and_dedupe_evidence([ev])
        assert len(cleaned) == 1, f"Expected {url} to be accepted"


def test_invalid_and_missing_urls_rejected():
    invalid_urls = [
        "",
        "not_a_url",
        "ftp://example.com/file.txt",
        "file:///C:/test.txt",
        "http://",
        "https://",
        "https:///path/only",
        "://missing-scheme",
    ]
    for url in invalid_urls:
        ev = make_item(source_url="https://valid.com")
        # Bypass pydantic validation for testing raw/edge inputs
        object.__setattr__(ev, "source_url", url)
        cleaned = _clean_and_dedupe_evidence([ev])
        assert len(cleaned) == 0, f"Expected invalid url '{url}' to be rejected"

    # None URL
    ev_none = make_item(source_url="https://valid.com")
    object.__setattr__(ev_none, "source_url", None)
    assert len(_clean_and_dedupe_evidence([ev_none])) == 0


def test_confidence_validation():
    # Invalid confidence: below 0, above 1, NaN, inf, -inf, None, non-numeric
    invalid_confs = [-0.1, -1.0, 1.01, 2.0, float("nan"), float("inf"), float("-inf"), None, "0.9", True, False]
    for conf in invalid_confs:
        ev = make_item()
        object.__setattr__(ev, "confidence", conf)
        cleaned = _clean_and_dedupe_evidence([ev])
        assert len(cleaned) == 0, f"Expected confidence {conf} to be rejected"

    # Valid confidence: 0.0, 1.0, and between
    for conf in [0.0, 0.5, 0.95, 1.0]:
        ev = make_item(confidence=conf)
        cleaned = _clean_and_dedupe_evidence([ev])
        assert len(cleaned) == 1, f"Expected confidence {conf} to be accepted"
        assert cleaned[0].confidence == conf


def test_proff_exceptions():
    # Proff for employees should be allowed (main domain and subdomains)
    ev_emp = make_item(fact_key="employees", fact_value=42, source_url="https://proff.no/selskap/test")
    assert len(_clean_and_dedupe_evidence([ev_emp])) == 1

    ev_emp_sub = make_item(fact_key="employees", fact_value=42, source_url="https://sub.proff.no/selskap/test")
    assert len(_clean_and_dedupe_evidence([ev_emp_sub])) == 1

    # Proff for _proff_raw should be allowed
    ev_raw = make_item(fact_key="_proff_raw", fact_value={"raw": 1}, source_url="https://www.proff.no/selskap/test")
    assert len(_clean_and_dedupe_evidence([ev_raw])) == 1

    # Proff for published facts (e.g. company_name, revenue) must be rejected on proff.no and subdomains
    ev_name = make_item(fact_key="company_name", fact_value="Test Corp", source_url="https://proff.no/selskap/test")
    assert len(_clean_and_dedupe_evidence([ev_name])) == 0

    ev_name_sub = make_item(fact_key="company_name", fact_value="Test Corp", source_url="https://beta.proff.no/selskap/test")
    assert len(_clean_and_dedupe_evidence([ev_name_sub])) == 0

    ev_rev = make_item(fact_key="revenue", fact_value=1000000, source_url="https://proff.no/selskap/test")
    assert len(_clean_and_dedupe_evidence([ev_rev])) == 0

    # Unrelated domains containing 'proff.no' as a substring must NOT be rejected
    ev_unrelated_proff = make_item(fact_key="company_name", fact_value="Test Corp", source_url="https://notproff.no/news")
    assert len(_clean_and_dedupe_evidence([ev_unrelated_proff])) == 1

    ev_unrelated_proff2 = make_item(fact_key="company_name", fact_value="Test Corp", source_url="https://myproff.norway.com/news")
    assert len(_clean_and_dedupe_evidence([ev_unrelated_proff2])) == 1


def test_unrelated_domains_with_search_engine_substrings_accepted():
    unrelated_urls = [
        "https://notgoogle.com/news/story",
        "https://mybravecompany.com/about",
        "https://yahoocompany.org/press",
        "https://bingo-games.net/info",
        "https://duckduckgostore.com/products",
        "https://ecosialovers.org/mission",
        "https://baidublog.com/post",
        "https://yandexfoodfan.com/reviews",
    ]
    for url in unrelated_urls:
        ev = make_item(source_url=url)
        cleaned = _clean_and_dedupe_evidence([ev])
        assert len(cleaned) == 1, f"Expected unrelated domain '{url}' to be accepted"


def test_duplicate_evidence_remains_deduplicated():
    ev1 = make_item(fact_key="company_name", fact_value="Corp", source_url="https://example.com/a")
    ev2 = make_item(fact_key="company_name", fact_value="Corp", source_url="https://example.com/a")
    ev3 = make_item(fact_key="company_name", fact_value="Corp Different", source_url="https://example.com/a")
    ev4 = make_item(fact_key="company_name", fact_value="Corp", source_url="https://example.com/b")

    cleaned = _clean_and_dedupe_evidence([ev1, ev2, ev3, ev4])
    assert len(cleaned) == 3
