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
    SemanticsProvenance,
    SyncodeParserEvidence,
    failed_syncode_parser_evidence,
    unavailable_syncode_parser_evidence,
)

log = logging.getLogger(__name__)

GRAMMAR_END_TERMINALS = frozenset({"$END", "EOF"})

# Identifies the SynCode 0.4.16 construction rule used for new evidence.
SEQUENCE_CONSTRUCTION_SYNCODE_0416 = (
    "syncode.ParseResult.from_accept_terminals@0.4.16"
)
CORE_LOOKAHEAD_K_SYNCODE_0416 = 2
CORE_LOOKAHEAD_UNIT = "grammar_terminals"


def is_syncode_0416_version(version: str | None) -> bool:
    """True when *version* identifies the verified SynCode 0.4.16 line."""
    if not version:
        return False
    v = str(version).strip()
    return v == "0.4.16" or v.startswith("0.4.16")


def _sorted_unique_names(raw: Any) -> Optional[list[str]]:
    """Deterministic sorted unique terminal-name list, or None if absent."""
    if raw is None:
        return None
    try:
        items = [str(t) for t in list(raw)]
    except TypeError:
        return None
    return sorted(set(items))


def classify_accept_sequence(
    terminals: Sequence[str],
    *,
    remainder_state: str | None,
    ignore_terminals: Sequence[str] | None = None,
    current_accept_terminals: Sequence[str] | None = None,
    next_accept_terminals: Sequence[str] | None = None,
) -> tuple[str, bool]:
    """
    Classify one AcceptSequence under SynCode 0.4.16 ``from_accept_terminals``.

    Returns ``(construction_kind, contains_ignored_terminal)``.
    Prefers ``unknown`` when length alone would be ambiguous.
    """
    terms = [str(t) for t in terminals]
    n = len(terms)
    ignore_set = set(ignore_terminals or [])
    cur_set = set(current_accept_terminals) if current_accept_terminals is not None else None
    next_set = set(next_accept_terminals) if next_accept_terminals is not None else None
    rem = remainder_state

    def _has_ignore(ts: list[str]) -> bool:
        return any(t in ignore_set for t in ts) if ignore_set else False

    # Length 3 is produced only by MAYBE_COMPLETE ignore intercalation:
    # [final_terminal, tignore, t2].
    if n == 3 and rem == "MAYBE_COMPLETE":
        mid_ignored = (not ignore_set) or (terms[1] in ignore_set)
        if mid_ignored:
            return "final_ignore_next", True
        return "unknown", _has_ignore(terms)

    if n == 2 and rem == "MAYBE_COMPLETE":
        return "final_then_next", _has_ignore(terms)

    if n == 1:
        t0 = terms[0]
        in_ignore = bool(ignore_set) and t0 in ignore_set
        in_next = next_set is not None and t0 in next_set
        in_cur = cur_set is not None and t0 in cur_set

        if rem == "COMPLETE":
            if in_ignore and not in_next:
                return "ignore_only", True
            if in_next and not in_ignore:
                return "next_terminal", False
            if in_next and in_ignore:
                # Ambiguous membership — do not fabricate.
                return "unknown", True
            if next_set is not None or ignore_set:
                # Sets present but terminal matched neither → unknown.
                return "unknown", in_ignore
            return "unknown", False

        if rem == "INCOMPLETE":
            if in_ignore and not in_cur:
                return "ignore_only", True
            if in_cur and not in_ignore:
                return "current_terminal", False
            if in_cur and in_ignore:
                return "unknown", True
            if cur_set is not None or ignore_set:
                return "unknown", in_ignore
            return "unknown", False

        if rem == "MAYBE_COMPLETE":
            # Length-1 paths: ignore-only, or other cur terminals (≠ final).
            if in_ignore and not in_cur:
                return "ignore_only", True
            if in_cur and not in_ignore:
                return "current_terminal", False
            if in_ignore and in_cur:
                return "unknown", True
            if cur_set is not None or ignore_set:
                return "unknown", in_ignore
            return "unknown", False

        # Remainder state missing — only ignore-only is safe when exclusive.
        if in_ignore and not in_cur and not in_next:
            return "ignore_only", True
        return "unknown", in_ignore

    # Empty sequence or unexpected lengths without a clear rule.
    return "unknown", _has_ignore(terms)


