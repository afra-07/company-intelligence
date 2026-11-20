"""
Signalpost Company Intelligence Agent
=====================================
Storage layer — JSON-file-based profile store with update/merge logic.
Profiles are stored in ./profiles/<org_number>.json
"""

from __future__ import annotations
import json
import os
import threading
from datetime import datetime, timezone, timedelta, date
from typing import Optional, List, Any
from pathlib import Path
# Timezone resolution for Norway (Europe/Oslo):
# Python 3.9+ zoneinfo accesses the system tz database or the official 'tzdata' package.
# Both winter time (CET, UTC+1) and summer daylight saving time (CEST, UTC+2) are observed.
try:
    import zoneinfo
    try:
        NORWAY_TZ = zoneinfo.ZoneInfo("Europe/Oslo")
    except Exception:
        # If system database missing, attempt importing tzdata directly to populate zoneinfo
        import tzdata  # type: ignore # noqa: F401
        NORWAY_TZ = zoneinfo.ZoneInfo("Europe/Oslo")
except Exception:
    NORWAY_TZ = None
from models import CompanyProfile, EvidenceItem

PROFILES_DIR = Path("profiles")
PROFILES_DIR.mkdir(exist_ok=True)

# ── Cache TTL ──────────────────────────────────────────────────────────────────
# Profiles older than this are considered stale and will be re-fetched.
# Set to 1 day so the evaluator always gets data checked within the last 24 h.
# Override via env: CACHE_TTL_DAYS=0 to disable caching (always fresh).
CACHE_TTL_DAYS: float = float(os.getenv("CACHE_TTL_DAYS", "1"))


def _get_norway_timezone(dt_utc: Optional[datetime] = None) -> timezone:
    """
    Return the appropriate timezone object for Norway at dt_utc.
    If zoneinfo is available, returns ZoneInfo('Europe/Oslo').
    Otherwise calculates standard Central European Time (CET, UTC+1) or
    Daylight Saving Time (CEST, UTC+2) based on European Union/Norwegian DST rules:
      - Starts: last Sunday of March at 01:00 UTC (advances to UTC+2)
      - Ends: last Sunday of October at 01:00 UTC (returns to UTC+1)
    """
    if NORWAY_TZ:
        return NORWAY_TZ

    now_utc = dt_utc or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    year = now_utc.year
    # Last Sunday of March (01:00 UTC)
    march_31 = datetime(year, 3, 31, 1, 0, tzinfo=timezone.utc)
    dst_start = march_31 - timedelta(days=(march_31.weekday() + 1) % 7)
    # Last Sunday of October (01:00 UTC)
    oct_31 = datetime(year, 10, 31, 1, 0, tzinfo=timezone.utc)
    dst_end = oct_31 - timedelta(days=(oct_31.weekday() + 1) % 7)

    offset_hours = 2 if dst_start <= now_utc < dst_end else 1
    return timezone(timedelta(hours=offset_hours))


def _get_norway_today() -> str:
    """Return today's date in Norwegian local time (Europe/Oslo) formatted as YYYY-MM-DD."""
    tz = _get_norway_timezone()
    return datetime.now(tz).date().isoformat()


