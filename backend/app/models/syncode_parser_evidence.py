"""
Structured SynCode ParseResult evidence schemas (Phase 4A.1).

Pydantic models only — no SynCode imports, no mask-store construction.

Captures the exact SynCode 0.4.16 ParseResult used to build a live token mask.
This channel is distinct from:
  • Lark expected terminals (parser analysis / incremental parser state)
  • Tokenizer vocabulary tokens allowed by the final mask

``grammar_end_marker_present`` means an accept sequence begins with ``$END``
or ``EOF``.  It is grammar-end evidence only — never "EOS token allowed".
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

SyncodeParserEvidenceStatus = Literal[
    "recorded",
    "unavailable",
    "failed",
]

EvidenceTiming = Literal["before_selected_token"]

RemainderStateName = Literal[
    "COMPLETE",
    "MAYBE_COMPLETE",
    "INCOMPLETE",
]

RemainderKind = Literal[
    "text",
    "bytes_hex",
    "empty",
    "unavailable",
]


class AcceptSequenceRecord(BaseModel):
    """One ordered SynCode AcceptSequence (terminal names only)."""

    terminals: list[str] = Field(default_factory=list)


class RemainderRepresentation(BaseModel):
    """Honest remainder encoding — never invent UTF-8 for invalid bytes."""

    kind: RemainderKind = "unavailable"
    text: Optional[str] = None
    bytes_hex: Optional[str] = None
    original_type: str = ""
    truncated: bool = False
    original_byte_length: Optional[int] = None
    stored_byte_length: Optional[int] = None


class MaskEosObservation(BaseModel):
    """
    Token-level EOS allowance from the SynCode accept mask itself.

    Separate from grammar-end markers (``$END`` / ``EOF``) in accept sequences.
    """

    syncode_tokenizer_eos_token_id: Optional[int] = None
    application_eos_token_ids: list[int] = Field(default_factory=list)
    syncode_eos_allowed_by_accept_mask: Optional[bool] = None
    application_eos_allowed_by_accept_mask: dict[str, Optional[bool]] = Field(
        default_factory=dict
    )


class SyncodeParserEvidence(BaseModel):
    """Per-step structured record of the ParseResult used for masking."""

    status: SyncodeParserEvidenceStatus = "unavailable"
    evidence_timing: EvidenceTiming = "before_selected_token"

    syncode_version: str = ""
    mask_call_index: Optional[int] = None
    generated_token_count_before_selection: Optional[int] = None
    generated_prefix_char_count: Optional[int] = None
    generated_prefix_sha256: Optional[str] = None

    # Deterministically ordered; empty list with status=recorded means empty set.
    accept_sequences: list[AcceptSequenceRecord] = Field(default_factory=list)
    accept_sequence_count_total: int = 0
    accept_sequence_count_stored: int = 0
    accept_sequences_truncated: bool = False

    remainder_state: Optional[RemainderStateName] = None
    remainder: RemainderRepresentation = Field(
        default_factory=RemainderRepresentation
    )

    function_end: Optional[bool] = None
    # True when any stored/original sequence begins with $END or EOF.
    grammar_end_marker_present: bool = False

    mask_eos_observation: Optional[MaskEosObservation] = None

    warnings: list[str] = Field(default_factory=list)
    error: str = ""


def unavailable_syncode_parser_evidence(
    *,
    reason: str = "SynCode parser evidence not recorded",
    warnings: list[str] | None = None,
    mask_call_index: int | None = None,
) -> SyncodeParserEvidence:
    """Default / skip / not-captured payload."""
    return SyncodeParserEvidence(
        status="unavailable",
        mask_call_index=mask_call_index,
        warnings=list(warnings or []),
        error=reason,
        remainder=RemainderRepresentation(kind="unavailable", original_type=""),
    )


def failed_syncode_parser_evidence(
    *,
    error: str,
    warnings: list[str] | None = None,
    mask_call_index: int | None = None,
    syncode_version: str = "",
) -> SyncodeParserEvidence:
    """Capture/serialization failure — must not be confused with empty sets."""
    return SyncodeParserEvidence(
        status="failed",
        mask_call_index=mask_call_index,
        syncode_version=syncode_version,
        warnings=list(warnings or []),
        error=error,
        remainder=RemainderRepresentation(kind="unavailable", original_type=""),
    )
