"""
Structured SynCode ParseResult evidence schemas (Phase 4A.1 / 4A.2).

Pydantic models only — no SynCode imports, no mask-store construction.

Captures either:
  • live mask-time ParseResult (Phase 4A.1), or
  • imported parser-only recomputation (Phase 4A.2).

Semantics (do not conflate):
  • ``status`` — analysis availability/outcome:
      available | unavailable | failed
      (legacy Phase 4A.1 value ``recorded`` is accepted on load and
      normalized to ``available``; it never means Prov.kind Recorded)
  • ``origin`` — where the ParseResult came from:
      live_mask_runtime | import_recomputed_parser_only |
      import_recorded_bundle | none
  • Outer ``Prov.kind`` (on NormalizedTraceStep) — evidence provenance:
      recorded | recomputed | unavailable | derived

Imported recomputation must use ``origin=import_recomputed_parser_only`` and
outer Prov.kind=recomputed — never present as Prov Recorded solely because
``status`` is available.

``grammar_end_marker_present`` means an accept sequence begins with ``$END``
or ``EOF``.  It is grammar-end evidence only — never "EOS token allowed".
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

SyncodeParserEvidenceStatus = Literal[
    "available",
    "unavailable",
    "failed",
    # Legacy Phase 4A.1 wire value — normalized to "available" on validate.
    "recorded",
]

EvidenceOrigin = Literal[
    "live_mask_runtime",
    "import_recomputed_parser_only",
    "import_recorded_bundle",
    "none",
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

# How core_lookahead_k / construction metadata was established for this payload.
SemanticsProvenance = Literal[
    "recorded",
    "recomputed",
    "derived_from_version",
    "unavailable",
]

# Classification of one AcceptSequence under SynCode 0.4.16
# ``ParseResult.from_accept_terminals`` rules.  Prefer ``unknown`` when ambiguous.
AcceptSequenceConstructionKind = Literal[
    "current_terminal",
    "next_terminal",
    "final_then_next",
    "final_ignore_next",
    "ignore_only",
    "unknown",
]


class AcceptSequenceRecord(BaseModel):
    """
    One ordered SynCode AcceptSequence (grammar-terminal names only).

    Optional classification fields are filled for newly serialized evidence when
    construction can be established unambiguously.  Historical payloads that
    only carry ``terminals`` must leave the optional fields absent/None —
    never invent ``recorded`` classifications.
    """

    terminals: list[str] = Field(default_factory=list)
    displayed_terminal_count: Optional[int] = None
    construction_kind: Optional[AcceptSequenceConstructionKind] = None
    contains_ignored_terminal: Optional[bool] = None


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
    Unavailable for imported parser-only recomputation (no DFA mask).
    """

    syncode_tokenizer_eos_token_id: Optional[int] = None
    application_eos_token_ids: list[int] = Field(default_factory=list)
    syncode_eos_allowed_by_accept_mask: Optional[bool] = None
    application_eos_allowed_by_accept_mask: dict[str, Optional[bool]] = Field(
        default_factory=dict
    )


class SyncodeParserEvidence(BaseModel):
    """
    Per-step structured SynCode parser evidence.

    ``status="available"`` means a ParseResult was successfully serialized.
    Attribution is ``origin`` + outer Prov.kind — never infer Prov Recorded
    from status alone.
    """

    status: SyncodeParserEvidenceStatus = "unavailable"
    origin: EvidenceOrigin = "none"
    evidence_timing: EvidenceTiming = "before_selected_token"

    syncode_version: str = ""
    # Live mask-call index only.  Must remain None for import recomputation.
    mask_call_index: Optional[int] = None
    generated_token_count_before_selection: Optional[int] = None
    generated_prefix_char_count: Optional[int] = None
    generated_prefix_sha256: Optional[str] = None

    # Deterministically ordered; empty list with status=available means empty set.
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

    # --- Checkpoint 1: accept-sequence semantics (optional / backward-compat) ---
    # Core lookahead k counts grammar terminals (not LLM tokenizer tokens).
    # SynCode 0.4.16 effective core k=2; length-3 paths are ignore intercalation.
    core_lookahead_k: Optional[int] = None
    core_lookahead_unit: Optional[str] = None
    sequence_construction: Optional[str] = None
    current_accept_terminals: Optional[list[str]] = None
    next_accept_terminals: Optional[list[str]] = None
    ignore_terminals: Optional[list[str]] = None
    semantics_provenance: Optional[SemanticsProvenance] = None

    mask_eos_observation: Optional[MaskEosObservation] = None

    warnings: list[str] = Field(default_factory=list)
    error: str = ""

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_legacy_recorded_status(cls, v: Any) -> Any:
        # Phase 4A.1 used status="recorded" for successful capture.  Map to
        # "available" so recomputed evidence is never labelled Recorded via status.
        if v == "recorded":
            return "available"
        return v

    def is_structurally_available(self) -> bool:
        """True when a ParseResult was successfully serialized."""
        return self.status in ("available", "recorded")


def unavailable_syncode_parser_evidence(
    *,
    reason: str = "SynCode parser evidence not recorded",
    warnings: list[str] | None = None,
    mask_call_index: int | None = None,
    origin: EvidenceOrigin = "none",
) -> SyncodeParserEvidence:
    """Default / skip / not-captured payload."""
    return SyncodeParserEvidence(
        status="unavailable",
        origin=origin,
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
    origin: EvidenceOrigin = "none",
) -> SyncodeParserEvidence:
    """Capture/serialization failure — must not be confused with empty sets."""
    return SyncodeParserEvidence(
        status="failed",
        origin=origin,
        mask_call_index=mask_call_index,
        syncode_version=syncode_version,
        warnings=list(warnings or []),
        error=error,
        remainder=RemainderRepresentation(kind="unavailable", original_type=""),
    )
