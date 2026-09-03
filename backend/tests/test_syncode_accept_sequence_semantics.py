"""
Checkpoint 1 — SynCode accept-sequence semantics and provenance.

Controlled fakes only. Does not build a SynCode mask store or run the model.
"""

from __future__ import annotations

import json
from enum import Enum

from app.models.syncode_parser_evidence import (
    SyncodeParserEvidence,
    unavailable_syncode_parser_evidence,
)
from app.services.syncode_parser_evidence import (
    CORE_LOOKAHEAD_K_SYNCODE_0416,
    SEQUENCE_CONSTRUCTION_SYNCODE_0416,
    classify_accept_sequence,
    serialize_accept_sequences,
    serialize_parse_result,
    serialize_remainder,
)


# ---------------------------------------------------------------------------
# Fakes (SynCode 0.4.16-shaped) — duplicated to avoid cross-test imports
# ---------------------------------------------------------------------------


class FakeRemainderState(Enum):
    COMPLETE = 0
    MAYBE_COMPLETE = 1
    INCOMPLETE = 2


class FakeAcceptSequence:
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


def test_core_k_is_2_grammar_terminals_for_0416_live():
    pr = FakeParseResult(
        {FakeAcceptSequence(["MODULE"])},
        b"",
        FakeRemainderState.COMPLETE,
    )
    ev = serialize_parse_result(
        pr,
        syncode_version="0.4.16",
        origin="live_mask_runtime",
        next_accept_terminals=["MODULE"],
        ignore_terminals=["WS"],
    )
    assert ev.core_lookahead_k == CORE_LOOKAHEAD_K_SYNCODE_0416 == 2
    assert ev.core_lookahead_unit == "grammar_terminals"
    assert ev.sequence_construction == SEQUENCE_CONSTRUCTION_SYNCODE_0416
    assert ev.semantics_provenance == "recorded"
    assert ev.origin == "live_mask_runtime"
    assert ev.evidence_timing == "before_selected_token"


def test_complete_construction_next_and_ignore_only():
    pr = FakeParseResult(
        {
            FakeAcceptSequence(["MODULE"]),
            FakeAcceptSequence(["WS"]),
        },
        b"",
        FakeRemainderState.COMPLETE,
    )
    ev = serialize_parse_result(
        pr,
        syncode_version="0.4.16",
        origin="live_mask_runtime",
        next_accept_terminals=["MODULE"],
        ignore_terminals=["WS", "LINE_COMMENT"],
    )
    by_term = {tuple(r.terminals): r for r in ev.accept_sequences}
    assert by_term[("MODULE",)].construction_kind == "next_terminal"
    assert by_term[("MODULE",)].contains_ignored_terminal is False
    assert by_term[("WS",)].construction_kind == "ignore_only"
    assert by_term[("WS",)].contains_ignored_terminal is True


def test_incomplete_construction_current_terminal():
    pr = FakeParseResult(
        {FakeAcceptSequence(["IDENT"])},
        "foo",
        FakeRemainderState.INCOMPLETE,
    )
    ev = serialize_parse_result(
        pr,
        syncode_version="0.4.16",
        origin="live_mask_runtime",
        current_accept_terminals=["IDENT"],
        ignore_terminals=["WS"],
    )
    rec = ev.accept_sequences[0]
    assert rec.construction_kind == "current_terminal"
    assert rec.displayed_terminal_count == 1


def test_maybe_complete_final_then_next():
    pr = FakeParseResult(
        {FakeAcceptSequence(["FINAL", "IDENT"])},
        "x",
        FakeRemainderState.MAYBE_COMPLETE,
    )
    ev = serialize_parse_result(
        pr,
        syncode_version="0.4.16",
        origin="live_mask_runtime",
        current_accept_terminals=["FINAL"],
        next_accept_terminals=["IDENT"],
        ignore_terminals=["WS"],
    )
    rec = ev.accept_sequences[0]
    assert rec.construction_kind == "final_then_next"
    assert rec.displayed_terminal_count == 2
    assert ev.core_lookahead_k == 2


def test_final_ignore_next_length_3_does_not_change_core_k():
    pr = FakeParseResult(
        {FakeAcceptSequence(["FINAL", "WS", "IDENT"])},
        "x",
        FakeRemainderState.MAYBE_COMPLETE,
    )
    ev = serialize_parse_result(
        pr,
        syncode_version="0.4.16",
        origin="live_mask_runtime",
        current_accept_terminals=["FINAL"],
        next_accept_terminals=["IDENT"],
        ignore_terminals=["WS"],
    )
    rec = ev.accept_sequences[0]
    assert rec.terminals == ["FINAL", "WS", "IDENT"]
    assert rec.construction_kind == "final_ignore_next"
    assert rec.contains_ignored_terminal is True
    assert rec.displayed_terminal_count == 3
    assert ev.core_lookahead_k == 2  # intercalation ≠ k=3


