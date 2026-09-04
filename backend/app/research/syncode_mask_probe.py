"""
Main orchestrator for the SynCode mask diagnostic probe (Checkpoint 3A).

Research-only. Do not import from llm_service / FastAPI routes / generation.
"""

from __future__ import annotations

import hashlib
import os
import platform
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.core.grammar import (
    EXPECTED_GRAMMAR_SHA256,
    get_canonical_grammar_path,
    grammar_sha256,
    read_verilog_grammar,
)
from app.models.syncode_mask_probe import (
    PROBE_SCHEMA_VERSION,
    ByteTokenizerCandidateEvidence,
    ProbeCaseSpec,
    ProbeProvenance,
    SyncodeMaskProbeResult,
)
from app.research.syncode_mask_probe_attribution import attribute_mask
from app.research.syncode_mask_probe_byte_tokenizer import collect_byte_tokenizer_evidence
from app.research.syncode_mask_probe_mask_store import (
    MaskStoreCacheError,
    build_or_load_mask_store,
)
from app.research.syncode_mask_probe_oracle import (
    constructive_canonical_witness,
    minimal_grammar_control_witness,
)
from app.research.syncode_mask_probe_parser import (
    collect_parser_evidence,
    parse_result_for_mask_store,
)
from app.research.syncode_mask_probe_prefix import (
    ProbeCaseError,
    original_trace_token_text,
    prefix_metrics,
    resolve_case_prefix,
)
from app.research.syncode_mask_probe_report import build_root_cause, render_markdown_report
from app.research.syncode_mask_probe_tokenizer import collect_tokenizer_candidate_evidence
from app.research.syncode_mask_probe_version import (
    SyncodeAdapterError,
    SyncodeVersionError,
    collect_syncode_source_shas,
    require_candidate_ids_in_vocab,
    require_syncode_0416_adapter_surface,
    require_syncode_version,
    syncode_package_path,
)


def _git_commit_and_dirty() -> tuple[Optional[str], str, Optional[bool]]:
    repo = Path(__file__).resolve().parents[2]
    try:
        commit = (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                stderr=subprocess.DEVNULL,
                cwd=str(repo),
            )
            .decode()
            .strip()
        )
        dirty_out = subprocess.check_output(
            ["git", "status", "--porcelain"],
            stderr=subprocess.DEVNULL,
            cwd=str(repo),
        )
        dirty = bool(dirty_out.strip())
        return commit, "VERIFIED", dirty
    except Exception:  # noqa: BLE001
        return None, "UNAVAILABLE", None


def _pkg_version(name: str) -> str:
    try:
        import importlib.metadata as md

        return md.version(name)
    except Exception:  # noqa: BLE001
        return "UNAVAILABLE"


def _larkm_version() -> str:
    try:
        import syncode.larkm as larkm

        return str(getattr(larkm, "__version__", "") or "present")
    except Exception:  # noqa: BLE001
        return "UNAVAILABLE"


