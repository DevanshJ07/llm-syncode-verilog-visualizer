"""HTTP helpers for on-demand lossless parser-analysis endpoints."""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import HTTPException, status

from app.core.grammar import grammar_sha256
from app.models.lossless_parser_analysis import (
    AnalysisTiming,
    LosslessParserAnalysisResponse,
)
from app.models.normalized import NormalizedExperiment, NormalizedPromptResult
from app.models.schemas import ExperimentResult
from app.services.lossless_parser_analysis import (
    analyze_lossless_in_threadpool,
    build_llm_token_spans,
    construct_step_source,
    make_cache_key,
    source_sha256,
)

TimingQuery = Literal["before", "after", "final_source"]


def parse_timing_query(raw: str | None) -> AnalysisTiming:
    if raw is None or raw == "" or raw == "before":
        return "before_selected_token"
    if raw == "after":
        return "after_selected_token"
    if raw == "final_source":
        return "final_source"
    if raw in (
        "before_selected_token",
        "after_selected_token",
        "final_source",
    ):
        return raw  # type: ignore[return-value]
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=(
            f"unsupported timing '{raw}'; "
            "use before, after, or final_source"
        ),
    )


def _live_selected_texts(experiment: ExperimentResult) -> list[str]:
    return [s.selected_token for s in experiment.steps]


def _live_token_ids(experiment: ExperimentResult) -> list[Optional[int]]:
    return [s.selected_token_id for s in experiment.steps]


def _live_recorded_steps(experiment: ExperimentResult) -> list[Optional[int]]:
    return [s.step for s in experiment.steps]


def _imported_selected_texts(prompt: NormalizedPromptResult) -> list[str]:
    out: list[str] = []
    for step in prompt.steps:
        sel = step.selected
        if sel is None or sel.value is None or sel.value.token is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"selected_token unavailable at step_index "
                    f"{step.step_index}; cannot construct prefix"
                ),
            )
        out.append(sel.value.token)
    return out


def _imported_token_ids(prompt: NormalizedPromptResult) -> list[Optional[int]]:
    out: list[Optional[int]] = []
    for step in prompt.steps:
        sel = step.selected
        if sel is None or sel.value is None:
            out.append(None)
        else:
            out.append(sel.value.token_id)
    return out


def _imported_recorded_steps(prompt: NormalizedPromptResult) -> list[Optional[int]]:
    return [s.step_index for s in prompt.steps]


def _context_mismatch_warning(
    *,
    derived: str,
    context: str | None,
    step_index: int,
) -> Optional[str]:
    if context is None or context == derived:
        return None
    return (
        f"derived selected_token prefix at step_index={step_index} differs from "
        "stored context; neither value was rewritten "
        f"(derived_len={len(derived)}, context_len={len(context)})"
    )


async def analyze_live_step(
    experiment: ExperimentResult,
    *,
    step_index: int,
    timing: AnalysisTiming,
) -> LosslessParserAnalysisResponse:
    if timing == "final_source":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "timing=final_source is not valid on the per-step route; "
                "use GET /experiment/{id}/parser-analysis?timing=final_source"
            ),
        )
    n = len(experiment.steps)
    if n == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="experiment has no decoding steps",
        )
    if step_index < 0 or step_index >= n:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"step_index {step_index} out of range (0–{n - 1}).",
        )

    texts = _live_selected_texts(experiment)
    try:
        source, sw = construct_step_source(
            texts, step_index=step_index, timing=timing
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    warnings_extra = list(sw)
    stored_ctx = experiment.steps[step_index].context
    if timing == "before_selected_token":
        w = _context_mismatch_warning(
            derived=source, context=stored_ctx, step_index=step_index
        )
        if w:
            warnings_extra.append(w)

    spans = build_llm_token_spans(
        texts,
        token_ids=_live_token_ids(experiment),
        recorded_steps=_live_recorded_steps(experiment),
        current_step_index=step_index,
        timing=timing,
    )
    ghash = grammar_sha256()
    key = make_cache_key(
        experiment_id=experiment.experiment_id,
        prompt_id=None,
        step_index=step_index,
        timing=timing,
        source_sha=source_sha256(source),
        grammar_sha=ghash,
    )
    result = await analyze_lossless_in_threadpool(
        cache_key=key,
        source=source,
        timing=timing,
        source_provenance="derived_from_recorded_selected_tokens",
        llm_token_spans=spans,
        experiment_id=experiment.experiment_id,
        prompt_id=None,
        step_index=step_index,
    )
    if warnings_extra:
        result.warnings = list(result.warnings) + warnings_extra
    return result


async def analyze_live_final(
    experiment: ExperimentResult,
) -> LosslessParserAnalysisResponse:
    source = experiment.generated_code if experiment.generated_code is not None else ""
    texts = _live_selected_texts(experiment)
    spans = build_llm_token_spans(
        texts,
        token_ids=_live_token_ids(experiment),
        recorded_steps=_live_recorded_steps(experiment),
        current_step_index=None,
        timing="after_selected_token",
    )
    for sp in spans:
        sp.selected_at_current_step = False

    ghash = grammar_sha256()
    key = make_cache_key(
        experiment_id=experiment.experiment_id,
        prompt_id=None,
        step_index=None,
        timing="final_source",
        source_sha=source_sha256(source),
        grammar_sha=ghash,
    )
    result = await analyze_lossless_in_threadpool(
        cache_key=key,
        source=source,
        timing="final_source",
        source_provenance="final_generated_source",
        llm_token_spans=spans,
        experiment_id=experiment.experiment_id,
        prompt_id=None,
        step_index=None,
    )
    try:
        reconstructed = "".join(texts)
        if reconstructed != source:
            result.warnings = list(result.warnings) + [
                "concatenated selected_token strings differ from "
                "final generated_code; final analysis uses generated_code "
                "(Final generated source)"
            ]
    except Exception:
        pass
    return result


def find_imported_prompt(
    experiment: NormalizedExperiment, prompt_id: str
) -> NormalizedPromptResult:
    for p in experiment.prompt_results:
        if p.problem_id == prompt_id:
            return p
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=(
            f"Prompt '{prompt_id}' not found in experiment "
            f"'{experiment.experiment_id}'."
        ),
    )


