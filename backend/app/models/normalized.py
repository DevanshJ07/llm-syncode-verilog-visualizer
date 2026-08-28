"""
Normalized experiment schemas shared by live and imported SynViz sources.

Phase 2A.1 defined the provenance-aware shape.  Phase 2A.2 fills imported
experiments via normalization; live API responses still use ``schemas.py``.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.models.provenance import Prov
from app.models.parser_analysis import ParserAnalysis
from app.models.syncode_parser_evidence import SyncodeParserEvidence

NORMALIZED_SCHEMA_VERSION = "4A.2"

ExperimentSourceType = Literal["live_local", "imported"]


class SourceFileRef(BaseModel):
    """Pointer to a file inside an imported bundle (or live store)."""

    path: str
    category: str = ""
    role: str = ""  # e.g. authoritative_output, trace, record, summary


class TokenRef(BaseModel):
    """Tokenizer-level token identity when known."""

    token_id: Optional[int] = None
    token: Optional[str] = None


class NormalizedTraceStep(BaseModel):
    """One decoding step in the normalized representation."""

    step_index: int

    prefix_before_selected: Prov[str] = Field(
        default_factory=lambda: Prov[str].unavailable(
            method="prefix not provided"
        )
    )
    raw_preferred: Prov[TokenRef] = Field(
        default_factory=lambda: Prov[TokenRef].unavailable()
    )
    selected: Prov[TokenRef] = Field(
        default_factory=lambda: Prov[TokenRef].unavailable()
    )
    constrained_preferred: Prov[TokenRef] = Field(
        default_factory=lambda: Prov[TokenRef].unavailable()
    )
    masking_changed_selection: Prov[bool] = Field(
        default_factory=lambda: Prov[bool].unavailable(
            method="mask intervention unknown"
        )
    )

    raw_rank: Prov[int] = Field(default_factory=lambda: Prov[int].unavailable())
    selected_rank: Prov[int] = Field(default_factory=lambda: Prov[int].unavailable())
    raw_probability: Prov[float] = Field(
        default_factory=lambda: Prov[float].unavailable(
            method="full distribution unavailable; do not derive from top-k"
        )
    )
    selected_probability: Prov[float] = Field(
        default_factory=lambda: Prov[float].unavailable(
            method="full distribution unavailable; do not derive from top-k"
        )
    )

    entropy_before: Prov[float] = Field(
        default_factory=lambda: Prov[float].unavailable(
            method="entropy_before absent"
        )
    )
    entropy_after: Prov[float] = Field(
        default_factory=lambda: Prov[float].unavailable(
            method="entropy_after absent"
        )
    )

    valid_token_count: Prov[int] = Field(
        default_factory=lambda: Prov[int].unavailable()
    )
    masked_token_count: Prov[int] = Field(
        default_factory=lambda: Prov[int].unavailable()
    )
    newly_masked_token_count: Prov[int] = Field(
        default_factory=lambda: Prov[int].unavailable()
    )

    blocked_token_info: Prov[dict[str, Any]] = Field(
        default_factory=lambda: Prov[dict[str, Any]].unavailable()
    )
    recorded_top_raw_tokens: Prov[list[Any]] = Field(
        default_factory=lambda: Prov[list[Any]].unavailable()
    )
    recorded_vocab_logits: Prov[Any] = Field(
        default_factory=lambda: Prov[Any].unavailable(
            method="logits absent; probabilities not derived"
        )
    )

    # Keep Lark / SynCode / tokenizer channels separate.
    parser_info: Prov[dict[str, Any]] = Field(
        default_factory=lambda: Prov[dict[str, Any]].unavailable()
    )
    expected_terminals: Prov[list[str]] = Field(
        default_factory=lambda: Prov[list[str]].unavailable(
            method="Lark expected terminals absent"
        )
    )
    syncode_accept_sequences: Prov[list[Any]] = Field(
        default_factory=lambda: Prov[list[Any]].unavailable(
            method="SynCode accept sequences absent"
        )
    )
    remainder_state: Prov[str] = Field(
        default_factory=lambda: Prov[str].unavailable()
    )
    eos_eligible: Prov[bool] = Field(
        default_factory=lambda: Prov[bool].unavailable(
            method="EOS eligibility absent"
        )
    )
    # Phase 4A.2 — structured SynCode parser evidence (primary display field).
    # Holds recomputed evidence when requested, otherwise bundle-recorded if
    # present, otherwise unavailable.  Never silently mixes Lark terminals.
    syncode_parser_evidence: Prov[SyncodeParserEvidence] = Field(
        default_factory=lambda: Prov[SyncodeParserEvidence].unavailable(
            method="SynCode parser evidence absent"
        )
    )
    # Preserved bundle-recorded structured evidence when recomputation also ran
    # (honest coexistence — never overwrite recorded with recomputed).
    syncode_parser_evidence_recorded: Prov[SyncodeParserEvidence] = Field(
        default_factory=lambda: Prov[SyncodeParserEvidence].unavailable(
            method="no separate recorded SynCode parser evidence"
        )
    )

    step_warnings: list[str] = Field(default_factory=list)


class NormalizedPromptResult(BaseModel):
    """One prompt/problem result within a normalized experiment."""

    problem_id: str
    prompt_text: Prov[str] = Field(default_factory=lambda: Prov[str].unavailable())
    reference_program: Prov[str] = Field(
        default_factory=lambda: Prov[str].unavailable()
    )
    # Authoritative generated output: archive .sv/.v when present.
    generated_output: Prov[str] = Field(
        default_factory=lambda: Prov[str].unavailable(
            method="authoritative output absent"
        )
    )
    reconstructed_from_tokens: Prov[str] = Field(
        default_factory=lambda: Prov[str].unavailable(
            method="selected_token reconstruction not performed"
        )
    )
    reconstruction_matches_authoritative: Prov[bool] = Field(
        default_factory=lambda: Prov[bool].unavailable(
            method="reconstruction comparison not performed"
        )
    )
    termination_reason: Prov[str] = Field(
        default_factory=lambda: Prov[str].unavailable()
    )
    generated_token_count: Prov[int] = Field(
        default_factory=lambda: Prov[int].unavailable()
    )
    token_limit: Prov[int] = Field(default_factory=lambda: Prov[int].unavailable())
    # Recorded boolean from records/<problem>.json — False stays recorded, not unavailable.
    grammar_valid: Prov[bool] = Field(
        default_factory=lambda: Prov[bool].unavailable(
            method="grammar_valid absent (not false)"
        )
    )
    # Recorded grammar evidence from the bundle (not inferred false).
    grammar_verdict: Prov[str] = Field(
        default_factory=lambda: Prov[str].unavailable(
            method="grammar verdict absent (not false)"
        )
    )
    parse_error: Prov[str] = Field(default_factory=lambda: Prov[str].unavailable())
    findings: Prov[Any] = Field(
        default_factory=lambda: Prov[Any].unavailable(method="findings absent")
    )
    mask_counts: Prov[dict[str, Any]] = Field(
        default_factory=lambda: Prov[dict[str, Any]].unavailable(
            method="mask counts absent"
        )
    )
    # Optional Phase 2A.2 recomputation against canonical grammar.
    recomputed_grammar_verdict: Prov[str] = Field(
        default_factory=lambda: Prov[str].unavailable(
            method="recompute_with_current_grammar disabled or not run"
        )
    )
    recomputed_parse_error: Prov[str] = Field(
        default_factory=lambda: Prov[str].unavailable()
    )
    # Phase 3A — structured parser analysis. Unavailable unless
    # recompute_with_current_grammar=true at import time.
    parser_analysis: Prov[ParserAnalysis] = Field(
        default_factory=lambda: Prov[ParserAnalysis].unavailable(
            method="recompute_with_current_grammar disabled or not run"
        )
    )
    steps: list[NormalizedTraceStep] = Field(default_factory=list)
    source_files: list[SourceFileRef] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class NormalizedExperiment(BaseModel):
    """Common representation for live_local and imported experiments."""

    schema_version: str = NORMALIZED_SCHEMA_VERSION
    experiment_id: str
    source_type: ExperimentSourceType
    experiment_name: str = ""
    created_at: str = ""

    llm_metadata: Prov[dict[str, Any]] = Field(
        default_factory=lambda: Prov[dict[str, Any]].unavailable()
    )
    grammar_metadata: Prov[dict[str, Any]] = Field(
        default_factory=lambda: Prov[dict[str, Any]].unavailable()
    )
    tokenizer_metadata: Prov[dict[str, Any]] = Field(
        default_factory=lambda: Prov[dict[str, Any]].unavailable()
    )
    decoding_metadata: Prov[dict[str, Any]] = Field(
        default_factory=lambda: Prov[dict[str, Any]].unavailable()
    )
    runtime_metadata: Prov[dict[str, Any]] = Field(
        default_factory=lambda: Prov[dict[str, Any]].unavailable()
    )

    prompt_results: list[NormalizedPromptResult] = Field(default_factory=list)
    import_warnings: list[str] = Field(default_factory=list)


class ImportedExperimentSummary(BaseModel):
    """Lightweight list row — excludes per-step traces."""

    experiment_id: str
    experiment_name: str = ""
    source_type: ExperimentSourceType = "imported"
    created_at: str = ""
    schema_version: str = NORMALIZED_SCHEMA_VERSION
    prompt_count: int = 0
    prompt_ids: list[str] = Field(default_factory=list)
    import_warning_count: int = 0
    has_generated_outputs: bool = False
    model_name: Optional[str] = None
