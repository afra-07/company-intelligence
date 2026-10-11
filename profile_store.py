"""
Signalpost Company Intelligence Agent
=====================================
Storage layer — JSON-file-based profile store with update/merge logic.
Profiles are stored in ./profiles/<org_number>.json
"""

from __future__ import annotations
import json
import os
from datetime import datetime
from typing import Optional, List
from pathlib import Path
from models import CompanyProfile, EvidenceItem

PROFILES_DIR = Path("profiles")
PROFILES_DIR.mkdir(exist_ok=True)


def _profile_path(org_number: str) -> Path:
    return PROFILES_DIR / f"{org_number}.json"


def load_profile(org_number: str) -> Optional[CompanyProfile]:
    """Load an existing profile from disk, or return None."""
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
