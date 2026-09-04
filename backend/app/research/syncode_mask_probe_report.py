"""Root-cause Markdown/JSON decision report for the mask probe."""

from __future__ import annotations

from typing import Optional

from app.models.syncode_mask_probe import (
    EvidenceItem,
    RootCauseKind,
    RootCauseReport,
    SyncodeMaskProbeResult,
)


def build_root_cause(result: SyncodeMaskProbeResult) -> RootCauseReport:
    answers: list[EvidenceItem] = []
    uncertainty: list[str] = []
    unresolved: list[str] = []
    divergence: Optional[str] = None
    conclusion: RootCauseKind = "unresolved_internal_evidence_unavailable"
    causal_status = "unresolved"
    minimal_control_conclusion: Optional[RootCauseKind] = None
    original_nemotron_conclusion: Optional[RootCauseKind] = None
    conclusion_scope = None

    # Prefer Checkpoint 3D based-number causal when present and reliable.
    if result.number_causal is not None:
        from app.research.syncode_mask_probe_number_causal import (
            conclude_from_number_causal,
        )

        c_conc, c_status, c_unresolved, scope = conclude_from_number_causal(
            result.number_causal
        )
        conclusion = c_conc  # type: ignore[assignment]
        causal_status = c_status
        unresolved.extend(c_unresolved)
        # Persist scoping on the causal evidence object for JSON reports.
        result.number_causal.minimal_control_conclusion = scope.get(
            "minimal_control_conclusion"
        )
        result.number_causal.original_nemotron_conclusion = scope.get(
            "original_nemotron_conclusion"
        )
        result.number_causal.conclusion_scope = scope.get(  # type: ignore[assignment]
            "conclusion_scope", "mixed_pending_nscc"
        )
        minimal_control_conclusion = scope.get(  # type: ignore[assignment]
            "minimal_control_conclusion"
        )
        original_nemotron_conclusion = scope.get(  # type: ignore[assignment]
            "original_nemotron_conclusion"
        )
        conclusion_scope = scope.get("conclusion_scope")
        answers.append(
            EvidenceItem(
                claim="'h vs 'ha first differing NUMBER construction stage",
                classification=(
                    "VERIFIED" if c_status == "conclusive" else "UNAVAILABLE"
                ),
                detail=(
                    f"field={result.number_causal.first_differing_field}; "
                    f"reason={result.number_causal.first_differing_reason_code}; "
                    f"{result.number_causal.first_differing_detail}"
                ),
            )
        )
        answers.append(
            EvidenceItem(
                claim="minimal_control_conclusion",
                classification="VERIFIED"
                if scope.get("minimal_control_conclusion", "").startswith("verified_")
                else "UNAVAILABLE",
                detail=str(scope.get("minimal_control_conclusion")),
            )
        )
        answers.append(
            EvidenceItem(
                claim="original_nemotron_conclusion",
                classification=(
                    "VERIFIED"
                    if scope.get("original_nemotron_conclusion", "").startswith(
                        "verified_"
                    )
                    else "UNAVAILABLE"
                ),
                detail=(
                    f"{scope.get('original_nemotron_conclusion')} "
                    f"(scope={scope.get('conclusion_scope')})"
                ),
            )
        )
        if result.number_causal.first_differing_field:
            divergence = (
                f"{result.number_causal.first_differing_field}: "
                f"{result.number_causal.first_differing_reason_code}"
            )
        nf = result.number_causal.number_fsm
        if nf is not None:
            answers.append(
                EvidenceItem(
                    claim="NUMBER FSM live non-final after 'h",
                    classification=(
                        "VERIFIED"
                        if (
                            nf.state_after_short_is_live is True
                            and nf.state_after_short_is_final is False
                        )
                        else "CONTRADICTED"
                        if nf.state_after_short_is_final is True
                        else "UNAVAILABLE"
                    ),
                    detail=nf.detail or nf.classification,
                )
            )
        answers.append(
            EvidenceItem(
                claim="fixed-k hypothesis independently tested",
                classification=result.number_causal.fixed_k_status,
                detail=result.number_causal.fixed_k_detail,
            )
        )

    # Prefer Checkpoint 3C causal differential when present and reliable.
    elif result.causal is not None:
        from app.research.syncode_mask_probe_causal import conclude_from_causal

        c_conc, c_status, c_unresolved = conclude_from_causal(result.causal)
        conclusion = c_conc  # type: ignore[assignment]
        causal_status = c_status
        unresolved.extend(c_unresolved)
        answers.append(
            EvidenceItem(
                claim="newline vs space first differing construction stage",
                classification=(
                    "VERIFIED" if c_status == "conclusive" else "UNAVAILABLE"
                ),
                detail=(
                    f"field={result.causal.first_differing_field}; "
                    f"reason={result.causal.first_differing_reason_code}; "
                    f"{result.causal.first_differing_detail}"
                ),
            )
        )
        if result.causal.first_differing_field:
            divergence = (
                f"{result.causal.first_differing_field}: "
                f"{result.causal.first_differing_reason_code}"
            )
        answers.append(
            EvidenceItem(
                claim="SynCode WS DFA accepts LF (0a)",
                classification=(
                    "VERIFIED"
                    if result.causal.ws_dfa_accepts.get("lf_0a") is True
                    else "CONTRADICTED"
                    if result.causal.ws_dfa_accepts.get("lf_0a") is False
                    else "UNAVAILABLE"
                ),
                detail=result.causal.ws_grammar_definition_verbatim,
            )
        )
        answers.append(
            EvidenceItem(
                claim="fixed-k hypothesis independently tested",
                classification=result.causal.fixed_k_status,
                detail=result.causal.fixed_k_detail,
            )
        )

    # 1. Witness
    canon = next(
        (w for w in result.witnesses if w.oracle_kind == "constructive_canonical"),
        None,
    )
    if canon is None:
        answers.append(
            EvidenceItem(
                claim="canonical witness parse",
                classification="UNAVAILABLE",
                detail="no constructive witness was supplied",
            )
        )
        uncertainty.append("no constructive witness")
    elif canon.canonical_lark_parse_success is True:
        answers.append(
            EvidenceItem(
                claim="canonical witness parse",
                classification="VERIFIED",
                detail="P+T+S parses with canonical grammar",
            )
        )
    elif canon.canonical_lark_parse_success is False:
        answers.append(
            EvidenceItem(
                claim="canonical witness parse",
                classification="CONTRADICTED",
                detail=canon.parse_error or "witness rejected",
            )
        )
        if divergence is None:
            divergence = "grammar/witness problem"
        if causal_status != "conclusive":
            conclusion = "grammar_witness_problem"

    # 2. Trace candidate decode
    for tc in result.tokenizer_candidates:
        expected = None
        if result.case.expected_decoded_candidates:
            expected = result.case.expected_decoded_candidates.get(str(tc.token_id))
        if expected is None:
            answers.append(
                EvidenceItem(
                    claim=f"candidate {tc.token_id} decodes as expected",
                    classification="UNAVAILABLE",
                    detail="no expected_decoded_candidates entry",
                )
            )
            continue
        if tc.decode_cleanup_disabled == expected:
            answers.append(
                EvidenceItem(
                    claim=f"candidate {tc.token_id} decodes as expected",
                    classification="VERIFIED",
                    detail=repr(expected),
                )
            )
        else:
            answers.append(
                EvidenceItem(
                    claim=f"candidate {tc.token_id} decodes as expected",
                    classification="CONTRADICTED",
                    detail=f"got {tc.decode_cleanup_disabled!r} expected {expected!r}",
                )
            )
            if divergence is None:
                divergence = "tokenizer decode mismatch"
            if causal_status != "conclusive" and conclusion == (
                "unresolved_internal_evidence_unavailable"
            ):
                conclusion = "tokenizer_decode_mismatch"

    # 3. ByteTokenizer
    for bc in result.byte_tokenizer_candidates:
        answers.append(
            EvidenceItem(
                claim=f"ByteTokenizer representation for {bc.token_id}",
                classification=bc.equivalence_status,
                detail=bc.equivalence_detail or bc.syncode_bytes_repr,
            )
        )
        if bc.equivalence_status == "CONTRADICTED" and divergence is None:
            divergence = "ByteTokenizer conversion mismatch"
            if causal_status != "conclusive":
                conclusion = "byte_tokenizer_conversion_mismatch"

    # 4–7 Mask / accept sequences
    ma = result.mask_attribution
    if ma is None:
        answers.append(
            EvidenceItem(
                claim="runtime / attributed mask",
                classification="UNAVAILABLE",
                detail="mask attribution not run",
            )
        )
        uncertainty.append("mask attribution unavailable")
    else:
        answers.append(
            EvidenceItem(
                claim="reconstructed union equals runtime get_accept_mask",
                classification="VERIFIED" if ma.attribution_reliable else "CONTRADICTED",
                detail=(
                    f"differing_bit_count={ma.differing_bit_count}; "
                    f"reliable={ma.attribution_reliable}"
                ),
            )
        )
        if not ma.attribution_reliable and divergence is None:
            divergence = "mask attribution unreliable vs runtime union"
            if causal_status != "conclusive":
                conclusion = "integration_instrumentation_problem"

        for tid, admitted in ma.runtime_mask_bits.items():
            answers.append(
                EvidenceItem(
                    claim=f"final union admits candidate {tid}",
                    classification="VERIFIED",
                    detail=f"admitted={admitted}",
                )
            )
            if (
                causal_status != "conclusive"
                and admitted
                and conclusion == "unresolved_internal_evidence_unavailable"
            ):
                conclusion = "candidate_admitted_by_mask"
            if (
                causal_status != "conclusive"
                and not admitted
                and canon is not None
                and canon.canonical_lark_parse_success
                and conclusion
                in (
                    "unresolved_internal_evidence_unavailable",
                    "candidate_admitted_by_mask",
                )
            ):
                conclusion = "candidate_rejected_by_verified_mask"
                if divergence is None:
                    divergence = (
                        f"candidate {tid} rejected by verified runtime mask while "
                        "canonical witness parses"
                    )

        if ma.attribution_reliable:
            for seq in ma.per_sequence:
                for c in seq.candidates:
                    if c.contributed_bit:
                        answers.append(
                            EvidenceItem(
                                claim=(
                                    f"accept sequence {seq.terminals} admits "
                                    f"candidate {c.token_id}"
                                ),
                                classification="VERIFIED",
                                detail=f"construction={seq.construction_kind}",
                            )
                        )

        answers.append(
            EvidenceItem(
                claim="detailed DFA transitions",
                classification=ma.dfa_transitions_status,
                detail=ma.dfa_transitions_detail,
            )
        )
        uncertainty.append("DFA byte-transition trace unavailable in 0.4.16 public API")

    answers.append(
        EvidenceItem(
            claim="fresh cache differs from existing cache",
            classification="UNAVAILABLE",
            detail=(
                "run compare_syncode_mask_probes.py on existing_cache vs "
                "fresh_isolated reports"
            ),
        )
    )

    uncertainty.append(
        "raw_argmax_blocked=True alone never proves a SynCode bug; "
        "this report requires verified divergence evidence"
    )

    # Execution failure must not be confused with causal unresolved.
    exec_status = result.execution_status or (
        "failed"
        if result.report_status == "failed"
        else "complete"
        if result.report_status == "complete"
        else None
    )
    if exec_status == "failed":
        conclusion = "unresolved_internal_evidence_unavailable"
        causal_status = "unresolved"
        unresolved.append(
            f"execution_status=failed failure_stage={result.failure_stage}"
        )
        uncertainty.append(
            f"execution_status=failed; no SynCode-bug conclusion permitted "
            f"(failure_stage={result.failure_stage})"
        )

    for item in answers:
        if item.classification == "INFERENCE":
            uncertainty.append(f"inference retained (not verified): {item.claim}")

    if causal_status != "conclusive":
        unresolved.extend([u for u in uncertainty if u not in unresolved])

    return RootCauseReport(
        answers=answers,
        first_verified_divergence=divergence,
        supported_conclusion=conclusion,
        minimal_control_conclusion=minimal_control_conclusion,
        original_nemotron_conclusion=original_nemotron_conclusion,
        conclusion_scope=conclusion_scope,  # type: ignore[arg-type]
        remaining_uncertainty=uncertainty,
        unresolved_reasons=unresolved,
        causal_conclusion_status=causal_status,  # type: ignore[arg-type]
    )


