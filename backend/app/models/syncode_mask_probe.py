"""
SynCode mask diagnostic probe DTOs (Checkpoint 3A).

Research-only schemas. Not imported by production generation / routes.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

PROBE_SCHEMA_VERSION = "syncode_mask_probe_v1"
SUPPORTED_SYNCODE_VERSION = "0.4.16"

EvidenceClass = Literal["VERIFIED", "CONTRADICTED", "UNAVAILABLE", "INFERENCE"]

PrefixSource = Literal["reconstructed_from_selected_tokens", "explicit"]
MaskStoreMode = Literal["existing_cache", "fresh_isolated"]
ParserMode = Literal["lalr"]

RootCauseKind = Literal[
    "grammar_witness_problem",
    "incorrect_candidate_token_id",
    "tokenizer_decode_mismatch",
    "byte_tokenizer_conversion_mismatch",
    "parser_remainder_problem",
    "accept_sequence_fixed_k_limitation",
    "dfa_lookup_problem",
    "mask_union_problem",
    "stale_incompatible_mask_store_cache",
    "integration_instrumentation_problem",
    "unresolved_internal_evidence_unavailable",
    "candidate_admitted_by_mask",
    "candidate_rejected_by_verified_mask",
]


class ProbeCaseSpec(BaseModel):
    """JSON case specification (no CLI-escaped multiline prefixes)."""

    case_id: str
    description: str = ""
    source_trace_path: Optional[str] = None
    prompt_id: Optional[str] = None
    step_index: Optional[int] = None
    step_index_unit: Literal["zero_based"] = "zero_based"
    prefix_source: PrefixSource = "reconstructed_from_selected_tokens"
    explicit_prefix_file: Optional[str] = None
    candidate_token_ids: list[int] = Field(default_factory=list)
    expected_decoded_candidates: Optional[dict[str, str]] = None
    selected_token_id: Optional[int] = None
    raw_argmax_token_id: Optional[int] = None
    witness_source_file: Optional[str] = None
    witness_completion_suffix: Optional[str] = None
    expected_grammar_sha256: Optional[str] = None
    expected_tokenizer_model: Optional[str] = None
    expected_tokenizer_revision: Optional[str] = None
    tokenizer_model_id: Optional[str] = None
    tokenizer_revision: Optional[str] = None
    trust_remote_code: bool = False
    parser_mode: ParserMode = "lalr"
    mask_store_mode: MaskStoreMode = "fresh_isolated"
    mask_store_cache_path: Optional[str] = None
    syncode_mode: str = "grammar_mask"
    allow_unsupported_syncode_version: bool = False
    # Optional inline trace steps for offline fixtures: [{selected_token, selected_token_id?}, ...]
    inline_trace_steps: Optional[list[dict[str, Any]]] = None


class EvidenceItem(BaseModel):
    claim: str
    classification: EvidenceClass
    detail: str = ""
    data: Optional[dict[str, Any]] = None


class TokenizerCandidateEvidence(BaseModel):
    token_id: int
    convert_ids_to_tokens: Optional[str] = None
    decode_cleanup_disabled: Optional[str] = None
    decode_repr: Optional[str] = None
    unicode_codepoints: list[int] = Field(default_factory=list)
    utf8_bytes: list[int] = Field(default_factory=list)
    utf8_hex: str = ""
    encode_roundtrip_ids: list[int] = Field(default_factory=list)
    roundtrip_returns_original_id: Optional[bool] = None
    is_special: Optional[bool] = None
    original_trace_token_text: Optional[str] = None
    trace_text_equals_decode: Optional[bool] = None
    warnings: list[str] = Field(default_factory=list)


class ByteTokenizerCandidateEvidence(BaseModel):
    token_id: int
    vocab_type: Optional[str] = None
    raw_vocab_entry: Optional[str] = None
    syncode_byte_sequence: Optional[list[int]] = None
    syncode_bytes_hex: str = ""
    syncode_bytes_repr: str = ""
    method_symbol: str = ""
    matches_hf_decoded_utf8_bytes: Optional[bool] = None
    equivalence_status: EvidenceClass = "UNAVAILABLE"
    equivalence_detail: str = ""
    warnings: list[str] = Field(default_factory=list)


class AcceptSequenceProbeRecord(BaseModel):
    terminals: list[str]
    construction_kind: str = "unknown"
    contains_ignored_terminal: bool = False
    displayed_terminal_count: int = 0


class ParserProbeEvidence(BaseModel):
    visible_prefix: str
    visible_prefix_sha256: str
    fixed_prefix: Optional[str] = None
    fixed_prefix_sha256: Optional[str] = None
    fixed_prefix_length: Optional[int] = None
    fixed_prefix_status: EvidenceClass = "UNAVAILABLE"
    fixed_prefix_detail: str = ""
    remainder_text: Optional[str] = None
    remainder_bytes: Optional[list[int]] = None
    remainder_escaped: str = ""
    remainder_state: Optional[str] = None
    current_accept_terminals: list[str] = Field(default_factory=list)
    next_accept_terminals: list[str] = Field(default_factory=list)
    ignore_terminals: list[str] = Field(default_factory=list)
    accept_sequences: list[AcceptSequenceProbeRecord] = Field(default_factory=list)
    accept_sequence_count: int = 0
    truncated_for_storage: bool = False  # always False for research JSON
    core_lookahead_k: Optional[int] = None
    sequence_construction: Optional[str] = None
    function_end: Optional[bool] = None
    warnings: list[str] = Field(default_factory=list)


class SequenceCandidateAttribution(BaseModel):
    token_id: int
    contributed_bit: Optional[bool] = None
    syncode_bytes_hex: str = ""
    status: EvidenceClass = "UNAVAILABLE"
    detail: str = ""


class AcceptSequenceAttribution(BaseModel):
    terminals: list[str]
    construction_kind: str = "unknown"
    contains_ignored_terminal: bool = False
    remainder_state: Optional[str] = None
    fsm_state_ids: list[str] = Field(default_factory=list)
    overapprox_path_used: Optional[bool] = None
    lookup_status: EvidenceClass = "UNAVAILABLE"
    lookup_detail: str = ""
    candidates: list[SequenceCandidateAttribution] = Field(default_factory=list)


class MaskAttributionEvidence(BaseModel):
    runtime_mask_bits: dict[str, bool] = Field(default_factory=dict)
    reconstructed_union_bits: dict[str, bool] = Field(default_factory=dict)
    reconstructed_union_equal_runtime: Optional[bool] = None
    differing_bit_count: Optional[int] = None
    candidate_bits_differ: dict[str, bool] = Field(default_factory=dict)
    attribution_reliable: bool = False
    per_sequence: list[AcceptSequenceAttribution] = Field(default_factory=list)
    dfa_transitions_status: EvidenceClass = "UNAVAILABLE"
    dfa_transitions_detail: str = (
        "Detailed DFA byte-level transition traces are UNAVAILABLE in SynCode "
        "0.4.16 without unsupported private instrumentation; final mask bits "
        "and verified per-sequence attribution remain mandatory."
    )
    warnings: list[str] = Field(default_factory=list)


class WitnessEvidence(BaseModel):
    oracle_kind: Literal["constructive_canonical", "minimal_grammar_control"] = (
        "constructive_canonical"
    )
    prefix: str
    candidate_decoded_text: Optional[str] = None
    completion_suffix: Optional[str] = None
    witness_source: Optional[str] = None
    witness_sha256: Optional[str] = None
    candidate_at_exact_boundary: Optional[bool] = None
    canonical_lark_parse_success: Optional[bool] = None
    parse_error: str = ""
    lossless_completeness: Optional[str] = None
    grammar_sha256: str = ""
    warnings: list[str] = Field(default_factory=list)


class MaskStoreIdentity(BaseModel):
    mode: MaskStoreMode
    syncode_mode: str
    cache_root: str
    cache_path: Optional[str] = None
    cache_file_sha256: Optional[str] = None
    construction_seconds: Optional[float] = None
    claimed_original_nscc_cache: bool = False
    notes: list[str] = Field(default_factory=list)


class ProbeProvenance(BaseModel):
    probe_schema_version: str = PROBE_SCHEMA_VERSION
    repository_commit: Optional[str] = None
    repository_commit_status: EvidenceClass = "UNAVAILABLE"
    repository_dirty: Optional[bool] = None
    case_file_sha256: Optional[str] = None
    trace_file_sha256: Optional[str] = None
    timestamp_utc: str = ""
    host: str = ""
    platform: str = ""
    python_version: str = ""
    syncode_version: str = ""
    syncode_package_path: str = ""
    syncode_version_override_used: bool = False
    syncode_symbol_guard_status: EvidenceClass = "UNAVAILABLE"
    syncode_symbol_guard_detail: str = ""
    transformers_version: str = ""
    syncode_larkm_version: str = ""
    torch_version: str = ""
    torch_device: str = ""
    tokenizer_model_id: Optional[str] = None
    tokenizer_revision: Optional[str] = None
    tokenizer_revision_status: EvidenceClass = "UNAVAILABLE"
    tokenizer_class: Optional[str] = None
    vocabulary_size: Optional[int] = None
    eos_token_id: Optional[int] = None
    pad_token_id: Optional[int] = None
    bos_token_id: Optional[int] = None
    trust_remote_code: bool = False
    allow_download: bool = False
    local_files_only: bool = True
    grammar_path: str = ""
    grammar_sha256: str = ""
    parser_mode: str = "lalr"
    syncode_mode: str = "grammar_mask"
    mask_store: Optional[MaskStoreIdentity] = None
    syncode_source_file_sha256: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


class RootCauseReport(BaseModel):
    answers: list[EvidenceItem] = Field(default_factory=list)
    first_verified_divergence: Optional[str] = None
    supported_conclusion: RootCauseKind = "unresolved_internal_evidence_unavailable"
    remaining_uncertainty: list[str] = Field(default_factory=list)


ReportStatus = Literal["complete", "failed", "incomplete"]


class SyncodeMaskProbeResult(BaseModel):
    """Machine-readable probe output."""

    schema_version: str = PROBE_SCHEMA_VERSION
    report_status: ReportStatus = "incomplete"
    failure_stage: Optional[str] = None
    case: ProbeCaseSpec
    provenance: ProbeProvenance = Field(default_factory=ProbeProvenance)
    prefix_text: str = ""
    prefix_sha256_utf8: str = ""
    prefix_character_count: int = 0
    prefix_utf8_byte_count: int = 0
    tokenizer_candidates: list[TokenizerCandidateEvidence] = Field(default_factory=list)
    byte_tokenizer_candidates: list[ByteTokenizerCandidateEvidence] = Field(
        default_factory=list
    )
    parser: Optional[ParserProbeEvidence] = None
    mask_attribution: Optional[MaskAttributionEvidence] = None
    witnesses: list[WitnessEvidence] = Field(default_factory=list)
    root_cause: Optional[RootCauseReport] = None
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