def is_job_expired(ev_data: Any, today_str: Optional[str] = None) -> bool:
    """
    Check if a job_posting evidence item is expired.
    - A deadline earlier than today is expired (< today).
    - A deadline equal to today is NOT expired (matches today).
    - If deadline is missing or unparseable, it is NOT treated as expired.
    - Validates deadline using Python's actual date/datetime parsing.
    """
    if isinstance(ev_data, EvidenceItem):
        val = ev_data.fact_value
        key = ev_data.fact_key
    elif isinstance(ev_data, dict):
        val = ev_data.get("fact_value")
        key = ev_data.get("fact_key")
    else:
        return False

    if key != "job_posting" or not isinstance(val, dict):
        return False

    deadline_raw = val.get("deadline")
    if not deadline_raw or not isinstance(deadline_raw, str):
        return False

    deadline_clean = deadline_raw.strip()
    deadline_date_obj: Optional[date] = None

    # Validate entire string: first check if it is an exact ISO date (YYYY-MM-DD)
    try:
        deadline_date_obj = date.fromisoformat(deadline_clean)
    except Exception:
        # Otherwise check if it is a valid full ISO datetime
        try:
            deadline_date_obj = datetime.fromisoformat(deadline_clean).date()
        except Exception:
            return False

    if deadline_date_obj is None:
        return False

    # Validate today_date_obj: if today_str is explicitly provided, validate it strictly.
    # If today_str is invalid, safely treat as unexpired (return False).
    if today_str is not None:
        try:
            today_date_obj = date.fromisoformat(today_str.strip())
        except Exception:
            try:
                today_date_obj = datetime.fromisoformat(today_str.strip()).date()
            except Exception:
                return False
    else:
        try:
            today_date_obj = date.fromisoformat(_get_norway_today())
        except Exception:
            return False

    return deadline_date_obj < today_date_obj


def filter_expired_jobs(profile: CompanyProfile) -> CompanyProfile:
    """Filter out any expired job_posting evidence from a profile."""
    today_str = _get_norway_today()
    active_ev = [ev for ev in profile.meta.evidence if not is_job_expired(ev, today_str)]
    if len(active_ev) != len(profile.meta.evidence):
        profile.meta.evidence = active_ev
    return profile


def is_profile_stale(profile: CompanyProfile) -> bool:
    """Return True if the profile is older than CACHE_TTL_DAYS and should be refreshed."""
    if CACHE_TTL_DAYS <= 0:
        return True  # Caching disabled — always re-fetch
    try:
        updated = datetime.fromisoformat(profile.profile_updated_at)
        # Make timezone-aware if naive
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - updated
        return age > timedelta(days=CACHE_TTL_DAYS)
    except Exception:
        return True  # Unparseable timestamp → treat as stale


# ── Request counter (thread-safe, per-process) ─────────────────────────────────
_request_lock = threading.Lock()
_request_counts: dict = {
    "brreg_http": 0,
    "regnskap_http": 0,
    "proff_http": 0,
    "website_http": 0,
    "serpapi_http": 0,
    "openai_llm": 0,
    "other_http": 0,
}


def increment_request(category: str, n: int = 1) -> None:
    """Increment the outbound request counter for a given category."""
    with _request_lock:
        _request_counts[category] = _request_counts.get(category, 0) + n


def get_request_counts() -> dict:
    """Return a snapshot of all outbound request counts."""
    with _request_lock:
        total = sum(_request_counts.values())
        return {**_request_counts, "total": total}


def _profile_path(org_number: str) -> Path:
    return PROFILES_DIR / f"{org_number}.json"


def load_profile(org_number: str) -> Optional[CompanyProfile]:
    """Load an existing profile from disk, or return None if missing or stale."""
    path = _profile_path(org_number)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        profile = CompanyProfile(**data)
        if is_profile_stale(profile):
            return None
        return filter_expired_jobs(profile)
    except Exception:
        return None


def load_profile_any_age(org_number: str) -> Optional[CompanyProfile]:
    """Load a profile regardless of age (used for merge base, not for serving)."""
    path = _profile_path(org_number)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return CompanyProfile(**data)
    except Exception:
        return None


def save_profile(profile: CompanyProfile) -> None:
    """Persist a profile to disk."""
    path = _profile_path(profile.organization_number)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile.model_dump(), f, ensure_ascii=False, indent=2, default=str)


def _ev_fingerprint(ev_dict: dict) -> tuple:
    val = ev_dict.get("fact_value")
    if isinstance(val, dict):
        val_repr = str(val.get("headline") or val.get("title") or val)[:60]
    else:
        val_repr = str(val)[:60]
    return (ev_dict.get("fact_key"), ev_dict.get("source_url"), val_repr)


