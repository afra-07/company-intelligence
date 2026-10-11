"""
Signalpost — CLI tool
=====================
Command-line interface for the Norwegian Company Intelligence Agent.
Displays sourced profiles, missing-data explanations, and evidence trails.

Usage:
    python cli.py 974760843
    python cli.py 923609016 --refresh
    python cli.py 923609016 --json
    python cli.py 974760843 --evidence
    python cli.py            (interactive mode)
"""

import asyncio
import argparse
import json
import re
import sys
import textwrap

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from agent import run_pipeline
from models import CompanyProfile

GOVT_FORM_CODES = {'ORGL', 'STAT', 'KF', 'FKF', 'IKS', 'SF', 'SÆR', 'SAER', 'FLI', 'FYLK', 'KOMM'}


def is_government_entity(p: CompanyProfile) -> bool:
    if not p:
        return False
    lf = (p.legal_form or "").upper()
    sector = (p.sector or "").upper()
    m = re.search(r"\(([^)]+)\)", lf)
    if m and m.group(1).upper() in GOVT_FORM_CODES:
        return True
    if any(k in lf for k in ("ORGANISASJONSLEDD", "STATEN", "KOMMUNE", "FYLKESKOMMUNE")):
        return True
    if any(k in sector for k in ("OFFENTLIG", "STATLIG", "KOMMUNAL")):
        return True
    return False


def is_sole_proprietorship(p: CompanyProfile) -> bool:
    if not p:
        return False
    lf = (p.legal_form or "").upper()
    return "ENK" in lf or "ENKELTPERSONFORETAK" in lf


def fmt_nok(val):
    if val is None:
        return None
    abs_v = abs(val)
    sign = "-" if val < 0 else ""
    if abs_v >= 1e9:
        return f"{sign}NOK {abs_v/1e9:.1f}B"
    if abs_v >= 1e6:
        return f"{sign}NOK {abs_v/1e6:.1f}M"
    if abs_v >= 1e3:
        return f"{sign}NOK {abs_v/1e3:.0f}K"
    return f"{sign}NOK {abs_v:,.0f}"


def print_profile(result):
    if not result.success:
        print(f"\n❌ Error: {result.error}\n")
        return

    p: CompanyProfile = result.profile
    steps_str = " → ".join(result.steps_executed)

    is_govt = is_government_entity(p)
    is_enk = is_sole_proprietorship(p)
    no_financials = p.revenue is None and p.net_income is None and p.total_assets is None

    fin_note = "—"
    notice_title = ""
    notice_body = ""

    if no_financials:
        if is_govt:
            fin_note = "— (Public Sector Exempt)"
            notice_title = "🏛️  PUBLIC SECTOR / GOVERNMENT ENTITY — FINANCIALS EXEMPT"
            notice_body = (
                "Financial data (revenue, income, assets) is not available because public-sector "
                "organisations in Norway (e.g. Riksrevisjonen, ministries, courts, and municipalities) "
                "operate on parliamentary budget allocations and are legally exempt from filing commercial "
                "accounts with Regnskapsregisteret."
            )
        elif is_enk:
            fin_note = "— (ENK Exempt from Filing)"
            notice_title = "👤  SOLE PROPRIETORSHIP (ENK) — NO PUBLIC ACCOUNTS"
            notice_body = (
                "Sole proprietorships (ENK) in Norway are legally exempt from filing annual financial "
                "statements with Regnskapsregisteret unless their turnover exceeds statutory auditing thresholds."
            )
        elif p.financials_status == "no_data":
            fin_note = "— (Unparsed IFRS Format)"
            notice_title = "⚠️  SPECIALIZED NON-STANDARD ACCOUNTING STRUCTURE"
            notice_body = (
                "Annual accounts were found in Regnskapsregisteret, but the filing uses a specialized "
                "banking/insurance format that could not be mapped to standard operating figures."
            )
        else:
            fin_note = "— (Not in Regnskapsregisteret)"
            notice_title = "📋  ANNUAL ACCOUNTS NOT IN REGNSKAPSREGISTERET API"
            notice_body = (
                f"No annual accounts were returned by the Brreg API for #{p.organization_number}. "
                "Commercial banks, large listed corporations (filing via EU ESAP / XBRL portals), "
                "and holding entities often do not have standard open electronic statements here."
            )

    print()
    print("╔" + "═" * 68 + "╗")
    print(f"║  {'SIGNALPOST COMPANY PROFILE':^64}  ║")
    print("╠" + "═" * 68 + "╣")

    def row(label, value):
        val_str = str(value) if value is not None else "—"
        print(f"║  {label:<18} {val_str[:46]:<46}  ║")

    row("Org Number", f"#{p.organization_number}")
    row("Company Name", p.company_name)
    row("Legal Form", p.legal_form)
    row("Status", p.status)
    row("Registered", p.registered_date)
    print("╠" + "─" * 68 + "╣")
    row("Industry", p.industry_description or p.industry_code)
    row("Sector", p.sector)
    row("Address", f"{p.address or '—'} ({p.municipality or '—'})")
    row("Website", p.website)
    row("Email", p.email)
    row("Phone", p.phone)
    print("╠" + "─" * 68 + "╣")
    row("Employees", f"{p.employees:,}" if p.employees is not None else "— (Not in Aa-registeret)")
    row("Revenue", fmt_nok(p.revenue) if p.revenue is not None else fin_note)
    row("Net Income", fmt_nok(p.net_income) if p.net_income is not None else fin_note)
    row("Total Assets", fmt_nok(p.total_assets) if p.total_assets is not None else fin_note)
    row("Financial Year", f"FY {p.financial_year}" if p.financial_year else "—")
    print("╠" + "─" * 68 + "╣")
    row("CEO", p.ceo if p.ceo else "— (Not listed in Brreg)")
    if p.board_members:
        for i, bm in enumerate(p.board_members[:3]):
            row(f"Board [{i+1}]", bm)
    print("╠" + "─" * 68 + "╣")
    row("Confidence", f"{p.confidence_score*100:.0f}%")
    row("Duration", f"{result.duration_seconds}s")
    row("Sources Used", len(p.sources_used))
    print("╠" + "─" * 68 + "╣")
    print(f"║  Pipeline: {steps_str[:54]:<54}  ║")
    print("╚" + "═" * 68 + "╝")

    if notice_title:
        print()
        print("  ┌" + "─" * 66 + "┐")
        print(f"  │  {notice_title:<62}│")
        print("  ├" + "─" * 66 + "┤")
        for line in textwrap.wrap(notice_body, width=62):
            print(f"  │  {line:<62}│")
        print("  └" + "─" * 66 + "┘")

    if p.description:
        print(f"\n  📝 Description: {p.description}")

    print(f"\n  🔗 Sources: {', '.join(p.sources_used) if p.sources_used else '—'}\n")


