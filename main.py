"""
Signalpost Company Intelligence Agent
=====================================
FastAPI backend — REST API for the agent pipeline.
"""

from __future__ import annotations
import asyncio
from typing import List, Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import os

load_dotenv()

from agent import run_pipeline, run_batch
from profile_store import (
    load_profile, list_profiles, delete_profile, get_profile_count
)
from models import CompanyProfile, AgentRunResult

app = FastAPI(
    title="Signalpost Company Intelligence Agent",
    description="Automated Norwegian company profiling agent",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.middleware("http")
async def add_no_cache_headers(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# ─── Request/Response schemas ─────────────────────────────────────────────────

class ProfileRequest(BaseModel):
    organization_number: str
    force_refresh: bool = False


class BatchRequest(BaseModel):
    organization_numbers: List[str]
    concurrency: int = 5


# ─── Single company endpoint ──────────────────────────────────────────────────

@app.get("/profile/{org_number}", response_model=CompanyProfile)
async def get_profile(org_number: str, refresh: bool = False):
    """
    Get or build a company profile for the given Norwegian org number.
    If cached and refresh=False, returns cached version instantly.
    """
    org_number = org_number.strip().replace(" ", "")
    if not org_number.isdigit() or len(org_number) != 9:
        raise HTTPException(status_code=400, detail="Organization number must be exactly 9 digits")

    result = await run_pipeline(org_number, force_refresh=refresh)
    if not result.success:
        raise HTTPException(status_code=404, detail=result.error or "Company not found")
    return result.profile


@app.post("/profile", response_model=AgentRunResult)
async def create_profile(req: ProfileRequest):
    """
    Full agent run — returns AgentRunResult with pipeline steps and timing.
    """
    org = req.organization_number.strip().replace(" ", "")
    if not org.isdigit() or len(org) != 9:
        raise HTTPException(status_code=400, detail="Organization number must be exactly 9 digits")

    result = await run_pipeline(org, force_refresh=req.force_refresh)
    return result


# ─── Batch endpoint ───────────────────────────────────────────────────────────

@app.post("/batch", response_model=List[AgentRunResult])
async def batch_profiles(req: BatchRequest):
    """
    Run the agent for multiple companies in parallel.
    Max 50 per call. Use concurrency parameter to throttle.
    """
    if len(req.organization_numbers) > 50:
        raise HTTPException(status_code=400, detail="Max 50 per batch call")
    results = await run_batch(req.organization_numbers, concurrency=min(req.concurrency, 10))
    return results


# ─── Profile management endpoints ─────────────────────────────────────────────

@app.get("/profiles", response_model=List[str])
async def list_all_profiles(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
):
    """List all cached org numbers with pagination."""
    all_orgs = list_profiles()
    return all_orgs[skip : skip + limit]


@app.get("/stats")
async def get_stats():
    """Return system stats."""
    return {
        "total_profiles": get_profile_count(),
        "profile_list_sample": list_profiles()[:5],
    }


@app.delete("/profile/{org_number}")
async def remove_profile(org_number: str):
    """Delete a cached profile (will be rebuilt on next request)."""
    deleted = delete_profile(org_number)
    if not deleted:
        raise HTTPException(status_code=404, detail="Profile not found")
    return {"deleted": True, "organization_number": org_number}


# ─── Serve the web UI ─────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def serve_ui():
    return FileResponse("static/index.html")

@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    from fastapi.responses import Response
    return Response(status_code=204)


# ─── Health check ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "profiles_cached": get_profile_count(),
        "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
        "serpapi_configured": bool(os.getenv("SERPAPI_KEY")),
    }


@app.on_event("startup")
async def startup_banner():
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("\n" + "═" * 64, flush=True)
    print("  🚀 Signalpost Company Intelligence Dashboard is LIVE!", flush=True)
    print("  👉 Click or open in your browser: http://localhost:8000", flush=True)
    print("═" * 64 + "\n", flush=True)


if __name__ == "__main__":
    import uvicorn
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print("\n" + "═" * 64, flush=True)
    print("  🚀 Starting Signalpost Company Intelligence Dashboard...", flush=True)
    print("  👉 Open in your browser: http://localhost:8000", flush=True)
    print("═" * 64 + "\n", flush=True)
    uvicorn.run(app, host="localhost", port=8000)
