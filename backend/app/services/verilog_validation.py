"""
Final-output validation against the canonical Verilog Lark grammar
(``backend/grammar/verilog.lark``).

Used after generation to determine whether the produced module is actually
valid under the SynViz grammar (parser=lalr) — independent of whether SynCode
masking was attempted during decoding.

Comment handling:
  The tested grammar %ignore's COMMENT (// ...) and NEWLINE.  Slashes inside
  line comments must never be flagged as arithmetic division.  Block comments
  (/* ... */) are not in the grammar and are stripped before parse/scan.
  Unsupported-construct regex scans run on comment-stripped code only and cover
  intentionally reserved-but-unreachable constructs (generate/function/task).
"""

from __future__ import annotations

from app.console_safe import _safe_console_print

import importlib.util
import logging
import os
import re
import sys
import types
from dataclasses import dataclass, field
from functools import lru_cache

from app.core.grammar import (
    CANONICAL_GRAMMAR_PATH,
    read_verilog_grammar,
)

# Compatibility alias — path resolution lives only in app.core.grammar.
_VERILOG_GRAMMAR_PATH: str = str(CANONICAL_GRAMMAR_PATH)

_STUB_ATTR = "_verilog_validation_lark_stub"

_log = logging.getLogger(__name__)

# Block comments are not in the tested grammar — strip before validation.
_BLOCK_COMMENT_RE = re.compile(r"/\*[^*]*\*/", re.DOTALL)
# Line comments match grammar token: COMMENT: "//" /[^\n]*/ NEWLINE
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")

# Constructs intentionally outside the canonical VerilogEval subset grammar
# (see backend/grammar/verilog.lark reserved-but-unreachable terminals).
# Supported RTL (always/reg/case/vectors/arithmetic/…) is NOT listed here.
_UNSUPPORTED_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("generate", re.compile(r"\bgenerate\b", re.IGNORECASE)),
    ("endgenerate", re.compile(r"\bendgenerate\b", re.IGNORECASE)),
    ("genvar", re.compile(r"\bgenvar\b", re.IGNORECASE)),
    ("function", re.compile(r"\bfunction\b", re.IGNORECASE)),
    ("endfunction", re.compile(r"\bendfunction\b", re.IGNORECASE)),
    ("task", re.compile(r"\btask\b", re.IGNORECASE)),
    ("endtask", re.compile(r"\bendtask\b", re.IGNORECASE)),
]


@dataclass
class ParserFailureContext:
    """
    Rich context around a Lark parse failure for research diagnostics.

    Built from the failed parse exception so that the UI can display a
    human-readable explanation of *why* the output is invalid, including a
    line-and-caret excerpt and a heuristic interpretation of the LALR state
    at the point of failure.
    """
    available: bool
    prefix_before_error: str = ""          # numbered source lines near the failure
    error_line_text: str = ""              # the exact source line that caused the error
    caret_line: str = ""                   # spaces + "^" pointing at error column
    expected_terminals: list[str] = field(default_factory=list)
    likely_parser_state_summary: str = ""  # concise summary of parser state + research note
    likely_interpretation: str = ""        # heuristic natural-language explanation


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
    # Optional rich failure context — populated on parse failure only.
    parser_failure_context: "ParserFailureContext | None" = None


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
    lark_grammar_loaded: bool = False
    syncode_mask_store_loaded: bool = False
    constraint_active_during_generation: bool = False
    raw_unconstrained_generation_used: bool = False
    unconstrained_reason: str = ""


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
                setattr(stub, _STUB_ATTR, True)
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
            # Drop the temporary stub so real SynCode can import later.
            stub_mod = sys.modules.get("syncode")
            if stub_mod is not None and getattr(stub_mod, _STUB_ATTR, False):
                del sys.modules["syncode"]
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


# Keyword-like strings that the grammar lexes as IDENTIFIER in certain states.
_KEYWORD_IDENTIFIERS: frozenset[str] = frozenset({
    "reg", "wire", "input", "output", "inout", "parameter",
    "always", "begin", "end", "initial", "assign",
    "if", "else", "case", "generate",
})


