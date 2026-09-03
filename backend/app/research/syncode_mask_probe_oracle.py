"""Grammar / constructive-witness oracles for the mask probe."""

from __future__ import annotations

from typing import Optional

from app.core.grammar import grammar_sha256
from app.models.syncode_mask_probe import WitnessEvidence
from app.research.syncode_mask_probe_prefix import sha256_utf8
from app.services.lossless_parser_analysis import analyze_lossless_source
from app.services.parser_analysis import analyze_verilog_source


def constructive_canonical_witness(
    *,
    prefix: str,
    candidate_decoded_text: str,
    completion_suffix: str,
) -> WitnessEvidence:
    """
    Build P + T + S and parse with the canonical Verilog grammar.

    Success proves the candidate boundary participates in at least one complete
    string accepted by the grammar — not which SynCode component caused masking.
    """
    witness = f"{prefix}{candidate_decoded_text}{completion_suffix}"
    # Exact constructive identity at the tested boundary (not a substring search).
    boundary_ok = witness == f"{prefix}{candidate_decoded_text}{completion_suffix}"
    if (
        len(prefix) + len(candidate_decoded_text) + len(completion_suffix)
        != len(witness)
        or witness[len(prefix) : len(prefix) + len(candidate_decoded_text)]
        != candidate_decoded_text
    ):
        boundary_ok = False

    structural = analyze_verilog_source(witness, method="mask_probe_witness")
    lossless = analyze_lossless_source(
        witness,
        timing="final_source",
        source_provenance="final_generated_source",
        include_structural=False,
    )

    success = structural.status == "complete_valid"
    err = ""
    if not success:
        err = structural.error_message or structural.label or structural.status

    warnings: list[str] = []
    if not boundary_ok:
        warnings.append(
            "witness != exact_prefix + candidate_decoded_text + completion_suffix"
        )

    return WitnessEvidence(
        oracle_kind="constructive_canonical",
        prefix=prefix,
        candidate_decoded_text=candidate_decoded_text,
        completion_suffix=completion_suffix,
        witness_source=witness,
        witness_sha256=sha256_utf8(witness),
        candidate_at_exact_boundary=boundary_ok,
        canonical_lark_parse_success=success,
        parse_error=err,
        lossless_completeness=lossless.completeness,
        grammar_sha256=grammar_sha256(),
        warnings=warnings,
    )


def minimal_grammar_control_witness(
    *,
    prefix: str,
    candidate_decoded_text: str,
    completion_suffix: str,
    minimal_grammar_text: str,
) -> WitnessEvidence:
    """
    Explicitly labelled control oracle — NEVER confuse with canonical Verilog.
    """
    from app.services.verilog_validation import _load_lark_module

    witness = f"{prefix}{candidate_decoded_text}{completion_suffix}"
    boundary_ok = witness == f"{prefix}{candidate_decoded_text}{completion_suffix}"
    warnings = [
        "minimal_grammar_control: success here is NOT canonical-Verilog success",
        "minimal-grammar success must not be presented as canonical-Verilog proof",
    ]
    if not boundary_ok:
        warnings.append(
            "witness != exact_prefix + candidate_decoded_text + completion_suffix"
        )
    success: Optional[bool] = None
    err = ""
    try:
        lark = _load_lark_module()
        if lark is None:
            raise ImportError("lark unavailable")
        parser = lark.Lark(minimal_grammar_text, parser="lalr")
        parser.parse(witness)
        success = True
    except Exception as exc:  # noqa: BLE001
        success = False
        err = f"{type(exc).__name__}: {exc}"

    return WitnessEvidence(
        oracle_kind="minimal_grammar_control",
        prefix=prefix,
        candidate_decoded_text=candidate_decoded_text,
        completion_suffix=completion_suffix,
        witness_source=witness,
        witness_sha256=sha256_utf8(witness),
        candidate_at_exact_boundary=boundary_ok,
        canonical_lark_parse_success=success,
        parse_error=err,
        lossless_completeness=None,
        grammar_sha256="minimal_grammar_control_not_canonical",
        warnings=warnings,
    )
