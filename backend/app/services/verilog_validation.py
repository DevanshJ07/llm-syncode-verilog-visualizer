"""
Final-output validation against the tested Verilog Lark grammar.

Used after generation to determine whether the produced module is actually
valid under grammar=verilog, parser=lalr — independent of whether SynCode
masking was attempted during decoding.

Comment handling:
  The tested grammar %ignore's COMMENT (// ...) and NEWLINE.  Slashes inside
  line comments must never be flagged as arithmetic division.  Block comments
  (/* ... */) are not in the grammar and are stripped before parse/scan.
  Unsupported-construct regex scans run on comment-stripped code only.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import types
from dataclasses import dataclass, field
from functools import lru_cache

_VERILOG_GRAMMAR_PATH: str = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "verilog.lark")
)

# Block comments are not in the tested grammar — strip before validation.
_BLOCK_COMMENT_RE = re.compile(r"/\*[^*]*\*/", re.DOTALL)
# Line comments match grammar token: COMMENT: "//" /[^\n]*/ NEWLINE
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
class ParseTreeResult:
    """
    Result of building a Lark parse tree from the final generated output.

    Uses the same grammar and parser as final validation so the tree is
    consistent with ``final_parse_valid``.  Comments are stripped the same
    way — block comments removed, line comments left to the grammar's
    ``%ignore`` rule (they are gone after stripping too, which is fine).
    """
    parse_tree_available: bool
    parse_tree_text: str = ""          # tree.pretty() when available
    parse_tree_error_type: str = ""    # exception class name
    parse_tree_error_message: str = ""
    parse_tree_error_line: int = 0     # 0 means unknown
    parse_tree_error_column: int = 0
    parse_tree_unexpected_token: str = ""
    parse_tree_expected_terminals: list = field(default_factory=list)
    parse_tree_previous_token: str = ""


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
    Remove Verilog comments before validation scanning/parsing.

    Strips:
      - line comments ``// ...`` (grammar COMMENT token, %ignore'd)
      - block comments ``/* ... */`` (not in grammar — removed so parse succeeds)
    """
    without_block = _BLOCK_COMMENT_RE.sub(" ", code)
    return _LINE_COMMENT_RE.sub(" ", without_block)


def _load_lark_module():
    """
    Return the lark module, trying two locations:
      1. Standard ``import lark`` (works when lark is a standalone package).
      2. syncode's bundled ``syncode/larkm`` (syncode ships its own lark fork).

    This fallback is needed because syncode does not declare lark as an
    installable dependency — it bundles larkm instead.  The fallback loads
    larkm *without* executing ``syncode/__init__.py`` (which has an optional
    data-file dependency that may not be present).
    """
    try:
        import lark  # noqa: PLC0415
        return lark
    except ImportError:
        pass

    # Locate syncode/larkm under any sys.path entry.
    for path_entry in sys.path:
        larkm_dir = os.path.join(path_entry, "syncode", "larkm")
        larkm_init = os.path.join(larkm_dir, "__init__.py")
        if not (os.path.isdir(larkm_dir) and os.path.isfile(larkm_init)):
            continue
        try:
            # Ensure a stub 'syncode' package is in sys.modules so that
            # larkm's relative imports (from .lexer …) resolve correctly
            # without running syncode/__init__.py.
            if "syncode" not in sys.modules:
                stub = types.ModuleType("syncode")
                stub.__path__ = [os.path.dirname(larkm_dir)]  # type: ignore[attr-defined]
                stub.__package__ = "syncode"
                sys.modules["syncode"] = stub

            spec = importlib.util.spec_from_file_location(
                "syncode.larkm",
                larkm_init,
                submodule_search_locations=[larkm_dir],
            )
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            sys.modules["syncode.larkm"] = mod
            # Register as 'lark' so that subsequent `from lark import X` calls
            # in this process also resolve correctly.
            sys.modules.setdefault("lark", mod)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            return mod
        except Exception:  # noqa: BLE001
            continue

    return None


