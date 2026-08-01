"""
FastAPI application entry point.

Run with:
    uvicorn main:app --reload --port 8000

Lifecycle:
  - The model loads lazily on the first POST /generate request, so the
    server starts immediately without waiting for weights to download.
  - For a production setup, uncomment llm_service.load_model() inside
    the lifespan hook to pre-warm the model at startup.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import generate as generate_router
from app.api.routes import experiments as experiments_router
from app.api.routes import debug as debug_router
from app.console_safe import _safe_console_print, configure_app_console_logging
from app.core.config import settings
from app.services.grammar_diagnostics import log_grammar_diagnostics, run_grammar_diagnostics
from app.services.llm_service import llm_service

# Project-owned StreamHandlers: escape unsupported glyphs on Windows consoles.
configure_app_console_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle hook."""
    configure_app_console_logging()
    diag = run_grammar_diagnostics(build_mask_store=False)
    log_grammar_diagnostics(diag)

    _safe_console_print(
        f"[Startup] {settings.app_name} ready (model loads lazily on first request)."
    )
    yield
    _safe_console_print("[Shutdown] Cleaning up.")


app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    description=(
        "Research API for visualizing token-level LLM generation. "
        "Real TinyLlama generation with per-step probability logging."
    ),
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# CORS — allow the Next.js dev server and any configured origins
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
app.include_router(generate_router.router, tags=["Generation"])
app.include_router(experiments_router.router, tags=["Experiments"])
app.include_router(debug_router.router, tags=["Debug"])

# Backward-compatible alias: some clients proxy to /api/generate on the backend.
app.include_router(generate_router.router, prefix="/api", tags=["Generation"])


@app.get("/health", tags=["Meta"])
async def health():
    return {
        "status": "ok",
        "app": settings.app_name,
        "model": settings.model_name,
        "model_loaded": llm_service.is_loaded,
    }