def render_markdown_report(result: SyncodeMaskProbeResult) -> str:
    rc = result.root_cause or build_root_cause(result)
    exec_status = result.execution_status or result.report_status
    lines = [
        f"# SynCode mask probe — {result.case.case_id}",
        "",
        f"- Schema: `{result.schema_version}`",
        f"- execution_status: `{exec_status}`",
        f"- report_status: `{result.report_status}` "
        f"(legacy mirror of execution_status when finished)",
        f"- causal_conclusion_status: `{result.causal_conclusion_status}`",
        f"- supported_conclusion: `{rc.supported_conclusion}`",
        f"- minimal_control_conclusion: `{rc.minimal_control_conclusion}`",
        f"- original_nemotron_conclusion: `{rc.original_nemotron_conclusion}`",
        f"- conclusion_scope: `{rc.conclusion_scope}`",
        f"- failure_stage: `{result.failure_stage}`",
        f"- SynCode: `{result.provenance.syncode_version}` "
        f"(override={result.provenance.syncode_version_override_used})",
        f"- Grammar SHA-256: `{result.provenance.grammar_sha256}`",
        f"- Prefix SHA-256: `{result.prefix_sha256_utf8}` "
        f"({result.prefix_character_count} chars / "
        f"{result.prefix_utf8_byte_count} UTF-8 bytes)",
        f"- Prefix repr: `{result.prefix_text!r}`",
        f"- allow_download={result.provenance.allow_download} "
        f"local_files_only={result.provenance.local_files_only} "
        f"trust_remote_code={result.provenance.trust_remote_code}",
        "",
        "## Decision sequence",
        "",
    ]
    for i, item in enumerate(rc.answers, start=1):
        lines.append(
            f"{i}. **{item.classification}** — {item.claim}: {item.detail}"
        )
    lines += [
        "",
        f"**First verified divergence:** {rc.first_verified_divergence or 'none'}",
        f"**Supported conclusion:** `{result.supported_conclusion}`",
        f"**Causal conclusion status:** `{result.causal_conclusion_status}`",
        "",
        "### Unresolved reasons",
        "",
    ]
    for u in result.unresolved_reasons or rc.unresolved_reasons:
        lines.append(f"- {u}")
    lines += ["", "### Remaining uncertainty", ""]
    for u in rc.remaining_uncertainty:
        lines.append(f"- {u}")
    if result.errors:
        lines += ["", "### Errors", ""]
        for e in result.errors:
            lines.append(f"- {e}")
    lines.append("")
    return "\n".join(lines)
