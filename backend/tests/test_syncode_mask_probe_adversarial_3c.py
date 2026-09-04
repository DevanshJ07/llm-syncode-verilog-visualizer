"""
Adversarial Checkpoint 3C gates: counterfactual + minimal real MaskStore.

Research-only. No production SynCode mutation. No Nemotron full vocab build.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import torch

from app.core.grammar import EXPECTED_GRAMMAR_SHA256, grammar_sha256, read_verilog_grammar
from app.models.syncode_mask_probe import ProbeCaseSpec
from app.research.minimal_identifier_ws_grammar import MINIMAL_IDENTIFIER_WS_GRAMMAR
from app.research.syncode_mask_probe_causal import (
    GRAMMAR_WS_BYTES,
    build_causal_differential,
    conclude_from_causal,
    record_call_path_for_candidate,
    temporary_full_ws_strip_counterfactual,
    ws_dfa_accepts_bytes,
)
from app.research.syncode_mask_probe_mask_store import (
    build_or_load_mask_store,
    temporary_syncode_cache,
)
from app.research.syncode_mask_probe_oracle import constructive_canonical_witness
from app.research.syncode_mask_probe_report import render_markdown_report
from app.research.syncode_mask_probe_version import require_syncode_0416_adapter_surface
from app.research import syncode_mask_probe as probe_mod

BACKEND = Path(__file__).resolve().parents[1]
PREFIX_3B = "module TopModule (\n    input  [31:0] in,\n    output [31:0] out"


class TinyTok:
    """HF-compatible tiny vocab with BYTE_LEVEL marker."""

    def __init__(self):
        # IDs chosen for clarity: 1=LF, 2=space (like 1010/1032 roles)
        self._vocab = {
            "</s>": 0,
            "\n": 1,
            " ": 2,
            "module": 3,
            "Top": 4,
            "(": 5,
            ")": 6,
            ";": 7,
            "out": 8,
            ",": 9,
            "\u0120": 10,  # Ġ → BYTE_LEVEL
            "a": 11,
        }
        self.vocab_size = max(self._vocab.values()) + 1
        self.eos_token_id = 0
        self.pad_token_id = 0
        self.bos_token_id = 0
        self.all_special_ids = [0]

    def get_vocab(self):
        return dict(self._vocab)

    def convert_ids_to_tokens(self, tid):
        inv = {v: k for k, v in self._vocab.items()}
        return inv.get(int(tid))

    def decode(self, ids, skip_special_tokens=False, clean_up_tokenization_spaces=False):
        inv = {v: k for k, v in self._vocab.items()}
        return "".join(inv.get(int(i), "") for i in ids)

    def encode(self, text, add_special_tokens=False):
        if text in self._vocab:
            return [self._vocab[text]]
        raise ValueError(text)


@pytest.fixture(scope="module")
def syncode_ok():
    require_syncode_0416_adapter_surface()


def _parse_result_for(prefix: str, grammar_text: str):
    from syncode.parsers import create_parser
    from syncode.parsers.grammars.grammar import Grammar
    from app.research.syncode_mask_probe_parser import parse_result_for_mask_store

    g = Grammar(grammar_text)
    parser = create_parser(g)
    if hasattr(parser, "reset"):
        parser.reset()
    raw = parser.get_acceptable_next_terminals(prefix)
    return parse_result_for_mask_store(raw), g


def test_gate3_call_path_out_maybe_complete(syncode_ok):
    """Call-path proof on canonical grammar FSMs for remainder out."""
    from syncode.parsers.grammars.grammar import Grammar
    from syncode.parsers import create_base_parser
    from syncode.mask_store.fsm_set import FSMSet
    from syncode.mask_store.mask_store import MaskStore
    from types import SimpleNamespace

    gtext = read_verilog_grammar()
    base = create_base_parser(Grammar(gtext))
    ms = MaskStore.__new__(MaskStore)
    ms._fsms = FSMSet(base.terminals, Grammar(gtext).simplifications())
    ms._ignore_whitespace = True
    ms._mode = "grammar_mask"
    id_state = [
        s for s in ms._fsms.compute_fsm_states(b"out") if s.terminal == "IDENTIFIER"
    ][0]
    assert ms._fsms.is_final(id_state)

    path_space = record_call_path_for_candidate(
        ms, fsm_state=id_state, token_bytes=b" ", next_terminal="COMMA"
    )
    path_lf = record_call_path_for_candidate(
        ms, fsm_state=id_state, token_bytes=b"\n", next_terminal="COMMA"
    )
    assert path_space["remainder_before_strip_hex"] == "20"
    assert path_space["remainder_after_strip_hex"] == ""
    assert path_space["would_store_next_terminal_path"] is True
    assert path_lf["remainder_before_strip_hex"] == "0a"
    assert path_lf["remainder_after_strip_hex"] == "0a"
    assert path_lf["would_store_next_terminal_path"] is False
    assert path_lf["first_failed_operation"] == (
        "remove_left_whitespace_did_not_strip_then_next_terminal_failed"
    )


def test_gate5_minimal_maskstore_and_gate4_counterfactual(tmp_path, syncode_ok):
    """
    Minimal real SynCode MaskStore: space allowed, newline blocked;
    construction-time counterfactual admits newline; restore leaves defect.
    """
    from syncode.mask_store.mask_store import MaskStore
    from syncode.parsers.grammars.grammar import Grammar
    from syncode.parsers import create_parser
    from app.research.syncode_mask_probe_parser import parse_result_for_mask_store

    tok = TinyTok()
    grammar = Grammar(MINIMAL_IDENTIFIER_WS_GRAMMAR)
    cache_a = tmp_path / "cache_original"
    store, ident = build_or_load_mask_store(
        mode="fresh_isolated",
        cache_root=cache_a,
        grammar=grammar,
        tokenizer=tok,
        syncode_mode="grammar_mask",
    )
    assert ident.mode == "fresh_isolated"

    parser = create_parser(grammar)
    prefix = "module Top (out"
    if hasattr(parser, "reset"):
        parser.reset()
    raw = parser.get_acceptable_next_terminals(prefix)
    pr = parse_result_for_mask_store(raw)
    rem_name = (
        pr.remainder_state.name
        if hasattr(pr.remainder_state, "name")
        else str(pr.remainder_state)
    )
    assert rem_name == "MAYBE_COMPLETE", (rem_name, pr.remainder)
    rem = pr.remainder
    assert rem in (b"out", "out") or (
        isinstance(rem, (bytes, bytearray)) and bytes(rem) == b"out"
    )

    mask = store.get_accept_mask(pr)
    bit_nl = bool(mask[1].item())
    bit_sp = bool(mask[2].item())
    assert bit_sp is True, "space must be allowed under original MaskStore"
    assert bit_nl is False, "newline must be blocked under original MaskStore"

    gtext = MINIMAL_IDENTIFIER_WS_GRAMMAR
    acc = ws_dfa_accepts_bytes(gtext, {"lf": b"\n", "space": b" "})
    assert acc["lf"] is True and acc["space"] is True

    bt = store.byte_tokenizer.decode([1], skip_special_tokens=False)
    assert bytes(bt) == b"\n"

    id_states = [
        s
        for s in store.get_fsm_states(pr)
        if getattr(s, "terminal", None) in ("IDENT", "IDENTIFIER")
    ]
    assert id_states, list(store.get_fsm_states(pr))
    st = id_states[0]
    next_term = None
    for seq in pr.accept_sequences:
        terms = list(getattr(seq, "accept_terminals", None) or list(seq))
        if (
            len(terms) == 2
            and terms[0] in ("IDENT", "IDENTIFIER")
            and terms[1] not in ("WS", "LINE_COMMENT", "BLOCK_COMMENT")
        ):
            next_term = terms[1]
            break
    assert next_term is not None, [list(s) for s in pr.accept_sequences]
    assert next_term in ("COMMA", "RPAR", ",", ")"), next_term
    path_lf = record_call_path_for_candidate(
        store, fsm_state=st, token_bytes=b"\n", next_terminal=next_term
    )
    path_sp = record_call_path_for_candidate(
        store, fsm_state=st, token_bytes=b" ", next_terminal=next_term
    )
    assert path_sp["would_store_next_terminal_path"] is True
    assert path_lf["would_store_next_terminal_path"] is False
    assert path_lf["first_failed_operation"] == (
        "remove_left_whitespace_did_not_strip_then_next_terminal_failed"
    )
    class_original = MaskStore._remove_left_whitespace
    cache_b = tmp_path / "cache_counterfactual"
    with temporary_full_ws_strip_counterfactual(MaskStore) as cf_probe:
        assert MaskStore._remove_left_whitespace is not class_original
        store_cf, _ = build_or_load_mask_store(
            mode="fresh_isolated",
            cache_root=cache_b,
            grammar=grammar,
            tokenizer=tok,
            syncode_mode="grammar_mask",
        )
        mask_cf = store_cf.get_accept_mask(pr)
        assert bool(mask_cf[2].item()) is True
        assert bool(mask_cf[1].item()) is True
    assert cf_probe["restored"] is True
    assert cf_probe["experimental"] is True
    assert MaskStore._remove_left_whitespace is class_original

    cache_c = tmp_path / "cache_after_restore"
    store_after, _ = build_or_load_mask_store(
        mode="fresh_isolated",
        cache_root=cache_c,
        grammar=grammar,
        tokenizer=tok,
        syncode_mode="grammar_mask",
    )
    mask_after = store_after.get_accept_mask(pr)
    assert bool(mask_after[1].item()) is False
    assert bool(mask_after[2].item()) is True
    mask_orig2 = store.get_accept_mask(pr)
    assert bool(mask_orig2[1].item()) is False
    assert bool(mask_orig2[2].item()) is True


def test_gate4_counterfactual_hook_restoration_on_instance(syncode_ok):
    from syncode.mask_store.mask_store import MaskStore

    # Use unbound original on class
    original = MaskStore._remove_left_whitespace
    with temporary_full_ws_strip_counterfactual(MaskStore) as probe:
        assert MaskStore._remove_left_whitespace is not original
        raise_error = False
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            raise_error = True
        assert raise_error
    assert probe["restored"] is True
    # After context, class attribute restored
    assert MaskStore._remove_left_whitespace is original


def test_gate6_report_status_consistency_with_causal(tmp_path, syncode_ok):
    """JSON/Markdown agree on conclusive ignored-terminal conclusion when causal set."""
    from app.models.syncode_mask_probe import (
        CausalDifferentialEvidence,
        RootCauseReport,
        SyncodeMaskProbeResult,
    )
    from app.research.syncode_mask_probe_report import build_root_cause

    case = ProbeCaseSpec(case_id="adv_status", candidate_token_ids=[1, 2])
    result = SyncodeMaskProbeResult(
        case=case,
        execution_status="complete",
        report_status="complete",
        failure_stage=None,
        causal=CausalDifferentialEvidence(
            tracing_reliable=True,
            traced_mask_equals_runtime=True,
            ws_dfa_accepts={"lf_0a": True, "space_20": True},
            first_differing_field="process_complete_case/_remove_left_whitespace",
            first_differing_reason_code="whitespace_strip_asymmetric",
            first_differing_detail="space-only lstrip",
            ws_grammar_definition_verbatim="WS: /[ \\t\\f\\r\\n]/+/",
        ),
    )
    result.root_cause = build_root_cause(result)
    result.supported_conclusion = result.root_cause.supported_conclusion
    result.causal_conclusion_status = result.root_cause.causal_conclusion_status
    result.unresolved_reasons = list(result.root_cause.unresolved_reasons)
    assert result.execution_status == "complete"
    assert result.report_status == "complete"
    assert result.failure_stage is None
    assert result.causal_conclusion_status == "conclusive"
    assert (
        result.supported_conclusion == "verified_ignored_terminal_handling_defect"
    )
    md = render_markdown_report(result)
    assert "execution_status: `complete`" in md
    assert "report_status: `complete`" in md
    assert "causal_conclusion_status: `conclusive`" in md
    assert "verified_ignored_terminal_handling_defect" in md
    assert "report_status=incomplete" not in md
    dumped = json.loads(result.model_dump_json())
    assert dumped["execution_status"] == "complete"
    assert dumped["report_status"] == "complete"
    assert dumped["causal_conclusion_status"] == "conclusive"
    assert dumped["supported_conclusion"] == (
        "verified_ignored_terminal_handling_defect"
    )
    assert dumped["failure_stage"] is None


def test_gate2_byteto_tokenizer_1010_style_and_witness(syncode_ok):
    wit = constructive_canonical_witness(
        prefix=PREFIX_3B,
        candidate_decoded_text="\n",
        completion_suffix=");\nendmodule\n",
    )
    assert wit.canonical_lark_parse_success is True
    assert wit.candidate_at_exact_boundary is True
    acc = ws_dfa_accepts_bytes(
        read_verilog_grammar(), {"lf": b"\n", "space": b" "}
    )
    assert acc["lf"] is True
    assert grammar_sha256() == EXPECTED_GRAMMAR_SHA256
    assert GRAMMAR_WS_BYTES == bytes([0x20, 0x09, 0x0C, 0x0D, 0x0A])
