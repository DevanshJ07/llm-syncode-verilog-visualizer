"""
Phase 3A — structured Verilog parser analysis (complete / incomplete / invalid).

Uses the sole canonical grammar ``backend/grammar/verilog.lark`` and Lark's
interactive LALR parser.

Partial / recovered forests
---------------------------
Already-reduced ``Tree`` / ``Token`` objects are read from the interactive
parser value stack when available.  That stack is a **version-coupled
diagnostic interface** on the pinned ``syncode.larkm 1.1.8``
(``InteractiveParser.parser_state.value_stack``).  It is **not** a guaranteed
stable public API merely because the attribute lacks an underscore, and its
contents are **never** labelled a complete parse tree.

Access is centralized in ``read_interactive_value_stack``.  If the attribute
is missing, empty, or raises, analysis still returns classification,
expected terminals, and source-boundary diagnostics with an explicit warning
— it does not invent grammar-rule nodes.

Comment / position fidelity
---------------------------
The canonical grammar ``%ignore``s ``LINE_COMMENT``, ``BLOCK_COMMENT``, and
``WS``.  This analyzer feeds the **original** source to Lark (no
``strip_verilog_comments``) so error line/column/offsets match the displayed
text.  Legacy ``validate_verilog_output`` / ``build_parse_tree`` may still
strip comments for historical compatibility; those paths are unchanged here.

Does not invoke SynCode or build a mask store.

Pydantic schemas live in ``app.models.parser_analysis`` (no Lark imports).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Optional

from app.core.config import settings
from app.core.grammar import grammar_sha256, read_verilog_grammar
from app.models.parser_analysis import (
    ParserAnalysis,
    ParserAnalysisStatus,
    ParserNode,
    ParserNodeKind,
    ParserRepresentationKind,
    ParserSourcePosition,
    unavailable_parser_analysis,
)
from app.models.provenance import ProvenanceInfo, ProvenanceKind
from app.services.verilog_validation import _load_lark_module

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Safety limits (named / configurable via Settings)
# ---------------------------------------------------------------------------

def _max_source_chars() -> int:
    return int(getattr(settings, "parser_analysis_max_source_chars", 200_000))


def _max_nodes() -> int:
    return int(getattr(settings, "parser_analysis_max_nodes", 5_000))


def _max_depth() -> int:
    return int(getattr(settings, "parser_analysis_max_depth", 64))


_COMPLETE_LABEL = "Complete Lark parse tree"
_PARTIAL_LABEL = "Partial parser stack — incomplete prefix"
_RECOVERED_LABEL = "Recovered parser stack — valid prefix before error"
_STACK_UNAVAILABLE_MARKER = (
    "[value_stack unavailable — version-coupled syncode.larkm diagnostic interface]"
)


@lru_cache(maxsize=1)
def _analysis_lark_parser():
    """
    Dedicated LALR parser for structured analysis.

    ``propagate_positions=True`` so Tree.meta is populated when Lark supplies
    it.  Token line/column/start_pos remain available regardless.
    """
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
        propagate_positions=True,
    )


def _parser_implementation_label() -> str:
    """
    Report the parser implementation actually loaded.

    SynViz normally resolves Lark through syncode's bundled ``larkm``; the
    label must not claim a standalone ``lark`` package when that is not used.
    """
    mod = _load_lark_module()
    if mod is None:
        return ""
    ver = str(getattr(mod, "__version__", "") or "").strip()
    name = str(getattr(mod, "__name__", "") or "")
    file_path = str(getattr(mod, "__file__", "") or "").replace("\\", "/")
    is_larkm = (
        "larkm" in name
        or "/larkm/" in file_path
        or name.startswith("syncode")
    )
    if is_larkm:
        return f"syncode.larkm {ver}".strip() if ver else "syncode.larkm"
    if ver:
        return f"lark {ver}"
    return "lark"


@dataclass
class ValueStackRead:
    """Result of a guarded interactive value-stack read."""

    values: list[Any] = field(default_factory=list)
    available: bool = False
    warning: str = ""


def read_interactive_value_stack(interactive_parser: Any) -> ValueStackRead:
    """
    Read already-reduced Trees/Tokens from the interactive parser value stack.

    Available on the pinned ``syncode.larkm 1.1.8`` as
    ``interactive_parser.parser_state.value_stack``.  This is a version-coupled
    diagnostic interface — not a guaranteed stable public API.  Use only to
    visualize stack contents for partial/recovered forests; never interpret the
    result as a complete parse tree.

    On absence or incompatibility, returns ``available=False`` with a warning
    string and an empty value list (callers must not invent grammar nodes).
    """
    try:
        if interactive_parser is None:
            return ValueStackRead(
                values=[],
                available=False,
                warning=(
                    "value_stack unavailable: interactive parser is None "
                    "(version-coupled syncode.larkm diagnostic interface)"
                ),
            )
        state = getattr(interactive_parser, "parser_state", None)
        if state is None:
            return ValueStackRead(
                values=[],
                available=False,
                warning=(
                    "value_stack unavailable: parser_state missing on interactive "
                    "parser (version-coupled syncode.larkm diagnostic interface)"
                ),
            )
        if not hasattr(state, "value_stack"):
            return ValueStackRead(
                values=[],
                available=False,
                warning=(
                    "value_stack unavailable: attribute absent on parser_state "
                    "(version-coupled syncode.larkm diagnostic interface)"
                ),
            )
        stack = getattr(state, "value_stack")
        if stack is None:
            return ValueStackRead(
                values=[],
                available=False,
                warning=(
                    "value_stack unavailable: attribute is None "
                    "(version-coupled syncode.larkm diagnostic interface)"
                ),
            )
        return ValueStackRead(values=list(stack), available=True, warning="")
    except Exception as exc:  # noqa: BLE001
        return ValueStackRead(
            values=[],
            available=False,
            warning=(
                f"value_stack access failed ({type(exc).__name__}: {exc}); "
                "continuing without stack forest "
                "(version-coupled syncode.larkm diagnostic interface)"
            ),
        )


def _is_eof_token(tok: Any) -> bool:
    if tok is None:
        return True
    t = getattr(tok, "type", None)
    if t in ("$END", "EOF"):
        return True
    val = getattr(tok, "value", None)
    if val is None or val == "":
        # Empty $END tokens are common for UnexpectedToken-at-EOF.
        if t in ("$END", "EOF", None):
            return True
    s = str(tok)
    return "$END" in s or s.strip() == ""


def _token_start_pos(tok: Any) -> Optional[int]:
    if tok is None:
        return None
    pos = getattr(tok, "start_pos", None)
    if isinstance(pos, int) and pos >= 0:
        return pos
    return None


def _token_end_pos(tok: Any) -> Optional[int]:
    if tok is None:
        return None
    pos = getattr(tok, "end_pos", None)
    if isinstance(pos, int) and pos >= 0:
        return pos
    start = _token_start_pos(tok)
    val = getattr(tok, "value", None)
    if start is not None and isinstance(val, str):
        return start + len(val)
    return None


def _line_col_at(source: str, offset: int) -> tuple[int, int]:
    """1-based line/column for *offset* into *source*."""
    if offset <= 0:
        return 1, 1
    offset = min(offset, len(source))
    line = source.count("\n", 0, offset) + 1
    last_nl = source.rfind("\n", 0, offset)
    col = offset + 1 if last_nl < 0 else offset - last_nl
    return line, col


def _position_from_token(tok: Any) -> Optional[ParserSourcePosition]:
    if tok is None:
        return None
    line = getattr(tok, "line", None)
    column = getattr(tok, "column", None)
    start = getattr(tok, "start_pos", None)
    end = getattr(tok, "end_pos", None)
    end_line = getattr(tok, "end_line", None)
    end_column = getattr(tok, "end_column", None)
    # Only include fields Lark actually supplied (non-None).
    if all(v is None for v in (line, column, start, end, end_line, end_column)):
        return None
    return ParserSourcePosition(
        line=line if isinstance(line, int) and line > 0 else None,
        column=column if isinstance(column, int) and column > 0 else None,
        start_pos=start if isinstance(start, int) and start >= 0 else None,
        end_pos=end if isinstance(end, int) and end >= 0 else None,
        end_line=end_line if isinstance(end_line, int) and end_line > 0 else None,
        end_column=(
            end_column if isinstance(end_column, int) and end_column > 0 else None
        ),
    )


def _position_from_tree_meta(tree: Any) -> Optional[ParserSourcePosition]:
    meta = getattr(tree, "meta", None)
    if meta is None:
        return None
    # Lark sets meta.empty when no positions were recorded.
    if getattr(meta, "empty", False):
        return None
    line = getattr(meta, "line", None)
    column = getattr(meta, "column", None)
    start = getattr(meta, "start_pos", None)
    end = getattr(meta, "end_pos", None)
    end_line = getattr(meta, "end_line", None)
    end_column = getattr(meta, "end_column", None)
    if all(v is None for v in (line, column, start, end, end_line, end_column)):
        return None
    return ParserSourcePosition(
        line=line if isinstance(line, int) and line > 0 else None,
        column=column if isinstance(column, int) and column > 0 else None,
        start_pos=start if isinstance(start, int) and start >= 0 else None,
        end_pos=end if isinstance(end, int) and end >= 0 else None,
        end_line=end_line if isinstance(end_line, int) and end_line > 0 else None,
        end_column=(
            end_column if isinstance(end_column, int) and end_column > 0 else None
        ),
    )


class _NodeBuilder:
    """Serialize Lark Trees/Tokens into ParserNode graphs with safety limits."""

    def __init__(self) -> None:
        self.next_id = 0
        self.node_count = 0
        self.max_depth_seen = 0
        self.truncated = False
        self.warnings: list[str] = []
        self._Tree: Any = None
        self._Token: Any = None
        lark = _load_lark_module()
        if lark is not None:
            self._Tree = getattr(lark, "Tree", None)
            self._Token = getattr(lark, "Token", None)

    def _alloc_id(self) -> str:
        nid = f"n{self.next_id}"
        self.next_id += 1
        return nid

    def build_value(
        self, value: Any, *, depth: int, kind_hint: ParserNodeKind | None = None
    ) -> Optional[ParserNode]:
        self.max_depth_seen = max(self.max_depth_seen, depth)
        if depth > _max_depth():
            self.truncated = True
            if "max tree depth" not in " ".join(self.warnings):
                self.warnings.append(
                    f"representation truncated: max tree depth {_max_depth()} reached"
                )
            return ParserNode(
                id=self._alloc_id(),
                kind="recovery_marker",
                label="[truncated: max depth]",
            )
        if self.node_count >= _max_nodes():
            self.truncated = True
            if "max serialized nodes" not in " ".join(self.warnings):
                self.warnings.append(
                    f"representation truncated: max serialized nodes {_max_nodes()} reached"
                )
            return ParserNode(
                id=self._alloc_id(),
                kind="recovery_marker",
                label="[truncated: max nodes]",
            )

        self.node_count += 1
        node_id = self._alloc_id()

        if self._Token is not None and isinstance(value, self._Token):
            # Preserve token value exactly (including whitespace); do not trim.
            tok_val = value.value if hasattr(value, "value") else str(value)
            return ParserNode(
                id=node_id,
                kind="token",
                label=str(getattr(value, "type", "TOKEN")),
                token_value=tok_val if isinstance(tok_val, str) else str(tok_val),
                position=_position_from_token(value),
            )

        if self._Tree is not None and isinstance(value, self._Tree):
            children: list[ParserNode] = []
            for child in getattr(value, "children", []) or []:
                if self.node_count >= _max_nodes():
                    self.truncated = True
                    children.append(
                        ParserNode(
                            id=self._alloc_id(),
                            kind="recovery_marker",
                            label="[truncated: max nodes]",
                        )
                    )
                    self.node_count += 1
                    break
                built = self.build_value(child, depth=depth + 1)
                if built is not None:
                    children.append(built)
            return ParserNode(
                id=node_id,
                kind="rule",
                label=str(getattr(value, "data", "rule")),
                children=children,
                position=_position_from_tree_meta(value),
            )

        if isinstance(value, list):
            children = []
            for item in value:
                if self.node_count >= _max_nodes():
                    self.truncated = True
                    break
                built = self.build_value(item, depth=depth + 1)
                if built is not None:
                    children.append(built)
            return ParserNode(
                id=node_id,
                kind=kind_hint or "stack_value",
                label="stack_list",
                children=children,
            )

        # Unknown stack object — expose type only; never invent a grammar rule.
        return ParserNode(
            id=node_id,
            kind="stack_value",
            label=type(value).__name__,
            token_value=None,
        )

def _reset_builder_root(builder: _NodeBuilder, label: str, stack: list[Any]) -> ParserNode:
    """Build synthetic root with deterministic ids starting at n0."""
    builder.next_id = 0
    builder.node_count = 0
    root_id = builder._alloc_id()
    builder.node_count = 1
    children: list[ParserNode] = []
    for val in stack:
        if builder.node_count >= _max_nodes():
            builder.truncated = True
            if "max serialized nodes" not in " ".join(builder.warnings):
                builder.warnings.append(
                    f"representation truncated: max serialized nodes {_max_nodes()} reached"
                )
            children.append(
                ParserNode(
                    id=builder._alloc_id(),
                    kind="recovery_marker",
                    label="[truncated: max nodes]",
                )
            )
            builder.node_count += 1
            break
        built = builder.build_value(val, depth=1)
        if built is not None:
            children.append(built)
    return ParserNode(
        id=root_id,
        kind="synthetic_root",
        label=label,
        children=children,
    )


def _forest_root_from_stack(
    builder: _NodeBuilder,
    *,
    label: str,
    stack_read: ValueStackRead,
) -> tuple[ParserNode, list[str]]:
    """
    Build a synthetic forest root from a guarded stack read.

    When the stack is unavailable, return an honest synthetic root with a
    recovery marker — never invent grammar-rule ancestors.
    """
    extra: list[str] = []
    if stack_read.warning:
        extra.append(stack_read.warning)
    if not stack_read.available:
        builder.next_id = 0
        builder.node_count = 0
        root_id = builder._alloc_id()
        marker_id = builder._alloc_id()
        builder.node_count = 2
        builder.max_depth_seen = max(builder.max_depth_seen, 1)
        return (
            ParserNode(
                id=root_id,
                kind="synthetic_root",
                label=label,
                children=[
                    ParserNode(
                        id=marker_id,
                        kind="recovery_marker",
                        label=_STACK_UNAVAILABLE_MARKER,
                    )
                ],
            ),
            extra,
        )
    root = _reset_builder_root(builder, label, stack_read.values)
    return root, extra + list(builder.warnings)


def _pretty_from_node(node: ParserNode, indent: int = 0) -> str:
    pad = "  " * indent
    if node.kind == "token":
        val = node.token_value if node.token_value is not None else ""
        return f"{pad}{node.label}: {val!r}"
    lines = [f"{pad}{node.label}"]
    for child in node.children:
        lines.append(_pretty_from_node(child, indent + 1))
    return "\n".join(lines)


def _expected_from_interactive(ip: Any) -> list[str]:
    terms: set[str] = set()
    try:
        for t in ip.accepts():
            s = str(t)
            if s and not s.startswith("__"):
                terms.add(s)
    except Exception:  # noqa: BLE001
        pass
    try:
        for k in ip.choices().keys():
            s = str(k)
            if s and not s.startswith("__"):
                terms.add(s)
    except Exception:  # noqa: BLE001
        pass
    # Never present raw numeric LALR state ids as terminals.
    cleaned = sorted(t for t in terms if not t.isdigit())
    return cleaned


def _safe_error_message(exc: BaseException, *, limit: int = 500) -> str:
    msg = f"{type(exc).__name__}: {exc}"
    if len(msg) > limit:
        return msg[: limit - 3] + "..."
    return msg


def _status_flags(
    status: ParserAnalysisStatus,
) -> tuple[ParserRepresentationKind, str, bool, bool, bool]:
    if status == "complete_valid":
        return "complete_parse_tree", _COMPLETE_LABEL, True, False, False
    if status == "incomplete_prefix":
        return "partial_parse_forest", _PARTIAL_LABEL, False, True, False
    if status == "invalid_input":
        return "recovered_prefix_forest", _RECOVERED_LABEL, False, False, True
    return "none", "Parser analysis unavailable", False, False, False


def analyze_verilog_source(
    source: str,
    *,
    provenance_kind: ProvenanceKind = ProvenanceKind.derived,
    method: str = "analyze_verilog_source",
    grammar_hash: str | None = None,
) -> ParserAnalysis:
    """
    Produce a structured parser analysis for *source*.

    Classification
    --------------
    1. Full ``parse()`` succeeds → ``complete_valid`` / ``complete_parse_tree``.
    2. All tokens consumable but ``$END`` not accepted → ``incomplete_prefix`` /
       ``partial_parse_forest`` (empty/whitespace included).
    3. Non-EOF token/character rejected → ``invalid_input`` /
       ``recovered_prefix_forest`` with longest consumable prefix + invalid suffix.

    Unexpected internal failures return ``unavailable`` with an explicit warning
    (never an empty successful structure).
    """
    try:
        return _analyze_verilog_source_impl(
            source,
            provenance_kind=provenance_kind,
            method=method,
            grammar_hash=grammar_hash,
        )
    except Exception as exc:  # noqa: BLE001
        _log.exception("parser analysis failed unexpectedly")
        return unavailable_parser_analysis(
            method=method,
            warnings=[
                f"unexpected parser analysis failure "
                f"({type(exc).__name__}: {exc}); analysis marked unavailable"
            ],
        )


def _analyze_verilog_source_impl(
    source: str,
    *,
    provenance_kind: ProvenanceKind = ProvenanceKind.derived,
    method: str = "analyze_verilog_source",
    grammar_hash: str | None = None,
) -> ParserAnalysis:
    warnings: list[str] = []
    ghash = grammar_hash if grammar_hash is not None else grammar_sha256()
    pver = _parser_implementation_label()

    if source is None:
        source = ""
    # Do not trim — preserve caller source exactly for prefix/suffix identity.
    src = source
    source_len = len(src)

    if source_len > _max_source_chars():
        warnings.append(
            f"source truncated for analysis: length {source_len} exceeds "
            f"limit {_max_source_chars()}"
        )
        src = src[: _max_source_chars()]
        source_len = len(src)

    try:
        parser = _analysis_lark_parser()
    except ImportError as exc:
        return unavailable_parser_analysis(
            method=f"lark unavailable: {exc}",
            warnings=warnings,
        )

    lark_mod = _load_lark_module()
    lark_exceptions = getattr(lark_mod, "exceptions", None) if lark_mod else None
    UnexpectedToken = (
        getattr(lark_exceptions, "UnexpectedToken", None) if lark_exceptions else None
    )
    UnexpectedCharacters = (
        getattr(lark_exceptions, "UnexpectedCharacters", None)
        if lark_exceptions
        else None
    )
    UnexpectedEOF = (
        getattr(lark_exceptions, "UnexpectedEOF", None) if lark_exceptions else None
    )
    Tree = getattr(lark_mod, "Tree", None) if lark_mod else None

    def _finish(
        *,
        status: ParserAnalysisStatus,
        root: Optional[ParserNode],
        pretty: str,
        expected: list[str],
        accepts_end: bool,
        parsed_prefix: str,
        invalid_suffix: str,
        consumed: int,
        error_offset: Optional[int] = None,
        error_line: Optional[int] = None,
        error_column: Optional[int] = None,
        error_type: str = "",
        error_message: str = "",
        unexpected: str = "",
        previous: str = "",
        extra_warnings: list[str] | None = None,
        node_count: int = 0,
        max_depth: int = 0,
        truncated: bool = False,
    ) -> ParserAnalysis:
        kind, label, is_c, is_p, is_r = _status_flags(status)
        all_warns = list(warnings) + list(extra_warnings or [])
        # Integrity check: never label partial/recovered as complete.
        if status != "complete_valid":
            is_c = False
            if kind == "complete_parse_tree":
                kind = "none"
                label = "Parser analysis inconsistent — refused complete label"
                all_warns.append(
                    "internal guard: refused to label non-complete analysis "
                    "as complete_parse_tree"
                )
        if status != "unavailable" and (parsed_prefix + invalid_suffix) != src:
            all_warns.append(
                "parsed_prefix + invalid_suffix does not equal analyzed "
                "source (check offsets)"
            )
        if truncated and not any("truncat" in w.lower() for w in all_warns):
            all_warns.append(
                "representation truncated under configured node/depth limits"
            )
        return ParserAnalysis(
            status=status,
            representation_kind=kind,
            label=label,
            is_complete=is_c,
            is_partial=is_p,
            is_recovered=is_r,
            grammar_name="verilog",
            grammar_sha256=ghash,
            parser_name="lalr",
            parser_version=pver,
            root=root,
            pretty_text=pretty,
            expected_next_terminals=expected,
            accepts_end=accepts_end,
            parsed_prefix=parsed_prefix,
            invalid_suffix=invalid_suffix,
            consumed_char_offset=consumed,
            error_offset=error_offset,
            error_line=error_line,
            error_column=error_column,
            error_type=error_type,
            error_message=error_message,
            unexpected_token_or_char=unexpected,
            previous_token=previous,
            warnings=all_warns,
            provenance=ProvenanceInfo(
                kind=provenance_kind,
                method=method,
                grammar_sha256=ghash,
                warnings=all_warns,
            ),
            node_count=node_count,
            max_depth_seen=max_depth,
            truncated=truncated,
            source_length=source_len,
        )

    # ── 1. Genuine complete parse ─────────────────────────────────────────
    try:
        tree = parser.parse(src)
        builder = _NodeBuilder()
        builder.next_id = 0
        root_node = builder.build_value(tree, depth=0)
        pretty = ""
        try:
            pretty = tree.pretty()
        except Exception:  # noqa: BLE001
            pretty = _pretty_from_node(root_node) if root_node else ""
        return _finish(
            status="complete_valid",
            root=root_node,
            pretty=pretty,
            expected=["$END"],
            accepts_end=True,
            parsed_prefix=src,
            invalid_suffix="",
            consumed=len(src),
            extra_warnings=builder.warnings,
            node_count=builder.node_count,
            max_depth=builder.max_depth_seen,
            truncated=builder.truncated,
        )
    except Exception as full_exc:
        full_exc_type = type(full_exc).__name__
        _log.debug("full parse did not succeed (%s); interactive analysis", full_exc_type)

    # ── 2/3. Interactive LALR consumption ─────────────────────────────────
    builder = _NodeBuilder()
    previous_token = ""
    last_good_end = 0
    fed_tokens: list[Any] = []

    try:
        ip = parser.parse_interactive(src)
    except Exception as exc:  # noqa: BLE001
        return _finish(
            status="unavailable",
            root=None,
            pretty="",
            expected=[],
            accepts_end=False,
            parsed_prefix="",
            invalid_suffix=src,
            consumed=0,
            error_type=type(exc).__name__,
            error_message=_safe_error_message(exc),
            extra_warnings=["interactive parser could not be created"],
        )

    try:
        for tok in ip.iter_parse():
            fed_tokens.append(tok)
            end = _token_end_pos(tok)
            if end is not None:
                last_good_end = max(last_good_end, end)
            previous_token = str(tok)
        expected = _expected_from_interactive(ip)
        accepts_end = "$END" in expected
        stack_read = read_interactive_value_stack(ip)

        if accepts_end:
            try:
                tree = ip.feed_eof(fed_tokens[-1] if fed_tokens else None)
                if Tree is not None and isinstance(tree, Tree):
                    builder = _NodeBuilder()
                    root_node = builder.build_value(tree, depth=0)
                    pretty = ""
                    try:
                        pretty = tree.pretty()
                    except Exception:  # noqa: BLE001
                        pretty = _pretty_from_node(root_node) if root_node else ""
                    return _finish(
                        status="complete_valid",
                        root=root_node,
                        pretty=pretty,
                        expected=["$END"],
                        accepts_end=True,
                        parsed_prefix=src,
                        invalid_suffix="",
                        consumed=len(src),
                        extra_warnings=builder.warnings,
                        node_count=builder.node_count,
                        max_depth=builder.max_depth_seen,
                        truncated=builder.truncated,
                    )
            except Exception:
                accepts_end = False

        root, forest_warns = _forest_root_from_stack(
            builder, label=_PARTIAL_LABEL, stack_read=stack_read
        )
        pretty = _pretty_from_node(root)
        return _finish(
            status="incomplete_prefix",
            root=root,
            pretty=pretty,
            expected=[t for t in expected if t != "$END"],
            accepts_end=accepts_end,
            parsed_prefix=src,
            invalid_suffix="",
            consumed=len(src),
            error_type="",
            error_message="",
            unexpected="",
            previous=previous_token,
            extra_warnings=forest_warns,
            node_count=builder.node_count,
            max_depth=builder.max_depth_seen,
            truncated=builder.truncated,
        )

    except Exception as exc:
        unexpected = ""
        error_offset: Optional[int] = None
        error_line: Optional[int] = None
        error_column: Optional[int] = None
        expected: list[str] = []
        accepts_end = False

        is_incomplete = False
        is_invalid = False

        if UnexpectedEOF and isinstance(exc, UnexpectedEOF):
            is_incomplete = True
            raw_expected = getattr(exc, "expected", None) or []
            expected = sorted(str(t) for t in raw_expected if not str(t).isdigit())
            accepts_end = "$END" in expected
            error_offset = len(src)
            error_line, error_column = _line_col_at(src, error_offset)
        elif UnexpectedToken and isinstance(exc, UnexpectedToken):
            tok = getattr(exc, "token", None)
            raw_expected = getattr(exc, "expected", None) or []
            expected = sorted(str(t) for t in raw_expected if not str(t).isdigit())
            try:
                expected = _expected_from_interactive(ip) or expected
                accepts_end = "$END" in expected
            except Exception:  # noqa: BLE001
                accepts_end = "$END" in expected
            if _is_eof_token(tok):
                is_incomplete = True
                unexpected = "$END"
                error_offset = len(src)
                error_line, error_column = _line_col_at(src, error_offset)
            else:
                is_invalid = True
                unexpected = str(getattr(tok, "value", tok) or tok)
                error_offset = _token_start_pos(tok)
                if error_offset is None:
                    error_offset = last_good_end
                error_line = getattr(tok, "line", None) or None
                error_column = getattr(tok, "column", None) or None
                if error_line is None and error_offset is not None:
                    error_line, error_column = _line_col_at(src, error_offset)
                hist = getattr(exc, "token_history", None)
                if hist:
                    previous_token = str(list(hist)[-1])
        elif UnexpectedCharacters and isinstance(exc, UnexpectedCharacters):
            is_invalid = True
            unexpected = str(getattr(exc, "char", "") or "")
            error_offset = getattr(exc, "pos_in_stream", None)
            if not isinstance(error_offset, int):
                error_offset = last_good_end
            error_line = getattr(exc, "line", None) or None
            error_column = getattr(exc, "column", None) or None
            if error_line is None and error_offset is not None:
                error_line, error_column = _line_col_at(src, error_offset)
            raw_allowed = getattr(exc, "allowed", None) or []
            expected = sorted(str(t) for t in raw_allowed if not str(t).isdigit())
            try:
                expected = _expected_from_interactive(ip) or expected
                accepts_end = "$END" in expected
            except Exception:  # noqa: BLE001
                pass
        else:
            is_invalid = True
            error_offset = last_good_end
            error_line, error_column = _line_col_at(src, error_offset)
            try:
                expected = _expected_from_interactive(ip)
                accepts_end = "$END" in expected
            except Exception:  # noqa: BLE001
                pass

        stack_read = read_interactive_value_stack(ip)

        if is_incomplete:
            root, forest_warns = _forest_root_from_stack(
                builder, label=_PARTIAL_LABEL, stack_read=stack_read
            )
            pretty = _pretty_from_node(root)
            return _finish(
                status="incomplete_prefix",
                root=root,
                pretty=pretty,
                expected=[t for t in expected if t != "$END"],
                accepts_end=accepts_end,
                parsed_prefix=src,
                invalid_suffix="",
                consumed=len(src),
                error_offset=error_offset,
                error_line=error_line,
                error_column=error_column,
                error_type=type(exc).__name__,
                error_message=_safe_error_message(exc),
                unexpected=unexpected or "$END",
                previous=previous_token,
                extra_warnings=forest_warns,
                node_count=builder.node_count,
                max_depth=builder.max_depth_seen,
                truncated=builder.truncated,
            )

        if error_offset is None:
            error_offset = last_good_end
        error_offset = max(0, min(int(error_offset), len(src)))
        parsed_prefix = src[:error_offset]
        invalid_suffix = src[error_offset:]
        root, forest_warns = _forest_root_from_stack(
            builder, label=_RECOVERED_LABEL, stack_read=stack_read
        )
        pretty = _pretty_from_node(root)
        return _finish(
            status="invalid_input",
            root=root,
            pretty=pretty,
            expected=[t for t in expected if t != "$END"] if not accepts_end else expected,
            accepts_end=accepts_end,
            parsed_prefix=parsed_prefix,
            invalid_suffix=invalid_suffix,
            consumed=error_offset,
            error_offset=error_offset,
            error_line=error_line,
            error_column=error_column,
            error_type=type(exc).__name__,
            error_message=_safe_error_message(exc),
            unexpected=unexpected,
            previous=previous_token,
            extra_warnings=forest_warns,
            node_count=builder.node_count,
            max_depth=builder.max_depth_seen,
            truncated=builder.truncated,
        )


def binary_verdict_from_analysis(analysis: ParserAnalysis) -> str:
    """Map structured status to the legacy valid/invalid vocabulary."""
    if analysis.status == "complete_valid":
        return "valid"
    if analysis.status == "unavailable":
        return "unavailable"
    return "invalid"


def status_disagrees_with_recorded_verdict(
    recorded: str | None, analysis: ParserAnalysis
) -> bool:
    """
    True when recorded validity conflicts with ``status == complete_valid``.

    Recorded ``valid``/``pass`` disagrees unless analysis is ``complete_valid``.
    Recorded ``invalid``/``fail`` disagrees when analysis is ``complete_valid``.
    """
    if recorded is None or analysis.status == "unavailable":
        return False
    rec = str(recorded).strip().lower()
    is_complete = analysis.status == "complete_valid"
    if rec in ("valid", "true", "pass", "passed", "ok"):
        return not is_complete
    if rec in ("invalid", "false", "fail", "failed"):
        return is_complete
    return False
