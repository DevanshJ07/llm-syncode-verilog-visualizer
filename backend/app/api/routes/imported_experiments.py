"""
Imported experiment APIs (Phase 2A.2).

POST /import/bundle
GET  /imported-experiments
GET  /imported-experiment/{experiment_id}

Also mounted under ``/api`` when registered that way in ``main.py``.
"""

from __future__ import annotations

import logging
from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status

from app.core.config import settings
from app.models.lossless_parser_analysis import LosslessParserAnalysisResponse
from app.models.normalized import (
    ImportedExperimentCreatedResponse,
    ImportedExperimentSummary,
    NormalizedExperiment,
)
from app.services.import_normalize import (
    ImportNormalizationError,
    is_safe_experiment_id,
    normalize_imported_bundle,
)
from app.services.import_zip import ZipInspectionError
from app.services.imported_experiment_store import (
    ImportedStoreError,
    imported_store,
    to_imported_created_response,
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
    response_model=ImportedExperimentCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_bundle(
    file: UploadFile = File(..., description="Experiment result ZIP"),
    recompute_with_current_grammar: bool = Form(False),
    recompute_syncode_parser_evidence: bool = Form(False),
) -> ImportedExperimentCreatedResponse:
    """
    Inspect, normalize, and persist an uploaded experiment ZIP.

    Returns a lightweight created response (no per-step traces).  Fetch the
    full normalized experiment via GET /imported-experiment/{experiment_id}.

    ``recompute_with_current_grammar`` defaults to false (Lark / parser analysis).
    ``recompute_syncode_parser_evidence`` defaults to false (parser-only SynCode;
    independent of the grammar flag; never builds a MaskStore).
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
            recompute_syncode_parser_evidence=bool(
                recompute_syncode_parser_evidence
            ),
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

    return to_imported_created_response(experiment)

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


def _load_imported_or_404(experiment_id: str) -> NormalizedExperiment:
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


@router.get(
    "/imported-experiment/{experiment_id}/prompts/{prompt_id}/steps/{step_index}/parser-analysis",
    response_model=LosslessParserAnalysisResponse,
)
async def get_imported_step_parser_analysis(
    experiment_id: str,
    prompt_id: str,
    step_index: int,
    timing: str = Query(
        "before",
        description=(
            "before (default) | after. Zero-based step_index. "
            "Source = concat(selected_token) for [0,i) or [0,i+1)."
        ),
    ),
) -> LosslessParserAnalysisResponse:
    """
    On-demand lossless parser analysis for one imported decoding step.

    ``step_index`` is zero-based. Default timing is before the selected token.
    Does not mutate stored JSON; does not run generation/masking.
    """
    from app.services.lossless_parser_analysis_routes import (
        analyze_imported_step,
        find_imported_prompt,
        parse_timing_query,
    )

    experiment = _load_imported_or_404(experiment_id)
    prompt = find_imported_prompt(experiment, prompt_id)
    parsed = parse_timing_query(timing)
    return await analyze_imported_step(
        experiment, prompt, step_index=step_index, timing=parsed
    )


@router.get(
    "/imported-experiment/{experiment_id}/prompts/{prompt_id}/parser-analysis",
    response_model=LosslessParserAnalysisResponse,
)
async def get_imported_final_parser_analysis(
    experiment_id: str,
    prompt_id: str,
    timing: str = Query(
        "final_source",
        description="Must be final_source for this route.",
    ),
) -> LosslessParserAnalysisResponse:
    """Lossless analysis of the prompt's authoritative final generated source."""
    from app.services.lossless_parser_analysis_routes import (
        analyze_imported_final,
        find_imported_prompt,
        parse_timing_query,
    )

    experiment = _load_imported_or_404(experiment_id)
    prompt = find_imported_prompt(experiment, prompt_id)
    parsed = parse_timing_query(timing)
    if parsed != "final_source":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "use timing=final_source on this route, or the per-step "
                "parser-analysis route for before/after"
            ),
        )
    return await analyze_imported_final(experiment, prompt)
