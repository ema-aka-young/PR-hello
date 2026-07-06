import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from github_client import fetch_dashboard_data

app = FastAPI(title="PR Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

_cache: dict = {}
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "120"))

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")


@app.get("/api/dashboard")
async def dashboard():
    now = time.time()
    if _cache.get("data") and now - _cache.get("ts", 0) < CACHE_TTL:
        return _cache["data"]

    try:
        data = await fetch_dashboard_data()
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 403:
            return JSONResponse(
                {"error": "GitHub API rate limit exceeded. Set GITHUB_TOKEN in your environment."},
                status_code=502,
            )
        return JSONResponse({"error": f"GitHub API returned {status}"}, status_code=502)
    _cache["data"] = data
    _cache["ts"] = now
    return data


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/")
async def serve_index():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


if os.path.isdir(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
