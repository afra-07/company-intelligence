"""
Signalpost — Official evaluation runner
========================================
Reads a list of Norwegian org numbers from a file (one per line) and runs
the agent pipeline on each one, writing a JSONL result line per company.
Every output line is validated against OUTPUT_CONTRACT.md before being written.

Usage:
    python run.py <input_file> [options]

    python run.py org_numbers.txt
    python run.py org_numbers.txt --output results.jsonl
    python run.py org_numbers.txt --concurrency 5 --refresh

Arguments:
    input_file          Path to a text file with one 9-digit org number per line.
                        Lines that are blank or start with '#' are ignored.

Options:
    --output PATH       Where to write JSONL output  [default: results.jsonl]
    --concurrency N     Max parallel pipeline workers [default: 5]
    --refresh           Force re-fetch even if a cached profile exists
    --no-validate       Skip OUTPUT_CONTRACT checks (useful for debugging)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure UTF-8 output on Windows consoles
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from agent import run_batch, run_pipeline
from models import (
    AgentRunResult,
    CompanyProfile,
    ProfileIdentity,
    ProfileContact,
    ProfileBusiness,
    ProfileFinancials,
    ProfilePeople,
    ProfileMeta,
)


# ─── Contract validation ──────────────────────────────────────────────────────

CONTRACT_PATH = Path(__file__).parent / "OUTPUT_CONTRACT.md"

VALID_STATUSES = {"Active", "Bankrupt", "Under liquidation", "Forced liquidation"}
VALID_METHODS  = {"brreg_api", "regnskaps_api", "serp_search", "webpage_scrape", "llm_extract"}


def validate_result(result: AgentRunResult) -> List[str]:
    """
    Validate a single AgentRunResult against OUTPUT_CONTRACT.md.
    Enforces the 6 nested sections: identity, contact, business, financials, people, meta.
    Returns a (possibly empty) list of violation strings.
    """
    violations: List[str] = []
    org = str(result.organization_number or "")

    # ── Top-level required fields ─────────────────────────────────────────────
    if not org:
        violations.append("organization_number is empty")

    if result.duration_seconds < 0:
        violations.append(f"duration_seconds {result.duration_seconds} is negative")

    if not result.steps_executed:
        violations.append("steps_executed is empty")

    # ── Failure result validation ─────────────────────────────────────────────
    if not result.success:
        if result.profile is not None:
            violations.append("success=false but profile is not None")
        if not result.error:
            violations.append("success=false but error message is empty")
        return violations

    # ── Success result validation ─────────────────────────────────────────────
    if result.profile is None:
        violations.append("success=true but profile is None")
        return violations

    if result.error is not None:
        violations.append(f"success=true but error is not None: '{result.error}'")

    if not re.fullmatch(r"\d{9}", org):
        violations.append(f"organization_number '{org}' is not exactly 9 digits on successful result")

    p: CompanyProfile = result.profile

    # ── Verify exact 6 nested sections in serialized profile ─────────────────
    profile_dump = p.model_dump()
    expected_sections = {"identity", "contact", "business", "financials", "people", "meta"}
    actual_sections = set(profile_dump.keys())
    if actual_sections != expected_sections:
        violations.append(
            f"profile structure mismatch: expected sections {sorted(expected_sections)}, "
            f"got {sorted(actual_sections)}"
        )

    # ── 1. identity ───────────────────────────────────────────────────────────
    if not p.identity.company_name:
        violations.append("profile.identity.company_name is missing")

    if p.identity.status not in VALID_STATUSES:
        violations.append(f"profile.identity.status '{p.identity.status}' is not one of {VALID_STATUSES}")

    if p.identity.organization_number != org:
        violations.append(
            f"profile.identity.organization_number '{p.identity.organization_number}' "
            f"does not match record organization_number '{org}'"
        )

    # ── 2. contact ────────────────────────────────────────────────────────────
    if not isinstance(p.contact, ProfileContact):
        violations.append("profile.contact is not a valid ProfileContact section")

    # ── 3. business ───────────────────────────────────────────────────────────
    if not isinstance(p.business, ProfileBusiness):
        violations.append("profile.business is not a valid ProfileBusiness section")

    # ── 4. financials ─────────────────────────────────────────────────────────
    if not isinstance(p.financials, ProfileFinancials):
        violations.append("profile.financials is not a valid ProfileFinancials section")

    # ── 5. people ─────────────────────────────────────────────────────────────
    if not isinstance(p.people, ProfilePeople):
        violations.append("profile.people is not a valid ProfilePeople section")

    # ── 6. meta ───────────────────────────────────────────────────────────────
    if not isinstance(p.meta, ProfileMeta):
        violations.append("profile.meta is not a valid ProfileMeta section")

    if not p.meta.profile_created_at:
        violations.append("profile.meta.profile_created_at is missing")
    if not p.meta.profile_updated_at:
        violations.append("profile.meta.profile_updated_at is missing")

    if not (0.0 <= p.meta.confidence_score <= 1.0):
        violations.append(f"profile.meta.confidence_score {p.meta.confidence_score} is out of [0, 1]")

    if not p.meta.sources_used:
        violations.append("profile.meta.sources_used is empty — at least Brreg must be listed")

    # ── Evidence validation ───────────────────────────────────────────────────
    if not p.meta.evidence:
        violations.append("profile.meta.evidence is empty — every profile needs ≥ 1 evidence item")

    for i, ev in enumerate(p.meta.evidence):
        prefix = f"evidence[{i}] (fact_key={ev.fact_key!r})"

        if not ev.fact_key:
            violations.append(f"{prefix}: fact_key is empty")
        if ev.fact_value is None or ev.fact_value == "":
            violations.append(f"{prefix}: fact_value is empty")
        if not ev.source_name:
            violations.append(f"{prefix}: source_name is empty")
        if not ev.source_url:
            violations.append(f"{prefix}: source_url is empty")
        if not ev.date_checked:
            violations.append(f"{prefix}: date_checked is empty")
        if not (0.0 <= ev.confidence <= 1.0):
            violations.append(f"{prefix}: confidence {ev.confidence} is out of [0, 1]")
        if ev.method not in VALID_METHODS:
            violations.append(f"{prefix}: method '{ev.method}' not in {VALID_METHODS}")

        # Citation rule: Proff.no is permitted ONLY for employees and internal _proff_raw keys.
        # Never attribute Proff-extracted facts to a company's website.
        if "proff.no" in ev.source_url.lower():
            if ev.fact_key not in ("employees", "_proff_raw"):
                violations.append(
                    f"{prefix}: source_url cites proff.no ('{ev.source_url}') for fact '{ev.fact_key}'; "
                    "proff.no is permitted only for employees and internal _proff_raw keys"
                )

        # Citation rule: Search engines must not be cited as article source URLs
        if any(engine in ev.source_url.lower() for engine in ["google.", "bing.", "yahoo."]):
            violations.append(
                f"{prefix}: source_url cites a search engine ('{ev.source_url}'); "
                "facts must cite the actual article URL or official register"
            )

    return violations


# ─── File loading ─────────────────────────────────────────────────────────────

def load_org_numbers(path: Path) -> List[str]:
    """
    Read org numbers from a text file.
    Accepts one org number per line; ignores blank lines and # comments.
    Preserves all company inputs (including invalid numbers) so a proper
    failure output record is generated for every input line.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    orgs: List[str] = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        orgs.append(line)
    return orgs


