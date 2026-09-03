"""Prefix reconstruction and case validation for the mask probe."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Optional

from app.models.syncode_mask_probe import ProbeCaseSpec


class ProbeCaseError(ValueError):
    """Fail-closed case / trace validation error."""


def sha256_utf8(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_case_spec(path: str | Path) -> ProbeCaseSpec:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return ProbeCaseSpec.model_validate(data)


def _load_trace_payload(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ProbeCaseError(f"trace root must be an object: {path}")
    return raw


def _steps_from_trace(payload: dict[str, Any], prompt_id: str | None) -> list[dict[str, Any]]:
    """
    Accept either:
      - SynViz imported NormalizedExperiment JSON
      - flat {\"steps\": [...]} / {\"prompts\": {id: {steps}}}
      - raw research trace {\"problem\": ..., \"steps\": [...]}
    """
    if prompt_id and "prompt_results" in payload:
        for pr in payload.get("prompt_results") or []:
            if pr.get("problem_id") == prompt_id:
                steps = pr.get("steps") or []
                # Normalized steps use nested selected.value.token
                out = []
                for s in steps:
                    if "selected_token" in s:
                        out.append(s)
                        continue
                    sel = (s.get("selected") or {}).get("value") or {}
                    out.append(
                        {
                            "selected_token": sel.get("token"),
                            "selected_token_id": sel.get("token_id"),
                            "step_index": s.get("step_index"),
                            "raw_argmax_token_id": (
                                ((s.get("raw_preferred") or {}).get("value") or {}).get(
                                    "token_id"
                                )
                            ),
                        }
                    )
                return out
        raise ProbeCaseError(f"prompt_id {prompt_id!r} not found in trace")

    if "steps" in payload and isinstance(payload["steps"], list):
        return list(payload["steps"])

    if prompt_id and "prompts" in payload:
        block = (payload.get("prompts") or {}).get(prompt_id)
        if not block:
            raise ProbeCaseError(f"prompt_id {prompt_id!r} not found under prompts")
        return list(block.get("steps") or [])

    raise ProbeCaseError(
        "unable to locate steps; provide inline_trace_steps or a recognized trace JSON"
    )


def reconstruct_prefix_from_selected_tokens(
    steps: list[dict[str, Any]],
    *,
    step_index: int,
) -> str:
    """
    Before step i (zero-based) = concat exact selected_token for [0, i).

    Never uses prefix_tail. Never trims. Never silently substitutes context.
    """
    if step_index < 0:
        raise ProbeCaseError(f"step_index must be >= 0, got {step_index}")
    if step_index > len(steps):
        raise ProbeCaseError(
            f"step_index {step_index} exceeds available steps ({len(steps)})"
        )
    parts: list[str] = []
    for i in range(step_index):
        tok = steps[i].get("selected_token")
        if tok is None:
            raise ProbeCaseError(
                f"selected_token missing at step {i}; cannot reconstruct prefix"
            )
        if not isinstance(tok, str):
            raise ProbeCaseError(
                f"selected_token at step {i} must be str, got {type(tok).__name__}"
            )
        parts.append(tok)
    return "".join(parts)


def resolve_case_prefix(case: ProbeCaseSpec) -> tuple[str, list[str]]:
    """Return ``(prefix, warnings)``."""
    warnings: list[str] = []
    if case.prefix_source == "explicit":
        if not case.explicit_prefix_file:
            raise ProbeCaseError("explicit prefix_source requires explicit_prefix_file")
        text = Path(case.explicit_prefix_file).read_text(encoding="utf-8")
        # Preserve exact file bytes decoded as UTF-8 — do not strip.
        return text, warnings

    steps: list[dict[str, Any]]
    if case.inline_trace_steps is not None:
        steps = list(case.inline_trace_steps)
    elif case.source_trace_path:
        payload = _load_trace_payload(Path(case.source_trace_path))
        steps = _steps_from_trace(payload, case.prompt_id)
    else:
        raise ProbeCaseError(
            "reconstructed_from_selected_tokens requires source_trace_path "
            "or inline_trace_steps"
        )

    if case.step_index is None:
        raise ProbeCaseError("step_index is required for reconstruction")

    # Verify recorded IDs when present on the step.
    if case.step_index < len(steps):
        step = steps[case.step_index]
        if (
            case.selected_token_id is not None
            and step.get("selected_token_id") is not None
            and int(step["selected_token_id"]) != int(case.selected_token_id)
        ):
            raise ProbeCaseError(
                f"selected_token_id mismatch at step {case.step_index}: "
                f"case={case.selected_token_id} trace={step.get('selected_token_id')}"
            )
        if (
            case.raw_argmax_token_id is not None
            and step.get("raw_argmax_token_id") is not None
            and int(step["raw_argmax_token_id"]) != int(case.raw_argmax_token_id)
        ):
            raise ProbeCaseError(
                f"raw_argmax_token_id mismatch at step {case.step_index}: "
                f"case={case.raw_argmax_token_id} "
                f"trace={step.get('raw_argmax_token_id')}"
            )
    elif case.selected_token_id is not None or case.raw_argmax_token_id is not None:
        warnings.append(
            "step_index == len(steps); cannot verify selected/raw IDs on a "
            "non-existent current step row"
        )

    prefix = reconstruct_prefix_from_selected_tokens(steps, step_index=case.step_index)
    return prefix, warnings


def prefix_metrics(prefix: str) -> dict[str, Any]:
    encoded = prefix.encode("utf-8")
    return {
        "prefix_text": prefix,
        "prefix_sha256_utf8": sha256_utf8(prefix),
        "prefix_character_count": len(prefix),
        "prefix_utf8_byte_count": len(encoded),
    }


def original_trace_token_text(
    case: ProbeCaseSpec, token_id: int
) -> Optional[str]:
    """Best-effort map from candidate ID to recorded token text."""
    steps: list[dict[str, Any]] = []
    if case.inline_trace_steps:
        steps = list(case.inline_trace_steps)
    elif case.source_trace_path and case.prompt_id:
        try:
            payload = _load_trace_payload(Path(case.source_trace_path))
            steps = _steps_from_trace(payload, case.prompt_id)
        except Exception:  # noqa: BLE001
            return None
    if case.step_index is not None and case.step_index < len(steps):
        step = steps[case.step_index]
        if step.get("selected_token_id") == token_id:
            tok = step.get("selected_token")
            return tok if isinstance(tok, str) else None
    # Fall back: expected_decoded_candidates keyed by str(id)
    if case.expected_decoded_candidates:
        return case.expected_decoded_candidates.get(str(token_id))
    return None
