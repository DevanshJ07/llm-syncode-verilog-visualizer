"""
Structured Verilog parser-analysis schemas (Phase 3A).

Pydantic data models / enums only — no Lark parser construction or analysis.

Shared by live generation results and imported experiments.  Distinguishes
complete Lark trees from partial/recovered parser-stack forests so a
non-complete representation is never labelled as a complete parse tree.

Analysis logic lives in ``app.services.parser_analysis``.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.models.provenance import ProvenanceInfo, ProvenanceKind

ParserAnalysisStatus = Literal[
    "complete_valid",
    "incomplete_prefix",
    "invalid_input",
    "unavailable",
]

ParserRepresentationKind = Literal[
    "complete_parse_tree",
    "partial_parse_forest",
    "recovered_prefix_forest",
    "none",
]

ParserNodeKind = Literal[
    "rule",
    "token",
    "synthetic_root",
    "recovery_marker",
    "stack_value",
]


class ParserSourcePosition(BaseModel):
    """Optional source span. Omitted fields mean Lark did not supply them."""

    line: Optional[int] = None
    column: Optional[int] = None
    start_pos: Optional[int] = None
    end_pos: Optional[int] = None
    end_line: Optional[int] = None
    end_column: Optional[int] = None


class ParserNode(BaseModel):
    """One node in a complete tree or partial/recovered forest."""

    id: str
    kind: ParserNodeKind
    label: str
    token_value: Optional[str] = None
    children: list["ParserNode"] = Field(default_factory=list)
    position: Optional[ParserSourcePosition] = None


class ParserAnalysis(BaseModel):
    """
    Honest structured parser representation for one Verilog source string.

    Status and representation_kind must stay consistent:
      complete_valid      → complete_parse_tree
      incomplete_prefix   → partial_parse_forest
      invalid_input       → recovered_prefix_forest
      unavailable         → none
    """

    status: ParserAnalysisStatus = "unavailable"
    representation_kind: ParserRepresentationKind = "none"
    label: str = "Parser analysis unavailable"
    is_complete: bool = False
    is_partial: bool = False
    is_recovered: bool = False

    grammar_name: str = "verilog"
    grammar_sha256: str = ""
    parser_name: str = "lalr"
    parser_version: str = ""

    root: Optional[ParserNode] = None
    pretty_text: str = ""

    expected_next_terminals: list[str] = Field(default_factory=list)
    accepts_end: bool = False

    parsed_prefix: str = ""
    invalid_suffix: str = ""
    consumed_char_offset: int = 0

    error_offset: Optional[int] = None
    error_line: Optional[int] = None
    error_column: Optional[int] = None
    error_type: str = ""
    error_message: str = ""
    unexpected_token_or_char: str = ""
    previous_token: str = ""

    warnings: list[str] = Field(default_factory=list)
    provenance: ProvenanceInfo = Field(
        default_factory=lambda: ProvenanceInfo(
            kind=ProvenanceKind.unavailable,
            method="parser analysis not run",
        )
    )

    # Analysis bookkeeping (honest truncation / preprocessing notes).
    node_count: int = 0
    max_depth_seen: int = 0
    truncated: bool = False
    source_length: int = 0
    comment_handling: str = (
        "canonical grammar %ignore LINE_COMMENT, BLOCK_COMMENT, and WS; "
        "source is not comment-stripped for position fidelity"
    )


def unavailable_parser_analysis(
    *,
    method: str = "parser analysis not run",
    warnings: list[str] | None = None,
) -> ParserAnalysis:
    """Default / disabled analysis payload."""
    return ParserAnalysis(
        status="unavailable",
        representation_kind="none",
        label="Parser analysis unavailable",
        provenance=ProvenanceInfo(
            kind=ProvenanceKind.unavailable,
            method=method,
            warnings=list(warnings or []),
        ),
        warnings=list(warnings or []),
    )
