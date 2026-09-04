"""
Checkpoint 3D — based-number / "'h" vs "'ha" causal tests.

Research-only. Tiny vocab + minimal grammar. No Nemotron full-vocab build.
No production imports.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from app.core.grammar import EXPECTED_GRAMMAR_SHA256, grammar_sha256, read_verilog_grammar
from app.models.syncode_mask_probe import ProbeCaseSpec
from app.research.minimal_based_number_grammar import (
    CANONICAL_NUMBER_REGEXP,
    MINIMAL_BASED_NUMBER_GRAMMAR,
)
from app.research.syncode_mask_probe_mask_store import (
    build_or_load_mask_store,
    temporary_syncode_cache,
)
from app.research.syncode_mask_probe_number_causal import (
    analyze_number_terminal_fsm,
    build_based_number_causal,
    conclude_from_number_causal,
    extract_number_terminal_definition,
    temporary_viable_nonfinal_extension_counterfactual,
)
from app.research.syncode_mask_probe_oracle import constructive_canonical_witness
from app.research.syncode_mask_probe_parser import parse_result_for_mask_store
from app.research.syncode_mask_probe_report import build_root_cause, render_markdown_report
from app.research.syncode_mask_probe_trace import extract_step_evidence
from app.research.syncode_mask_probe_version import require_syncode_0416_adapter_surface
from app.research import syncode_mask_probe as probe_mod

BACKEND = Path(__file__).resolve().parents[1]
FIXTURES = BACKEND / "research_cases" / "fixtures"


class TinyTok:
    def __init__(self):
        self._vocab = {
            "</s>": 0,
            "assign": 1,
            "16": 2,
            "'h": 3,
            "'ha": 4,
            "a": 5,
            "a66": 6,
            ";": 7,
            "?": 8,
            ":": 9,
            "\u0120": 10,
            "'hb": 11,
            "'d": 12,
            "'": 13,
            "h": 14,
            " ": 15,
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


def test_grammar_sha_unchanged_3d():
    assert grammar_sha256() == EXPECTED_GRAMMAR_SHA256


def test_canonical_number_regexp_matches_grammar(syncode_ok):
    gtext = read_verilog_grammar()
    _v, regexp = extract_number_terminal_definition(gtext)
    assert regexp == CANONICAL_NUMBER_REGEXP
    # minimal grammar embeds the same construct
    assert CANONICAL_NUMBER_REGEXP in MINIMAL_BASED_NUMBER_GRAMMAR


def test_number_fsm_viable_nonfinal_after_h(syncode_ok):
    ev = analyze_number_terminal_fsm(
        grammar_text=read_verilog_grammar(),
        remainder_text="16",
        short_bytes=b"'h",
        long_bytes=b"'ha",
    )
    assert ev.state_after_remainder_is_final is True
    assert ev.state_after_short_is_final is False
    assert ev.state_after_short_is_live is True
    assert ev.state_after_short_has_digit_transitions is True
    assert ev.state_after_long_is_final is True
    assert ev.consume_prefix_short_ok is True
    assert ev.consume_prefix_short_remainder_hex == b"'h".hex()
    assert ev.consume_prefix_long_ok is True
    assert ev.consume_prefix_long_remainder_hex == ""
    assert ev.classification == "viable_nonfinal_discarded_by_consume_prefix"
    assert ev.accepts_remainder_plus_short is False
    assert ev.accepts_remainder_plus_long is True


def test_fixture_witness_boundary_and_parse():
    prefix = (FIXTURES / "nemotron_base_literal_step65_prefix.txt").read_text(
        encoding="utf-8"
    )
    assert prefix.endswith("16")
    wh = (FIXTURES / "nemotron_base_literal_step65_witness_h.sv").read_text(
        encoding="utf-8"
    )
    assert wh.startswith(prefix + "'h")
    w = constructive_canonical_witness(
        prefix=prefix,
        candidate_decoded_text="'h",
        completion_suffix=wh[len(prefix) + 2 :],
    )
    assert w.candidate_at_exact_boundary is True
    assert w.canonical_lark_parse_success is True
    assert w.lossless_completeness == "complete"
    # P+T alone is not a completeness failure
    incomplete = constructive_canonical_witness(
        prefix=prefix,
        candidate_decoded_text="'h",
        completion_suffix="",
    )
    assert incomplete.canonical_lark_parse_success is False


def test_trace_extraction_fail_closed(tmp_path):
    inline = json.loads(
        (FIXTURES / "nemotron_base_literal_step65_inline_steps.json").read_text(
            encoding="utf-8"
        )
    )
    trace = tmp_path / "t.json"
    trace.write_text(json.dumps(inline), encoding="utf-8")
    ev = extract_step_evidence(
        trace_path=trace,
        prompt_id="Prob126_circuit6",
        step_index=65,
        expected_raw_argmax_id=6782,
        expected_raw_argmax_text="'h",
        expected_selected_id=56257,
        expected_selected_text="'ha",
    )
    assert ev["step_index_unit"] == "zero_based"
    assert ev["recorded_step_equals_index"] is True
    assert ev["prefix"].endswith("16")
    assert ev["raw_argmax_blocked"] is True
    assert len(ev["neighbours"]) >= 5
    with pytest.raises(Exception):
        extract_step_evidence(
            trace_path=trace,
            prompt_id="Prob126_circuit6",
            step_index=65,
            expected_raw_argmax_id=99999,
        )


def _parse_and_store(tmp_path, prefix: str):
    from syncode.parsers import create_parser
    from syncode.parsers.grammars.grammar import Grammar

    tok = TinyTok()
    g = Grammar(MINIMAL_BASED_NUMBER_GRAMMAR)
    with temporary_syncode_cache(tmp_path / "cache"):
        store, _ident = build_or_load_mask_store(
            mode="fresh_isolated",
            cache_root=tmp_path / "cache",
            grammar=g,
            tokenizer=tok,
            syncode_mode="grammar_mask",
        )
    parser = create_parser(g)
    if hasattr(parser, "reset"):
        parser.reset()
    raw = parser.get_acceptable_next_terminals(prefix)
    pr = parse_result_for_mask_store(raw)
    return tok, store, pr, g


def test_minimal_grammar_mask_h_vs_ha(tmp_path, syncode_ok):
    prefix = "assign 16"
    tok, store, pr, _g = _parse_and_store(tmp_path, prefix)
    mask = store.get_accept_mask(pr)
    bit_h = bool(mask[3].item() if hasattr(mask[3], "item") else mask[3])
    bit_ha = bool(mask[4].item() if hasattr(mask[4], "item") else mask[4])
    assert bit_h is False
    assert bit_ha is True

    runtime = {"3": bit_h, "4": bit_ha}
    causal = build_based_number_causal(
        mask_store=store,
        parse_result=pr,
        grammar_text=MINIMAL_BASED_NUMBER_GRAMMAR,
        short_token_id=3,
        long_token_id=4,
        short_bytes=b"'h",
        long_bytes=b"'ha",
        short_decode="'h",
        long_decode="'ha",
        runtime_bits=runtime,
        reconstructed_bits=runtime,
        remainder_text="16",
        digit_token_id=5,
        digit_bytes=b"a",
    )
    assert causal.tracing_reliable is True
    assert causal.first_differing_reason_code == "viable_nonfinal_state_discarded"
    conc, status, _u, scope = conclude_from_number_causal(causal)
    assert conc == "verified_viable_nonfinal_number_state_discarded"
    assert status == "conclusive"
    assert scope["minimal_control_conclusion"] == (
        "verified_viable_nonfinal_number_state_discarded"
    )
    assert scope["original_nemotron_conclusion"] == (
        "awaiting_full_runtime_verification"
    )
    assert causal.separate_digit_token_hypothesis["dfa_allows_digit_after_short"] is True


def test_counterfactual_preserves_h_and_restores(tmp_path, syncode_ok):
    from syncode.mask_store.byte_fsm import ByteFSM
    from syncode.parsers.grammars.grammar import Grammar

    # Observational consume_prefix behaviour
    ev0 = analyze_number_terminal_fsm(
        grammar_text=MINIMAL_BASED_NUMBER_GRAMMAR,
        remainder_text="16",
    )
    assert ev0.consume_prefix_short_remainder_hex == b"'h".hex()

    original = ByteFSM.consume_prefix
    tok = TinyTok()
    g = Grammar(MINIMAL_BASED_NUMBER_GRAMMAR)
    with temporary_viable_nonfinal_extension_counterfactual() as probe:
        assert ByteFSM.consume_prefix is not original
        ev1 = analyze_number_terminal_fsm(
            grammar_text=MINIMAL_BASED_NUMBER_GRAMMAR,
            remainder_text="16",
        )
        assert ev1.consume_prefix_short_remainder_hex == ""
        assert ev1.state_after_short_is_live is True
        with temporary_syncode_cache(tmp_path / "cf"):
            store, _ = build_or_load_mask_store(
                mode="fresh_isolated",
                cache_root=tmp_path / "cf",
                grammar=g,
                tokenizer=tok,
                syncode_mode="grammar_mask",
            )
        from syncode.parsers import create_parser

        parser = create_parser(g)
        if hasattr(parser, "reset"):
            parser.reset()
        pr = parse_result_for_mask_store(
            parser.get_acceptable_next_terminals("assign 16")
        )
        mask = store.get_accept_mask(pr)
        assert bool(mask[3].item() if hasattr(mask[3], "item") else mask[3]) is True
        # Control that cannot extend NUMBER from remainder 16 remains blocked.
        assert bool(mask[1].item() if hasattr(mask[1], "item") else mask[1]) is False
        assert bool(mask[14].item() if hasattr(mask[14], "item") else mask[14]) is False

    assert probe["restored"] is True
    assert ByteFSM.consume_prefix is original
    # Original behaviour restored
    ev2 = analyze_number_terminal_fsm(
        grammar_text=MINIMAL_BASED_NUMBER_GRAMMAR,
        remainder_text="16",
    )
    assert ev2.consume_prefix_short_remainder_hex == b"'h".hex()


def test_report_prefers_number_causal(tmp_path, syncode_ok):
    pf = tmp_path / "prefix.txt"
    pf.write_text("assign 16", encoding="utf-8")
    case = ProbeCaseSpec(
        case_id="num_report",
        prefix_source="explicit",
        explicit_prefix_file=str(pf),
        candidate_token_ids=[3, 4],
        expected_decoded_candidates={"3": "'h", "4": "'ha"},
        raw_argmax_token_id=3,
        selected_token_id=4,
        tokenizer_model_id="tiny",
    )
    tok = TinyTok()
    result = probe_mod.run_probe(
        case,
        tokenizer=tok,
        cache_root=tmp_path / "c",
        grammar_text=MINIMAL_BASED_NUMBER_GRAMMAR,
        run_number_causal_trace=True,
        short_number_token_id=3,
        long_number_token_id=4,
    )
    assert result.execution_status == "complete"
    assert result.number_causal is not None
    assert (
        result.supported_conclusion
        == "verified_viable_nonfinal_number_state_discarded"
    )
    assert result.root_cause is not None
    assert (
        result.root_cause.minimal_control_conclusion
        == "verified_viable_nonfinal_number_state_discarded"
    )
    assert (
        result.root_cause.original_nemotron_conclusion
        == "awaiting_full_runtime_verification"
    )
    assert result.root_cause.conclusion_scope == "mixed_pending_nscc"
    md = render_markdown_report(result)
    assert "execution_status: `complete`" in md
    assert "original_nemotron_conclusion" in md
    assert result.report_status == result.execution_status


def test_no_production_imports_of_research_number_modules():
    prod_roots = [
        BACKEND / "app" / "services" / "llm_service.py",
        BACKEND / "app" / "api",
        BACKEND / "app" / "core",
    ]
    banned = (
        "syncode_mask_probe_number_causal",
        "minimal_based_number_grammar",
        "syncode_mask_probe_trace",
    )
    for root in prod_roots:
        paths = [root] if root.is_file() else list(root.rglob("*.py"))
        for p in paths:
            text = p.read_text(encoding="utf-8")
            for b in banned:
                assert b not in text, f"{p} imports research module {b}"


def test_template_step65_ids():
    data = json.loads(
        (BACKEND / "research_cases" / "nemotron_base_literal_step65.template.json")
        .read_text(encoding="utf-8")
    )
    assert data["step_index"] == 65
    assert data["raw_argmax_token_id"] == 6782
    assert data["selected_token_id"] == 56257
    assert data["expected_decoded_candidates"]["6782"] == "'h"
    assert data["expected_decoded_candidates"]["56257"] == "'ha"


def test_research_modules_parse_cleanly():
    for rel in (
        "app/research/syncode_mask_probe_number_causal.py",
        "app/research/syncode_mask_probe_trace.py",
        "app/research/minimal_based_number_grammar.py",
    ):
        src = (BACKEND / rel).read_text(encoding="utf-8")
        ast.parse(src)
