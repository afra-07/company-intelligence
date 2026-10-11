"""
Signalpost Company Intelligence Agent
=====================================
Source 1: Brønnøysundregistrene (Brreg) — Official Norwegian Business Registry
Free, keyless REST API. Returns authoritative identity + address + industry data.
"""

from __future__ import annotations
import httpx
from datetime import date
from typing import Optional, Dict, Any
from models import EvidenceItem


BRREG_BASE = "https://data.brreg.no/enhetsregisteret/api"
REGNSKAP_BASE = "https://data.brreg.no/regnskapsregisteret/regnskap"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "SignalpostAgent/1.0 (hackathon; contact@example.com)",
}

TODAY = date.today().isoformat()


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _format_address(addr: Dict[str, Any]) -> Optional[str]:
    if not addr:
        return None
    parts = addr.get("adresse", []) + [
        addr.get("postnummer", ""),
        addr.get("poststed", ""),
        addr.get("land", ""),
    ]
    return ", ".join(p for p in parts if p)


def _make_evidence(fact_key: str, fact_value: Any, confidence: float = 1.0) -> EvidenceItem:
    return EvidenceItem(
        fact_key=fact_key,
        fact_value=fact_value,
        source_name="Brønnøysundregistrene (Brreg)",
        source_url=f"{BRREG_BASE}/enheter",
        date_checked=TODAY,
        confidence=confidence,
        method="brreg_api",
    )


# ─── Main fetch ───────────────────────────────────────────────────────────────

async def fetch_brreg_entity(org_number: str) -> Dict[str, Any]:
    """
    Fetch the main entity record from Enhetsregisteret.
    Returns a dict with parsed fields + a list of EvidenceItem objects.
    Falls back to underenheter if not found in main register.
    """
    url = f"{BRREG_BASE}/enheter/{org_number}"
    evidence: list[EvidenceItem] = []
    result: Dict[str, Any] = {}

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=HEADERS)

        if resp.status_code == 404:
            # Try sub-units (underenheter)
            url = f"{BRREG_BASE}/underenheter/{org_number}"
            resp = await client.get(url, headers=HEADERS)

        if resp.status_code != 200:
            return {"error": f"Brreg returned {resp.status_code}", "evidence": []}

        data = resp.json()

    # ── Parse fields ──────────────────────────────────────────────────────────
    name = data.get("navn")
    if name:
        result["company_name"] = name
        evidence.append(_make_evidence("company_name", name))

    # Legal form
    org_form = data.get("organisasjonsform", {})
    legal_form_desc = org_form.get("beskrivelse")
    legal_form_code = org_form.get("kode")
    if legal_form_desc:
        result["legal_form"] = f"{legal_form_desc} ({legal_form_code})"
        evidence.append(_make_evidence("legal_form", result["legal_form"]))

    # Status
    konkurs = data.get("konkurs", False)
    avvikling = data.get("underAvvikling", False)
    tvangs = data.get("underTvangsavviklingEllerTvangsopplosning", False)
    if konkurs:
        result["status"] = "Bankrupt"
    elif tvangs:
        result["status"] = "Forced liquidation"
    elif avvikling:
        result["status"] = "Under liquidation"
    else:
        result["status"] = "Active"
    evidence.append(_make_evidence("status", result["status"]))

    # Registration date
    reg_date = data.get("registreringsdatoEnhetsregisteret")
    if reg_date:
        result["registered_date"] = reg_date
        evidence.append(_make_evidence("registered_date", reg_date))

    # Address
    forr_addr = data.get("forretningsadresse", {})
    post_addr = data.get("postadresse", {})
    if forr_addr:
        result["address"] = _format_address(forr_addr)
        result["municipality"] = forr_addr.get("kommune")
        evidence.append(_make_evidence("address", result["address"]))
        if result["municipality"]:
            evidence.append(_make_evidence("municipality", result["municipality"]))
    if post_addr:
        result["postal_address"] = _format_address(post_addr)
        evidence.append(_make_evidence("postal_address", result["postal_address"]))

    # Contact
    website = data.get("hjemmeside")
    if website:
        # normalize
        if not website.startswith("http"):
            website = "https://" + website.rstrip("/")
        result["website"] = website
        evidence.append(_make_evidence("website", website))

    email = data.get("epostadresse")
    if email:
        result["email"] = email
        evidence.append(_make_evidence("email", email))

    phone = data.get("telefon")
    if phone:
        result["phone"] = phone
        evidence.append(_make_evidence("phone", phone))

    # Industry
    nk1 = data.get("naeringskode1", {})
    if nk1:
        result["industry_code"] = nk1.get("kode")
        result["industry_description"] = nk1.get("beskrivelse")
        evidence.append(_make_evidence("industry_code", result["industry_code"]))
        evidence.append(_make_evidence("industry_description", result["industry_description"]))

    # Employees
    employees = data.get("antallAnsatte")
    if employees is not None:
        result["employees"] = employees
        evidence.append(_make_evidence("employees", employees))

    # Sector
    sector = data.get("institusjonellSektorkode", {}).get("beskrivelse")
    if sector:
        result["sector"] = sector
        evidence.append(_make_evidence("sector", sector))

    result["evidence"] = evidence
    result["_raw"] = data
    return result