def merge_profile(existing: CompanyProfile, updates: dict, new_evidence: List[EvidenceItem]) -> CompanyProfile:
    """
    Merge new data into an existing profile.
    - Preserves all 6 nested sections (identity, contact, business, financials, people, meta).
    - Protects core identity fields from lower-confidence overwrites.
    - Merges evidence with smart deduplication across news, jobs, and diverse facts.
    - Updates profile_updated_at timestamp.
    """
    existing_flat = {
        "organization_number": existing.identity.organization_number,
        "company_name": existing.identity.company_name,
        "legal_form": existing.identity.legal_form,
        "status": existing.identity.status,
        "registered_date": existing.identity.registered_date,
        "address": existing.contact.address,
        "postal_address": existing.contact.postal_address,
        "municipality": existing.contact.municipality,
        "website": existing.contact.website,
        "email": existing.contact.email,
        "phone": existing.contact.phone,
        "industry_code": existing.business.industry_code,
        "industry_description": existing.business.industry_description,
        "employees": existing.business.employees,
        "sector": existing.business.sector,
        "revenue": existing.financials.revenue,
        "operating_profit": existing.financials.operating_profit,
        "net_income": existing.financials.net_income,
        "total_assets": existing.financials.total_assets,
        "equity": existing.financials.equity,
        "financial_year": existing.financials.financial_year,
        "financials_status": existing.financials.financials_status,
        "ceo": existing.people.ceo,
        "board_members": existing.people.board_members,
        "description": existing.people.description,
        "profile_created_at": existing.meta.profile_created_at,
        "profile_updated_at": existing.meta.profile_updated_at,
        "confidence_score": existing.meta.confidence_score,
        "sources_used": list(existing.meta.sources_used),
    }

    # Flatten updates if nested
    updates_flat: dict = {}
    if "identity" in updates and isinstance(updates["identity"], dict):
        for section in ("identity", "contact", "business", "financials", "people", "meta"):
            if section in updates and isinstance(updates[section], dict):
                updates_flat.update(updates[section])
    else:
        updates_flat = dict(updates)

    PROTECTED_FIELDS = {"organization_number", "company_name", "legal_form", "registered_date"}

    for field, new_value in updates_flat.items():
        if field in ("evidence", "profile_created_at", "profile_updated_at", "confidence_score", "sources_used"):
            continue
        if new_value is None:
            continue

        current_value = existing_flat.get(field)
        if current_value is None:
            existing_flat[field] = new_value
        elif field in PROTECTED_FIELDS:
            new_ev = next(
                (e for e in new_evidence if e.fact_key == field and e.method == "brreg_api"),
                None,
            )
            if new_ev:
                existing_flat[field] = new_value
        else:
            if current_value != new_value:
                existing_flat[field] = new_value

    # Merge evidence with smart fingerprint deduplication
    existing_evidence = [e.model_dump() for e in existing.meta.evidence]
    existing_fps = {_ev_fingerprint(e) for e in existing_evidence}
    for ev in new_evidence:
        ev_dict = ev.model_dump()
        fp = _ev_fingerprint(ev_dict)
        if fp not in existing_fps:
            existing_evidence.append(ev_dict)
            existing_fps.add(fp)

    # Filter out expired job postings
    today_norway = _get_norway_today()
    existing_evidence = [e for e in existing_evidence if not is_job_expired(e, today_norway)]

    existing_flat["evidence"] = existing_evidence
    existing_flat["profile_updated_at"] = datetime.utcnow().isoformat()

    return CompanyProfile(**existing_flat)


def list_profiles() -> List[str]:
    """Return all org numbers that have saved profiles."""
    return [p.stem for p in PROFILES_DIR.glob("*.json")]


def delete_profile(org_number: str) -> bool:
    path = _profile_path(org_number)
    if path.exists():
        path.unlink()
        return True
    return False


def get_profile_count() -> int:
    return len(list(PROFILES_DIR.glob("*.json")))
