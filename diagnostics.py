# diagnostics.py
"""Fail-safe diagnostics module for the numbers-clean scraper.

Provides a ContextVar-based store for per-pipeline diagnostics and a small set of
helpers that never raise exceptions.
"""

import json
import os
import threading
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

DIAG_CTX: ContextVar[Optional[Dict[str, Any]]] = ContextVar("DIAG_CTX", default=None)


def new_diag(org_number: str) -> Tuple[Dict[str, Any], Any]:
    """Return a new diagnostic dict and token, and install it in DIAG_CTX."""
    try:
        diag: Dict[str, Any] = {
            "org_number": org_number,
            "started_at": datetime.utcnow().isoformat(),
            "counters": {
                "website_attempted": 0,
                "website_successful": 0,
                "news_attempted": 0,
                "news_successful": 0,
                "jobs_homepage_attempted": 0,
                "jobs_homepage_successful": 0,
                "job_pages_attempted": 0,
                "job_pages_successful": 0,
                "workday_api_attempted": 0,
                "workday_api_successful": 0,
                "workday_job_get_attempted": 0,
                "workday_job_get_successful": 0,
                "successful_extraction": 0,
                "article_links_discovered": 0,
                "news_items_extracted": 0,
                "job_postings_extracted": 0,
                "workday_listings_returned": 0,
                "jobs_homepage_failed": 0,
                "news_links_attempted": 0,
                "news_fetch_failed": 0,
                "job_links_attempted": 0,
                "career_links_discovered": 0,
                "expired_postings_detected": 0,
            },
            "flags": {
                "invalid_input": False,
                "cache_hit": False,
                "company_not_found": False,
                "pipeline_error": False,
                "website_identity_rejected": False,
                "no_news_links": False,
                "no_job_links": False,
                "workday_empty_listings": False,
            },
            "requests": [],
            "final_reason": None,
        }
        token = DIAG_CTX.set(diag)
        return diag, token
    except Exception:
        return {}, None


_ATTEMPT_MAP = {
    "website": ("website_attempted", "website_successful"),
    "news": ("news_attempted", "news_successful"),
    "jobs_homepage": ("jobs_homepage_attempted", "jobs_homepage_successful"),
    "job_page": ("job_pages_attempted", "job_pages_successful"),
    "workday_api": ("workday_api_attempted", "workday_api_successful"),
    "workday_job_get": ("workday_job_get_attempted", "workday_job_get_successful"),
}


def _current_diag() -> Optional[Dict[str, Any]]:
    return DIAG_CTX.get()


def start_request(kind: str, url: str, method: str = "GET") -> None:
    try:
        diag = _current_diag()
        if not diag:
            return
        attempt_counter = _ATTEMPT_MAP.get(kind, (None, None))[0]
        if attempt_counter:
            diag["counters"][attempt_counter] += 1
        diag["requests"].append({
            "kind": kind,
            "url": url,
            "method": method,
            "started_at": datetime.utcnow().isoformat(),
            "ended_at": None,
            "status": None,
            "exception": None,
            "rejection": None,
        })
    except Exception:
        pass


def end_request(
    kind: str,
    url: str,
    status: Optional[int] = None,
    exception: Optional[Exception] = None,
    rejection: Optional[bool] = None,
) -> None:
    try:
        diag = _current_diag()
        if not diag:
            return
        for rec in reversed(diag["requests"]):
            if rec["kind"] == kind and rec["url"] == url:
                rec["ended_at"] = datetime.utcnow().isoformat()
                rec["status"] = status
                rec["exception"] = str(exception) if exception else None
                rec["rejection"] = rejection
                break
        success_counter = _ATTEMPT_MAP.get(kind, (None, None))[1]
        if success_counter and status == 200:
            diag["counters"][success_counter] += 1
    except Exception:
        pass


def final_reason(diag: Dict[str, Any]) -> str:
    try:
        c = diag.get("counters", {})
        f = diag.get("flags", {})

        # 1. Successful evidence extraction is always highest priority.
        if c.get("successful_extraction"):
            return "successful_extraction"

        # 2. Early-return / error flags set directly by the pipeline.
        if f.get("invalid_input"):
            return "invalid_input"
        if f.get("cache_hit"):
            return "cache_hit"
        if f.get("company_not_found"):
            return "company_not_found"
        if f.get("pipeline_error"):
            return "pipeline_error"
        if f.get("website_identity_rejected"):
            return "website_identity_rejected"

        # 3. no_workday_jobs: only when the scraper explicitly confirmed that
        #    the Workday API response contained an empty jobPostings list
        #    (workday_empty_listings flag), the POST itself succeeded, no
        #    ordinary job page was attempted, and no evidence was extracted.
        if (
            f.get("workday_empty_listings")
            and c.get("workday_api_successful", 0) > 0
            and c.get("job_pages_attempted", 0) == 0
            and c.get("workday_job_get_attempted", 0) == 0
            and c.get("successful_extraction", 0) == 0
        ):
            return "no_workday_jobs"

        # 4. no_job_links: the jobs homepage was reached successfully but no job
        #    links of any kind were discovered or attempted.  A failed or
        #    unattempted homepage does NOT qualify.
        if (
            c.get("jobs_homepage_successful", 0) > 0
            and c.get("career_links_discovered", 0) == 0
            and c.get("job_links_attempted", 0) == 0
            and c.get("job_pages_attempted", 0) == 0
            and c.get("workday_api_attempted", 0) == 0
            and c.get("workday_job_get_attempted", 0) == 0
        ):
            return "no_job_links"

        return "unknown"
    except Exception:
        return "unknown"


_STORE: List[Dict[str, Any]] = []
_STORE_LOCK = threading.Lock()


def store(diag: Dict[str, Any]) -> None:
    try:
        if not diag:
            return
        with _STORE_LOCK:
            _STORE.append(diag)
    except Exception:
        pass


def dump_all(path: str) -> None:
    """Write all stored diagnostic records to *path* in JSONL format.

    _STORE_LOCK is held for the entire operation so two concurrent callers
    cannot write the same records twice.  Records are cleared from _STORE only
    after the write succeeds; a failed write leaves them intact for retry.
    Errors never propagate to the caller.
    """
    try:
        with _STORE_LOCK:
            if not _STORE:
                return

            parent = os.path.dirname(path)
            if parent:
                os.makedirs(parent, exist_ok=True)

            lines = "".join(json.dumps(rec, default=str) + "\n" for rec in _STORE)
            with open(path, "a", encoding="utf-8") as f:
                f.write(lines)

            # Clear only after the write succeeds.
            _STORE.clear()
    except Exception:
        # Preserve _STORE unchanged so the caller can retry later.
        pass
