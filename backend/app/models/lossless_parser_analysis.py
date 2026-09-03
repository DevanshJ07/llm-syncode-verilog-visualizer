"""
Lossless per-step Verilog parser-analysis DTOs (Checkpoint 2).

Backward-independent response models — not stored inside ExperimentResult.
Offsets are Unicode code-point / Python ``str`` indices (not UTF-8 byte offsets).
UTF-8 byte length is reported separately as ``source_utf8_byte_count``.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.models.parser_analysis import ParserAnalysis

LOSSLESS_ANALYSIS_SCHEMA_VERSION = "lossless_cst_v1"

AnalysisKind = Literal["lossless_cst"]

AnalysisTiming = Literal[
    "before_selected_token",
    "after_selected_token",
    "final_source",
]

AnalysisCompleteness = Literal[
    "complete",
    "incomplete_prefix",
    "invalid_prefix",
    "empty",
]

SourceProvenance = Literal[
    "final_generated_source",
    "derived_from_recorded_selected_tokens",
]

CstNodeKind = Literal["rule", "terminal"]

LosslessSegmentKind = Literal[
    "terminal",
    "whitespace",
    "line_comment",
    "block_comment",
    "unparsed",
]


class LosslessCstNode(BaseModel):
    """One node in a lossless CST (rules + terminals with positions)."""

    id: str
    kind: CstNodeKind
    name: str
    # Original Lark terminal type when kind=terminal (may differ from display name).
    lark_terminal_type: Optional[str] = None
    lexeme: Optional[str] = None
    start_offset: Optional[int] = None
    end_offset: Optional[int] = None
    start_line: Optional[int] = None
    start_column: Optional[int] = None
    end_line: Optional[int] = None
    end_column: Optional[int] = None
    children: list["LosslessCstNode"] = Field(default_factory=list)
    # True when this subtree is explicitly partial / not a complete program CST.
    is_partial: bool = False


class LosslessSourceSegment(BaseModel):
    """Ordered lossless source segment covering exact characters."""

    id: str
    kind: LosslessSegmentKind
    terminal_name: Optional[str] = None
    lark_terminal_type: Optional[str] = None
    exact_text: str
    start_offset: int
    end_offset: int
    start_line: int
    start_column: int
    end_line: int
    end_column: int
    cst_node_id: Optional[str] = None


class LlmTokenSpan(BaseModel):
    """Span of one recorded selected LLM tokenizer token inside source_text."""

    step_index: int
    recorded_step: Optional[int] = None
    token_id: Optional[int] = None
    exact_text: str
    start_offset: int
    end_offset: int
    selected_at_current_step: bool = False


class LosslessParserAnalysisResponse(BaseModel):
    """On-demand lossless parser analysis for one source snapshot."""

    analysis_kind: AnalysisKind = "lossless_cst"
    analysis_schema_version: str = LOSSLESS_ANALYSIS_SCHEMA_VERSION
    timing: AnalysisTiming
    completeness: AnalysisCompleteness

    source_text: str
    source_sha256: str
    source_provenance: SourceProvenance
    source_character_count: int
    source_utf8_byte_count: int
    # Offsets in this payload are Python str / Unicode code-point indices.
    offset_unit: str = "unicode_code_point"

    grammar_name: str = "verilog"
    grammar_sha256: str = ""
    parser_engine: str = "lalr"
    parser_version: str = ""
    parser_mode: str = "analysis_only_keep_all_tokens"
    keep_all_tokens: bool = True

    cst_root: Optional[LosslessCstNode] = None
    source_segments: list[LosslessSourceSegment] = Field(default_factory=list)
    llm_token_spans: list[LlmTokenSpan] = Field(default_factory=list)

    # Existing structural / partial diagnostics (never replaces this lossless view).
    structural_parser_analysis: Optional[ParserAnalysis] = None

    consumed_prefix: str = ""
    invalid_suffix: str = ""
    consumed_char_offset: int = 0

    warnings: list[str] = Field(default_factory=list)

    # Request echo for UI cache invalidation / stale response checks.
    experiment_id: str = ""
    prompt_id: Optional[str] = None
    step_index: Optional[int] = None