def test_ignore_only_path():
    kind, has_ign = classify_accept_sequence(
        ["WS"],
        remainder_state="COMPLETE",
        ignore_terminals=["WS", "LINE_COMMENT"],
        next_accept_terminals=["MODULE"],
    )
    assert kind == "ignore_only"
    assert has_ign is True


def test_stored_total_truncated_separate_from_k():
    seqs = {FakeAcceptSequence([f"T{i:03d}"]) for i in range(70)}
    records, total, truncated, warnings, _ = serialize_accept_sequences(seqs)
    assert total == 70
    assert len(records) == 64
    assert truncated is True
    # Caps are sequence counts — not core k.
    assert CORE_LOOKAHEAD_K_SYNCODE_0416 == 2


def test_sequence_safety_caps_still_enforced():
    long_name = "X" * 100
    long_terms = [f"T{i}" for i in range(20)]
    records, total, truncated, warnings, _ = serialize_accept_sequences(
        {FakeAcceptSequence(long_terms), FakeAcceptSequence([long_name])}
    )
    assert total == 2
    # Terminal-per-sequence cap 16
    assert all(len(r.terminals) <= 16 for r in records)
    # Char cap 64
    assert all(all(len(t) <= 64 for t in r.terminals) for r in records)
    assert any("truncated" in w.lower() for w in warnings)


def test_live_evidence_recorded_live_mask_runtime():
    ev = serialize_parse_result(
        FakeParseResult(
            {FakeAcceptSequence(["MODULE"])},
            b"",
            FakeRemainderState.COMPLETE,
        ),
        syncode_version="0.4.16",
        origin="live_mask_runtime",
    )
    assert ev.origin == "live_mask_runtime"
    assert ev.semantics_provenance == "recorded"


def test_imported_recompute_semantics_recomputed():
    ev = serialize_parse_result(
        FakeParseResult(
            {FakeAcceptSequence(["MODULE"])},
            b"",
            FakeRemainderState.COMPLETE,
        ),
        syncode_version="0.4.16",
        origin="import_recomputed_parser_only",
        next_accept_terminals=["MODULE"],
        ignore_terminals=["WS"],
        accept_mask=None,
        mask_call_index=None,
    )
    assert ev.origin == "import_recomputed_parser_only"
    assert ev.semantics_provenance == "recomputed"
    assert ev.mask_call_index is None
    assert ev.core_lookahead_k == 2


def test_historical_json_without_new_fields_still_loads():
    legacy = {
        "status": "available",
        "origin": "live_mask_runtime",
        "evidence_timing": "before_selected_token",
        "syncode_version": "0.4.16",
        "accept_sequences": [{"terminals": ["MODULE"]}],
        "accept_sequence_count_total": 1,
        "accept_sequence_count_stored": 1,
        "accept_sequences_truncated": False,
        "remainder_state": "COMPLETE",
        "remainder": {"kind": "empty", "text": "", "original_type": "bytes"},
        "grammar_end_marker_present": False,
        "warnings": [],
        "error": "",
    }
    ev = SyncodeParserEvidence.model_validate(legacy)
    assert ev.accept_sequences[0].terminals == ["MODULE"]
    assert ev.accept_sequences[0].construction_kind is None
    assert ev.core_lookahead_k is None
    assert ev.semantics_provenance is None
    assert ev.ignore_terminals is None
    # Round-trip JSON
    again = SyncodeParserEvidence.model_validate(json.loads(ev.model_dump_json()))
    assert again.core_lookahead_k is None


def test_missing_fields_remain_unavailable_not_recorded():
    missing = unavailable_syncode_parser_evidence(reason="not recorded")
    assert missing.status == "unavailable"
    assert missing.core_lookahead_k is None
    assert missing.semantics_provenance is None
    assert missing.semantics_provenance != "recorded"


def test_remainder_whitespace_preserved_exactly():
    for raw in ["\n", "\r", "\t", " ", "", '"', "\\", "  a\nb  "]:
        rem = serialize_remainder(raw)
        assert rem.kind in ("text", "empty")
        if raw == "":
            assert rem.kind == "empty"
            assert rem.text == ""
        else:
            assert rem.text == raw


def test_ambiguous_length1_without_sets_is_unknown():
    kind, _ = classify_accept_sequence(
        ["MODULE"],
        remainder_state="COMPLETE",
        ignore_terminals=None,
        next_accept_terminals=None,
    )
    assert kind == "unknown"


def test_length3_without_ignore_set_still_final_ignore_next():
    # Unambiguous under MAYBE_COMPLETE: only construction that yields len 3.
    kind, has_ign = classify_accept_sequence(
        ["FINAL", "WS", "IDENT"],
        remainder_state="MAYBE_COMPLETE",
        ignore_terminals=None,
    )
    assert kind == "final_ignore_next"
    assert has_ign is True
