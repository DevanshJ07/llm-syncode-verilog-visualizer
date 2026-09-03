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
    divergence: Optional[str] = None
    conclusion: RootCauseKind = "unresolved_internal_evidence_unavailable"

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
            if conclusion == "unresolved_internal_evidence_unavailable":
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
            conclusion = "integration_instrumentation_problem"

        for tid, admitted in ma.runtime_mask_bits.items():
            answers.append(
                EvidenceItem(
                    claim=f"final union admits candidate {tid}",
                    classification="VERIFIED",
                    detail=f"admitted={admitted}",
                )
            )
            if admitted and conclusion == "unresolved_internal_evidence_unavailable":
                conclusion = "candidate_admitted_by_mask"
            if (
                not admitted
                and canon is not None
                and canon.canonical_lark_parse_success
                and conclusion
                in (
                    "unresolved_internal_evidence_unavailable",
                    "candidate_admitted_by_mask",
                )
            ):
                # Grammar allows a completion through this boundary but mask rejects.
                conclusion = "candidate_rejected_by_verified_mask"
                if divergence is None:
                    divergence = (
                        f"candidate {tid} rejected by verified runtime mask while "
                        "canonical witness parses"
                    )

        if ma.attribution_reliable:
            # Which sequences admit each candidate
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

    # 8. Cache comparison is external — note if only one mode present
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

    # Never conclude "SynCode bug confirmed" from raw_argmax_blocked alone.
    uncertainty.append(
        "raw_argmax_blocked=True alone never proves a SynCode bug; "
        "this report requires verified divergence evidence"
    )
    if result.report_status in ("failed", "incomplete"):
        conclusion = "unresolved_internal_evidence_unavailable"
        uncertainty.append(
            f"report_status={result.report_status}; no SynCode-bug conclusion permitted"
        )

    # INFERENCE classifications must not be upgraded to VERIFIED here.
    for item in answers:
        if item.classification == "INFERENCE":
            uncertainty.append(
                f"inference retained (not verified): {item.claim}"
            )

    return RootCauseReport(
        answers=answers,
        first_verified_divergence=divergence,
        supported_conclusion=conclusion,
        remaining_uncertainty=uncertainty,
    )


def render_markdown_report(result: SyncodeMaskProbeResult) -> str:
    rc = result.root_cause or build_root_cause(result)
    lines = [
        f"# SynCode mask probe — {result.case.case_id}",
        "",
        f"- Schema: `{result.schema_version}`",
        f"- Report status: `{result.report_status}`"
        + (
            f" (failure_stage=`{result.failure_stage}`)"
            if result.failure_stage
            else ""
        ),
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
        f"**Supported conclusion:** `{rc.supported_conclusion}`",
        "",
        "### Remaining uncertainty",
        "",
    ]
    for u in rc.remaining_uncertainty:
        lines.append(f"- {u}")
    if result.errors:
        lines += ["", "### Errors", ""]
        for e in result.errors:
            lines.append(f"- {e}")
    lines.append("")
    return "\n".join(lines)