def _file_sha(path: Optional[str | Path]) -> Optional[str]:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def build_provenance(
    case: ProbeCaseSpec,
    *,
    tokenizer: Any = None,
    mask_store_identity: Any = None,
    case_file: Optional[Path] = None,
    allow_download: bool = False,
    local_files_only: bool = True,
    symbol_guard_status: str = "UNAVAILABLE",
    symbol_guard_detail: str = "",
) -> ProbeProvenance:
    commit, commit_status, dirty = _git_commit_and_dirty()
    syncode_ver, override = require_syncode_version(
        allow_unsupported=case.allow_unsupported_syncode_version
    )
    warnings: list[str] = []
    if override:
        warnings.append(
            f"UNSUPPORTED SynCode version override in effect: installed "
            f"{syncode_ver} (probe designed for 0.4.16)"
        )

    gsha = grammar_sha256()
    if case.expected_grammar_sha256 and case.expected_grammar_sha256 != gsha:
        raise ProbeCaseError(
            f"grammar SHA mismatch: expected {case.expected_grammar_sha256} got {gsha}"
        )
    if gsha != EXPECTED_GRAMMAR_SHA256:
        warnings.append(
            f"grammar SHA {gsha} differs from EXPECTED_GRAMMAR_SHA256 "
            f"{EXPECTED_GRAMMAR_SHA256}"
        )

    tok_rev = case.tokenizer_revision
    tok_rev_status = "UNAVAILABLE"
    if tok_rev:
        tok_rev_status = "INFERENCE"
        warnings.append(
            "tokenizer_revision is recorded as supplied by the case; not "
            "independently verified against a remote registry in this probe run"
        )

    import torch

    prov = ProbeProvenance(
        probe_schema_version=PROBE_SCHEMA_VERSION,
        repository_commit=commit,
        repository_commit_status=commit_status,  # type: ignore[arg-type]
        repository_dirty=dirty,
        case_file_sha256=_file_sha(case_file),
        trace_file_sha256=_file_sha(case.source_trace_path),
        timestamp_utc=datetime.now(timezone.utc).isoformat(),
        host=socket.gethostname(),
        platform=platform.platform(),
        python_version=sys.version.replace("\n", " "),
        syncode_version=syncode_ver,
        syncode_package_path=syncode_package_path(),
        syncode_version_override_used=override,
        syncode_symbol_guard_status=symbol_guard_status,  # type: ignore[arg-type]
        syncode_symbol_guard_detail=symbol_guard_detail,
        transformers_version=_pkg_version("transformers"),
        syncode_larkm_version=_larkm_version(),
        torch_version=torch.__version__,
        torch_device=str(torch.device("cuda" if torch.cuda.is_available() else "cpu")),
        tokenizer_model_id=case.tokenizer_model_id,
        tokenizer_revision=tok_rev,
        tokenizer_revision_status=tok_rev_status,  # type: ignore[arg-type]
        trust_remote_code=case.trust_remote_code,
        allow_download=allow_download,
        local_files_only=local_files_only,
        grammar_path=str(get_canonical_grammar_path()),
        grammar_sha256=gsha,
        parser_mode=case.parser_mode,
        syncode_mode=case.syncode_mode,
        mask_store=mask_store_identity,
        syncode_source_file_sha256=collect_syncode_source_shas(),
        warnings=warnings,
    )
    if tokenizer is not None:
        prov.tokenizer_class = type(tokenizer).__name__
        prov.vocabulary_size = int(getattr(tokenizer, "vocab_size", 0) or 0)
        prov.eos_token_id = getattr(tokenizer, "eos_token_id", None)
        prov.pad_token_id = getattr(tokenizer, "pad_token_id", None)
        prov.bos_token_id = getattr(tokenizer, "bos_token_id", None)
    return prov


def create_incremental_parser(grammar_text: str):
    from syncode.parsers import create_parser
    from syncode.parsers.grammars.grammar import Grammar

    grammar = Grammar(grammar_text)
    return create_parser(grammar), grammar


def _finalize_statuses(result: SyncodeMaskProbeResult) -> SyncodeMaskProbeResult:
    """Keep JSON/Markdown status fields consistent after root_cause is built."""
    if result.execution_status == "failed":
        result.report_status = "failed"
    elif result.execution_status == "complete":
        result.report_status = "complete"
    if result.root_cause is not None:
        result.supported_conclusion = result.root_cause.supported_conclusion
        result.causal_conclusion_status = result.root_cause.causal_conclusion_status
        result.unresolved_reasons = list(result.root_cause.unresolved_reasons)
    return result


