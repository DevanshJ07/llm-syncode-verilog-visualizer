"""
Checkpoint 3D — read-only original-trace extraction helpers.

Fail closed when candidate ID/decode disagree with the recorded step.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from app.research.syncode_mask_probe_prefix import (
    ProbeCaseError,
    _load_trace_payload,
    _steps_from_trace,
    reconstruct_prefix_from_selected_tokens,
    sha256_utf8,
)


def extract_step_evidence(
    *,
    trace_path: str | Path,
    prompt_id: Optional[str],
    step_index: int,
    expected_raw_argmax_id: Optional[int] = None,
    expected_raw_argmax_text: Optional[str] = None,
    expected_selected_id: Optional[int] = None,
    expected_selected_text: Optional[str] = None,
    neighbor_radius: int = 5,
) -> dict[str, Any]:
    """
    Extract verified fields for one zero-based decoding step.

    Prefix = concat(selected_token for steps [0, step_index)).
    Never uses prefix_tail as the authoritative prefix.
    """
    payload = _load_trace_payload(Path(trace_path))
    steps = _steps_from_trace(payload, prompt_id)
    if step_index < 0 or step_index >= len(steps):
        raise ProbeCaseError(
            f"step_index {step_index} out of range for {len(steps)} steps"
        )

    step = steps[step_index]
    recorded_step = step.get("step", step.get("step_index", step_index))
    raw_id = step.get("raw_argmax_token_id")
    raw_text = step.get("raw_argmax_token")
    sel_id = step.get("selected_token_id")
    sel_text = step.get("selected_token")
    blocked = step.get("raw_argmax_blocked")

    if expected_raw_argmax_id is not None and int(raw_id) != int(expected_raw_argmax_id):
        raise ProbeCaseError(
            f"raw_argmax_token_id mismatch: case={expected_raw_argmax_id} "
            f"trace={raw_id}"
        )
    if (
        expected_raw_argmax_text is not None
        and raw_text is not None
        and raw_text != expected_raw_argmax_text
    ):
        raise ProbeCaseError(
            f"raw_argmax_token text mismatch: case={expected_raw_argmax_text!r} "
            f"trace={raw_text!r}"
        )
    if expected_selected_id is not None and int(sel_id) != int(expected_selected_id):
        raise ProbeCaseError(
            f"selected_token_id mismatch: case={expected_selected_id} trace={sel_id}"
        )
    if (
        expected_selected_text is not None
        and sel_text is not None
        and sel_text != expected_selected_text
    ):
        raise ProbeCaseError(
            f"selected_token text mismatch: case={expected_selected_text!r} "
            f"trace={sel_text!r}"
        )

    prefix = reconstruct_prefix_from_selected_tokens(steps, step_index=step_index)
    lo = max(0, step_index - neighbor_radius)
    hi = min(len(steps), step_index + neighbor_radius + 1)
    neighbours = []
    for i in range(lo, hi):
        s = steps[i]
        neighbours.append(
            {
                "index": i,
                "recorded_step": s.get("step", s.get("step_index", i)),
                "selected_token_id": s.get("selected_token_id"),
                "selected_token": s.get("selected_token"),
                "raw_argmax_token_id": s.get("raw_argmax_token_id"),
                "raw_argmax_token": s.get("raw_argmax_token"),
                "raw_argmax_blocked": s.get("raw_argmax_blocked"),
            }
        )

    mask_evidence = {
        "allowed_token_count": step.get("allowed_token_count"),
        "newly_masked_token_count": step.get("newly_masked_token_count"),
        "constrained_argmax_token_id": step.get("constrained_argmax_token_id"),
        "constrained_argmax_token": step.get("constrained_argmax_token"),
        "selected_equals_constrained_argmax": step.get(
            "selected_equals_constrained_argmax"
        ),
        "top_raw_tokens": step.get("top_raw_tokens"),
        "prefix_tail_recorded": step.get("prefix_tail"),
        "note": (
            "prefix_tail is recorded evidence only; authoritative prefix is "
            "reconstructed from selected tokens"
        ),
    }

    return {
        "trace_path": str(Path(trace_path)),
        "prompt_id": prompt_id or payload.get("problem"),
        "step_index": step_index,
        "step_index_unit": "zero_based",
        "recorded_step_field": recorded_step,
        "recorded_step_equals_index": recorded_step == step_index,
        "prefix": prefix,
        "prefix_sha256_utf8": sha256_utf8(prefix),
        "prefix_character_count": len(prefix),
        "prefix_tail_reconstructed": prefix[-40:] if len(prefix) >= 40 else prefix,
        "raw_argmax_token_id": raw_id,
        "raw_argmax_token": raw_text,
        "raw_argmax_blocked": blocked,
        "selected_token_id": sel_id,
        "selected_token": sel_text,
        "neighbours": neighbours,
        "mask_evidence": mask_evidence,
        "fail_closed_id_decode_matched": True,
    }


def write_trace_extraction_report(evidence: dict[str, Any], output_json: Path) -> Path:
    output_json = Path(output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(evidence, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return output_json
