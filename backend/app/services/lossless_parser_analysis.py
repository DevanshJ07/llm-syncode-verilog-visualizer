"""
Checkpoint 2 — analysis-only lossless Verilog CST / source-segment analysis.

Uses a **separate** Lark parser with ``keep_all_tokens=True``. Never passed to
SynCode, production masking, or the DFA mask store. Does not mutate the
canonical grammar file.

Offsets are Python ``str`` / Unicode code-point indices. UTF-8 byte counts are
reported separately.

Losslessness invariant: ordered ``source_segments`` concatenate to
``source_text`` exactly, cover ``[0, len(source_text))`` without gaps/overlaps.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from threading import Lock
from typing import Any, Optional, Sequence

from app.core.grammar import EXPECTED_GRAMMAR_SHA256, grammar_sha256, read_verilog_grammar
from app.models.lossless_parser_analysis import (
    LOSSLESS_ANALYSIS_SCHEMA_VERSION,
    AnalysisCompleteness,
    AnalysisTiming,
    LlmTokenSpan,
    LosslessCstNode,
    LosslessParserAnalysisResponse,
    LosslessSourceSegment,
    SourceProvenance,
)
from app.models.parser_analysis import ParserAnalysis
from app.services.parser_analysis import (
    _parser_implementation_label,
    analyze_verilog_source,
)
from app.services.verilog_validation import _load_lark_module

_log = logging.getLogger(__name__)

# Friendly display labels for common punctuation lexemes.
# Original Lark terminal type is preserved separately and never falsified.
_PUNCT_DISPLAY: dict[str, str] = {
    ",": "COMMA",
    ";": "SEMICOLON",
    "(": "LPAR",
    ")": "RPAR",
    "[": "LSQB",
    "]": "RSQB",
    "{": "LBRACE",
    "}": "RBRACE",
    ":": "COLON",
    ".": "DOT",
    "#": "HASH",
    "@": "AT",
    "=": "EQUAL",
    "'": "TICK",
}

# Analysis-only trivia classifiers consistent with canonical %ignore definitions.
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*(?:.|\n)*?\*/", re.DOTALL)
# common.WS: spaces, tabs, newlines, carriage returns, form feeds, vertical tabs.
_WS_RE = re.compile(r"[ \t\r\n\f\v]+")

_CACHE_MAX = 48
_cache_lock = Lock()
_analysis_cache: "OrderedDict[str, LosslessParserAnalysisResponse]" = OrderedDict()
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="lossless-parse")


def cache_stats() -> dict[str, int]:
    with _cache_lock:
        return {"size": len(_analysis_cache), "max": _CACHE_MAX}


def clear_analysis_cache() -> None:
    with _cache_lock:
        _analysis_cache.clear()


def _cache_get(key: str) -> Optional[LosslessParserAnalysisResponse]:
    with _cache_lock:
        item = _analysis_cache.get(key)
        if item is None:
            return None
        _analysis_cache.move_to_end(key)
        return item.model_copy(deep=True)


def _cache_put(key: str, value: LosslessParserAnalysisResponse) -> None:
    with _cache_lock:
        if key in _analysis_cache:
            _analysis_cache.move_to_end(key)
        _analysis_cache[key] = value.model_copy(deep=True)
        while len(_analysis_cache) > _CACHE_MAX:
            _analysis_cache.popitem(last=False)


def make_cache_key(
    *,
    experiment_id: str,
    prompt_id: str | None,
    step_index: int | None,
    timing: str,
    source_sha: str,
    grammar_sha: str,
    schema_version: str = LOSSLESS_ANALYSIS_SCHEMA_VERSION,
) -> str:
    return "|".join(
        [
            experiment_id,
            prompt_id or "",
            "" if step_index is None else str(step_index),
            timing,
            source_sha,
            grammar_sha,
            schema_version,
        ]
    )


@lru_cache(maxsize=1)
def _lossless_lark_parser():
    """Analysis-only LALR parser. Never share with SynCode / masking."""
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
        keep_all_tokens=True,
    )


def source_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _line_col_at(source: str, offset: int) -> tuple[int, int]:
    """1-based line/column for a Python-str (code-point) offset."""
    if offset <= 0:
        return 1, 1
    offset = min(offset, len(source))
    line = source.count("\n", 0, offset) + 1
    last_nl = source.rfind("\n", 0, offset)
    col = offset + 1 if last_nl < 0 else offset - last_nl
    return line, col


def _end_line_col(source: str, start: int, end: int) -> tuple[int, int, int, int]:
    sl, sc = _line_col_at(source, start)
    el2, ec2 = _line_col_at(source, end)
    return sl, sc, el2, ec2


def display_terminal_name(lark_type: str, lexeme: str) -> str:
    """Readable label; does not replace ``lark_terminal_type`` in the payload."""
    if lark_type and not str(lark_type).startswith("__ANON"):
        return str(lark_type)
    mapped = _PUNCT_DISPLAY.get(lexeme)
    if mapped:
        return mapped
    if lark_type:
        return str(lark_type)
    return repr(lexeme) if lexeme else "TERMINAL"


@dataclass
class _LeafSpan:
    start: int
    end: int
    lark_type: str
    lexeme: str
    node_id: str


class _CstBuilder:
    def __init__(self, source: str) -> None:
        self.source = source
        self.next_id = 0
        self.leaves: list[_LeafSpan] = []
        lark = _load_lark_module()
        self._Tree = getattr(lark, "Tree", None) if lark else None
        self._Token = getattr(lark, "Token", None) if lark else None

    def _alloc(self) -> str:
        nid = f"c{self.next_id}"
        self.next_id += 1
        return nid

    def build(self, value: Any, *, partial: bool = False) -> Optional[LosslessCstNode]:
        if self._Token is not None and isinstance(value, self._Token):
            return self._token_node(value, partial=partial)
        if self._Tree is not None and isinstance(value, self._Tree):
            children: list[LosslessCstNode] = []
            for child in getattr(value, "children", []) or []:
                built = self.build(child, partial=partial)
                if built is not None:
                    children.append(built)
            meta = getattr(value, "meta", None)
            start = getattr(meta, "start_pos", None) if meta is not None else None
            end = getattr(meta, "end_pos", None) if meta is not None else None
            sl = getattr(meta, "line", None) if meta is not None else None
            sc = getattr(meta, "column", None) if meta is not None else None
            el = getattr(meta, "end_line", None) if meta is not None else None
            ec = getattr(meta, "end_column", None) if meta is not None else None
            if meta is not None and getattr(meta, "empty", False):
                start = end = sl = sc = el = ec = None
            return LosslessCstNode(
                id=self._alloc(),
                kind="rule",
                name=str(getattr(value, "data", "rule")),
                start_offset=start if isinstance(start, int) else None,
                end_offset=end if isinstance(end, int) else None,
                start_line=sl if isinstance(sl, int) else None,
                start_column=sc if isinstance(sc, int) else None,
                end_line=el if isinstance(el, int) else None,
                end_column=ec if isinstance(ec, int) else None,
                children=children,
                is_partial=partial,
            )
        return None

    def _token_node(self, tok: Any, *, partial: bool) -> LosslessCstNode:
        lexeme = tok.value if hasattr(tok, "value") else str(tok)
        if not isinstance(lexeme, str):
            lexeme = str(lexeme)
        lark_type = str(getattr(tok, "type", "TOKEN") or "TOKEN")
        start = getattr(tok, "start_pos", None)
        end = getattr(tok, "end_pos", None)
        if not isinstance(start, int) or start < 0:
            start = None
        if not isinstance(end, int) or end < 0:
            end = start + len(lexeme) if start is not None else None
        if (
            start is not None
            and end is not None
            and 0 <= start <= end <= len(self.source)
        ):
            slice_text = self.source[start:end]
            if slice_text != lexeme:
                lexeme = slice_text
        nid = self._alloc()
        if start is not None and end is not None:
            self.leaves.append(
                _LeafSpan(
                    start=start,
                    end=end,
                    lark_type=lark_type,
                    lexeme=lexeme,
                    node_id=nid,
                )
            )
        sl = getattr(tok, "line", None)
        sc = getattr(tok, "column", None)
        el = getattr(tok, "end_line", None)
        ec = getattr(tok, "end_column", None)
        if start is not None and end is not None and (
            not isinstance(sl, int) or not isinstance(sc, int)
        ):
            sl, sc, el, ec = _end_line_col(self.source, start, end)
        return LosslessCstNode(
            id=nid,
            kind="terminal",
            name=display_terminal_name(lark_type, lexeme),
            lark_terminal_type=lark_type,
            lexeme=lexeme,
            start_offset=start,
            end_offset=end,
            start_line=sl if isinstance(sl, int) else None,
            start_column=sc if isinstance(sc, int) else None,
            end_line=el if isinstance(el, int) else None,
            end_column=ec if isinstance(ec, int) else None,
            is_partial=partial,
        )


def _classify_gap(text: str) -> list[tuple[str, str]]:
    """Split a gap into (kind, exact_text). Never drop characters."""
    if text == "":
        return []
    out: list[tuple[str, str]] = []
    i = 0
    n = len(text)
    while i < n:
        m = _LINE_COMMENT_RE.match(text, i)
        if m and m.start() == i:
            out.append(("line_comment", m.group(0)))
            i = m.end()
            continue
        m = _BLOCK_COMMENT_RE.match(text, i)
        if m and m.start() == i:
            out.append(("block_comment", m.group(0)))
            i = m.end()
            continue
        m = _WS_RE.match(text, i)
        if m and m.start() == i:
            out.append(("whitespace", m.group(0)))
            i = m.end()
            continue
        j = i + 1
        while j < n:
            if (
                _LINE_COMMENT_RE.match(text, j)
                or _BLOCK_COMMENT_RE.match(text, j)
                or _WS_RE.match(text, j)
            ):
                break
            j += 1
        out.append(("unparsed", text[i:j]))
        i = j
    return out


def _segments_from_leaves(
    source: str,
    leaves: Sequence[_LeafSpan],
    *,
    unparsed_from: int | None = None,
) -> list[LosslessSourceSegment]:
    segs: list[LosslessSourceSegment] = []
    seg_i = 0
    cursor = 0
    ordered = sorted(leaves, key=lambda x: (x.start, x.end))

    def add_gap(start: int, end: int, force_unparsed: bool = False) -> None:
        nonlocal seg_i
        if end <= start:
            return
        text = source[start:end]
        pieces = [("unparsed", text)] if force_unparsed else _classify_gap(text)
        pos = start
        for kind, exact in pieces:
            sl, sc, el, ec = _end_line_col(source, pos, pos + len(exact))
            segs.append(
                LosslessSourceSegment(
                    id=f"s{seg_i}",
                    kind=kind,  # type: ignore[arg-type]
                    exact_text=exact,
                    start_offset=pos,
                    end_offset=pos + len(exact),
                    start_line=sl,
                    start_column=sc,
                    end_line=el,
                    end_column=ec,
                )
            )
            seg_i += 1
            pos += len(exact)

    cut = unparsed_from if unparsed_from is not None else len(source)

    for leaf in ordered:
        if leaf.start < cursor:
            continue
        # Terminals at/after the invalid cut belong to the unparsed suffix.
        if leaf.start >= cut:
            break
        if leaf.start > cursor:
            add_gap(cursor, leaf.start, force_unparsed=False)
        end = min(leaf.end, cut)
        exact = source[leaf.start:end]
        sl, sc, el, ec = _end_line_col(source, leaf.start, end)
        segs.append(
            LosslessSourceSegment(
                id=f"s{seg_i}",
                kind="terminal",
                terminal_name=display_terminal_name(leaf.lark_type, exact),
                lark_terminal_type=leaf.lark_type,
                exact_text=exact,
                start_offset=leaf.start,
                end_offset=end,
                start_line=sl,
                start_column=sc,
                end_line=el,
                end_column=ec,
                cst_node_id=leaf.node_id,
            )
        )
        seg_i += 1
        cursor = end

    if cursor < cut:
        add_gap(cursor, cut, force_unparsed=False)
    if cut < len(source):
        add_gap(cut, len(source), force_unparsed=True)

    return segs


def verify_lossless_segments(
    source: str, segments: Sequence[LosslessSourceSegment]
) -> list[str]:
    warnings: list[str] = []
    if not segments:
        if source != "":
            warnings.append("lossless segments empty for non-empty source")
        return warnings
    concat = "".join(s.exact_text for s in segments)
    if concat != source:
        warnings.append(
            "losslessness violation: concatenated source_segments != source_text"
        )
    cursor = 0
    for s in segments:
        if s.start_offset != cursor:
            warnings.append(
                f"segment coverage gap/overlap at offset {cursor} "
                f"(next starts at {s.start_offset})"
            )
            break
        if s.end_offset != s.start_offset + len(s.exact_text):
            warnings.append(
                f"segment {s.id} end_offset inconsistent with exact_text length"
            )
        if s.kind == "terminal":
            slice_text = source[s.start_offset : s.end_offset]
            if s.exact_text != slice_text:
                warnings.append(f"terminal segment {s.id} lexeme != source slice")
        cursor = s.end_offset
    if cursor != len(source):
        warnings.append(
            f"segment coverage ends at {cursor}, source length {len(source)}"
        )
    return warnings


def _lex_leaf_spans(parser: Any, source: str, builder: _CstBuilder) -> list[_LeafSpan]:
    try:
        builder.leaves = []
        for tok in parser.lex(source):
            builder._token_node(tok, partial=True)
        return list(builder.leaves)
    except Exception as exc:  # noqa: BLE001
        _log.debug("lossless lex failed: %s", exc)
        return list(builder.leaves)


def analyze_lossless_source(
    source: str,
    *,
    timing: AnalysisTiming,
    source_provenance: SourceProvenance,
    llm_token_spans: list[LlmTokenSpan] | None = None,
    include_structural: bool = True,
    experiment_id: str = "",
    prompt_id: str | None = None,
    step_index: int | None = None,
) -> LosslessParserAnalysisResponse:
    """Produce a lossless analysis for *source* (exact characters preserved)."""
    if source is None:
        source = ""
    src = source
    ghash = grammar_sha256()
    pver = _parser_implementation_label()
    warnings: list[str] = []
    if ghash != EXPECTED_GRAMMAR_SHA256:
        warnings.append(
            f"grammar SHA-256 {ghash} differs from expected {EXPECTED_GRAMMAR_SHA256}"
        )

    utf8_len = len(src.encode("utf-8"))
    common = {
        "timing": timing,
        "source_text": src,
        "source_sha256": source_sha256(src),
        "source_provenance": source_provenance,
        "source_character_count": len(src),
        "source_utf8_byte_count": utf8_len,
        "grammar_name": "verilog",
        "grammar_sha256": ghash,
        "parser_engine": "lalr",
        "parser_version": pver,
        "parser_mode": "analysis_only_keep_all_tokens",
        "keep_all_tokens": True,
        "llm_token_spans": list(llm_token_spans or []),
        "experiment_id": experiment_id,
        "prompt_id": prompt_id,
        "step_index": step_index,
    }

    structural: Optional[ParserAnalysis] = None
    if include_structural:
        structural = analyze_verilog_source(
            src, method="lossless_companion_structural"
        )

    if src == "":
        return LosslessParserAnalysisResponse(
            completeness="empty",
            cst_root=None,
            source_segments=[],
            structural_parser_analysis=structural,
            consumed_prefix="",
            invalid_suffix="",
            consumed_char_offset=0,
            warnings=warnings
            + ["empty source — no CST / segments (safe empty analysis)"],
            **common,  # type: ignore[arg-type]
        )

    try:
        parser = _lossless_lark_parser()
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"lossless parser unavailable: {type(exc).__name__}: {exc}")
        sl, sc, el, ec = _end_line_col(src, 0, len(src))
        segs = [
            LosslessSourceSegment(
                id="s0",
                kind="unparsed",
                exact_text=src,
                start_offset=0,
                end_offset=len(src),
                start_line=sl,
                start_column=sc,
                end_line=el,
                end_column=ec,
            )
        ]
        return LosslessParserAnalysisResponse(
            completeness="invalid_prefix",
            cst_root=None,
            source_segments=segs,
            structural_parser_analysis=structural,
            consumed_prefix="",
            invalid_suffix=src,
            consumed_char_offset=0,
            warnings=warnings,
            **common,  # type: ignore[arg-type]
        )

    builder = _CstBuilder(src)
    completeness: AnalysisCompleteness = "incomplete_prefix"
    cst_root: Optional[LosslessCstNode] = None
    consumed_offset = 0
    invalid_suffix = ""
    consumed_prefix = ""
    segments: list[LosslessSourceSegment] = []

    try:
        tree = parser.parse(src)
        cst_root = builder.build(tree, partial=False)
        completeness = "complete"
        consumed_offset = len(src)
        consumed_prefix = src
        invalid_suffix = ""
        segments = _segments_from_leaves(src, builder.leaves)
    except Exception as parse_exc:  # noqa: BLE001
        builder2 = _CstBuilder(src)
        leaves = _lex_leaf_spans(parser, src, builder2)

        if structural is not None and structural.status == "invalid_input":
            completeness = "invalid_prefix"
            if structural.invalid_suffix:
                invalid_suffix = structural.invalid_suffix
                consumed_prefix = src[: len(src) - len(invalid_suffix)]
                # Prefer exact suffix match from the end when recorded.
                if not src.endswith(invalid_suffix):
                    consumed_prefix = structural.parsed_prefix or src[
                        : structural.consumed_char_offset
                    ]
                    invalid_suffix = src[len(consumed_prefix) :]
                consumed_offset = len(consumed_prefix)
            else:
                consumed_offset = structural.consumed_char_offset
                consumed_prefix = src[:consumed_offset]
                invalid_suffix = src[consumed_offset:]
            segments = _segments_from_leaves(
                src, leaves, unparsed_from=consumed_offset
            )
        else:
            # Incomplete (or UnexpectedEOF) — entire source is an incomplete prefix.
            completeness = "incomplete_prefix"
            consumed_offset = len(src)
            consumed_prefix = src
            invalid_suffix = ""
            if structural is not None and structural.invalid_suffix == "":
                consumed_offset = len(src)
            segments = _segments_from_leaves(src, leaves)

        children = [
            LosslessCstNode(
                id=lf.node_id,
                kind="terminal",
                name=display_terminal_name(lf.lark_type, lf.lexeme),
                lark_terminal_type=lf.lark_type,
                lexeme=lf.lexeme,
                start_offset=lf.start,
                end_offset=lf.end,
                start_line=_line_col_at(src, lf.start)[0],
                start_column=_line_col_at(src, lf.start)[1],
                end_line=_line_col_at(src, lf.end)[0],
                end_column=_line_col_at(src, lf.end)[1],
                is_partial=True,
            )
            for lf in leaves
            if completeness == "incomplete_prefix" or lf.start < consumed_offset
        ]
        cst_root = LosslessCstNode(
            id="c_partial_root",
            kind="rule",
            name="partial_token_stream",
            children=children,
            is_partial=True,
        )
        warnings.append(
            f"full parse did not succeed ({type(parse_exc).__name__}); "
            f"returning truthful {completeness} lossless view"
        )

    warnings.extend(verify_lossless_segments(src, segments))

    return LosslessParserAnalysisResponse(
        completeness=completeness,
        cst_root=cst_root,
        source_segments=segments,
        structural_parser_analysis=structural,
        consumed_prefix=consumed_prefix,
        invalid_suffix=invalid_suffix,
        consumed_char_offset=consumed_offset,
        warnings=warnings,
        **common,  # type: ignore[arg-type]
    )


def build_llm_token_spans(
    selected_texts: Sequence[str],
    *,
    token_ids: Sequence[Optional[int]] | None = None,
    recorded_steps: Sequence[Optional[int]] | None = None,
    current_step_index: int | None = None,
    timing: AnalysisTiming = "before_selected_token",
) -> list[LlmTokenSpan]:
    spans: list[LlmTokenSpan] = []
    if current_step_index is None:
        end = len(selected_texts)
    elif timing == "after_selected_token":
        end = min(current_step_index + 1, len(selected_texts))
    else:
        end = min(current_step_index, len(selected_texts))
    cursor = 0
    for i in range(end):
        text = selected_texts[i]
        tid = token_ids[i] if token_ids is not None and i < len(token_ids) else None
        rec = (
            recorded_steps[i]
            if recorded_steps is not None and i < len(recorded_steps)
            else None
        )
        start = cursor
        end_off = start + len(text)
        spans.append(
            LlmTokenSpan(
                step_index=i,
                recorded_step=rec,
                token_id=tid,
                exact_text=text,
                start_offset=start,
                end_offset=end_off,
                selected_at_current_step=(
                    current_step_index is not None
                    and timing == "after_selected_token"
                    and i == current_step_index
                ),
            )
        )
        cursor = end_off
    return spans


def construct_step_source(
    selected_texts: Sequence[str],
    *,
    step_index: int,
    timing: AnalysisTiming,
) -> tuple[str, list[str]]:
    """
    before: concat [0, step_index)
    after:  concat [0, step_index+1)
    """
    warnings: list[str] = []
    if timing == "final_source":
        raise ValueError("construct_step_source does not accept final_source timing")
    if step_index < 0:
        raise ValueError("step_index must be >= 0")
    end = step_index if timing == "before_selected_token" else step_index + 1
    if end > len(selected_texts):
        raise ValueError(
            f"step_index {step_index} with timing={timing} exceeds "
            f"{len(selected_texts)} tokens"
        )
    parts: list[str] = []
    for i in range(end):
        tok = selected_texts[i]
        if tok is None:
            raise ValueError(f"selected_token missing at index {i}")
        parts.append(tok)
    return "".join(parts), warnings


def analyze_lossless_cached(
    *,
    cache_key: str,
    source: str,
    timing: AnalysisTiming,
    source_provenance: SourceProvenance,
    llm_token_spans: list[LlmTokenSpan] | None = None,
    experiment_id: str = "",
    prompt_id: str | None = None,
    step_index: int | None = None,
) -> LosslessParserAnalysisResponse:
    hit = _cache_get(cache_key)
    if hit is not None:
        return hit
    result = analyze_lossless_source(
        source,
        timing=timing,
        source_provenance=source_provenance,
        llm_token_spans=llm_token_spans,
        experiment_id=experiment_id,
        prompt_id=prompt_id,
        step_index=step_index,
    )
    _cache_put(cache_key, result)
    return result


async def analyze_lossless_in_threadpool(
    *,
    cache_key: str,
    source: str,
    timing: AnalysisTiming,
    source_provenance: SourceProvenance,
    llm_token_spans: list[LlmTokenSpan] | None = None,
    experiment_id: str = "",
    prompt_id: str | None = None,
    step_index: int | None = None,
) -> LosslessParserAnalysisResponse:
    """Run CPU analysis off the asyncio event loop."""
    import asyncio

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _executor,
        lambda: analyze_lossless_cached(
            cache_key=cache_key,
            source=source,
            timing=timing,
            source_provenance=source_provenance,
            llm_token_spans=llm_token_spans,
            experiment_id=experiment_id,
            prompt_id=prompt_id,
            step_index=step_index,
        ),
    )


def timed_analyze_lossless_source(
    source: str, **kwargs: Any
) -> tuple[LosslessParserAnalysisResponse, float]:
    t0 = time.perf_counter()
    resp = analyze_lossless_source(source, **kwargs)
    return resp, time.perf_counter() - t0