# ─── Main runner ──────────────────────────────────────────────────────────────

async def run(
    input_path: Path,
    output_path: Path,
    concurrency: int,
    force_refresh: bool,
    validate: bool,
) -> None:
    org_numbers = load_org_numbers(input_path)
    if not org_numbers:
        print("❌ No valid org numbers found in input file.", file=sys.stderr)
        sys.exit(1)

    print(f"📋 Loaded {len(org_numbers)} org numbers from {input_path}", flush=True)
    print(f"📝 Writing output to {output_path} (preserving input order)", flush=True)
    if validate:
        print("✅ Contract validation enabled (OUTPUT_CONTRACT.md)", flush=True)
    print()

    total_ok = 0
    total_err = 0
    total_violations = 0

    # Run batch with controlled concurrency and exception isolation
    results = await run_batch(
        org_numbers,
        concurrency=concurrency,
        force_refresh=force_refresh,
    )

    # Write results in strictly preserved input order
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as out_f:
        for result in results:
            # Validate against contract
            if validate:
                violations = validate_result(result)
                if violations:
                    total_violations += len(violations)
                    for v in violations:
                        print(f"  ⚠️  CONTRACT VIOLATION [{result.organization_number}]: {v}", flush=True)

            # Serialize and write
            line = result.model_dump_json()
            out_f.write(line + "\n")

            if result.success:
                total_ok += 1
            else:
                total_err += 1
                print(f"  ✗ [{result.organization_number}] {result.error}", flush=True)

    print()
    print(f"✅ Done. {total_ok} succeeded, {total_err} failed.")
    if validate:
        if total_violations:
            print(f"⚠️  {total_violations} contract violation(s) detected — see above.")
        else:
            print("✅ All output lines satisfy OUTPUT_CONTRACT.md.")
    print(f"📦 Results written to {output_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Signalpost — official evaluation runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run.py org_numbers.txt
  python run.py org_numbers.txt --output results.jsonl
  python run.py /path/to/eval_set.txt --concurrency 8 --refresh
        """,
    )
    parser.add_argument(
        "input_file",
        type=Path,
        help="Text file with one 9-digit Norwegian org number per line",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results.jsonl"),
        metavar="PATH",
        help="Output JSONL file path (default: results.jsonl)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        metavar="N",
        help="Max parallel pipeline workers (default: 5)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Force re-fetch even if a cached profile exists",
    )
    parser.add_argument(
        "--no-validate",
        action="store_true",
        help="Skip OUTPUT_CONTRACT validation",
    )
    args = parser.parse_args()

    if not args.input_file.exists():
        print(f"❌ Input file not found: {args.input_file}", file=sys.stderr)
        sys.exit(1)

    asyncio.run(
        run(
            input_path=args.input_file,
            output_path=args.output,
            concurrency=args.concurrency,
            force_refresh=args.refresh,
            validate=not args.no_validate,
        )
    )


if __name__ == "__main__":
    main()

