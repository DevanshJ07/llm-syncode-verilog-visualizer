"""
Provenance wrappers for imported and computed SynViz experiment fields.

Phase 2A.1: every optional experimental value can carry an explicit evidence
source so missing data is never silently coerced to ``False``, ``0``, ``[]``,
or a success verdict.
"""

from __future__ import annotations

from enum import Enum
from typing import Generic, Optional, TypeVar

from pydantic import BaseModel, Field, model_validator

T = TypeVar("T")


class ProvenanceKind(str, Enum):
    """How a field's value was obtained."""

    recorded = "recorded"  # present in the imported/live experiment artifact
    derived = "derived"  # deterministically reconstructed from recorded data
    recomputed = "recomputed"  # calculated in the current SynViz environment
    unavailable = "unavailable"  # absent and not safely reconstructable


class ProvenanceInfo(BaseModel):
    """Metadata describing the evidence for a provenanced value."""

    kind: ProvenanceKind
    source_file: Optional[str] = None
    source_field: Optional[str] = None
    method: Optional[str] = None
    grammar_sha256: Optional[str] = None
    warnings: list[str] = Field(default_factory=list)


class Prov(BaseModel, Generic[T]):
    """
    A value plus provenance.

    ``unavailable`` requires ``value is None``.  Recorded/derived/recomputed
    values may legitimately be ``False``, ``0``, ``""``, or ``[]`` — those are
    distinct from unavailability.
    """

    value: Optional[T] = None
    provenance: ProvenanceInfo

    @model_validator(mode="after")
    def _enforce_unavailable_null(self) -> "Prov[T]":
        if (
            self.provenance.kind == ProvenanceKind.unavailable
            and self.value is not None
        ):
            raise ValueError(
                "provenance kind 'unavailable' requires value=None "
                "(do not substitute False, 0, or [])"
            )
        return self

    @classmethod
    def recorded(
        cls,
        value: T,
        *,
        source_file: str | None = None,
        source_field: str | None = None,
        method: str | None = None,
        warnings: list[str] | None = None,
    ) -> "Prov[T]":
        return cls(
            value=value,
            provenance=ProvenanceInfo(
                kind=ProvenanceKind.recorded,
                source_file=source_file,
                source_field=source_field,
                method=method,
                warnings=list(warnings or []),
            ),
        )

    @classmethod
    def derived(
        cls,
        value: T,
        *,
        source_file: str | None = None,
        source_field: str | None = None,
        method: str | None = None,
        warnings: list[str] | None = None,
    ) -> "Prov[T]":
        return cls(
            value=value,
            provenance=ProvenanceInfo(
                kind=ProvenanceKind.derived,
                source_file=source_file,
                source_field=source_field,
                method=method,
                warnings=list(warnings or []),
            ),
        )

    @classmethod
    def recomputed(
        cls,
        value: T,
        *,
        method: str | None = None,
        grammar_sha256: str | None = None,
        source_file: str | None = None,
        source_field: str | None = None,
        warnings: list[str] | None = None,
    ) -> "Prov[T]":
        return cls(
            value=value,
            provenance=ProvenanceInfo(
                kind=ProvenanceKind.recomputed,
                source_file=source_file,
                source_field=source_field,
                method=method,
                grammar_sha256=grammar_sha256,
                warnings=list(warnings or []),
            ),
        )

    @classmethod
    def unavailable(
        cls,
        *,
        method: str | None = None,
        source_file: str | None = None,
        source_field: str | None = None,
        warnings: list[str] | None = None,
    ) -> "Prov[T]":
        return cls(
            value=None,
            provenance=ProvenanceInfo(
                kind=ProvenanceKind.unavailable,
                source_file=source_file,
                source_field=source_field,
                method=method or "absent from source artifacts",
                warnings=list(warnings or []),
            ),
        )

    @property
    def is_unavailable(self) -> bool:
        return self.provenance.kind == ProvenanceKind.unavailable