async def fetch_brreg_roles(org_number: str) -> Dict[str, Any]:
    """
    Fetch management roles (styret, daglig leder) from Brreg.
    The roles endpoint returns board members and CEO.
    """
    url = f"{BRREG_BASE}/enheter/{org_number}/roller"
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=HEADERS)
        if resp.status_code != 200:
            return {"evidence": []}
        data = resp.json()

    evidence: list[EvidenceItem] = []
    result: Dict[str, Any] = {}
    board: list[str] = []
    ceo: Optional[str] = None

    for role_group in data.get("rollegrupper", []):
        role_code = role_group.get("type", {}).get("kode", "")
        for role in role_group.get("roller", []):
            person = role.get("person", {})
            navn = person.get("navn", {})
            if not navn:
                enhet = role.get("enhet", {})
                raw = enhet.get("navn", "Unknown")
                # navn can be a list of name parts in some responses
                if isinstance(raw, list):
                    full_name = " ".join(str(x) for x in raw if x)
                else:
                    full_name = str(raw)
            else:
                if isinstance(navn, dict):
                    fornavn = navn.get("fornavn", "")
                    etternavn = navn.get("etternavn", "")
                    full_name = f"{fornavn} {etternavn}".strip()
                else:
                    full_name = str(navn)

            if role_code == "DAGL":  # Daglig leder = CEO
                ceo = full_name
            else:
                board.append(f"{full_name} ({role_group.get('type', {}).get('beskrivelse', role_code)})")

    if ceo:
        result["ceo"] = ceo
        evidence.append(EvidenceItem(
            fact_key="ceo",
            fact_value=ceo,
            source_name="Brønnøysundregistrene — Roller",
            source_url=url,
            date_checked=TODAY,
            confidence=1.0,
            method="brreg_api",
        ))
    if board:
        result["board_members"] = board
        evidence.append(EvidenceItem(
            fact_key="board_members",
            fact_value=board,
            source_name="Brønnøysundregistrene — Roller",
            source_url=url,
            date_checked=TODAY,
            confidence=1.0,
            method="brreg_api",
        ))

    result["evidence"] = evidence
    return result


async def fetch_regnskap(org_number: str) -> Dict[str, Any]:
    """
    Fetch financial statements from Regnskapsregisteret.
    Returns the most recent year's key financial figures.
    """
    url = f"{REGNSKAP_BASE}/{org_number}"
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url, headers=HEADERS)
            if resp.status_code != 200:
                return {"evidence": [], "financials_status": "not_filed"}
            data = resp.json()
    except Exception:
        return {"evidence": [], "financials_status": "not_filed"}

    evidence: list[EvidenceItem] = []
    result: Dict[str, Any] = {}
    records = data if isinstance(data, list) else []

    if not records:
        return {"evidence": [], "financials_status": "not_filed"}

    # Sort by year descending and pick latest
    records_sorted = sorted(records, key=lambda r: r.get("regnskapsperiode", {}).get("fraDato", ""), reverse=True)
    latest = records_sorted[0]

    year_str = latest.get("regnskapsperiode", {}).get("fraDato", "")
    year = int(year_str[:4]) if year_str else None
    if year:
        result["financial_year"] = year

    rs = latest.get("resultatregnskapResultat", {})
    source_url = f"https://data.brreg.no/regnskapsregisteret/regnskap/{org_number}"

    def _add_financial(key: str, value: Any, label: str):
        if value is not None:
            result[key] = float(value)
            evidence.append(EvidenceItem(
                fact_key=key,
                fact_value=value,
                source_name=f"Regnskapsregisteret ({year})",
                source_url=source_url,
                date_checked=TODAY,
                confidence=0.95,
                method="regnskaps_api",
            ))

    def _first(*vals):
        """Return first non-None value."""
        for v in vals:
            if v is not None:
                return v
        return None

    # ── Standard NGAAP (most companies) ──────────────────────────────────────
    driftsresultat = rs.get("driftsresultat", {})
    standard_revenue = driftsresultat.get("driftsinntekter", {}).get("sumDriftsinntekter")
    standard_profit  = driftsresultat.get("driftsresultat")
    standard_net     = rs.get("aarsresultat")

    # ── Bank / financial institution format (IFRS / bankVirksomhet) ──────────
    bank_section = rs.get("bankVirksomhet") or rs.get("finansiellVirksomhet") or {}
    bank_revenue  = bank_section.get("sumInntekter") or bank_section.get("renteinntekterOgLignendeInntekter")
    bank_net      = bank_section.get("aarsresultat")

    # Top-level fallbacks for holding/insurance companies
    top_revenue = rs.get("sumDriftsinntekter") or rs.get("totalInntekter")

    _add_financial("revenue",          _first(standard_revenue, bank_revenue, top_revenue), "Operating revenue")
    _add_financial("operating_profit", standard_profit, "Operating profit")
    _add_financial("net_income",       _first(standard_net, bank_net), "Net income")

    eiendeler = latest.get("eiendeler", {})
    bal_gjeld = latest.get("egenkapitalGjeld", {})
    bank_assets = latest.get("sumEiendeler") or latest.get("forvaltningskapital")

    _add_financial("total_assets", _first(eiendeler.get("sumEiendeler"), bank_assets), "Total assets")
    _add_financial("equity", bal_gjeld.get("egenkapital", {}).get("sumEgenkapital"), "Equity")

    # Set status: did we actually extract any financial values?
    has_data = any(k in result for k in ("revenue", "net_income", "total_assets", "equity"))
    result["financials_status"] = "found" if has_data else "no_data"

    result["evidence"] = evidence
    return result
