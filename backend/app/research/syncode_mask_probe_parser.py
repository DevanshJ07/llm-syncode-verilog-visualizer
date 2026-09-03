"""Incremental parser / accept-sequence evidence for the mask probe."""

from __future__ import annotations

from typing import Any, Optional

from app.models.syncode_mask_probe import (
    AcceptSequenceProbeRecord,
    ParserProbeEvidence,
)
from app.research.syncode_mask_probe_prefix import sha256_utf8
from app.services.syncode_parser_evidence import (
    CORE_LOOKAHEAD_K_SYNCODE_0416,
    SEQUENCE_CONSTRUCTION_SYNCODE_0416,
    classify_accept_sequence,
    extract_parser_terminal_sets,
)


def _seq_terminals(seq: Any) -> list[str]:
    terms = getattr(seq, "accept_terminals", None)
    if terms is not None:
        return [str(t) for t in terms]
    return [str(t) for t in list(seq)]


def collect_parser_evidence(
    incremental_parser: Any,
    prefix: str,
    *,
    syncode_version: str = "0.4.16",
) -> ParserProbeEvidence:
    """
    Call get_acceptable_next_terminals once. Do not truncate accept sequences.
    Does not mutate production singleton parsers when given a dedicated instance.
    """
    warnings: list[str] = []
    # Ensure clean state for this prefix analysis.
    if hasattr(incremental_parser, "reset"):
        try:
            incremental_parser.reset()
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"parser reset failed: {exc}")

    parse_result = incremental_parser.get_acceptable_next_terminals(prefix)

    rem_state_obj = getattr(parse_result, "remainder_state", None)
    rem_state = (
        rem_state_obj.name
        if hasattr(rem_state_obj, "name")
        else (str(rem_state_obj) if rem_state_obj is not None else None)
    )

    remainder = getattr(parse_result, "remainder", None)
    rem_text: Optional[str] = None
    rem_bytes: Optional[list[int]] = None
    if isinstance(remainder, bytes):
        rem_bytes = list(remainder)
        try:
            rem_text = remainder.decode("utf-8")
        except UnicodeDecodeError:
            rem_text = None
            warnings.append("remainder bytes are not valid UTF-8")
    elif isinstance(remainder, str):
        rem_text = remainder
        rem_bytes = list(remainder.encode("utf-8"))

    # Fixed prefix = visible prefix minus remainder when remainder is exact suffix.
    fixed_prefix: Optional[str] = None
    fixed_status = "UNAVAILABLE"
    fixed_detail = ""
    if rem_text is None and rem_bytes is not None:
        fixed_detail = (
            "remainder is non-UTF-8 bytes; fixed prefix left UNAVAILABLE rather "
            "than fabricating a string subtraction"
        )
    elif rem_text is not None:
        if rem_text == "":
            fixed_prefix = prefix
            fixed_status = "VERIFIED"
            fixed_detail = "empty remainder; fixed prefix equals visible prefix"
        elif prefix.endswith(rem_text):
            fixed_prefix = prefix[: len(prefix) - len(rem_text)]
            fixed_status = "VERIFIED"
            fixed_detail = "remainder is an exact suffix of the visible prefix"
        else:
            fixed_status = "CONTRADICTED"
            fixed_detail = (
                "remainder is not an exact suffix of the visible prefix; "
                "fixed prefix marked UNAVAILABLE"
            )
            fixed_status = "UNAVAILABLE"
            fixed_detail = (
                "remainder is not an exact suffix of the visible prefix; "
                "refusing to derive fixed prefix"
            )
    else:
        fixed_detail = "remainder unavailable"

    terminal_sets = extract_parser_terminal_sets(incremental_parser)
    cur = list(terminal_sets.get("current_accept_terminals") or [])
    nxt = list(terminal_sets.get("next_accept_terminals") or [])
    ign = list(terminal_sets.get("ignore_terminals") or [])

    seqs_raw = list(getattr(parse_result, "accept_sequences", None) or [])
    records: list[AcceptSequenceProbeRecord] = []
    for seq in seqs_raw:
        terms = _seq_terminals(seq)
        kind, has_ignore = classify_accept_sequence(
            terms,
            remainder_state=rem_state,
            ignore_terminals=ign,
            current_accept_terminals=cur,
            next_accept_terminals=nxt,
        )
        records.append(
            AcceptSequenceProbeRecord(
                terminals=terms,
                construction_kind=kind,
                contains_ignored_terminal=has_ignore,
                displayed_terminal_count=len(terms),
            )
        )

    core_k = CORE_LOOKAHEAD_K_SYNCODE_0416 if syncode_version.startswith("0.4.16") else None
    construction = (
        SEQUENCE_CONSTRUCTION_SYNCODE_0416
        if syncode_version.startswith("0.4.16")
        else None
    )

    return ParserProbeEvidence(
        visible_prefix=prefix,
        visible_prefix_sha256=sha256_utf8(prefix),
        fixed_prefix=fixed_prefix,
        fixed_prefix_sha256=sha256_utf8(fixed_prefix) if fixed_prefix is not None else None,
        fixed_prefix_length=len(fixed_prefix) if fixed_prefix is not None else None,
        fixed_prefix_status=fixed_status,  # type: ignore[arg-type]
        fixed_prefix_detail=fixed_detail,
        remainder_text=rem_text,
        remainder_bytes=rem_bytes,
        remainder_escaped=repr(rem_text) if rem_text is not None else (
            repr(bytes(rem_bytes)) if rem_bytes is not None else ""
        ),
        remainder_state=rem_state,
        current_accept_terminals=cur,
        next_accept_terminals=nxt,
        ignore_terminals=ign,
        accept_sequences=records,
        accept_sequence_count=len(records),
        truncated_for_storage=False,
        core_lookahead_k=core_k,
        sequence_construction=construction,
        function_end=bool(getattr(parse_result, "function_end", False)),
        warnings=warnings,
    )


def parse_result_for_mask_store(parse_result: Any) -> Any:
    """
    Return a ParseResult suitable for MaskStore.get_accept_mask.

    SynCode GrammarConstrainer encodes remainder to bytes before mask lookup.
    We mirror that without mutating the parser's stored result object when possible.
    """
    rem = getattr(parse_result, "remainder", None)
    if isinstance(rem, bytes):
        return parse_result
    # Shallow copy fields onto a simple namespace-like object if needed.
    from syncode.parse_result import ParseResult

    rem_bytes: bytes
    if isinstance(rem, str):
        rem_bytes = rem.encode("utf-8")
    elif rem is None:
        rem_bytes = b""
    else:
        rem_bytes = bytes(rem)

    return ParseResult(
        accept_sequences=getattr(parse_result, "accept_sequences", set()) or set(),
        remainder=rem_bytes,
        remainder_state=getattr(parse_result, "remainder_state"),
        next_ac_indents=getattr(parse_result, "next_ac_indents", None),
        function_end=getattr(parse_result, "function_end", False),
    )
