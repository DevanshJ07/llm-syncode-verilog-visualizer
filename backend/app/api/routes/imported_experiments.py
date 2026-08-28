"""
Imported experiment APIs (Phase 2A.2).

POST /import/bundle
GET  /imported-experiments
GET  /imported-experiment/{experiment_id}

Also mounted under ``/api`` when registered that way in ``main.py``.
"""

from __future__ import annotations

import logging
from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.core.config import settings
from app.models.normalized import ImportedExperimentSummary, NormalizedExperiment
from app.services.import_normalize import (
    ImportNormalizationError,
    is_safe_experiment_id,
    normalize_imported_bundle,
)
from app.services.import_zip import ZipInspectionError
from app.services.imported_experiment_store import (
    ImportedStoreError,
    imported_store,
)

log = logging.getLogger(__name__)
router = APIRouter()


def _client_safe_detail(message: str) -> str:
    """Strip absolute Windows/Unix paths from client-facing errors."""
    # Keep messages short and avoid leaking host filesystem layout.
    cleaned = message.replace("\\", "/")
    # Drop drive-qualified segments if somehow present.
    parts = cleaned.split()
    safe_parts = []
    for p in parts:
        if len(p) >= 3 and p[1] == ":" and p[0].isalpha():
            safe_parts.append("<path>")
        elif p.startswith("/") and len(p) > 1:
            # Absolute unix path token — redact
            safe_parts.append("<path>")
        else:
            safe_parts.append(p)
    return " ".join(safe_parts)


async def _read_upload_limited(upload: UploadFile, *, max_bytes: int) -> bytes:
    """Read multipart upload with a hard compressed-size cap while streaming."""
    chunks: list[bytes] = []
    total = 0
    while True:
        # Read at most one byte past the remaining budget so an arbitrarily large
        # upload is rejected without buffering the entire body.
        room = max_bytes - total
        chunk = await upload.read(min(1024 * 1024, room + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            chunks.clear()
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=(
                    f"compressed upload exceeds limit of {max_bytes} bytes"
                ),
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post(
    "/import/bundle",
    response_model=NormalizedExperiment,
    status_code=status.HTTP_201_CREATED,
)
async def import_bundle(
    file: UploadFile = File(..., description="Experiment result ZIP"),
    recompute_with_current_grammar: bool = Form(False),
) -> NormalizedExperiment:
    """
    Inspect, normalize, and persist an uploaded experiment ZIP.

    ``recompute_with_current_grammar`` defaults to false.
    """
    max_bytes = settings.max_import_upload_bytes
    content_type = (file.content_type or "").lower()
    filename = file.filename or ""
    if filename and not filename.lower().endswith(".zip"):
        # Soft hint only when a name is present; still allow octet-stream ZIPs.
        if content_type and "zip" not in content_type and content_type not in {
            "application/octet-stream",
            "application/x-zip-compressed",
            "",
        }:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="expected a ZIP experiment bundle",
            )

    try:
        zip_bytes = await _read_upload_limited(file, max_bytes=max_bytes)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("import upload read failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="unable to read uploaded file",
        ) from None

    if not zip_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="empty upload",
        )

    experiment_id = imported_store.new_id()
    try:
        experiment = normalize_imported_bundle(
            zip_bytes,
            recompute_with_current_grammar=bool(recompute_with_current_grammar),
            experiment_id=experiment_id,
        )
    except ZipInspectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_client_safe_detail(str(exc)),
        ) from None
    except ImportNormalizationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_client_safe_detail(str(exc)),
        ) from None
    except Exception as exc:  # noqa: BLE001
        # Pydantic validation during normalize maps to client 422.
        if type(exc).__name__ == "ValidationError":
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="invalid experiment evidence",
            ) from None
        log.exception("unexpected import failure: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="invalid experiment bundle",
        ) from None

    try:
        imported_store.save(experiment)
    except ImportedStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_client_safe_detail(str(exc)),
        ) from None

    return experiment


@router.get(
    "/imported-experiments",
    response_model=list[ImportedExperimentSummary],
)
async def list_imported_experiments() -> list[ImportedExperimentSummary]:
    """Lightweight summaries (no per-step traces)."""
    return imported_store.list_summaries()


@router.get(
    "/imported-experiment/{experiment_id}",
    response_model=NormalizedExperiment,
)
async def get_imported_experiment(experiment_id: str) -> NormalizedExperiment:
    """Return a full normalized imported experiment."""
    if not is_safe_experiment_id(experiment_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="malformed experiment id",
        )
    try:
        experiment = imported_store.load(experiment_id)
    except ImportedStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_client_safe_detail(str(exc)),
        ) from None
    if experiment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="imported experiment not found",
        )
    return experiment
