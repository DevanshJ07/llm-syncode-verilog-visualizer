"""
Serialize SynCode 0.4.16 ParseResult evidence used during live masking (Phase 4A.1).

Adapter notes (version-coupled):
  • SynCode ``AcceptSequence`` subclasses ``list`` but stores terminals on
    ``.accept_terminals`` (the list body is often empty).  Always read
    ``accept_terminals`` when present.
  • ``ParseResult.accept_sequences`` is a ``set`` — serialize deterministically
    by sorting lexicographically on the terminal-name tuples.
  • Capture is intended at ``dfa_mask_store.get_accept_mask(parse_result)`` so
    the recorded object is the one that built the token mask — not a recompute.

This module does not import SynCode at module level (unit tests use fakes).
Optional interface probes may import ``syncode.parse_result`` separately.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Optional, Sequence

from app.core.config import settings
from app.models.syncode_parser_evidence import (
    AcceptSequenceRecord,
    MaskEosObservation,
    RemainderRepresentation,
    SyncodeParserEvidence,
    failed_syncode_parser_evidence,
    unavailable_syncode_parser_evidence,
)

log = logging.getLogger(__name__)

GRAMMAR_END_TERMINALS = frozenset({"$END", "EOF"})


def _max_sequences() -> int:
    return int(getattr(settings, "syncode_parser_evidence_max_sequences", 64))


def _max_terminals_per_sequence() -> int:
    return int(
        getattr(settings, "syncode_parser_evidence_max_terminals_per_sequence", 16)
    )


def _max_terminal_chars() -> int:
    return int(
        getattr(settings, "syncode_parser_evidence_max_terminal_chars", 64)
    )


def _max_remainder_bytes() -> int:
    return int(
        getattr(settings, "syncode_parser_evidence_max_remainder_bytes", 512)
    )


def syncode_package_version() -> str:
    """Best-effort installed SynCode version string (no SynCode import)."""
    try:
        import importlib.metadata as md

        return md.version("syncode")
    except Exception:
        return ""


def extract_accept_terminals(seq: Any) -> list[str]:
    """
    Extract ordered terminal names from a SynCode AcceptSequence-like object.

    Prefers ``.accept_terminals`` (authoritative on SynCode 0.4.16).
    """
    if seq is None:
        return []
    terminals = getattr(seq, "accept_terminals", None)
    if terminals is not None:
        return [str(t) for t in terminals]
    if isinstance(seq, (list, tuple)):
        return [str(t) for t in seq]
    return [str(seq)]


def _clip_terminal(name: str, max_chars: int, warnings: list[str]) -> str:
    if len(name) <= max_chars:
        return name
    warnings.append(
        f"terminal name truncated from {len(name)} to {max_chars} characters"
    )
    return name[:max_chars]


def serialize_accept_sequences(
    raw_sequences: Any,
    *,
    max_sequences: int | None = None,
    max_terminals: int | None = None,
    max_terminal_chars: int | None = None,
) -> tuple[list[AcceptSequenceRecord], int, bool, list[str], bool]:
    """
    Deterministically serialize a set/iterable of AcceptSequence-like objects.

    Returns:
      (stored_records, total_count, truncated, warnings, grammar_end_marker_present)

    ``total_count`` is the original cardinality before truncation.
    An empty iterable yields ``([], 0, False, [], False)`` — recorded empty set.
    """
    max_sequences = _max_sequences() if max_sequences is None else max_sequences
    max_terminals = (
        _max_terminals_per_sequence() if max_terminals is None else max_terminals
    )
    max_terminal_chars = (
        _max_terminal_chars() if max_terminal_chars is None else max_terminal_chars
    )
    warnings: list[str] = []

    if raw_sequences is None:
        return [], 0, False, ["accept_sequences attribute was None"], False

    try:
        items = list(raw_sequences)
    except TypeError:
        return [], 0, False, ["accept_sequences was not iterable"], False

    terminal_tuples: list[list[str]] = []
    grammar_end = False
    for seq in items:
        terms = extract_accept_terminals(seq)
        if terms and terms[0] in GRAMMAR_END_TERMINALS:
            grammar_end = True
        clipped: list[str] = []
        for i, t in enumerate(terms):
            if i >= max_terminals:
                warnings.append(
                    f"accept sequence truncated from {len(terms)} to "
                    f"{max_terminals} terminals"
                )
                break
            clipped.append(_clip_terminal(str(t), max_terminal_chars, warnings))
        terminal_tuples.append(clipped)

    # Lexicographic sort of sequences; preserve terminal order within each.
    terminal_tuples.sort(key=lambda t: tuple(t))
    total = len(terminal_tuples)
    truncated = total > max_sequences
    stored_tuples = terminal_tuples[:max_sequences]
    if truncated:
        warnings.append(
            f"accept_sequences truncated: original_total={total} "
            f"stored_total={len(stored_tuples)} max={max_sequences}"
        )

    records = [AcceptSequenceRecord(terminals=t) for t in stored_tuples]
    return records, total, truncated, warnings, grammar_end


def serialize_remainder(
    remainder: Any,
    *,
    max_bytes: int | None = None,
) -> RemainderRepresentation:
    """Encode remainder as text or hex; never claim invalid UTF-8 is text."""
    max_bytes = _max_remainder_bytes() if max_bytes is None else max_bytes
    original_type = type(remainder).__name__ if remainder is not None else "NoneType"

    if remainder is None:
        return RemainderRepresentation(
            kind="unavailable",
            original_type=original_type,
        )

    if isinstance(remainder, bytes):
        original_len = len(remainder)
        data = remainder
        truncated = False
        if original_len > max_bytes:
            data = remainder[:max_bytes]
            truncated = True
        try:
            text = data.decode("utf-8")
            if original_len == 0:
                return RemainderRepresentation(
                    kind="empty",
                    text="",
                    original_type=original_type,
                    truncated=False,
                    original_byte_length=0,
                    stored_byte_length=0,
                )
            return RemainderRepresentation(
                kind="text",
                text=text,
                original_type=original_type,
                truncated=truncated,
                original_byte_length=original_len,
                stored_byte_length=len(data),
            )
        except UnicodeDecodeError:
            return RemainderRepresentation(
                kind="bytes_hex",
                bytes_hex=data.hex(),
                original_type=original_type,
                truncated=truncated,
                original_byte_length=original_len,
                stored_byte_length=len(data),
            )

    if isinstance(remainder, str):
        encoded = remainder.encode("utf-8")
        original_len = len(encoded)
        truncated = False
        text = remainder
        stored_len = original_len
        if original_len > max_bytes:
            # Truncate by UTF-8 bytes without splitting codepoints when possible.
            truncated = True
            data = encoded[:max_bytes]
            text = data.decode("utf-8", errors="ignore")
            stored_len = len(data)
        if text == "" and original_len == 0:
            return RemainderRepresentation(
                kind="empty",
                text="",
                original_type=original_type,
                truncated=False,
                original_byte_length=0,
                stored_byte_length=0,
            )
        return RemainderRepresentation(
            kind="text",
            text=text,
            original_type=original_type,
            truncated=truncated,
            original_byte_length=original_len,
            stored_byte_length=stored_len,
        )

    # Fallback — string form, still bounded.
    as_text = str(remainder)
    encoded = as_text.encode("utf-8", errors="replace")
    truncated = len(encoded) > max_bytes
    if truncated:
        as_text = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return RemainderRepresentation(
        kind="text",
        text=as_text,
        original_type=original_type,
        truncated=truncated,
        original_byte_length=len(encoded),
        stored_byte_length=len(as_text.encode("utf-8", errors="replace")),
    )


def _remainder_state_name(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    name = getattr(raw, "name", None)
    if isinstance(name, str) and name in {
        "COMPLETE",
        "MAYBE_COMPLETE",
        "INCOMPLETE",
    }:
        return name
    text = str(raw)
    for candidate in ("COMPLETE", "MAYBE_COMPLETE", "INCOMPLETE"):
        if candidate in text:
            return candidate
    return None


def observe_eos_from_accept_mask(
    accept_mask: Any,
    *,
    syncode_tokenizer_eos_token_id: int | None,
    application_eos_token_ids: Sequence[int] | None = None,
) -> MaskEosObservation:
    """
    Record whether EOS token IDs are allowed by the boolean accept mask.

    Does not consult accept sequences / ``$END``.
    """
    app_ids = list(application_eos_token_ids or [])
    obs = MaskEosObservation(
        syncode_tokenizer_eos_token_id=syncode_tokenizer_eos_token_id,
        application_eos_token_ids=app_ids,
    )

    def _allowed(tid: int | None) -> Optional[bool]:
        if tid is None or accept_mask is None:
            return None
        try:
            n = int(accept_mask.numel()) if hasattr(accept_mask, "numel") else len(accept_mask)
            if tid < 0 or tid >= n:
                return None
            val = accept_mask[tid]
            return bool(val.item() if hasattr(val, "item") else val)
        except Exception:
            return None

    obs.syncode_eos_allowed_by_accept_mask = _allowed(syncode_tokenizer_eos_token_id)
    for eid in app_ids:
        obs.application_eos_allowed_by_accept_mask[str(eid)] = _allowed(eid)
    return obs


def serialize_parse_result(
    parse_result: Any,
    *,
    mask_call_index: int | None = None,
    generated_token_count_before_selection: int | None = None,
    generated_prefix: str | None = None,
    syncode_version: str | None = None,
    accept_mask: Any = None,
    syncode_tokenizer_eos_token_id: int | None = None,
    application_eos_token_ids: Sequence[int] | None = None,
    extra_warnings: Sequence[str] | None = None,
    origin: str = "live_mask_runtime",
) -> SyncodeParserEvidence:
    """
    Build SyncodeParserEvidence from a ParseResult (live or recomputed).

    For live masking, pass the ParseResult given to ``get_accept_mask`` and
    ``origin="live_mask_runtime"``.  For import recomputation, use
    ``origin="import_recomputed_parser_only"`` and leave ``accept_mask`` /
    ``mask_call_index`` unset.
    """
    from app.models.syncode_parser_evidence import EvidenceOrigin  # noqa: PLC0415

    version = syncode_version if syncode_version is not None else syncode_package_version()
    warnings: list[str] = list(extra_warnings or [])
    origin_val: EvidenceOrigin = origin  # type: ignore[assignment]

    if parse_result is None:
        return unavailable_syncode_parser_evidence(
            reason="ParseResult was None at get_accept_mask",
            warnings=warnings,
            mask_call_index=mask_call_index,
            origin=origin_val,
        )

    try:
        raw_seqs = getattr(parse_result, "accept_sequences", None)
        records, total, truncated, seq_warnings, grammar_end = serialize_accept_sequences(
            raw_seqs
        )
        warnings.extend(seq_warnings)

        rem_state = _remainder_state_name(
            getattr(parse_result, "remainder_state", None)
        )
        remainder = serialize_remainder(getattr(parse_result, "remainder", None))
        if remainder.truncated:
            warnings.append(
                f"remainder truncated: original_byte_length="
                f"{remainder.original_byte_length} "
                f"stored_byte_length={remainder.stored_byte_length}"
            )

        function_end = getattr(parse_result, "function_end", None)
        if function_end is not None:
            function_end = bool(function_end)

        prefix_sha: Optional[str] = None
        prefix_chars: Optional[int] = None
        if generated_prefix is not None:
            prefix_chars = len(generated_prefix)
            prefix_sha = hashlib.sha256(
                generated_prefix.encode("utf-8", errors="replace")
            ).hexdigest()

        eos_obs = None
        if accept_mask is not None or syncode_tokenizer_eos_token_id is not None:
            eos_obs = observe_eos_from_accept_mask(
                accept_mask,
                syncode_tokenizer_eos_token_id=syncode_tokenizer_eos_token_id,
                application_eos_token_ids=application_eos_token_ids,
            )

        return SyncodeParserEvidence(
            status="available",
            origin=origin_val,
            evidence_timing="before_selected_token",
            syncode_version=version,
            mask_call_index=mask_call_index,
            generated_token_count_before_selection=generated_token_count_before_selection,
            generated_prefix_char_count=prefix_chars,
            generated_prefix_sha256=prefix_sha,
            accept_sequences=records,
            accept_sequence_count_total=total,
            accept_sequence_count_stored=len(records),
            accept_sequences_truncated=truncated,
            remainder_state=rem_state,  # type: ignore[arg-type]
            remainder=remainder,
            function_end=function_end,
            grammar_end_marker_present=grammar_end,
            mask_eos_observation=eos_obs,
            warnings=warnings,
            error="",
        )
    except Exception as exc:
        log.warning(
            "SynCode parser-evidence serialization failed (mask unchanged): %s",
            exc,
        )
        return failed_syncode_parser_evidence(
            error=f"{type(exc).__name__}: {exc}",
            warnings=warnings
            + ["capture serialization failure; mask path unaffected"],
            mask_call_index=mask_call_index,
            syncode_version=version,
            origin=origin_val,
        )


def format_legacy_accept_sequences(
    evidence: SyncodeParserEvidence,
    *,
    max_entries: int = 8,
) -> list[str]:
    """
    Deterministic legacy ``DecodingStep.accept_sequences`` strings.

    Matches SynCode AcceptSequence ``__repr__`` style:
    ``accept_terminals: ('MODULE',)``
    """
    if not evidence.is_structurally_available():
        return []
    out: list[str] = []
    for rec in evidence.accept_sequences[:max_entries]:
        out.append(f"accept_terminals: {tuple(rec.terminals)!s}")
    return out


def wrap_get_accept_mask(
    original_get_accept_mask,
    *,
    on_captured,
    mask_call_index: int,
    generated_token_count_before_selection: int | None,
    generated_prefix: str | None,
    syncode_version: str,
    syncode_tokenizer_eos_token_id: int | None = None,
    application_eos_token_ids: Sequence[int] | None = None,
):
    """
    Return a wrapper that calls ``original_get_accept_mask`` exactly once and
    invokes ``on_captured(evidence)`` without altering the returned mask.

    Capture failures never change the mask or swallow original errors.
    """

    def wrapped(parse_result: Any):
        mask = original_get_accept_mask(parse_result)
        try:
            evidence = serialize_parse_result(
                parse_result,
                mask_call_index=mask_call_index,
                generated_token_count_before_selection=(
                    generated_token_count_before_selection
                ),
                generated_prefix=generated_prefix,
                syncode_version=syncode_version,
                accept_mask=mask,
                syncode_tokenizer_eos_token_id=syncode_tokenizer_eos_token_id,
                application_eos_token_ids=application_eos_token_ids,
                origin="live_mask_runtime",
            )
            on_captured(evidence)
        except Exception as exc:
            try:
                on_captured(
                    failed_syncode_parser_evidence(
                        error=f"capture hook: {type(exc).__name__}: {exc}",
                        mask_call_index=mask_call_index,
                        syncode_version=syncode_version,
                        origin="live_mask_runtime",
                    )
                )
            except Exception:
                pass
        return mask

    return wrapped


def validate_imported_structured_evidence(raw: Any) -> SyncodeParserEvidence | None:
    """
    Validate a future bundle's structured ``syncode_parser_evidence`` object.

    Returns None when absent or invalid.  Does not parse legacy stringified
    accept-sequence text into terminal lists.
    """
    if raw is None or not isinstance(raw, dict):
        return None
    try:
        ev = SyncodeParserEvidence.model_validate(raw)
    except Exception:
        return None
    if (
        ev.status == "unavailable"
        and ev.origin == "none"
        and not ev.accept_sequences
        and not ev.error
        and not ev.warnings
    ):
        return None
    if ev.origin == "none":
        ev = ev.model_copy(update={"origin": "import_recorded_bundle"})
    return ev