def _fail(
    result: SyncodeMaskProbeResult,
    *,
    stage: str,
    error: str,
) -> SyncodeMaskProbeResult:
    result.execution_status = "failed"
    result.report_status = "failed"
    result.failure_stage = stage
    result.errors = list(result.errors) + [error]
    result.causal_conclusion_status = "unresolved"
    result.supported_conclusion = "unresolved_internal_evidence_unavailable"
    result.root_cause = build_root_cause(result)
    result.root_cause.supported_conclusion = (
        "unresolved_internal_evidence_unavailable"
    )
    result.root_cause.causal_conclusion_status = "unresolved"
    result.root_cause.remaining_uncertainty = list(
        result.root_cause.remaining_uncertainty
    ) + [f"probe failed at stage={stage}"]
    result.root_cause.unresolved_reasons = list(
        result.root_cause.unresolved_reasons
    ) + [f"execution_status=failed failure_stage={stage}"]
    return _finalize_statuses(result)


def run_probe(
    case: ProbeCaseSpec,
    *,
    tokenizer: Any,
    cache_root: Path,
    minimal_grammar_control: Optional[str] = None,
    skip_mask_store: bool = False,
    grammar_text: Optional[str] = None,
    case_file: Optional[Path] = None,
    allow_download: bool = False,
    local_files_only: bool = True,
    run_causal_trace: bool = False,
    run_number_causal_trace: bool = False,
    newline_token_id: int = 1010,
    space_token_id: int = 1032,
    short_number_token_id: Optional[int] = None,
    long_number_token_id: Optional[int] = None,
) -> SyncodeMaskProbeResult:
    """
    Execute the diagnostic probe.

    ``tokenizer`` must be provided by the caller (never loaded from llm_service).
    ``grammar_text`` defaults to the canonical Verilog grammar; tests may supply
    a minimal grammar. Production Verilog mask store is never built by unit tests
    that pass a minimal grammar + tiny vocab.
    """
    result = SyncodeMaskProbeResult(
        case=case,
        report_status="incomplete",
        execution_status=None,
        causal_conclusion_status="unresolved",
    )
    warnings: list[str] = []
    symbol_status = "UNAVAILABLE"
    symbol_detail = ""
    # Retain locals for optional causal pass
    _mask_store_for_causal = None
    _pr_mask_for_causal = None
    _gtext_for_causal = None

    try:
        require_syncode_version(
            allow_unsupported=case.allow_unsupported_syncode_version
        )
        if not case.allow_unsupported_syncode_version:
            symbol_detail, _sigs = require_syncode_0416_adapter_surface()
            symbol_status = "VERIFIED"
        else:
            symbol_detail = "symbol guard skipped due to unsupported-version override"
            symbol_status = "INFERENCE"
            warnings.append(symbol_detail)
    except (SyncodeVersionError, SyncodeAdapterError) as exc:
        result.provenance = ProbeProvenance(
            timestamp_utc=datetime.now(timezone.utc).isoformat(),
            syncode_symbol_guard_status="CONTRADICTED",
            syncode_symbol_guard_detail=str(exc),
        )
        return _fail(result, stage="version_or_adapter_guard", error=str(exc))

    try:
        require_candidate_ids_in_vocab(tokenizer, list(case.candidate_token_ids))
    except SyncodeAdapterError as exc:
        return _fail(result, stage="candidate_vocab_bounds", error=str(exc))

    try:
        if case.expected_tokenizer_model and case.tokenizer_model_id:
            if case.expected_tokenizer_model != case.tokenizer_model_id:
                raise ProbeCaseError(
                    f"tokenizer model mismatch: expected "
                    f"{case.expected_tokenizer_model} got {case.tokenizer_model_id}"
                )
        if case.expected_tokenizer_revision and case.tokenizer_revision:
            if case.expected_tokenizer_revision != case.tokenizer_revision:
                raise ProbeCaseError(
                    f"tokenizer revision mismatch: expected "
                    f"{case.expected_tokenizer_revision} got {case.tokenizer_revision}"
                )

        prefix, prefix_warnings = resolve_case_prefix(case)
        warnings.extend(prefix_warnings)
    except ProbeCaseError as exc:
        return _fail(result, stage="trace_prefix_reconstruction", error=str(exc))

    metrics = prefix_metrics(prefix)
    result.prefix_text = metrics["prefix_text"]
    result.prefix_sha256_utf8 = metrics["prefix_sha256_utf8"]
    result.prefix_character_count = metrics["prefix_character_count"]
    result.prefix_utf8_byte_count = metrics["prefix_utf8_byte_count"]

    expected_map = case.expected_decoded_candidates or {}
    for tid in case.candidate_token_ids:
        ev = collect_tokenizer_candidate_evidence(
            tokenizer,
            tid,
            expected_decode=expected_map.get(str(tid)),
            original_trace_token_text=original_trace_token_text(case, tid),
        )
        result.tokenizer_candidates.append(ev)
        if (
            expected_map.get(str(tid)) is not None
            and ev.decode_cleanup_disabled != expected_map.get(str(tid))
        ):
            return _fail(
                result,
                stage="tokenizer_decode_expectation",
                error=(
                    f"candidate {tid} decode {ev.decode_cleanup_disabled!r} "
                    f"!= expected {expected_map.get(str(tid))!r}"
                ),
            )

    gtext = grammar_text if grammar_text is not None else read_verilog_grammar()
    try:
        inc_parser, grammar = create_incremental_parser(gtext)
        result.parser = collect_parser_evidence(
            inc_parser, prefix, syncode_version="0.4.16"
        )
    except Exception as exc:  # noqa: BLE001
        return _fail(result, stage="parser_evidence", error=f"{type(exc).__name__}: {exc}")

    mask_identity = None
    byte_tok = None
    if not skip_mask_store:
        try:
            if case.mask_store_mode == "existing_cache" and not str(cache_root).strip():
                raise MaskStoreCacheError(
                    "existing_cache requires an explicit --cache-root path"
                )
            mask_store, mask_identity = build_or_load_mask_store(
                mode=case.mask_store_mode,
                cache_root=Path(cache_root),
                grammar=grammar,
                tokenizer=tokenizer,
                syncode_mode=case.syncode_mode,
            )
            byte_tok = getattr(mask_store, "byte_tokenizer", None)
            inc2, _ = create_incremental_parser(gtext)
            if hasattr(inc2, "reset"):
                inc2.reset()
            raw_pr = inc2.get_acceptable_next_terminals(prefix)
            pr_mask = parse_result_for_mask_store(raw_pr)
            result.mask_attribution = attribute_mask(
                mask_store,
                pr_mask,
                candidate_token_ids=list(case.candidate_token_ids),
                byte_tokenizer=byte_tok,
                current_accept_terminals=result.parser.current_accept_terminals
                if result.parser
                else None,
                next_accept_terminals=result.parser.next_accept_terminals
                if result.parser
                else None,
                ignore_terminals=result.parser.ignore_terminals
                if result.parser
                else None,
            )
            _mask_store_for_causal = mask_store
            _pr_mask_for_causal = pr_mask
            _gtext_for_causal = gtext
            if (
                result.mask_attribution is not None
                and not result.mask_attribution.attribution_reliable
            ):
                return _fail(
                    result,
                    stage="mask_attribution_union_mismatch",
                    error=(
                        "reconstructed per-sequence union differs from runtime "
                        "get_accept_mask; attribution marked unreliable"
                    ),
                )
        except Exception as exc:  # noqa: BLE001
            return _fail(
                result,
                stage="mask_store_or_attribution",
                error=f"{type(exc).__name__}: {exc}",
            )
    else:
        warnings.append("skip_mask_store=True; mask attribution not executed")

    if byte_tok is not None:
        for tc in result.tokenizer_candidates:
            result.byte_tokenizer_candidates.append(
                collect_byte_tokenizer_evidence(
                    byte_tok,
                    tc.token_id,
                    hf_decoded_text=tc.decode_cleanup_disabled,
                    raw_vocab_entry=tc.convert_ids_to_tokens,
                )
            )
    else:
        for tid in case.candidate_token_ids:
            result.byte_tokenizer_candidates.append(
                ByteTokenizerCandidateEvidence(
                    token_id=tid,
                    equivalence_status="UNAVAILABLE",
                    equivalence_detail="ByteTokenizer unavailable (mask store skipped)",
                )
            )

    if case.candidate_witness_suffixes or case.witness_completion_suffix is not None:
        for tc in result.tokenizer_candidates:
            if tc.decode_cleanup_disabled is None:
                continue
            suffix = None
            if case.candidate_witness_suffixes:
                suffix = case.candidate_witness_suffixes.get(
                    str(tc.token_id)
                ) or case.candidate_witness_suffixes.get(tc.decode_cleanup_disabled)
            if suffix is None:
                suffix = case.witness_completion_suffix
            if suffix is None:
                continue
            result.witnesses.append(
                constructive_canonical_witness(
                    prefix=prefix,
                    candidate_decoded_text=tc.decode_cleanup_disabled,
                    completion_suffix=suffix,
                )
            )
    elif case.witness_source_file:
        text = Path(case.witness_source_file).read_text(encoding="utf-8")
        for tc in result.tokenizer_candidates:
            dec = tc.decode_cleanup_disabled or ""
            if text.startswith(prefix + dec):
                suffix = text[len(prefix) + len(dec) :]
                result.witnesses.append(
                    constructive_canonical_witness(
                        prefix=prefix,
                        candidate_decoded_text=dec,
                        completion_suffix=suffix,
                    )
                )
            else:
                warnings.append(
                    f"witness file does not start with prefix+decode({tc.token_id})"
                )

    if minimal_grammar_control:
        for tc in result.tokenizer_candidates:
            if tc.decode_cleanup_disabled is None:
                continue
            result.witnesses.append(
                minimal_grammar_control_witness(
                    prefix=prefix,
                    candidate_decoded_text=tc.decode_cleanup_disabled,
                    completion_suffix=case.witness_completion_suffix or "",
                    minimal_grammar_text=minimal_grammar_control,
                )
            )

    try:
        result.provenance = build_provenance(
            case,
            tokenizer=tokenizer,
            mask_store_identity=mask_identity,
            case_file=case_file,
            allow_download=allow_download,
            local_files_only=local_files_only,
            symbol_guard_status=symbol_status,
            symbol_guard_detail=symbol_detail,
        )
    except ProbeCaseError as exc:
        return _fail(result, stage="provenance", error=str(exc))

    result.warnings = warnings

    # Checkpoint 3C causal differential (optional; requires mask store).
    if (
        run_causal_trace
        and _mask_store_for_causal is not None
        and _pr_mask_for_causal is not None
        and result.mask_attribution is not None
    ):
        try:
            from app.research.syncode_mask_probe_causal import build_causal_differential

            nl_bytes = b"\n"
            sp_bytes = b" "
            # Prefer ByteTokenizer bytes when available
            for bc in result.byte_tokenizer_candidates:
                if bc.token_id == newline_token_id and bc.syncode_bytes_hex:
                    nl_bytes = bytes.fromhex(bc.syncode_bytes_hex)
                if bc.token_id == space_token_id and bc.syncode_bytes_hex:
                    sp_bytes = bytes.fromhex(bc.syncode_bytes_hex)
            result.causal = build_causal_differential(
                mask_store=_mask_store_for_causal,
                parse_result=_pr_mask_for_causal,
                grammar_text=_gtext_for_causal or gtext,
                newline_token_id=newline_token_id,
                space_token_id=space_token_id,
                newline_bytes=nl_bytes,
                space_bytes=sp_bytes,
                runtime_bits=dict(result.mask_attribution.runtime_mask_bits),
                reconstructed_bits=dict(
                    result.mask_attribution.reconstructed_union_bits
                ),
                ignore_terminals=(
                    result.parser.ignore_terminals if result.parser else None
                ),
                current_accept_terminals=(
                    result.parser.current_accept_terminals if result.parser else None
                ),
                next_accept_terminals=(
                    result.parser.next_accept_terminals if result.parser else None
                ),
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"causal trace failed: {type(exc).__name__}: {exc}")
            result.warnings = warnings

    # Checkpoint 3D based-number causal differential (optional).
    if (
        run_number_causal_trace
        and _mask_store_for_causal is not None
        and _pr_mask_for_causal is not None
        and result.mask_attribution is not None
    ):
        try:
            from app.research.syncode_mask_probe_number_causal import (
                build_based_number_causal,
            )

            short_id = short_number_token_id
            long_id = long_number_token_id
            if short_id is None and case.raw_argmax_token_id is not None:
                short_id = int(case.raw_argmax_token_id)
            if long_id is None and case.selected_token_id is not None:
                long_id = int(case.selected_token_id)
            if short_id is None or long_id is None:
                raise ValueError(
                    "run_number_causal_trace requires short/long token IDs "
                    "(raw_argmax/selected or explicit args)"
                )
            short_bytes = b"'h"
            long_bytes = b"'ha"
            short_decode = "'h"
            long_decode = "'ha"
            for bc in result.byte_tokenizer_candidates:
                if bc.token_id == short_id and bc.syncode_bytes_hex:
                    short_bytes = bytes.fromhex(bc.syncode_bytes_hex)
                if bc.token_id == long_id and bc.syncode_bytes_hex:
                    long_bytes = bytes.fromhex(bc.syncode_bytes_hex)
            for tc in result.tokenizer_candidates:
                if tc.token_id == short_id and tc.decode_cleanup_disabled is not None:
                    short_decode = tc.decode_cleanup_disabled
                if tc.token_id == long_id and tc.decode_cleanup_disabled is not None:
                    long_decode = tc.decode_cleanup_disabled
            rem_text = "16"
            if result.parser and result.parser.remainder_text:
                rem_text = result.parser.remainder_text
            result.number_causal = build_based_number_causal(
                mask_store=_mask_store_for_causal,
                parse_result=_pr_mask_for_causal,
                grammar_text=_gtext_for_causal or gtext,
                short_token_id=short_id,
                long_token_id=long_id,
                short_bytes=short_bytes,
                long_bytes=long_bytes,
                short_decode=short_decode,
                long_decode=long_decode,
                runtime_bits=dict(result.mask_attribution.runtime_mask_bits),
                reconstructed_bits=dict(
                    result.mask_attribution.reconstructed_union_bits
                ),
                remainder_text=rem_text,
                ignore_terminals=(
                    result.parser.ignore_terminals if result.parser else None
                ),
                current_accept_terminals=(
                    result.parser.current_accept_terminals if result.parser else None
                ),
                next_accept_terminals=(
                    result.parser.next_accept_terminals if result.parser else None
                ),
            )
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                f"number causal trace failed: {type(exc).__name__}: {exc}"
            )
            result.warnings = warnings

    # Set execution complete BEFORE building root_cause so status text matches.
    result.execution_status = "complete"
    result.report_status = "complete"
    result.failure_stage = None
    result.root_cause = build_root_cause(result)
    return _finalize_statuses(result)


def write_probe_outputs(
    result: SyncodeMaskProbeResult, output_dir: Path
) -> tuple[Path, Path]:
    """Atomically write JSON and Markdown so partial files cannot look complete."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{result.case.case_id}.json"
    md_path = output_dir / f"{result.case.case_id}.md"
    _atomic_write_text(json_path, result.model_dump_json(indent=2) + "\n")
    _atomic_write_text(md_path, render_markdown_report(result))
    return json_path, md_path