async def analyze_imported_step(
    experiment: NormalizedExperiment,
    prompt: NormalizedPromptResult,
    *,
    step_index: int,
    timing: AnalysisTiming,
) -> LosslessParserAnalysisResponse:
    if timing == "final_source":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "timing=final_source is not valid on the per-step route; "
                "use the prompt-level parser-analysis endpoint"
            ),
        )
    n = len(prompt.steps)
    if n == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="prompt has no decoding steps",
        )
    if step_index < 0 or step_index >= n:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"step_index {step_index} out of range (0–{n - 1}).",
        )

    texts = _imported_selected_texts(prompt)
    try:
        source, sw = construct_step_source(
            texts, step_index=step_index, timing=timing
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    warnings_extra = list(sw)
    if timing == "before_selected_token":
        pref = prompt.steps[step_index].prefix_before_selected
        if pref is not None and pref.value is not None:
            w = _context_mismatch_warning(
                derived=source,
                context=pref.value,
                step_index=step_index,
            )
            if w:
                warnings_extra.append(w)

    spans = build_llm_token_spans(
        texts,
        token_ids=_imported_token_ids(prompt),
        recorded_steps=_imported_recorded_steps(prompt),
        current_step_index=step_index,
        timing=timing,
    )
    ghash = grammar_sha256()
    key = make_cache_key(
        experiment_id=experiment.experiment_id,
        prompt_id=prompt.problem_id,
        step_index=step_index,
        timing=timing,
        source_sha=source_sha256(source),
        grammar_sha=ghash,
    )
    result = await analyze_lossless_in_threadpool(
        cache_key=key,
        source=source,
        timing=timing,
        source_provenance="derived_from_recorded_selected_tokens",
        llm_token_spans=spans,
        experiment_id=experiment.experiment_id,
        prompt_id=prompt.problem_id,
        step_index=step_index,
    )
    if warnings_extra:
        result.warnings = list(result.warnings) + warnings_extra
    return result


async def analyze_imported_final(
    experiment: NormalizedExperiment,
    prompt: NormalizedPromptResult,
) -> LosslessParserAnalysisResponse:
    gen = prompt.generated_output
    if gen is not None and gen.value is not None:
        source = gen.value
    elif (
        prompt.reconstructed_from_tokens is not None
        and prompt.reconstructed_from_tokens.value is not None
    ):
        source = prompt.reconstructed_from_tokens.value
    else:
        try:
            source = "".join(_imported_selected_texts(prompt))
        except HTTPException:
            source = ""

    try:
        texts = _imported_selected_texts(prompt)
        spans = build_llm_token_spans(
            texts,
            token_ids=_imported_token_ids(prompt),
            recorded_steps=_imported_recorded_steps(prompt),
            current_step_index=None,
            timing="after_selected_token",
        )
        for sp in spans:
            sp.selected_at_current_step = False
    except HTTPException:
        spans = []

    ghash = grammar_sha256()
    key = make_cache_key(
        experiment_id=experiment.experiment_id,
        prompt_id=prompt.problem_id,
        step_index=None,
        timing="final_source",
        source_sha=source_sha256(source),
        grammar_sha=ghash,
    )
    return await analyze_lossless_in_threadpool(
        cache_key=key,
        source=source,
        timing="final_source",
        source_provenance="final_generated_source",
        llm_token_spans=spans,
        experiment_id=experiment.experiment_id,
        prompt_id=prompt.problem_id,
        step_index=None,
    )
