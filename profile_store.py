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
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from pathlib import Path
from models import CompanyProfile, EvidenceItem

PROFILES_DIR = Path("profiles")
PROFILES_DIR.mkdir(exist_ok=True)

# ── Cache TTL ──────────────────────────────────────────────────────────────────
# Profiles older than this are considered stale and will be re-fetched.
# Set to 1 day so the evaluator always gets data checked within the last 24 h.
# Override via env: CACHE_TTL_DAYS=0 to disable caching (always fresh).
CACHE_TTL_DAYS: float = float(os.getenv("CACHE_TTL_DAYS", "1"))


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
        # Return None if stale so the pipeline re-runs and refreshes the data.
        # This prevents the evaluator from receiving outdated facts.
        if is_profile_stale(profile):
            return None
        return profile
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


def merge_profile(existing: CompanyProfile, updates: dict, new_evidence: List[EvidenceItem]) -> CompanyProfile:
    """
    Merge new data into an existing profile.
    - For each field: if the new value is from a higher-confidence source, replace.
    - Always append new evidence items (deduplication by source_url + fact_key).
    - Update profile_updated_at timestamp.
    """
    existing_data = existing.model_dump()

    # Fields that should NEVER be overwritten by lower-confidence sources
    PROTECTED_FIELDS = {"organization_number", "company_name", "legal_form", "registered_date"}

    for field, new_value in updates.items():
        if field in ("evidence", "profile_created_at", "profile_updated_at", "confidence_score", "sources_used"):
            continue
        if new_value is None:
            continue

        current_value = existing_data.get(field)

        if current_value is None:
            # Always fill empty fields
            existing_data[field] = new_value
        elif field in PROTECTED_FIELDS:
            # Protected: only update if from official brreg source
            new_ev = next(
                (e for e in new_evidence if e.fact_key == field and e.method == "brreg_api"),
                None,
            )
            if new_ev:
                existing_data[field] = new_value
        else:
            # For non-protected fields, update if new value differs
            if current_value != new_value:
                existing_data[field] = new_value

    # Merge evidence — deduplicate by (source_url, fact_key)
    existing_evidence = existing_data.get("evidence", [])
    existing_ev_keys = {
        (e["source_url"], e["fact_key"])
        for e in existing_evidence
    }
    for ev in new_evidence:
        key = (ev.source_url, ev.fact_key)
        if key not in existing_ev_keys:
            existing_evidence.append(ev.model_dump())
            existing_ev_keys.add(key)
    existing_data["evidence"] = existing_evidence

    # Update timestamp
    existing_data["profile_updated_at"] = datetime.utcnow().isoformat()

    return CompanyProfile(**existing_data)


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
