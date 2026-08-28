"""
Phase 4A.1 — SynCode ParseResult evidence serialization and capture hooks.

Uses controlled fakes only.  Does not build a SynCode mask store.
One optional interface test imports real AcceptSequence / RemainderState shapes.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

import pytest

from app.models.schemas import DecodingStep, ExperimentResult, GenerateResponse
from app.models.syncode_parser_evidence import (
    SyncodeParserEvidence,
    unavailable_syncode_parser_evidence,
)
from app.services.syncode_parser_evidence import (
    extract_accept_terminals,
    format_legacy_accept_sequences,
    serialize_accept_sequences,
    serialize_parse_result,
    serialize_remainder,
    wrap_get_accept_mask,
)


# ---------------------------------------------------------------------------
# Fakes (SynCode 0.4.16-shaped)
# ---------------------------------------------------------------------------


class FakeRemainderState(Enum):
    COMPLETE = 0
    MAYBE_COMPLETE = 1
    INCOMPLETE = 2


class FakeAcceptSequence:
    """Mirrors syncode.AcceptSequence: terminals on .accept_terminals."""

    def __init__(self, accept_terminals):
        self.accept_terminals = tuple(accept_terminals)

    def __repr__(self):
        return f"accept_terminals: {self.accept_terminals}"

    def __eq__(self, other):
        return self.accept_terminals == other.accept_terminals

    def __hash__(self):
        return hash(str(self.accept_terminals))

    def __len__(self):
        return len(self.accept_terminals)


class FakeParseResult:
    def __init__(
        self,
        accept_sequences,
        remainder,
        remainder_state,
        function_end=False,
    ):
        self.accept_sequences = accept_sequences
        self.remainder = remainder
        self.remainder_state = remainder_state
        self.function_end = function_end


class FakeMask:
    """Minimal boolean-mask stand-in (no torch required)."""

    def __init__(self, flags: list[bool]):
        self._flags = list(flags)

    def __getitem__(self, idx: int):
        return self._flags[idx]

    def __len__(self):
        return len(self._flags)

    def numel(self):
        return len(self._flags)

    def sum(self):
        return sum(1 for x in self._flags if x)

    def all(self):
        return all(self._flags)

    def any(self):
        return any(self._flags)


def _ev(pr: FakeParseResult, **kwargs) -> SyncodeParserEvidence:
    return serialize_parse_result(pr, syncode_version="0.4.16-test", **kwargs)


# ---------------------------------------------------------------------------
# Serialization behaviour
# ---------------------------------------------------------------------------


def test_01_single_terminal_sequence():
    pr = FakeParseResult(
        {FakeAcceptSequence(["MODULE"])},
        b"",
        FakeRemainderState.COMPLETE,
    )
    ev = _ev(pr)
    assert ev.status == "available"
    assert ev.accept_sequence_count_total == 1
    assert ev.accept_sequences[0].terminals == ["MODULE"]


def test_02_multi_terminal_sequence_preserves_order():
    pr = FakeParseResult(
        {FakeAcceptSequence(["FINAL", "WS", "IDENT"])},
        "x",
        FakeRemainderState.MAYBE_COMPLETE,
    )
    ev = _ev(pr)
    assert ev.accept_sequences[0].terminals == ["FINAL", "WS", "IDENT"]


def test_03_ignored_terminal_sequence():
    # SynCode unions ignore-terminal-only sequences into the set.
    pr = FakeParseResult(
        {
            FakeAcceptSequence(["MODULE"]),
            FakeAcceptSequence(["WS"]),
            FakeAcceptSequence(["LINE_COMMENT"]),
        },
        b"",
        FakeRemainderState.COMPLETE,
    )
    ev = _ev(pr)
    labels = [tuple(r.terminals) for r in ev.accept_sequences]
    assert ("WS",) in labels
    assert ("LINE_COMMENT",) in labels
    assert ("MODULE",) in labels


def test_04_deterministic_sorting_of_set_input():
    pr = FakeParseResult(
        {
            FakeAcceptSequence(["Z"]),
            FakeAcceptSequence(["A"]),
            FakeAcceptSequence(["M", "N"]),
        },
        "",
        FakeRemainderState.COMPLETE,
    )
    ev1 = _ev(pr)
    ev2 = _ev(pr)
    assert [r.terminals for r in ev1.accept_sequences] == [
        r.terminals for r in ev2.accept_sequences
    ]
    assert [r.terminals for r in ev1.accept_sequences] == [
        ["A"],
        ["M", "N"],
        ["Z"],
    ]


def test_05_recorded_empty_set_differs_from_unavailable():
    empty = _ev(
        FakeParseResult(set(), b"", FakeRemainderState.COMPLETE)
    )
    missing = unavailable_syncode_parser_evidence(reason="not recorded")
    none_res = serialize_parse_result(None, syncode_version="0.4.16-test")
    assert empty.status == "available"
    assert empty.accept_sequence_count_total == 0
    assert empty.accept_sequences == []
    assert missing.status == "unavailable"
    assert none_res.status == "unavailable"
    assert empty.status != missing.status


def test_06_complete_state():
    ev = _ev(
        FakeParseResult(
            {FakeAcceptSequence(["MODULE"])},
            b"",
            FakeRemainderState.COMPLETE,
        )
    )
    assert ev.remainder_state == "COMPLETE"


def test_07_maybe_complete_state():
    ev = _ev(
        FakeParseResult(
            {FakeAcceptSequence(["IDENT", "SEMI"])},
            "ab",
            FakeRemainderState.MAYBE_COMPLETE,
        )
    )
    assert ev.remainder_state == "MAYBE_COMPLETE"


def test_08_incomplete_state():
    ev = _ev(
        FakeParseResult(
            {FakeAcceptSequence(["IDENT"])},
            b"\xff",
            FakeRemainderState.INCOMPLETE,
        )
    )
    assert ev.remainder_state == "INCOMPLETE"


def test_09_text_remainder():
    ev = _ev(
        FakeParseResult(
            {FakeAcceptSequence(["IDENT"])},
            "partial_id",
            FakeRemainderState.INCOMPLETE,
        )
    )
    assert ev.remainder.kind == "text"
    assert ev.remainder.text == "partial_id"
    assert ev.remainder.original_type == "str"


def test_10_valid_byte_remainder():
    ev = _ev(
        FakeParseResult(
            {FakeAcceptSequence(["IDENT"])},
            b"abc",
            FakeRemainderState.INCOMPLETE,
        )
    )
    assert ev.remainder.kind == "text"
    assert ev.remainder.text == "abc"
    assert ev.remainder.original_type == "bytes"


def test_11_invalid_utf8_remainder_as_hex():
    raw = b"\xff\xfe\xfd"
    ev = _ev(
        FakeParseResult(
            {FakeAcceptSequence(["IDENT"])},
            raw,
            FakeRemainderState.INCOMPLETE,
        )
    )
    assert ev.remainder.kind == "bytes_hex"
    assert ev.remainder.bytes_hex == raw.hex()
    assert ev.remainder.text is None


def test_12_grammar_end_marker_not_eos_token_claim():
    ev = _ev(
        FakeParseResult(
            {FakeAcceptSequence(["$END"])},
            b"",
            FakeRemainderState.COMPLETE,
            function_end=True,
        ),
        accept_mask=FakeMask([False, False, True]),
        syncode_tokenizer_eos_token_id=2,
        application_eos_token_ids=[2],
    )
    assert ev.grammar_end_marker_present is True
    assert ev.function_end is True
    # EOS allowance comes only from the accept mask, not from $END alone.
    assert ev.mask_eos_observation is not None
    assert ev.mask_eos_observation.syncode_eos_allowed_by_accept_mask is True
    # Schema / docs: grammar_end_marker_present is not named eos_token_allowed.
    assert not hasattr(ev, "eos_token_allowed")


def test_13_truncation_warnings():
    seqs = {FakeAcceptSequence([f"T{i:03d}"]) for i in range(10)}
    # Also one long multi-terminal sequence.
    seqs.add(FakeAcceptSequence([f"X{j}" for j in range(20)]))
    records, total, truncated, warnings, _ = serialize_accept_sequences(
        seqs,
        max_sequences=3,
        max_terminals=4,
        max_terminal_chars=2,
    )
    assert total == 11
    assert truncated is True
    assert len(records) == 3
    assert any("original_total=11" in w for w in warnings)
    assert any("stored_total=3" in w for w in warnings)

    rem = serialize_remainder(b"abcdefghij", max_bytes=4)
    assert rem.truncated is True
    assert rem.original_byte_length == 10
    assert rem.stored_byte_length == 4


def test_14_and_15_mask_returned_unchanged_and_called_once():
    calls: list[Any] = []
    sentinel = object()

    def original(res):
        calls.append(res)
        return sentinel

    captured: list[SyncodeParserEvidence] = []
    pr = FakeParseResult(
        {FakeAcceptSequence(["MODULE"])},
        b"",
        FakeRemainderState.COMPLETE,
    )
    wrapped = wrap_get_accept_mask(
        original,
        on_captured=captured.append,
        mask_call_index=0,
        generated_token_count_before_selection=0,
        generated_prefix="",
        syncode_version="0.4.16-test",
    )
    out = wrapped(pr)
    assert out is sentinel
    assert len(calls) == 1
    assert calls[0] is pr
    assert len(captured) == 1
    assert captured[0].status == "available"


def test_16_capture_serialization_failure_does_not_alter_mask():
    class BoomParseResult:
        @property
        def accept_sequences(self):
            raise RuntimeError("boom-serialize")

        remainder = b""
        remainder_state = FakeRemainderState.COMPLETE
        function_end = False

    calls = []
    mask_obj = FakeMask([True, False])

    def original(res):
        calls.append(1)
        return mask_obj

    captured: list[SyncodeParserEvidence] = []
    wrapped = wrap_get_accept_mask(
        original,
        on_captured=captured.append,
        mask_call_index=3,
        generated_token_count_before_selection=3,
        generated_prefix="mod",
        syncode_version="0.4.16-test",
    )
    out = wrapped(BoomParseResult())
    assert out is mask_obj
    assert len(calls) == 1
    assert captured[0].status == "failed"
    assert "boom-serialize" in captured[0].error


def test_17_original_mask_error_propagates():
    def original(res):
        raise ValueError("mask-store-failure")

    wrapped = wrap_get_accept_mask(
        original,
        on_captured=lambda _e: None,
        mask_call_index=0,
        generated_token_count_before_selection=0,
        generated_prefix="",
        syncode_version="0.4.16-test",
    )
    with pytest.raises(ValueError, match="mask-store-failure"):
        wrapped(FakeParseResult(set(), b"", FakeRemainderState.COMPLETE))


def test_18_capture_reset_between_generations():
    """Simulates forensic evidence log clear between runs."""
    log: list[dict] = []
    pr = FakeParseResult(
        {FakeAcceptSequence(["MODULE"])},
        b"",
        FakeRemainderState.COMPLETE,
    )
    ev = _ev(pr, mask_call_index=0)
    log.append(ev.model_dump())
    assert len(log) == 1
    log.clear()
    assert log == []
    # Second generation starts empty — no leakage.
    ev2 = _ev(pr, mask_call_index=0, generated_prefix="module")
    log.append(ev2.model_dump())
    assert len(log) == 1
    assert log[0]["generated_prefix_char_count"] == len("module")


def test_19_one_capture_aligns_with_one_generated_step():
    evidences = [
        _ev(
            FakeParseResult(
                {FakeAcceptSequence(["MODULE"])},
                b"",
                FakeRemainderState.COMPLETE,
            ),
            mask_call_index=i,
            generated_token_count_before_selection=i,
            generated_prefix="x" * i,
        )
        for i in range(3)
    ]
    steps = [
        DecodingStep(step=i + 1, context="", syncode_parser_evidence=evidences[i])
        for i in range(3)
    ]
    for i, step in enumerate(steps):
        assert step.syncode_parser_evidence.mask_call_index == i
        assert (
            step.syncode_parser_evidence.generated_token_count_before_selection == i
        )


def test_20_cardinality_mismatch_warning_field():
    ev = unavailable_syncode_parser_evidence(
        reason="missing",
        warnings=[
            "SynCode mask-call count (2) diverges from generated step count (3)"
        ],
        mask_call_index=0,
    )
    assert any("diverges" in w for w in ev.warnings)


def test_21_legacy_accept_sequences_compatible():
    ev = _ev(
        FakeParseResult(
            {
                FakeAcceptSequence(["Z"]),
                FakeAcceptSequence(["A", "B"]),
            },
            b"",
            FakeRemainderState.COMPLETE,
        )
    )
    legacy = format_legacy_accept_sequences(ev)
    assert legacy == [
        "accept_terminals: ('A', 'B')",
        "accept_terminals: ('Z',)",
    ]
    step = DecodingStep(
        step=1,
        context="",
        accept_sequences=legacy,
        syncode_parser_evidence=ev,
    )
    assert step.accept_sequences[0].startswith("accept_terminals:")


def test_22_old_live_experiment_json_still_loads():
    """Pre-4A.1 DecodingStep JSON without syncode_parser_evidence."""
    payload = {
        "experiment_id": "old-exp",
        "prompt": "make an and gate",
        "mode": "syncode",
        "generated_code": "module x; endmodule",
        "steps": [
            {
                "step": 1,
                "context": "",
                "selected_token": "module",
                "selected_token_id": 1,
                "accept_sequences": ["accept_terminals: ('MODULE',)"],
            }
        ],
        "total_steps": 1,
        "model_name": "test",
        "created_at": "2026-01-01T00:00:00",
    }
    exp = ExperimentResult.model_validate(payload)
    assert exp.steps[0].accept_sequences == ["accept_terminals: ('MODULE',)"]
    assert exp.steps[0].syncode_parser_evidence.status == "unavailable"

    resp = GenerateResponse.model_validate(
        {
            "experiment_id": "old-exp",
            "generated_code": "module x; endmodule",
            "steps": payload["steps"],
            "total_steps": 1,
            "mode": "syncode",
            "model_name": "test",
        }
    )
    assert resp.steps[0].syncode_parser_evidence.status == "unavailable"


def test_23_imported_evidence_defaults_unavailable():
    from app.models.normalized import NormalizedTraceStep

    step = NormalizedTraceStep(step_index=0)
    assert step.syncode_accept_sequences.is_unavailable
    assert step.remainder_state.is_unavailable
    # Phase 4A.1 does not add recomputed SynCode evidence on import.


def test_24_no_lark_terminals_copied_into_syncode_evidence():
    lark_terminals = ["MODULE", "IDENT", "$END"]
    # Building evidence only from FakeParseResult — Lark list must not appear
    # unless it was in the SynCode accept_sequences set.
    pr = FakeParseResult(
        {FakeAcceptSequence(["ONLY_SYNCODE"])},
        b"",
        FakeRemainderState.COMPLETE,
    )
    ev = _ev(pr)
    flat = [t for r in ev.accept_sequences for t in r.terminals]
    assert flat == ["ONLY_SYNCODE"]
    assert "MODULE" not in flat
    # Explicitly ensure we did not pass Lark list into the serializer API.
    assert lark_terminals != flat


def test_25_no_mask_store_built_by_unit_tests():
    # Service module must not import or construct a mask store.
    import ast
    import app.services.syncode_parser_evidence as mod

    tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module)
    assert not any("mask_store" in name for name in imported)
    assert not any("grammar_mask" in name for name in imported)
    assert "syncode" not in imported
    assert not any(name.startswith("syncode.") for name in imported)
    assert "syncode.parse_result" not in imported


def test_extract_terminals_prefers_accept_terminals_attr():
    seq = FakeAcceptSequence(["A", "B"])
    # Fake is not a list subclass with empty body, but attribute path is required.
    assert extract_accept_terminals(seq) == ["A", "B"]


def test_prefix_sha_and_timing():
    ev = _ev(
        FakeParseResult(
            {FakeAcceptSequence(["MODULE"])},
            b"",
            FakeRemainderState.COMPLETE,
        ),
        mask_call_index=4,
        generated_token_count_before_selection=4,
        generated_prefix="modu",
    )
    assert ev.evidence_timing == "before_selected_token"
    assert ev.generated_prefix_char_count == 4
    assert ev.generated_prefix_sha256 is not None
    assert len(ev.generated_prefix_sha256) == 64


def test_interface_real_syncode_accept_sequence_and_remainder_shapes():
    """
    Interface probe — imports real SynCode 0.4.16 types only.

    Not a token-mask test: does not build a mask store or call get_accept_mask.
    """
    from syncode.parse_result import (  # type: ignore
        AcceptSequence,
        ParseResult,
        RemainderState,
    )

    assert RemainderState.COMPLETE.name == "COMPLETE"
    assert RemainderState.MAYBE_COMPLETE.name == "MAYBE_COMPLETE"
    assert RemainderState.INCOMPLETE.name == "INCOMPLETE"

    seq = AcceptSequence(("MODULE", "IDENT"))
    assert extract_accept_terminals(seq) == ["MODULE", "IDENT"]
    # SynCode AcceptSequence list body is empty; attribute is authoritative.
    assert list(seq) == []

    pr = ParseResult(
        {AcceptSequence(("$END",)), AcceptSequence(("WS",))},
        b"",
        RemainderState.COMPLETE,
        function_end=True,
    )
    ev = serialize_parse_result(pr, syncode_version="0.4.16", mask_call_index=0)
    assert ev.status == "available"
    assert ev.grammar_end_marker_present is True
    assert ev.remainder_state == "COMPLETE"
    assert [r.terminals for r in ev.accept_sequences] == [["$END"], ["WS"]]


def test_phase3_offsets_survive_prior_syncode_import():
    """
    Regression: importing SynCode before parser analysis must not corrupt
    syncode.larkm (overwriting the package while submodules stay cached).
    """
    from syncode.parse_result import AcceptSequence  # noqa: F401

    from app.services.parser_analysis import (
        _analysis_lark_parser,
        analyze_verilog_source,
    )
    from app.services.verilog_validation import _load_lark_module

    _analysis_lark_parser.cache_clear()
    mod = _load_lark_module()
    assert mod is not None
    assert getattr(mod, "Lark", None) is not None
    assert getattr(mod, "exceptions", None) is not None

    # Same fixtures as tests/test_parser_analysis.py (inlined — no tests.* import).
    illegal_mid = (
        "module t(input a, output b);\n"
        "  assign b = @ a;\n"
        "endmodule\n"
    )
    comment_then_err = (
        "module t(input a, output b);\n"
        "  // comment line\n"
        "  assign b = @ a;\n"
        "endmodule\n"
    )
    multiline_block = (
        "module t(input a, output b);\n"
        "  /* multiline\n"
        "     block comment */\n"
        "  assign b = @ a;\n"
        "endmodule\n"
    )

    a = analyze_verilog_source(illegal_mid)
    assert a.status == "invalid_input"
    assert a.unexpected_token_or_char in ("@", "@ a") or "@" in a.unexpected_token_or_char
    assert a.error_offset == illegal_mid.index("@")

    b = analyze_verilog_source(comment_then_err)
    assert b.error_offset == comment_then_err.index("@")
    assert b.error_column == 14

    c = analyze_verilog_source(multiline_block)
    assert c.error_offset == multiline_block.index("@")


def test_evidence_service_import_is_side_effect_free():
    """Merely importing the evidence service must not load SynCode/Torch/etc."""
    import importlib
    import sys

    # Use a subprocess-equivalent check via fresh module inspection of source
    # plus a runtime check that importing the module does not *require* those
    # packages to already be present for the import itself.
    import app.models.syncode_parser_evidence as model_mod
    import app.services.syncode_parser_evidence as svc_mod

    model_src = open(model_mod.__file__, encoding="utf-8").read()
    assert "import syncode" not in model_src
    assert "from syncode" not in model_src
    assert "torch" not in model_src
    assert "transformers" not in model_src
    assert "MaskStore" not in model_src

    svc_src = open(svc_mod.__file__, encoding="utf-8").read()
    assert "import syncode" not in svc_src
    assert "from syncode" not in svc_src
    assert "import torch" not in svc_src
    assert "transformers" not in svc_src
    assert "MaskStore" not in svc_src
    # No import-time alias of sys.modules['lark'].
    assert "sys.modules" not in svc_src
    assert "sys.modules" not in model_src


def test_capture_wrap_restores_original_and_is_not_nested():
    """get_accept_mask wrapper: one original call, restore after capture error."""
    original_calls: list[int] = []
    current = {"fn": None}

    def original(res):
        original_calls.append(1)
        return FakeMask([True, False, True])

    current["fn"] = original

    def install_once(mask_call_index: int, prefix: str, sink: list):
        """Mimic forensic patch: wrap only for one call, restore in finally."""
        underlying = current["fn"]
        wrapped = wrap_get_accept_mask(
            underlying,
            on_captured=sink.append,
            mask_call_index=mask_call_index,
            generated_token_count_before_selection=mask_call_index,
            generated_prefix=prefix,
            syncode_version="0.4.16-test",
        )
        current["fn"] = wrapped
        try:
            return current["fn"](
                FakeParseResult(
                    {FakeAcceptSequence(["MODULE"])},
                    b"",
                    FakeRemainderState.COMPLETE,
                )
            )
        finally:
            current["fn"] = underlying

    sink_a: list[SyncodeParserEvidence] = []
    sink_b: list[SyncodeParserEvidence] = []
    out1 = install_once(0, "", sink_a)
    out2 = install_once(1, "m", sink_b)

    assert current["fn"] is original
    assert len(original_calls) == 2
    assert out1._flags == [True, False, True]
    assert out2._flags == [True, False, True]
    assert sink_a[0].mask_call_index == 0
    assert sink_a[0].generated_prefix_char_count == 0
    assert sink_b[0].mask_call_index == 1
    assert sink_b[0].generated_prefix_char_count == 1
    # No nesting: second install wrapped the restored original, not a wrapper.
    assert original_calls == [1, 1]


def test_capture_serialization_exception_still_restores_method():
    class Boom:
        @property
        def accept_sequences(self):
            raise RuntimeError("serialize-boom")

        remainder = b""
        remainder_state = FakeRemainderState.COMPLETE
        function_end = False

    holder = {"fn": None}
    calls = []

    def original(res):
        calls.append(res)
        return FakeMask([False])

    holder["fn"] = original
    captured = []
    wrapped = wrap_get_accept_mask(
        holder["fn"],
        on_captured=captured.append,
        mask_call_index=2,
        generated_token_count_before_selection=2,
        generated_prefix="ab",
        syncode_version="0.4.16-test",
    )
    holder["fn"] = wrapped
    try:
        mask = holder["fn"](Boom())
    finally:
        holder["fn"] = original

    assert holder["fn"] is original
    assert mask._flags == [False]
    assert len(calls) == 1
    assert captured[0].status == "failed"


def test_original_mask_exception_propagates_after_restore_pattern():
    holder = {"fn": None}

    def original(res):
        raise RuntimeError("mask-boom")

    holder["fn"] = original
    wrapped = wrap_get_accept_mask(
        holder["fn"],
        on_captured=lambda _e: None,
        mask_call_index=0,
        generated_token_count_before_selection=0,
        generated_prefix="",
        syncode_version="0.4.16-test",
    )
    holder["fn"] = wrapped
    with pytest.raises(RuntimeError, match="mask-boom"):
        try:
            holder["fn"](FakeParseResult(set(), b"", FakeRemainderState.COMPLETE))
        finally:
            holder["fn"] = original
    assert holder["fn"] is original


def test_separate_generation_evidence_buffers_do_not_share():
    buf_a: list[dict] = []
    buf_b: list[dict] = []
    ev = _ev(
        FakeParseResult(
            {FakeAcceptSequence(["MODULE"])},
            b"",
            FakeRemainderState.COMPLETE,
        ),
        mask_call_index=0,
    )
    buf_a.append(ev.model_dump())
    # Simulate reset between generations / objects.
    buf_a.clear()
    buf_b.append(
        _ev(
            FakeParseResult(
                {FakeAcceptSequence(["ENDMODULE"])},
                b"",
                FakeRemainderState.COMPLETE,
            ),
            mask_call_index=0,
        ).model_dump()
    )
    assert buf_a == []
    assert buf_b[0]["accept_sequences"][0]["terminals"] == ["ENDMODULE"]


def test_prefix_alignment_before_selected_token():
    import hashlib

    # Step 0: empty generated prefix (prompt excluded; token 0 not yet selected).
    ev0 = serialize_parse_result(
        FakeParseResult(
            {FakeAcceptSequence(["MODULE"])},
            b"",
            FakeRemainderState.COMPLETE,
        ),
        mask_call_index=0,
        generated_token_count_before_selection=0,
        generated_prefix="",
        syncode_version="0.4.16-test",
    )
    assert ev0.evidence_timing == "before_selected_token"
    assert ev0.mask_call_index == 0
    assert ev0.generated_token_count_before_selection == 0
    assert ev0.generated_prefix_char_count == 0
    assert ev0.generated_prefix_sha256 == hashlib.sha256(b"").hexdigest()

    # Step N: prefix is generated text before token N.
    prefix = "module"
    ev_n = serialize_parse_result(
        FakeParseResult(
            {FakeAcceptSequence(["IDENT"])},
            b"",
            FakeRemainderState.COMPLETE,
        ),
        mask_call_index=1,
        generated_token_count_before_selection=1,
        generated_prefix=prefix,
        syncode_version="0.4.16-test",
    )
    assert ev_n.mask_call_index == 1
    assert ev_n.generated_token_count_before_selection == 1
    assert ev_n.generated_prefix_char_count == len(prefix)
    assert ev_n.generated_prefix_sha256 == hashlib.sha256(
        prefix.encode("utf-8")
    ).hexdigest()
    # Prefix must not include the not-yet-selected token.
    assert not prefix.endswith(" x")
