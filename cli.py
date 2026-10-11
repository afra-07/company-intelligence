"""
Signalpost — CLI tool
=====================
Quick command-line interface for the agent.

Usage:
    python cli.py 974760843
    python cli.py 974760843 --refresh
    python cli.py 974760843 --json
    python cli.py 974760843 --evidence
"""

import asyncio
import argparse
import json
import sys

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from agent import run_pipeline
from models import CompanyProfile


def fmt_nok(val):
    if val is None:
        return "—"
    abs_v = abs(val)
    sign = "-" if val < 0 else ""
    if abs_v >= 1e9:
        return f"{sign}NOK {abs_v/1e9:.1f}B"
    if abs_v >= 1e6:
        return f"{sign}NOK {abs_v/1e6:.1f}M"
    if abs_v >= 1e3:
        return f"{sign}NOK {abs_v/1e3:.0f}K"
    return f"{sign}NOK {abs_v:.0f}"


def print_profile(result):
    if not result.success:
        print(f"\n❌ Error: {result.error}\n")
        return

    p: CompanyProfile = result.profile
    steps_str = " → ".join(result.steps_executed)

    print()
    print("╔" + "═" * 62 + "╗")
    print(f"║  {'SIGNALPOST COMPANY PROFILE':^58}  ║")
    print("╠" + "═" * 62 + "╣")

    def row(label, value):
        val_str = str(value) if value is not None else "—"
        print(f"║  {label:<20} {val_str[:38]:<38}  ║")

    row("Org Number", p.organization_number)
    row("Company Name", p.company_name)
    row("Legal Form", p.legal_form)
    row("Status", p.status)
    row("Registered", p.registered_date)
    print("╠" + "─" * 62 + "╣")
    row("Industry", p.industry_description)
    row("Sector", p.sector)
    row("Address", p.address)
    row("Municipality", p.municipality)
    row("Website", p.website)
    row("Email", p.email)
    row("Phone", p.phone)
    print("╠" + "─" * 62 + "╣")
    row("Employees", f"{p.employees:,}" if p.employees else None)
    row("Revenue", fmt_nok(p.revenue))
    row("Net Income", fmt_nok(p.net_income))
    row("Total Assets", fmt_nok(p.total_assets))
    row("Financial Year", p.financial_year)
    print("╠" + "─" * 62 + "╣")
    row("CEO", p.ceo)
    if p.board_members:
        for i, bm in enumerate(p.board_members[:3]):
            row(f"Board [{i+1}]", bm)
    print("╠" + "─" * 62 + "╣")
    row("Confidence", f"{p.confidence_score*100:.0f}%")
    row("Duration", f"{result.duration_seconds}s")
    row("Sources", len(p.sources_used))
    print("╠" + "─" * 62 + "╣")
    print(f"║  Pipeline: {steps_str[:50]:<50}  ║")
    print("╚" + "═" * 62 + "╝")

    if p.description:
        print(f"\n  📝 {p.description}\n")

    print(f"  Sources: {', '.join(p.sources_used) if p.sources_used else '—'}\n")


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
  python cli.py 974760843
  python cli.py 923609016 --refresh
  python cli.py 923609016 --json
  python cli.py 974760843 --evidence
        """,
    )
    parser.add_argument("org_number", help="9-digit Norwegian organisation number")
    parser.add_argument("--refresh", action="store_true", help="Force re-fetch even if cached")
    parser.add_argument("--json", action="store_true", help="Output raw JSON profile")
    parser.add_argument("--evidence", action="store_true", help="Show evidence trail")
    args = parser.parse_args()

    org = args.org_number.strip().replace(" ", "")
    if not org.isdigit() or len(org) != 9:
        print("❌ Organisation number must be exactly 9 digits.")
        return

    print(f"🔍 Looking up: {org}…")
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