@lru_cache(maxsize=1)
def _lark_parser():
    lark = _load_lark_module()
    if lark is None:
        raise ImportError(
            "lark not available — tried standalone lark and syncode.larkm"
        )
    Lark = lark.Lark  # type: ignore[attr-defined]
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

    Comments are stripped first; the grammar only defines ``//`` line comments.
    """
    if not code or not code.strip():
        return False, "empty output"

    parse_target = strip_verilog_comments(code)

    try:
        parser = _lark_parser()
        parser.parse(parse_target)
        return True, ""
    except ImportError as exc:
        return False, f"lark not available — {exc}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def build_parse_tree(code: str) -> ParseTreeResult:
    """
    Build a Lark parse tree for *code* using the same grammar/parser as
    ``parse_with_verilog_grammar``.

    On success: ``parse_tree_available=True`` and ``parse_tree_text`` holds
    ``tree.pretty()``.

    On failure: ``parse_tree_available=False`` and the error fields carry
    structured diagnostics extracted from Lark's exception hierarchy.
    """
    if not code or not code.strip():
        return ParseTreeResult(
            parse_tree_available=False,
            parse_tree_error_type="EmptyOutput",
            parse_tree_error_message="No output to parse.",
        )

    # Strip comments the same way as parse_with_verilog_grammar.
    parse_target = strip_verilog_comments(code)

    lark_mod = _load_lark_module()
    if lark_mod is None:
        return ParseTreeResult(
            parse_tree_available=False,
            parse_tree_error_type="ImportError",
            parse_tree_error_message="lark not available — tried standalone lark and syncode.larkm.",
        )

    # Grab exception types from whichever lark we loaded.
    lark_exceptions = getattr(lark_mod, "exceptions", None)
    UnexpectedToken = getattr(lark_exceptions, "UnexpectedToken", None) if lark_exceptions else None
    UnexpectedCharacters = getattr(lark_exceptions, "UnexpectedCharacters", None) if lark_exceptions else None
    UnexpectedEOF = getattr(lark_exceptions, "UnexpectedEOF", None) if lark_exceptions else None

    try:
        print(
            f"[parser-tree] parsing final output length: {len(parse_target)}",
            flush=True,
        )
        parser = _lark_parser()
        tree = parser.parse(parse_target)
        tree_text = tree.pretty()
        print(
            f"[parser-tree] parse success: True  tree length: {len(tree_text)}",
            flush=True,
        )
        return ParseTreeResult(
            parse_tree_available=True,
            parse_tree_text=tree_text,
        )

    except Exception as exc:
        error_type = type(exc).__name__
        error_msg = str(exc)
        line = 0
        column = 0
        unexpected = ""
        expected: list[str] = []
        previous = ""

        try:
            if UnexpectedToken and isinstance(exc, UnexpectedToken):
                line = getattr(exc, "line", 0) or 0
                column = getattr(exc, "column", 0) or 0
                tok = getattr(exc, "token", None)
                unexpected = str(tok) if tok is not None else ""
                raw_expected = getattr(exc, "expected", None) or []
                expected = sorted(str(t) for t in raw_expected)
                prev_tok = getattr(exc, "token_history", None)
                if prev_tok:
                    previous = str(list(prev_tok)[-1])
            elif UnexpectedCharacters and isinstance(exc, UnexpectedCharacters):
                line = getattr(exc, "line", 0) or 0
                column = getattr(exc, "column", 0) or 0
                unexpected = getattr(exc, "char", "") or ""
                raw_allowed = getattr(exc, "allowed", None) or []
                expected = sorted(str(t) for t in raw_allowed)
            elif UnexpectedEOF and isinstance(exc, UnexpectedEOF):
                raw_expected = getattr(exc, "expected", None) or []
                expected = sorted(str(t) for t in raw_expected)
        except Exception:
            pass

        print(
            f"[parser-tree] parse success: False  error: {error_type}: {error_msg[:120]}",
            flush=True,
        )
        return ParseTreeResult(
            parse_tree_available=False,
            parse_tree_error_type=error_type,
            parse_tree_error_message=error_msg,
            parse_tree_error_line=line,
            parse_tree_error_column=column,
            parse_tree_unexpected_token=unexpected,
            parse_tree_expected_terminals=expected,
            parse_tree_previous_token=previous,
        )


def validate_verilog_output(code: str) -> FinalValidationResult:
    """
    Validate extracted Verilog output.

    Primary validity: Lark parse (parser=lalr) on comment-stripped text.
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
