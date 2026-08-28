"""
Normalized experiment schemas shared by live and imported SynViz sources.

Phase 2A.1 defines the target representation only.  Live API responses continue
to use ``schemas.ExperimentResult`` / ``DecodingStep`` unchanged.  Phase 2A.2
will map imported bundles (and optionally live runs) into these models.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from app.models.provenance import Prov

NORMALIZED_SCHEMA_VERSION = "2A.1"

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
            method="prefix not provided in Phase 2A.1 construction"
        )
    )
    raw_preferred: Prov[TokenRef] = Field(
        default_factory=lambda: Prov[TokenRef].unavailable()
    )
    selected: Prov[TokenRef] = Field(
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
        default_factory=lambda: Prov[float].unavailable()
    )
    selected_probability: Prov[float] = Field(
        default_factory=lambda: Prov[float].unavailable()
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

    # Keep Lark / SynCode / tokenizer channels separate (Phase 2A.2+).
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

    step_warnings: list[str] = Field(default_factory=list)


class NormalizedPromptResult(BaseModel):
    """One prompt/problem result within a normalized experiment."""

    problem_id: str
    prompt_text: Prov[str] = Field(default_factory=lambda: Prov[str].unavailable())
    reference_program: Prov[str] = Field(
        default_factory=lambda: Prov[str].unavailable()
    )
    # Authoritative generated output is decided in Phase 2A.2 (.sv/.v preferred).
    generated_output: Prov[str] = Field(
        default_factory=lambda: Prov[str].unavailable(
            method="authoritative output not selected in Phase 2A.1"
        )
    )
    termination_reason: Prov[str] = Field(
        default_factory=lambda: Prov[str].unavailable()
    )
    generated_token_count: Prov[int] = Field(
        default_factory=lambda: Prov[int].unavailable()
    )
    token_limit: Prov[int] = Field(default_factory=lambda: Prov[int].unavailable())
    grammar_verdict: Prov[str] = Field(
        default_factory=lambda: Prov[str].unavailable(
            method="grammar verdict absent (not false)"
        )
    )
    steps: list[NormalizedTraceStep] = Field(default_factory=list)
    source_files: list[SourceFileRef] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class NormalizedExperiment(BaseModel):
    """
    Common representation for live_local and imported experiments.

    Phase 2A.1 ships the schema only — no import persistence or API yet.
    """

    schema_version: str = NORMALIZED_SCHEMA_VERSION
    experiment_id: str
    source_type: ExperimentSourceType

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