def print_evidence(result):
    if not result.success or not result.profile:
        return
    p = result.profile
    print(f"\n📋 Evidence Trail ({len(p.evidence)} items):\n")
    for ev in p.evidence:
        if ev.fact_key.startswith("_"):
            continue
        print(f"  [{ev.method}] {ev.fact_key}: {str(ev.fact_value)[:60]}")
        print(f"    Source: {ev.source_name} ({ev.date_checked}) | Confidence: {ev.confidence*100:.0f}%")
        print(f"    URL: {ev.source_url[:80]}")
        print()


async def main():
    parser = argparse.ArgumentParser(
        description="Signalpost Company Intelligence CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cli.py 974760843              # Riksrevisjonen (Government)
  python cli.py 923609016              # Equinor ASA (Commercial)
  python cli.py 984851006              # DNB Bank ASA (Bank)
  python cli.py 923609016 --refresh    # Force refresh cache
  python cli.py 923609016 --json       # Output JSON
  python cli.py 974760843 --evidence   # Show full evidence trail
        """,
    )
    parser.add_argument("org_number", nargs="?", default=None,
                        help="9-digit Norwegian organisation number (optional: interactive if omitted)")
    parser.add_argument("--refresh", action="store_true", help="Force re-fetch even if cached")
    parser.add_argument("--json", action="store_true", help="Output raw JSON profile")
    parser.add_argument("--evidence", action="store_true", help="Show evidence trail")
    args = parser.parse_args()

    org = args.org_number
    if not org:
        print("=" * 60)
        print("🇳🇴  Signalpost Company Intelligence CLI")
        print("=" * 60)
        try:
            org = input("Enter 9-digit Norwegian organisation number (e.g. 974760843): ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExited.")
            return

    org = org.strip().replace(" ", "")
    if not org.isdigit() or len(org) != 9:
        print("❌ Organisation number must be exactly 9 digits.")
        return

    print(f"\n🔍 Looking up organisation #{org}…")
    result = await run_pipeline(org, force_refresh=args.refresh)

    if args.json:
        if result.profile:
            print(result.profile.model_dump_json(indent=2))
        else:
            print(json.dumps({"error": result.error}, indent=2))
    else:
        print_profile(result)
        if args.evidence:
            print_evidence(result)


if __name__ == "__main__":
    asyncio.run(main())
