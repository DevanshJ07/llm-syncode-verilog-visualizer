"""
Final-output validation against the tested Verilog Lark grammar.

Used after generation to determine whether the produced module is actually
valid under grammar=verilog, parser=lalr — independent of whether SynCode
masking was attempted during decoding.

Comment handling:
  The tested grammar %ignore's COMMENT and MULTILINE_COMMENT.  Slashes inside
  comments must never be flagged as arithmetic division.  Unsupported-construct
  regex scans run on comment-stripped code only; Lark parse runs on the full
  text so ignored comments behave as the grammar specifies.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache

_VERILOG_GRAMMAR_PATH: str = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verilog.lark")
)

# Match the grammar's comment tokens (see verilog.lark).
_MULTILINE_COMMENT_RE = re.compile(r"/\*[^*]*\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")

# Constructs outside the tested grammar — scanned on comment-stripped code only.
_UNSUPPORTED_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("always", re.compile(r"\balways\b", re.IGNORECASE)),
    ("reg", re.compile(r"\breg\b", re.IGNORECASE)),
    ("case", re.compile(r"\bcase\b", re.IGNORECASE)),
    ("begin", re.compile(r"\bbegin\b", re.IGNORECASE)),
    ("end (not endmodule)", re.compile(r"\bend\b(?!module\b)", re.IGNORECASE)),
    ("@", re.compile(r"@")),
    ("input wire", re.compile(r"\binput\s+wire\b", re.IGNORECASE)),
    ("output reg", re.compile(r"\boutput\s+reg\b", re.IGNORECASE)),
    ("if", re.compile(r"\bif\b", re.IGNORECASE)),
    ("else", re.compile(r"\belse\b", re.IGNORECASE)),
    ("parameter", re.compile(r"\bparameter\b", re.IGNORECASE)),
    ("generate", re.compile(r"\bgenerate\b", re.IGNORECASE)),
    ("vector/range", re.compile(r"\[[^\]]+\]")),
    ("arithmetic +", re.compile(r"\+")),
    ("arithmetic -", re.compile(r"(?<![a-zA-Z0-9_])-(?![a-zA-Z0-9_])")),
    ("arithmetic *", re.compile(r"\*")),
    ("arithmetic /", re.compile(r"/")),
    ("arithmetic %", re.compile(r"%")),
]


@dataclass
class FinalValidationResult:
    final_parse_valid: bool
    final_parse_error: str = ""
    unsupported_constructs_detected: list[str] = field(default_factory=list)
    comments_stripped_for_validation: bool = True


@dataclass
class ConstraintEvidence:
    """Truthful SynCode constraint summary for API / UI."""

    constraint_requested: bool
    constraint_status: str  # off | unavailable | none | partial | full | failed
    constraint_applied: bool
    fallback_occurred: bool
    syncode_error: str = ""


def read_verilog_grammar() -> str:
    if not os.path.isfile(_VERILOG_GRAMMAR_PATH):
        raise FileNotFoundError(
            f"Verilog grammar file not found: {_VERILOG_GRAMMAR_PATH}"
        )
    with open(_VERILOG_GRAMMAR_PATH, "r", encoding="utf-8") as fh:
        return fh.read()


def strip_verilog_comments(code: str) -> str:
    """
    Remove Verilog comments before unsupported-construct scanning.

    Strips tokens matching the tested grammar:
      COMMENT:            // ...
      MULTILINE_COMMENT:  /* ... */  (non-nested, DFA-compatible pattern)
    """
    without_block = _MULTILINE_COMMENT_RE.sub(" ", code)
    return _LINE_COMMENT_RE.sub(" ", without_block)


@lru_cache(maxsize=1)
def _lark_parser():
    from lark import Lark  # noqa: PLC0415 — provided by syncode dependency

    return Lark(
        read_verilog_grammar(),
        parser="lalr",
        maybe_placeholders=False,
        propagate_positions=False,
    )


def detect_unsupported_constructs(code: str, *, strip_comments: bool = True) -> list[str]:
    """
    Return labels for grammar-external constructs in *code*.

    When *strip_comments* is True (default), comments are removed first so
    slashes in ``//`` or ``/* */`` are not mistaken for arithmetic ``/``.
    """
    scan_target = strip_verilog_comments(code) if strip_comments else code
    found: list[str] = []
    for label, pattern in _UNSUPPORTED_PATTERNS:
        if pattern.search(scan_target):
            found.append(label)
    return found


def parse_with_verilog_grammar(code: str) -> tuple[bool, str]:
    """
    Parse *code* with the tested Verilog Lark grammar (parser=lalr).

    The full text is passed to Lark; COMMENT / MULTILINE_COMMENT tokens are
    %ignore'd by the grammar definition.
    """
    if not code or not code.strip():
        return False, "empty output"

    try:
        parser = _lark_parser()
        parser.parse(code)
        return True, ""
    except ImportError:
        return False, "lark not installed — cannot run grammar parse"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def validate_verilog_output(code: str) -> FinalValidationResult:
    """
    Validate extracted Verilog output.

    Primary validity: Lark parse of the full text (grammar ignores comments).
    Secondary guard: unsupported-construct scan on comment-stripped code only.
    """
    stripped = strip_verilog_comments(code)
    unsupported = detect_unsupported_constructs(stripped, strip_comments=False)
    parse_ok, parse_err = parse_with_verilog_grammar(code)

    if unsupported:
        return FinalValidationResult(
            final_parse_valid=False,
            final_parse_error=(
                "unsupported constructs detected (outside comments): "
                + ", ".join(unsupported)
            ),
            unsupported_constructs_detected=unsupported,
            comments_stripped_for_validation=True,
        )

    return FinalValidationResult(
        final_parse_valid=parse_ok,
        final_parse_error=parse_err,
        unsupported_constructs_detected=[],
        comments_stripped_for_validation=True,
    )


def compute_constraint_status(
    *,
    mode: str,
    syncode_available: bool,
    total_steps: int,
    syncode_active_steps: int,
    syncode_fallback_steps: int,
    final_parse_valid: bool,
    final_parse_error: str,
) -> ConstraintEvidence:
    """
    Derive honest constraint evidence. Never report full/applied when the
    final output fails grammar validation.
    """
    requested = mode == "syncode"
    fallback_occurred = syncode_fallback_steps > 0

    if not requested:
        return ConstraintEvidence(
            constraint_requested=False,
            constraint_status="off",
            constraint_applied=False,
            fallback_occurred=False,
        )

    if not syncode_available:
        return ConstraintEvidence(
            constraint_requested=True,
            constraint_status="unavailable",
            constraint_applied=False,
            fallback_occurred=True,
            syncode_error=final_parse_error if not final_parse_valid else "",
        )

    if total_steps <= 0:
        step_status = "none"
    elif syncode_active_steps == 0:
        step_status = "none"
    elif syncode_active_steps == total_steps and syncode_fallback_steps == 0:
        step_status = "full"
    else:
        step_status = "partial"

    if not final_parse_valid:
        syncode_error = final_parse_error or "final output is not valid under tested grammar"
        if step_status == "full":
            status = "failed"
        elif step_status == "partial":
            status = "partial"
        else:
            status = "failed"
        return ConstraintEvidence(
            constraint_requested=True,
            constraint_status=status,
            constraint_applied=False,
            fallback_occurred=fallback_occurred or step_status != "full",
            syncode_error=syncode_error,
        )

    applied = step_status == "full" and not fallback_occurred
    return ConstraintEvidence(
        constraint_requested=True,
        constraint_status=step_status,
        constraint_applied=applied,
        fallback_occurred=fallback_occurred,
        syncode_error="",
    )


def constraint_applied_label(
    status: str, active: int, total: int, fallback: int
) -> str:
    """Human-readable constraint-applied string for the evidence panel."""
    if status == "off":
        return "off"
    if status == "unavailable":
        return "unavailable"
    if status == "none":
        return f"none (0/{total})"
    if status == "partial":
        return f"partial {active}/{total}"
    if status == "full":
        return f"full {active}/{total}"
    if status == "failed":
        if active > 0:
            return f"partial {active}/{total} (output invalid)"
        return f"failed (fallback {fallback}/{total})"
    return status
