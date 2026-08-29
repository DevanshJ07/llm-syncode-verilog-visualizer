"""
Shared live-generation runner: generate → enrich → validate → save.

Used by:
  - POST /generate (synchronous lightweight ack)
  - POST /generate/jobs background worker

Does not change model/SynCode/masking semantics — only factors the route body
so jobs and the sync path share one implementation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Optional

from app.core.config import settings
from app.models.provenance import ProvenanceKind
from app.models.schemas import (
    GenerateCreatedResponse,
    GenerateRequest,
    ParserFailureContextSchema,
)
from app.services.experiment_store import store
from app.services.generation_validation import GenerationFailedError, SyncodeUnavailableError
from app.services.grammar_diagnostics import get_grammar_diagnostics
from app.services.llm_service import llm_service
from app.services.parser_analysis import analyze_verilog_source
from app.services.verilog_validation import (
    build_parse_tree,
    compute_constraint_status,
    enrich_steps_with_incremental_parser_state,
    validate_verilog_output,
)

log = logging.getLogger(__name__)

ProgressCb = Optional[Callable[[str], None]]


def _extract_verilog(text: str) -> str:
    """Extract the first complete module...endmodule block from generated text."""
    import re

    cleaned = re.sub(r"```[a-zA-Z]*\n?", "", text)
    cleaned = re.sub(r"```", "", cleaned).strip()
    match = re.search(r"(module\b.+?endmodule)", cleaned, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return cleaned


def _notify(progress: ProgressCb, message: str) -> None:
    if progress is not None:
        try:
            progress(message)
        except Exception:  # noqa: BLE001
            log.debug("progress callback failed", exc_info=True)


async def run_generate_and_save(
    request: GenerateRequest,
    *,
    progress: ProgressCb = None,
) -> GenerateCreatedResponse:
    """
    Run the full live generation pipeline and persist ExperimentResult.

    Returns a lightweight GenerateCreatedResponse. Raises
    SyncodeUnavailableError / GenerationFailedError / Exception on failure.
    Never reports success without a successful store.save().
    """
    mode = "syncode" if request.use_syncode else "raw"

    print("[generate] request received", flush=True)
    print(f"[generate] backend_mode: {mode}", flush=True)
    print("[generate] grammar: verilog", flush=True)
    print(f"[generate] max_tokens: {request.max_new_tokens}", flush=True)
    print(f"[generate] prompt length: {len(request.prompt)}", flush=True)

    log.info(
        "[API /generate request] mode=%s prompt_len=%d max_new_tokens=%d "
        "top_k=%d T=%.2f do_sample=%s use_syncode=%s",
        mode,
        len(request.prompt),
        request.max_new_tokens,
        request.top_k,
        request.temperature,
        request.do_sample,
        request.use_syncode,
    )

    experiment = store.create_empty(
        prompt=request.prompt,
        mode=mode,
        model_name=settings.model_name,
    )

    if not llm_service.is_loaded:
        _notify(progress, "Loading model…")
    else:
        _notify(progress, "Generating…")

    try:
        print("[generate] loading model...", flush=True)
        (
            generated_text,
            steps,
            early_termination,
            eos_allowed_at_completion,
        ) = await llm_service.generate(
            prompt=request.prompt,
            max_new_tokens=request.max_new_tokens,
            top_k=request.top_k,
            temperature=request.temperature,
            use_syncode=request.use_syncode,
            do_sample=request.do_sample,
            top_p=request.top_p,
            repetition_penalty=request.repetition_penalty,
        )
        print("[generate] generation complete", flush=True)
    except SyncodeUnavailableError:
        raise
    except GenerationFailedError:
        raise
    except Exception:
        raise

    early_termination = early_termination or ""
    is_syncode_fail_fast = early_termination.startswith("syncode_parser_error")

    log.info(
        "[API /generate] early_termination=%r is_syncode_fail_fast=%s steps=%d",
        early_termination,
        is_syncode_fail_fast,
        len(steps),
    )

    if len(steps) == 0 and not is_syncode_fail_fast:
        raise GenerationFailedError(
            "Route validation: zero decoding steps after generate()",
            reasons=["len(steps) == 0 at route boundary"],
            step_count=0,
        )
    if (not generated_text or not generated_text.strip()) and not is_syncode_fail_fast:
        raise GenerationFailedError(
            "Route validation: empty generated_text",
            reasons=["generated_text is empty at route boundary"],
            step_count=len(steps),
        )

    try:
        enrich_steps_with_incremental_parser_state(steps)
        log.info(
            "[API /generate] incremental parser state enriched for %d steps",
            len(steps),
        )
    except Exception as enrich_exc:
        log.warning(
            "[API /generate] incremental parser enrichment failed (non-fatal): %s",
            enrich_exc,
            exc_info=True,
        )

    syncode_stopped_reason: str = early_termination
    if syncode_stopped_reason.startswith("max_new_tokens_reached_"):
        syncode_stopped_reason = "max_tokens_incomplete"
    if syncode_stopped_reason == "max_tokens":
        syncode_stopped_reason = "max_tokens_incomplete"

    normal_max_tokens = request.max_new_tokens
    if mode == "syncode":
        absolute_max_tokens = min(
            normal_max_tokens + settings.completion_extra_tokens,
            settings.absolute_max_tokens,
        )
    else:
        absolute_max_tokens = normal_max_tokens

    raw_fallback_prevented: bool = (
        mode == "syncode"
        and is_syncode_fail_fast
        and not settings.allow_syncode_fallback
    )

    if is_syncode_fail_fast:
        print(
            f"[generation] stopping due to parser error, not continuing raw "
            f"(allow_syncode_fallback={settings.allow_syncode_fallback})",
            flush=True,
        )

    clean_text = _extract_verilog(generated_text) if generated_text.strip() else ""
    log.info(
        "[API /generate] Verilog extraction: raw_len=%d clean_len=%d",
        len(generated_text),
        len(clean_text),
    )

    validation = validate_verilog_output(clean_text)
    log.info(
        "[API /generate] final_parse_valid=%s unsupported=%s error=%r",
        validation.final_parse_valid,
        validation.unsupported_constructs_detected,
        validation.final_parse_error[:200] if validation.final_parse_error else "",
    )

    parse_tree = build_parse_tree(clean_text)
    log.info(
        "[API /generate] parse_tree_available=%s error_type=%r",
        parse_tree.parse_tree_available,
        parse_tree.parse_tree_error_type or "none",
    )

    syncode_active_steps = sum(1 for s in steps if s.syncode_active)
    syncode_fallback_steps = sum(1 for s in steps if s.fallback_used)
    syncode_parse_error_steps = sum(1 for s in steps if s.parser_error)

    syncode_available = bool(
        llm_service._syncode is not None
        and getattr(llm_service._syncode, "available", False)
    )
    grammar_diag = get_grammar_diagnostics()
    syncode_init_error = ""
    if llm_service._syncode is not None and not syncode_available:
        syncode_init_error = (
            llm_service._syncode.init_error
            or grammar_diag.syncode_mask_store_error
            or grammar_diag.syncode_grammar_error
        )

    evidence = compute_constraint_status(
        mode=mode,
        syncode_available=syncode_available,
        total_steps=len(steps),
        syncode_active_steps=syncode_active_steps,
        syncode_fallback_steps=syncode_fallback_steps,
        final_parse_valid=validation.final_parse_valid,
        final_parse_error=validation.final_parse_error,
        lark_grammar_loaded=grammar_diag.lark_grammar_loaded,
        syncode_mask_store_loaded=syncode_available,
        syncode_init_error=syncode_init_error,
    )

    status_message = ""
    if mode == "syncode" and not validation.final_parse_valid:
        status_message = (
            "Generation completed but final output is not valid under the "
            "tested Verilog grammar."
        )

    experiment.generated_code = clean_text
    experiment.steps = steps
    experiment.total_steps = len(steps)
    experiment.grammar_name = "verilog"
    experiment.parser_name = "lalr"
    experiment.syncode_mode_name = "grammar_mask"
    experiment.syncode_available = syncode_available
    experiment.syncode_active_steps = syncode_active_steps
    experiment.syncode_fallback_steps = syncode_fallback_steps
    experiment.syncode_parse_error_steps = syncode_parse_error_steps
    experiment.final_parse_valid = validation.final_parse_valid
    experiment.final_parse_error = validation.final_parse_error
    experiment.unsupported_constructs_detected = validation.unsupported_constructs_detected
    experiment.comments_stripped_for_validation = validation.comments_stripped_for_validation
    experiment.constraint_requested = evidence.constraint_requested
    experiment.constraint_status = evidence.constraint_status
    experiment.constraint_applied = evidence.constraint_applied
    experiment.fallback_occurred = evidence.fallback_occurred
    experiment.syncode_error = evidence.syncode_error
    experiment.lark_grammar_loaded = evidence.lark_grammar_loaded
    experiment.syncode_mask_store_loaded = evidence.syncode_mask_store_loaded
    experiment.constraint_active_during_generation = evidence.constraint_active_during_generation
    experiment.raw_unconstrained_generation_used = evidence.raw_unconstrained_generation_used
    experiment.unconstrained_reason = evidence.unconstrained_reason
    experiment.syncode_init_error = syncode_init_error
    experiment.syncode_stopped_reason = syncode_stopped_reason
    experiment.raw_fallback_prevented = raw_fallback_prevented
    experiment.eos_allowed_at_completion = bool(eos_allowed_at_completion)
    experiment.normal_max_tokens = normal_max_tokens
    experiment.absolute_max_tokens = absolute_max_tokens
    experiment.parse_tree_available = parse_tree.parse_tree_available
    experiment.parse_tree_text = parse_tree.parse_tree_text
    experiment.parse_tree_error_type = parse_tree.parse_tree_error_type
    experiment.parse_tree_error_message = parse_tree.parse_tree_error_message
    experiment.parse_tree_error_line = parse_tree.parse_tree_error_line
    experiment.parse_tree_error_column = parse_tree.parse_tree_error_column
    experiment.parse_tree_unexpected_token = parse_tree.parse_tree_unexpected_token
    experiment.parse_tree_expected_terminals = parse_tree.parse_tree_expected_terminals
    experiment.parse_tree_previous_token = parse_tree.parse_tree_previous_token
    _pfc = parse_tree.parser_failure_context
    experiment.parser_failure_context = ParserFailureContextSchema(
        available=_pfc.available if _pfc else False,
        prefix_before_error=_pfc.prefix_before_error if _pfc else "",
        error_line_text=_pfc.error_line_text if _pfc else "",
        caret_line=_pfc.caret_line if _pfc else "",
        expected_terminals=_pfc.expected_terminals if _pfc else [],
        likely_parser_state_summary=_pfc.likely_parser_state_summary if _pfc else "",
        likely_interpretation=_pfc.likely_interpretation if _pfc else "",
    )

    try:
        experiment.parser_analysis = analyze_verilog_source(
            clean_text,
            provenance_kind=ProvenanceKind.derived,
            method="analyze_verilog_source (live final output)",
        )
    except Exception as analysis_exc:  # noqa: BLE001
        log.exception(
            "[API /generate] parser analysis failed; continuing with unavailable"
        )
        from app.models.parser_analysis import (  # noqa: PLC0415
            unavailable_parser_analysis,
        )

        experiment.parser_analysis = unavailable_parser_analysis(
            method="analyze_verilog_source (live final output)",
            warnings=[
                f"unexpected parser analysis failure "
                f"({type(analysis_exc).__name__}: {analysis_exc}); "
                "live generation result preserved"
            ],
        )

    _notify(progress, "Saving experiment…")
    try:
        store.save(experiment)
        log.info(
            "[API experiment save] success experiment_id=%s steps=%d",
            experiment.experiment_id,
            len(steps),
        )
    except Exception as save_exc:
        log.error(
            "[API experiment save] FAILED experiment_id=%s: %s",
            experiment.experiment_id,
            save_exc,
            exc_info=True,
        )
        raise RuntimeError(
            f"Generation succeeded but experiment save failed: {save_exc}"
        ) from save_exc

    response = GenerateCreatedResponse(
        experiment_id=experiment.experiment_id,
        status="completed",
        message=status_message,
        mode=mode,
        step_count=len(steps),
        early_termination=syncode_stopped_reason or "",
        final_parse_valid=validation.final_parse_valid,
        created_at=experiment.created_at,
        detail_path=f"/experiment/{experiment.experiment_id}",
        model_name=settings.model_name,
        constraint_status=evidence.constraint_status,
        constraint_requested=evidence.constraint_requested,
        constraint_active_during_generation=evidence.constraint_active_during_generation,
    )

    try:
        payload_json = response.model_dump_json()
        payload_bytes = len(payload_json.encode("utf-8"))
        log.info(
            "[API /generate response] experiment_id=%s status=completed "
            "step_count=%d payload_bytes=%d generated_text_len=%d "
            "syncode_active=%d/%d fallback=%d/%d "
            "final_parse_valid=%s constraint_status=%s detail_path=%s",
            experiment.experiment_id,
            len(steps),
            payload_bytes,
            len(clean_text),
            syncode_active_steps,
            len(steps),
            syncode_fallback_steps,
            len(steps),
            validation.final_parse_valid,
            evidence.constraint_status,
            response.detail_path,
        )
    except Exception as ser_exc:
        log.error(
            "[API generate-created serialization] FAILED: %s",
            ser_exc,
            exc_info=True,
        )
        # Experiment already saved — surface serialization as hard failure for
        # the HTTP/job layer without claiming incomplete persistence.
        raise RuntimeError(
            f"Failed to serialize lightweight response: {ser_exc}"
        ) from ser_exc

    return response