def _build_failure_context(
    parse_target: str,
    line: int,
    column: int,
    unexpected: str,
    expected: list[str],
    previous: str,
) -> ParserFailureContext:
    """
    Build a rich ``ParserFailureContext`` from the already-extracted
    Lark exception fields.

    ``parse_target`` is the comment-stripped source that was fed to the
    parser, so its line numbers match those reported by Lark.
    """
    source_lines = parse_target.splitlines()

    # ── Error line text ─────────────────────────────────────────────────────
    error_line_text = ""
    if 1 <= line <= len(source_lines):
        error_line_text = source_lines[line - 1]

    # ── Caret line ──────────────────────────────────────────────────────────
    caret_line = ""
    if column > 0:
        caret_line = " " * (column - 1) + "^"

    # ── Prefix excerpt (up to 4 lines ending with the error line) ───────────
    start_idx = max(0, line - 4)      # 0-based
    end_idx = min(line, len(source_lines))  # exclusive
    prefix_lines: list[str] = []
    for i in range(start_idx, end_idx):
        prefix_lines.append(f"{i + 1:>3} | {source_lines[i]}")
    prefix_before_error = "\n".join(prefix_lines)

    # ── Heuristic interpretation ─────────────────────────────────────────────
    prev_lower = previous.lower().strip() if previous else ""
    unexpected_str = unexpected or "$END"
    expects_lpar = "LPAR" in expected
    expects_comma_or_rpar = "COMMA" in expected or "RPAR" in expected

    # When token_history is empty, infer the previous meaningful token from
    # the source text immediately before the error column / line.
    inferred_prev: str = ""
    if not prev_lower:
        if 1 <= line <= len(source_lines):
            pre_col = source_lines[line - 1][: max(0, column - 1)].strip()
            words = pre_col.split()
            if words:
                inferred_prev = words[-1].rstrip(";,")
            elif line > 1:
                words = source_lines[line - 2].strip().split()
                if words:
                    inferred_prev = words[-1].rstrip(";,")
        if inferred_prev:
            prev_lower = inferred_prev.lower()

    likely_interpretation = ""

    # Prefer the explicit previous token; fall back to the inferred one.
    display_prev = previous or inferred_prev

    if expects_lpar and prev_lower in _KEYWORD_IDENTIFIERS:
        likely_interpretation = (
            f"The parser accepted `{display_prev}` as name_of_module: IDENTIFIER, "
            f"beginning a module_instantiation statement. "
            f"It then expected a module_instance of the form "
            f"name_of_instance '(' ... ')'. "
            f"Parsing failed because the required '(' (LPAR) was never produced "
            f"before {'end of input' if '$END' in unexpected_str else repr(unexpected_str)}."
        )
    elif expects_lpar:
        likely_interpretation = (
            f"The parser expected '(' (LPAR) to begin a parenthesized port or "
            f"argument list. "
            f"This often occurs after a module name in a module_instantiation, "
            f"or after the identifier in a module header. "
            f"Unexpected: {unexpected_str!r} at line {line}, column {column}."
        )
    elif expects_comma_or_rpar and prev_lower in _KEYWORD_IDENTIFIERS:
        likely_interpretation = (
            f"The parser treated `{display_prev}` (a keyword-looking token) as a "
            f"port IDENTIFIER inside a port list or argument list. "
            f"It then expected a comma or ')' to continue or close the list, "
            f"but found {unexpected_str!r} instead. "
            f"This commonly occurs with ANSI-style port declarations "
            f"(e.g. `input wire`, `output reg`) that are not supported by the "
            f"tested grammar."
        )
    elif expects_comma_or_rpar:
        likely_interpretation = (
            f"The parser was inside a port list or argument list and expected "
            f"',' or ')' to continue or close it. "
            f"Unexpected: {unexpected_str!r} at line {line}, column {column}."
        )
    else:
        if expected:
            likely_interpretation = (
                f"The parser expected one of: {', '.join(expected[:6])}. "
                f"Got: {unexpected_str!r} at line {line}, column {column}."
            )

    # ── Parser state summary + research note ────────────────────────────────
    state_parts = [
        f"Parser stopped at line {line}, column {column}.",
        f"Unexpected: {unexpected_str}.",
        f"Expected one of: {', '.join(expected[:8]) if expected else 'N/A'}.",
    ]
    if expects_lpar:
        state_parts.append(
            "Research note: this often indicates that a keyword-like string "
            "such as `reg` was accepted as IDENTIFIER/name_of_module and the "
            "parser was waiting for the parenthesized module-instance "
            "connection list."
        )
    likely_parser_state_summary = "\n".join(state_parts)

    return ParserFailureContext(
        available=True,
        prefix_before_error=prefix_before_error,
        error_line_text=error_line_text,
        caret_line=caret_line,
        expected_terminals=expected,
        likely_parser_state_summary=likely_parser_state_summary,
        likely_interpretation=likely_interpretation,
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
        _safe_console_print(
            f"[parser-tree] parsing final output length: {len(parse_target)}",
            flush=True,
        )
        parser = _lark_parser()
        tree = parser.parse(parse_target)
        tree_text = tree.pretty()
        _safe_console_print(
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

        _safe_console_print(
            f"[parser-tree] parse success: False  error: {error_type}: {error_msg[:120]}",
            flush=True,
        )

        # Build rich failure context for the research UI.
        failure_ctx: ParserFailureContext | None = None
        try:
            failure_ctx = _build_failure_context(
                parse_target=parse_target,
                line=line,
                column=column,
                unexpected=unexpected,
                expected=expected,
                previous=previous,
            )
        except Exception as ctx_exc:  # noqa: BLE001
            _safe_console_print(
                f"[parser-tree] failure context build error (non-fatal): {ctx_exc}",
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
            parser_failure_context=failure_ctx,
        )


# ---------------------------------------------------------------------------
# Incremental parser state (per-step post-generation analysis)
# ---------------------------------------------------------------------------

@dataclass
class IncrementalParserState:
    """Research snapshot of Lark parser state for one decoding-step prefix."""
    available: bool
    prefix_output: str = ""
    prefix_parse_status: str = ""       # valid_prefix | invalid_prefix | complete_parse
    parser_accepts_end: bool = False
    expected_next_terminals: list[str] = field(default_factory=list)
    accepted_next_terminals: list[str] = field(default_factory=list)
    likely_grammar_context: str = ""
    likely_grammar_path: str = ""
    selected_token_interpretation: str = ""
    likely_parser_interpretation: str = ""
    partial_parse_view: str = ""
    parse_tree_text: str = ""
    parser_error_type: str = ""
    parser_error_message: str = ""


def _extract_rule_names_from_partial_view(partial_view: str) -> list[str]:
    """Pull grammar rule names from Lark InteractiveParser.pretty() output."""
    import re  # noqa: PLC0415
    names: list[str] = []
    for match in re.finditer(r"Rule\(NonTerminal\(Token\('RULE', '([^']+)'\)", partial_view):
        rule = match.group(1)
        if rule not in names:
            names.append(rule)
    return names


def _infer_grammar_context(prefix: str) -> str:
    """Heuristic open-context label from prefix shape."""
    lower = prefix.lower()
    if "endmodule" in lower:
        return "inside module — after endmodule (complete or trailing tokens)"
    if "module" not in lower:
        return "before first module"
    if "(" in prefix and ")" not in prefix.split("module", 1)[-1]:
        return "inside module header — port list"
    if "assign" in lower:
        return "inside module — continuous assignment"
    return "inside module — module_item boundary"


def _build_likely_grammar_path(
    rule_names: list[str],
    expected: list[str],
    accepted: list[str],
) -> str:
    """Format a readable likely grammar path from interactive parser hints."""
    lines: list[str] = []
    if rule_names:
        for name in rule_names[:8]:
            lines.append(f"  {name}")
    elif expected or accepted:
        lines.append("  (rule stack not serialized — see expected/accepted terminals)")
    else:
        return ""
    return "\n".join(lines)


def _build_incremental_interpretation(
    *,
    prefix: str,
    selected_token: str,
    prefix_parse_status: str,
    parser_accepts_end: bool,
    expected: list[str],
    accepted: list[str],
    partial_view: str,
    rule_names: list[str],
) -> tuple[str, str, str]:
    """
    Return (selected_token_interpretation, likely_grammar_path, research_conclusion).

    Implements research-specific cases A–D from the spec.
    """
    sel = selected_token.strip()
    sel_lower = sel.lower()
    expected_set = set(expected)
    accepted_set = set(accepted)

    # Token text without surrounding whitespace for keyword checks.
    sel_word = sel_lower.strip()

    selected_interp = ""
    conclusion_parts: list[str] = []

    # Case A — keyword-like token at module_item boundary → IDENTIFIER
    if sel_word in _KEYWORD_IDENTIFIERS and "IDENTIFIER" in expected_set.union(accepted_set):
        if "name_of_module" in partial_view or "module_instantiation" in rule_names:
            selected_interp = f"{sel_word} matched IDENTIFIER"
            conclusion_parts.append(
                f"`{sel_word}` matched IDENTIFIER and is likely being interpreted "
                f"as name_of_module for module_instantiation, not as a "
                f"{sel_word} declaration keyword."
            )
        elif sel_word == "input" and expected_set.intersection({"COMMA", "RPAR"}):
            selected_interp = f"{sel_word} matched IDENTIFIER (port name)"
            conclusion_parts.append(
                "`input` was interpreted as a port IDENTIFIER, not as an "
                "input declaration keyword."
            )
        else:
            selected_interp = f"{sel_word} matched IDENTIFIER"
            conclusion_parts.append(
                f"`{sel_word}` was accepted as IDENTIFIER under the current "
                f"parser state (keyword-like token, not a dedicated declaration rule)."
            )

    # Case B — after reg + instance name, expect LPAR
    if "LPAR" in expected_set or "LPAR" in accepted_set:
        words = prefix.strip().split()
        if len(words) >= 2:
            prev_word = words[-2].rstrip(";,").lower()
            last_word = words[-1].rstrip(";,").lower()
            if prev_word in _KEYWORD_IDENTIFIERS and last_word.isidentifier():
                conclusion_parts.append(
                    f"The parser has accepted `{prev_word}` as module type and "
                    f"`{last_word}` as instance name. It now expects `(` to begin "
                    f"the module connection list."
                )
                if not selected_interp:
                    selected_interp = f"{last_word} matched IDENTIFIER (name_of_instance)"

    # Case C — input as port IDENTIFIER in port list
    if (
        sel_word == "input"
        and expected_set.intersection({"COMMA", "RPAR"})
        and "input_declaration" not in partial_view
    ):
        selected_interp = "input matched IDENTIFIER (port name)"
        conclusion_parts.append(
            "`input` was interpreted as a port IDENTIFIER, not as an "
            "input declaration keyword."
        )

    # Case D — parser accepts $END
    if parser_accepts_end or "$END" in accepted_set:
        conclusion_parts.append(
            "The parser can accept $END here. The model EOS token should be allowed."
        )

    if prefix_parse_status == "complete_parse":
        conclusion_parts.append(
            "Prefix is a complete grammar-valid derivation — full parse tree available."
        )
    elif prefix_parse_status == "invalid_prefix":
        conclusion_parts.append(
            "Prefix is not a valid prefix under the tested Verilog grammar."
        )
    elif prefix_parse_status == "valid_prefix" and not conclusion_parts:
        nxt = ", ".join(accepted[:6]) if accepted else ", ".join(expected[:6])
        conclusion_parts.append(
            f"Prefix is a valid incomplete derivation. Expected next: {nxt or 'N/A'}."
        )

    grammar_path = _build_likely_grammar_path(rule_names, expected, accepted)

    # Enrich path for module_instantiation case
    if "module_instantiation" in rule_names or "name_of_module" in rule_names:
        path_lines = ["module_item", "  module_instantiation"]
        if "name_of_module" in rule_names or sel_word in _KEYWORD_IDENTIFIERS:
            path_lines.append(f"    name_of_module: IDENTIFIER ({sel_word or '?'})")
        if "name_of_instance" in rule_names or "LPAR" in accepted_set:
            inst = prefix.strip().split()[-1].rstrip(";,") if prefix.strip() else "?"
            path_lines.append(f"    module_instance")
            path_lines.append(f"      name_of_instance: {inst}")
        grammar_path = "\n".join(path_lines)

    return (
        selected_interp,
        grammar_path,
        " ".join(conclusion_parts),
    )


def analyze_incremental_prefix(
    prefix: str,
    selected_token: str = "",
) -> IncrementalParserState:
    """
    Analyze *prefix* (context + selected token) with the tested Verilog grammar.

    Uses Lark full-parse for status classification and ``parse_interactive``
    + ``iter_parse`` for expected/accepted terminals and partial stack view.
    """
    if not prefix or not prefix.strip():
        return IncrementalParserState(
            available=False,
            prefix_output=prefix,
            prefix_parse_status="unavailable",
        )

    try:
        parser = _lark_parser()
    except ImportError as exc:
        return IncrementalParserState(
            available=False,
            prefix_output=prefix,
            prefix_parse_status="unavailable",
            parser_error_message=str(exc),
        )

    lark_mod = _load_lark_module()
    lark_exceptions = getattr(lark_mod, "exceptions", None) if lark_mod else None
    UnexpectedToken = getattr(lark_exceptions, "UnexpectedToken", None) if lark_exceptions else None
    UnexpectedEOF = getattr(lark_exceptions, "UnexpectedEOF", None) if lark_exceptions else None

    target = strip_verilog_comments(prefix)
    status = "valid_prefix"
    parser_accepts_end = False
    expected: list[str] = []
    accepted: list[str] = []
    partial_view = ""
    parse_tree_text = ""
    error_type = ""
    error_message = ""

    # ── Full parse: complete vs incomplete vs invalid ───────────────────
    try:
        tree = parser.parse(target)
        status = "complete_parse"
        parse_tree_text = tree.pretty()
        parser_accepts_end = True
    except Exception as exc:
        error_type = type(exc).__name__
        error_message = str(exc)
        if UnexpectedEOF and isinstance(exc, UnexpectedEOF):
            status = "valid_prefix"
            expected = sorted(str(x) for x in (getattr(exc, "expected", None) or []))
            parser_accepts_end = "$END" in expected
        elif UnexpectedToken and isinstance(exc, UnexpectedToken):
            tok = getattr(exc, "token", None)
            tok_str = str(tok) if tok is not None else ""
            expected = sorted(str(x) for x in (getattr(exc, "expected", None) or []))
            # EOF-at-end often appears as empty UnexpectedToken
            if not tok_str.strip() or "EOF" in tok_str or tok_str == "$END":
                status = "valid_prefix"
                parser_accepts_end = "$END" in expected
            else:
                status = "invalid_prefix"
                parser_accepts_end = False
        else:
            status = "invalid_prefix"

    # ── Interactive parser state after consuming prefix ─────────────────
    try:
        ip = parser.parse_interactive(target)
        for _ in ip.iter_parse():
            pass
        accepted = sorted(str(x) for x in ip.accepts())
        if "$END" in accepted:
            parser_accepts_end = True
        partial_view = ip.pretty()
        if not expected and status != "complete_parse":
            # choices() keys are the interactive expected symbols
            ch = ip.choices()
            choice_terms = sorted(
                str(k) for k in ch.keys()
                if not str(k).startswith("__") and str(k) != "$END"
            )
            if choice_terms:
                expected = choice_terms
    except Exception as exc:
        if not error_message:
            error_message = str(exc)
            error_type = type(exc).__name__

    rule_names = _extract_rule_names_from_partial_view(partial_view)
    grammar_context = _infer_grammar_context(target)

    sel_interp, grammar_path, conclusion = _build_incremental_interpretation(
        prefix=target,
        selected_token=selected_token,
        prefix_parse_status=status,
        parser_accepts_end=parser_accepts_end,
        expected=expected,
        accepted=accepted,
        partial_view=partial_view,
        rule_names=rule_names,
    )

    return IncrementalParserState(
        available=True,
        prefix_output=prefix,
        prefix_parse_status=status,
        parser_accepts_end=parser_accepts_end,
        expected_next_terminals=expected,
        accepted_next_terminals=accepted,
        likely_grammar_context=grammar_context,
        likely_grammar_path=grammar_path,
        selected_token_interpretation=sel_interp,
        likely_parser_interpretation=conclusion,
        partial_parse_view=partial_view,
        parse_tree_text=parse_tree_text,
        parser_error_type=error_type if status == "invalid_prefix" else "",
        parser_error_message=error_message if status == "invalid_prefix" else "",
    )


def enrich_steps_with_incremental_parser_state(steps: list) -> None:
    """
    Attach incremental parser snapshots to each ``DecodingStep`` in *steps*.

    Mutates steps in place.  Safe to call after generation; does not affect
    masking or decoding.
    """
    from app.models.schemas import IncrementalParserStateSchema  # noqa: PLC0415

    for step in steps:
        prefix = (getattr(step, "context", "") or "") + (
            getattr(step, "selected_token", "") or ""
        )
        selected = getattr(step, "selected_token", "") or ""
        snap = analyze_incremental_prefix(prefix, selected_token=selected)
        step.incremental_parser_state = IncrementalParserStateSchema(
            available=snap.available,
            prefix_output=snap.prefix_output,
            prefix_parse_status=snap.prefix_parse_status,
            parser_accepts_end=snap.parser_accepts_end,
            expected_next_terminals=snap.expected_next_terminals,
            accepted_next_terminals=snap.accepted_next_terminals,
            likely_grammar_context=snap.likely_grammar_context,
            likely_grammar_path=snap.likely_grammar_path,
            selected_token_interpretation=snap.selected_token_interpretation,
            likely_parser_interpretation=snap.likely_parser_interpretation,
            partial_parse_view=snap.partial_parse_view,
            parse_tree_text=snap.parse_tree_text,
            parser_error_type=snap.parser_error_type,
            parser_error_message=snap.parser_error_message,
        )


def validate_verilog_output(code: str) -> FinalValidationResult:
    """
    Validate extracted Verilog output.

    Primary validity: Lark parse (parser=lalr) on comment-stripped text.
    Unsupported-construct scan is informational only — a successful Lark
    parse means final_parse_valid=True even when keyword-like identifiers
    (e.g. ``reg`` used as name_of_module) appear via grammar-legal paths
    such as module_instantiation.
    """
    stripped = strip_verilog_comments(code)
    unsupported = detect_unsupported_constructs(stripped, strip_comments=False)
    parse_ok, parse_err = parse_with_verilog_grammar(code)

    return FinalValidationResult(
        final_parse_valid=parse_ok,
        final_parse_error=parse_err,
        unsupported_constructs_detected=unsupported,
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
    lark_grammar_loaded: bool = False,
    syncode_mask_store_loaded: bool = False,
    syncode_init_error: str = "",
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
            lark_grammar_loaded=lark_grammar_loaded,
            syncode_mask_store_loaded=False,
            constraint_active_during_generation=False,
            raw_unconstrained_generation_used=False,
        )

    if not syncode_available:
        err = syncode_init_error or final_parse_error or "SynCode mask store unavailable"
        return ConstraintEvidence(
            constraint_requested=True,
            constraint_status="unavailable",
            constraint_applied=False,
            fallback_occurred=False,
            syncode_error=err,
            lark_grammar_loaded=lark_grammar_loaded,
            syncode_mask_store_loaded=False,
            constraint_active_during_generation=False,
            raw_unconstrained_generation_used=total_steps > 0,
            unconstrained_reason="syncode_mask_store_unavailable",
        )

    constraint_active = syncode_active_steps > 0
    raw_unconstrained = syncode_fallback_steps > 0 or (
        total_steps > 0 and syncode_active_steps == 0
    )
    unconstrained_reason = ""
    if fallback_occurred:
        unconstrained_reason = "fallback_enabled"
    elif raw_unconstrained and total_steps > 0:
        unconstrained_reason = "parser_error"

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
            lark_grammar_loaded=lark_grammar_loaded,
            syncode_mask_store_loaded=syncode_mask_store_loaded,
            constraint_active_during_generation=constraint_active,
            raw_unconstrained_generation_used=raw_unconstrained,
            unconstrained_reason=unconstrained_reason or "parser_error",
        )

    applied = step_status == "full" and not fallback_occurred
    return ConstraintEvidence(
        constraint_requested=True,
        constraint_status=step_status,
        constraint_applied=applied,
        fallback_occurred=fallback_occurred,
        syncode_error="",
        lark_grammar_loaded=lark_grammar_loaded,
        syncode_mask_store_loaded=syncode_mask_store_loaded,
        constraint_active_during_generation=constraint_active,
        raw_unconstrained_generation_used=raw_unconstrained,
        unconstrained_reason=unconstrained_reason,
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
