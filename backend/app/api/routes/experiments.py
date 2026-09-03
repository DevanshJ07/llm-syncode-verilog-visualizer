"""
GET /experiment/{id}
GET /experiment/{id}/steps/{step}
GET /experiment/{id}/steps/{step_index}/parser-analysis  (0-based step_index)
GET /experiment/{id}/parser-analysis?timing=final_source
GET /experiments  (bonus list endpoint)

Read-only endpoints for retrieving stored experiment data.
All data is read from the JSON files written by the generate route.
Parser-analysis endpoints never write back to stored ExperimentResult.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from app.models.lossless_parser_analysis import LosslessParserAnalysisResponse
from app.models.schemas import ExperimentResult, StepResponse
from app.services.experiment_store import store
from app.services.lossless_parser_analysis_routes import (
    analyze_live_final,
    analyze_live_step,
    parse_timing_query,
)

router = APIRouter()


def _load_or_404(experiment_id: str) -> ExperimentResult:
    experiment = store.load(experiment_id)
    if experiment is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Experiment '{experiment_id}' not found.",
        )
    return experiment


@router.get("/experiments", response_model=list[str])
async def list_experiments() -> list[str]:
    """Return all stored experiment IDs, newest first."""
    return store.list_ids()


@router.get("/experiment/{experiment_id}", response_model=ExperimentResult)
async def get_experiment(experiment_id: str) -> ExperimentResult:
    """Return the full experiment record including all decoding steps."""
    return _load_or_404(experiment_id)


@router.get("/experiment/{experiment_id}/steps/{step}", response_model=StepResponse)
async def get_step(experiment_id: str, step: int) -> StepResponse:
    """Return a single decoding step from an experiment.

    step is 1-indexed to match the JSON log format in PROJECT_SPEC.
    """
    experiment = _load_or_404(experiment_id)

    if step < 1 or step > experiment.total_steps:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Step {step} out of range (1–{experiment.total_steps}).",
        )

    # Steps list is 0-indexed internally; step param is 1-indexed.
    return StepResponse(step=experiment.steps[step - 1], total_steps=experiment.total_steps)


@router.get(
    "/experiment/{experiment_id}/steps/{step_index}/parser-analysis",
    response_model=LosslessParserAnalysisResponse,
)
async def get_step_parser_analysis(
    experiment_id: str,
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
    On-demand lossless parser analysis for one decoding step.

    ``step_index`` is **zero-based** (unlike GET .../steps/{step} which is 1-based).
    Default timing is before the selected token (matches SynCode mask timing).
    Does not mutate stored ExperimentResult; does not run generation/masking.
    """
    experiment = _load_or_404(experiment_id)
    parsed = parse_timing_query(timing)
    return await analyze_live_step(
        experiment, step_index=step_index, timing=parsed
    )


@router.get(
    "/experiment/{experiment_id}/parser-analysis",
    response_model=LosslessParserAnalysisResponse,
)
async def get_final_parser_analysis(
    experiment_id: str,
    timing: str = Query(
        "final_source",
        description="Must be final_source for this route.",
    ),
) -> LosslessParserAnalysisResponse:
    """Lossless analysis of the authoritative final generated_code."""
    experiment = _load_or_404(experiment_id)
    parsed = parse_timing_query(timing)
    if parsed != "final_source":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "use timing=final_source on this route, or the per-step "
                "parser-analysis route for before/after"
            ),
        )
    return await analyze_live_final(experiment)