def apply_sequence_classifications(
    records: list[AcceptSequenceRecord],
    *,
    remainder_state: str | None,
    ignore_terminals: Sequence[str] | None = None,
    current_accept_terminals: Sequence[str] | None = None,
    next_accept_terminals: Sequence[str] | None = None,
) -> list[AcceptSequenceRecord]:
    """Attach classification metadata to newly built AcceptSequenceRecords."""
    out: list[AcceptSequenceRecord] = []
    for rec in records:
        terms = list(rec.terminals)
        kind, has_ignore = classify_accept_sequence(
            terms,
            remainder_state=remainder_state,
            ignore_terminals=ignore_terminals,
            current_accept_terminals=current_accept_terminals,
            next_accept_terminals=next_accept_terminals,
        )
        out.append(
            AcceptSequenceRecord(
                terminals=terms,
                displayed_terminal_count=len(terms),
                construction_kind=kind,  # type: ignore[arg-type]
                contains_ignored_terminal=has_ignore,
            )
        )
    return out


def semantics_fields_for_new_evidence(
    *,
    syncode_version: str,
    origin: str,
) -> dict[str, Any]:
    """
    Core-k / construction metadata for newly serialized evidence.

    Only filled when the verified SynCode 0.4.16 construction is in use.
    Historical loads must leave these fields absent (None).
    """
    if not is_syncode_0416_version(syncode_version):
        return {
            "core_lookahead_k": None,
            "core_lookahead_unit": None,
            "sequence_construction": None,
            "semantics_provenance": None,
        }

    sem: SemanticsProvenance
    if origin == "live_mask_runtime":
        sem = "recorded"
    elif origin == "import_recomputed_parser_only":
        sem = "recomputed"
    elif origin == "import_recorded_bundle":
        # Bundle-carried evidence may predate these fields; do not invent
        # recorded semantics here — caller should leave unset for old bundles.
        sem = "recorded"
    else:
        sem = "unavailable"

    return {
        "core_lookahead_k": CORE_LOOKAHEAD_K_SYNCODE_0416,
        "core_lookahead_unit": CORE_LOOKAHEAD_UNIT,
        "sequence_construction": SEQUENCE_CONSTRUCTION_SYNCODE_0416,
        "semantics_provenance": sem,
    }


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
    current_accept_terminals: Sequence[str] | None = None,
    next_accept_terminals: Sequence[str] | None = None,
    ignore_terminals: Sequence[str] | None = None,
) -> SyncodeParserEvidence:
    """
    Build SyncodeParserEvidence from a ParseResult (live or recomputed).

    For live masking, pass the ParseResult given to ``get_accept_mask`` and
    ``origin="live_mask_runtime"``.  For import recomputation, use
    ``origin="import_recomputed_parser_only"`` and leave ``accept_mask`` /
    ``mask_call_index`` unset.

    Optional ``current_accept_terminals`` / ``next_accept_terminals`` /
    ``ignore_terminals`` improve sequence classification when captured from the
    IncrementalParser at the same moment as the ParseResult.  They are stored
    as sorted unique lists when provided.
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

        cur_terms = _sorted_unique_names(current_accept_terminals)
        next_terms = _sorted_unique_names(next_accept_terminals)
        ignore_terms = _sorted_unique_names(ignore_terminals)

        records = apply_sequence_classifications(
            records,
            remainder_state=rem_state,
            ignore_terminals=ignore_terms,
            current_accept_terminals=cur_terms,
            next_accept_terminals=next_terms,
        )

        sem = semantics_fields_for_new_evidence(
            syncode_version=version,
            origin=origin_val,
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
            core_lookahead_k=sem["core_lookahead_k"],
            core_lookahead_unit=sem["core_lookahead_unit"],
            sequence_construction=sem["sequence_construction"],
            current_accept_terminals=cur_terms,
            next_accept_terminals=next_terms,
            ignore_terminals=ignore_terms,
            semantics_provenance=sem["semantics_provenance"],
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


def extract_parser_terminal_sets(inc_parser: Any) -> dict[str, Optional[list[str]]]:
    """
    Read cur/next/ignore terminal name sets from a SynCode IncrementalParser.

    Best-effort only — returns None values when attributes are absent.
    Does not mutate the parser.
    """
    cur = _sorted_unique_names(getattr(inc_parser, "cur_ac_terminals", None))
    nxt = _sorted_unique_names(getattr(inc_parser, "next_ac_terminals", None))
    ignore = None
    try:
        base = getattr(inc_parser, "base_parser", None)
        lexer_conf = getattr(base, "lexer_conf", None) if base is not None else None
        ignore = _sorted_unique_names(getattr(lexer_conf, "ignore", None))
    except Exception:
        ignore = None
    return {
        "current_accept_terminals": cur,
        "next_accept_terminals": nxt,
        "ignore_terminals": ignore,
    }


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
