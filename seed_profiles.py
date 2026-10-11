"""
Signalpost — Batch seeder
=========================
Downloads a list of active Norwegian org numbers from Brreg's open data
and runs the agent pipeline on all of them.

Usage:
    python seed_profiles.py --count 1000 --concurrency 8
    python seed_profiles.py --from-file orgs.txt --concurrency 5
"""

import asyncio
import argparse
import httpx
import json
import sys
from pathlib import Path

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from agent import run_batch
from profile_store import get_profile_count, list_profiles

BRREG_DOWNLOAD = (
    "https://data.brreg.no/enhetsregisteret/api/enheter"
    "?konkurs=false&underAvvikling=false&size={size}&page={page}"
    "&sort=organisasjonsnummer,asc"
)


async def fetch_org_numbers_from_brreg(count: int) -> list[str]:
    """
    Fetch `count` active Norwegian org numbers from the Brreg API.
    Paginates up to 10,000 results (Brreg limit per query).
    """
    org_numbers = []
    page_size = min(count, 100)
    page = 0

    print(f"📡 Fetching {count} org numbers from Brreg…", flush=True)
    async with httpx.AsyncClient(timeout=30) as client:
        while len(org_numbers) < count:
            url = BRREG_DOWNLOAD.format(size=page_size, page=page)
            resp = await client.get(url, headers={"Accept": "application/json"})
            if resp.status_code != 200:
                print(f"  ⚠️  Brreg returned {resp.status_code} on page {page}", flush=True)
                break
            data = resp.json()
            embedded = data.get("_embedded", {}).get("enheter", [])
            if not embedded:
                break
            for enhet in embedded:
                org = enhet.get("organisasjonsnummer")
                if org:
                    org_numbers.append(org)
            page += 1
            print(f"  Page {page}: got {len(embedded)} → total {len(org_numbers)}", flush=True)
            if len(embedded) < page_size:
                break  # last page

    return org_numbers[:count]


async def seed(count: int, concurrency: int, from_file: str | None):
    already_done = set(list_profiles())
    print(f"ℹ️  {len(already_done)} profiles already cached — skipping those.")

    if from_file:
        orgs = Path(from_file).read_text().splitlines()
        orgs = [o.strip() for o in orgs if o.strip().isdigit() and len(o.strip()) == 9]
    else:
        orgs = await fetch_org_numbers_from_brreg(count + len(already_done))

    # Skip already profiled
    orgs = [o for o in orgs if o not in already_done][:count]

    if not orgs:
        print("✅ Nothing new to process.")
        return

    print(f"\n🚀 Running pipeline for {len(orgs)} companies (concurrency={concurrency})…\n")

    # Process in chunks to show progress
    chunk_size = concurrency * 4
    total_ok = 0
    total_err = 0

    for i in range(0, len(orgs), chunk_size):
        chunk = orgs[i : i + chunk_size]
        results = await run_batch(chunk, concurrency=concurrency)
        ok = sum(1 for r in results if r.success)
        err = sum(1 for r in results if not r.success)
        total_ok += ok
        total_err += err
        done = i + len(chunk)
        pct = done / len(orgs) * 100
        print(
            f"  [{done:>4}/{len(orgs)}] {pct:5.1f}% — "
            f"✓ {total_ok} ok  ✗ {total_err} failed  "
            f"(total cached: {get_profile_count()})",
            flush=True
        )

    print(f"\n✅ Done! {total_ok} profiles created, {total_err} errors.")
    print(f"📦 Total profiles in store: {get_profile_count()}")


def main():
    parser = argparse.ArgumentParser(description="Signalpost Batch Seeder")
    parser.add_argument("--count", type=int, default=1000,
                        help="Number of company profiles to generate (default: 1000)")
    parser.add_argument("--concurrency", type=int, default=5,
                        help="Parallel pipeline workers (default: 5)")
    parser.add_argument("--from-file", type=str, default=None,
                        help="Read org numbers from a text file (one per line)")
    args = parser.parse_args()
    asyncio.run(seed(args.count, args.concurrency, args.from_file))


if __name__ == "__main__":
    main()
