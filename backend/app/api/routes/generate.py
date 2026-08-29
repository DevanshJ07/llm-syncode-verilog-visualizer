"""
Live generation HTTP routes.

POST /generate          — synchronous generate+save, lightweight ack (compat)
POST /generate/jobs     — immediate job create (browser live path)
GET  /generate/jobs/{id} — job status for polling
"""

from __future__ import annotations

import json
import logging
import re
import traceback

from fastapi import APIRouter, HTTPException, status

from app.models.schemas import (
    GenerateCreatedResponse,
    GenerateJobCreatedResponse,
    GenerateJobStatusResponse,
    GenerateRequest,
)
from app.services.generation_jobs import generation_jobs
from app.services.generation_runner import run_generate_and_save
from app.services.generation_validation import GenerationFailedError, SyncodeUnavailableError

log = logging.getLogger(__name__)
router = APIRouter()

_JOB_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _http_500_from_syncode_unavailable(exc: SyncodeUnavailableError) -> HTTPException:
    detail = exc.to_detail()
    log.error(
        "[GEN syncode unavailable] %s | detail=%s",
        exc,
        json.dumps({k: v for k, v in detail.items() if k != "init_traceback"}, default=str),
        exc_info=True,
    )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=detail,
    )


def _http_500_from_generation_error(exc: GenerationFailedError) -> HTTPException:
    detail = exc.to_detail()
    log.error(
        "[GEN validation failed] %s | detail=%s",
        exc,
        json.dumps(detail, default=str),
        exc_info=True,
    )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# Async jobs (browser live path)
# ---------------------------------------------------------------------------

@router.post(
    "/generate/jobs",
    response_model=GenerateJobCreatedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_generate_job(request: GenerateRequest) -> GenerateJobCreatedResponse:
    """
    Enqueue a live generation job and return immediately.

    Does not wait for model generation. Poll GET /generate/jobs/{job_id}.
    Extra jobs are queued FIFO (max waiting jobs enforced); overflow → 429.
    """
    try:
        return generation_jobs.enqueue(request)
    except OverflowError as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "generation_queue_full",
                "message": str(exc),
            },
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "generation_jobs_unavailable",
                "message": str(exc),
            },
        ) from exc


@router.get(
    "/generate/jobs/{job_id}",
    response_model=GenerateJobStatusResponse,
    status_code=status.HTTP_200_OK,
)
async def get_generate_job(job_id: str) -> GenerateJobStatusResponse:
    """Return current job status. Unknown / expired ids → 404."""
    if not _JOB_ID_RE.match(job_id or ""):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="malformed job id",
        )
    status_body = generation_jobs.get_status(job_id)
    if status_body is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "job_not_found",
                "message": (
                    f"Generation job '{job_id}' was not found. "
                    "In-memory job records are lost if the backend restarted."
                ),
            },
        )
    return status_body


# ---------------------------------------------------------------------------
# Synchronous generate (compat / tests)
# ---------------------------------------------------------------------------

@router.post(
    "/generate",
    response_model=GenerateCreatedResponse,
    status_code=status.HTTP_200_OK,
)
async def generate(request: GenerateRequest) -> GenerateCreatedResponse:
    """
    Run generation synchronously, persist ExperimentResult, return lightweight ack.

    Prefer POST /generate/jobs for browser live generation so the HTTP
    connection is not held open for the full CPU run.
    """
    try:
        return await run_generate_and_save(request)
    except SyncodeUnavailableError as exc:
        print(f"[generate] SynCode unavailable: {exc}", flush=True)
        raise _http_500_from_syncode_unavailable(exc) from exc
    except GenerationFailedError as exc:
        print(f"[generate] generation failed: {exc}", flush=True)
        raise _http_500_from_generation_error(exc) from exc
    except Exception as exc:
        print(f"[generate] exception: {type(exc).__name__}: {exc}", flush=True)
        traceback.print_exc()
        log.error(
            "[API /generate exception] %s: %s\n%s",
            type(exc).__name__,
            exc,
            traceback.format_exc(),
        )
        msg = str(exc)
        if "experiment save failed" in msg.lower():
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "error": "experiment_save_failed",
                    "message": msg,
                },
            ) from exc
        if "serialize lightweight response" in msg.lower():
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={
                    "error": "response_serialization_failed",
                    "message": msg,
                },
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "error": "generation_exception",
                "message": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            },
        ) from exc
