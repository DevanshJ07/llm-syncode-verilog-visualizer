"""
Checkpoint 3C — causal tracer / status / WS differential tests.

Uses tiny local vocab + minimal grammar. No Nemotron mask-store build.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.grammar import EXPECTED_GRAMMAR_SHA256, grammar_sha256, read_verilog_grammar
from app.models.syncode_mask_probe import ProbeCaseSpec, SyncodeMaskProbeResult
from app.research.minimal_identifier_ws_grammar import MINIMAL_IDENTIFIER_WS_GRAMMAR
from app.research.syncode_mask_probe_causal import (
    build_causal_differential,
    conclude_from_causal,
    extract_ws_terminal_definition,
    temporary_accept_mask_hook,
    ws_dfa_accepts_bytes,
)
from app.research.syncode_mask_probe_oracle import (
    CONTROL_WITNESS_SUFFIXES,
    constructive_canonical_witness,
    control_canonical_witness,
)
from app.research.syncode_mask_probe_report import build_root_cause, render_markdown_report
from app.research.syncode_mask_probe_version import (
    SyncodeAdapterError,
    require_syncode_0416_adapter_surface,
    require_syncode_version,
)
from app.research import syncode_mask_probe as probe_mod

BACKEND_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_PREFIX = (
    "module TopModule (\n    input  [31:0] in,\n    output [31:0] out"
)


class TinyTok:
    def __init__(self, vocab: dict[str, int]):
        self._vocab = dict(vocab)
        self._id_to = {i: t for t, i in self._vocab.items()}
        self.vocab_size = max(self._vocab.values()) + 1
        self.eos_token_id = 0
        self.pad_token_id = 0
        self.bos_token_id = 0
        self.all_special_ids = [0]

    def get_vocab(self):
        return dict(self._vocab)

    def convert_ids_to_tokens(self, tid):
        return self._id_to.get(int(tid))

    def decode(self, ids, skip_special_tokens=False, clean_up_tokenization_spaces=False):
        return "".join(self._id_to.get(int(i), "") for i in ids)

    def encode(self, text, add_special_tokens=False):
        if text in self._vocab:
            return [self._vocab[text]]
        raise ValueError(text)


@pytest.fixture(scope="module")
def syncode_ok():
    ver, _ = require_syncode_version(allow_unsupported=False)
    require_syncode_0416_adapter_surface()
    return ver


def test_grammar_sha_unchanged_3c():
    assert grammar_sha256() == EXPECTED_GRAMMAR_SHA256


def test_report_status_consistency_complete(tmp_path, syncode_ok):
    case = ProbeCaseSpec(
        case_id="status_ok",
        prefix_source="explicit",
        candidate_token_ids=[1],
        expected_decoded_candidates={"1": "a"},
        tokenizer_model_id="fake",
    )
    pf = tmp_path / "p.txt"
    pf.write_text("a", encoding="utf-8")
    case.explicit_prefix_file = str(pf)
    tok = TinyTok({"</s>": 0, "a": 1, "\u0120": 2})
    result = probe_mod.run_probe(
        case, tokenizer=tok, cache_root=tmp_path / "c", skip_mask_store=True
    )
    assert result.execution_status == "complete"
    assert result.report_status == "complete"
    md = render_markdown_report(result)
    assert "execution_status: `complete`" in md
    assert "report_status: `complete`" in md
    assert "report_status=incomplete" not in md


def test_report_status_consistency_failed(tmp_path, syncode_ok):
    case = ProbeCaseSpec(
        case_id="status_fail",
        prefix_source="explicit",
        candidate_token_ids=[999],
        tokenizer_model_id="fake",
    )
    pf = tmp_path / "p.txt"
    pf.write_text("a", encoding="utf-8")
    case.explicit_prefix_file = str(pf)
    tok = TinyTok({"</s>": 0, "a": 1})
    result = probe_mod.run_probe(
        case, tokenizer=tok, cache_root=tmp_path / "c", skip_mask_store=True
    )
    assert result.execution_status == "failed"
    assert result.report_status == "failed"
    md = render_markdown_report(result)
    assert "execution_status: `failed`" in md
    assert "report_status: `failed`" in md


def test_ws_grammar_definition_and_dfa(syncode_ok):
    g = read_verilog_grammar()
    verbatim, regexp = extract_ws_terminal_definition(g)
    assert "WS:" in verbatim
    assert "\\n" in verbatim or "n" in verbatim
    acc = ws_dfa_accepts_bytes(
        g,
        {
            "space_20": b" ",
            "lf_0a": b"\n",
            "cr_0d": b"\r",
            "tab_09": b"\t",
            "lf_lf_0a0a": b"\n\n",
            "crlf_0d0a": b"\r\n",
        },
    )
    assert acc["space_20"] is True
    assert acc["lf_0a"] is True
    assert acc["cr_0d"] is True
    assert acc["tab_09"] is True
    assert acc["lf_lf_0a0a"] is True
    assert acc["crlf_0d0a"] is True


def test_hook_restoration_success_and_exception(syncode_ok):
    store = SimpleNamespace(get_accept_mask=lambda r, get_list=False: "ok")
    original = store.get_accept_mask
    with temporary_accept_mask_hook(store) as probe:
        assert store.get_accept_mask is not original
        assert store.get_accept_mask(None) == "ok"
        assert probe["calls"] == 1
    assert store.get_accept_mask is original
    assert probe["restored"] is True

    store2 = SimpleNamespace(get_accept_mask=lambda r, get_list=False: "ok")
    original2 = store2.get_accept_mask
    try:
        with temporary_accept_mask_hook(store2) as probe2:
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert store2.get_accept_mask is original2
    assert probe2["restored"] is True


def test_newline_space_construction_differential_full_grammar(syncode_ok):
    """Uses real FSMs from canonical grammar; no full mask store build."""
    from syncode.parsers.grammars.grammar import Grammar
    from syncode.parsers import create_base_parser, create_parser
    from syncode.mask_store.fsm_set import FSMSet
    from syncode.mask_store.mask_store import MaskStore
    from syncode.parse_result import ParseResult, RemainderState, AcceptSequence

    gtext = read_verilog_grammar()
    grammar = Grammar(gtext)
    base = create_base_parser(grammar)
    # Build a lightweight MaskStore-like shell with real FSMs + ignore_whitespace
    # without populating the full lookup table (too heavy). We only need
    # _remove_left_whitespace + _fsms for construction replay.
    tok = TinyTok({"</s>": 0, "\n": 1, " ": 2, "\u0120": 3, "a": 4})
    # Use MaskStore.__new__ to avoid _store_token_masks
    ms = MaskStore.__new__(MaskStore)
    ms._vocab = ["</s>", "\n", " ", "a"]
    ms._mode = "grammar_mask"
    ms.byte_tokenizer = SimpleNamespace(decode=lambda ids, skip_special_tokens=False: {1: b"\n", 2: b" "}[ids[0]])
    ms.special_token_ids = [0]
    ms.eos_token_id = 0
    ms._fsms = FSMSet(base.terminals, grammar.simplifications())
    ms._ignore_whitespace = True
    ms._lookup_table = SimpleNamespace(
        _fsm_state_and_next_terminal_to_tokens={},
        _get_default_mask=lambda: __import__("torch").zeros(4, dtype=__import__("torch").bool),
        incomplete_case_lookup=lambda s: __import__("torch").zeros(4, dtype=__import__("torch").bool),
    )
    ms.indentation = False

    rem = b"out"
    states = ms._fsms.compute_fsm_states(rem)
    id_state = [s for s in states if s.terminal == "IDENTIFIER"][0]
    assert ms._fsms.is_final(id_state)

    # Fake parse result with the key sequences from 3B
    seqs = [
        AcceptSequence(["IDENTIFIER", "COMMA"]),
        AcceptSequence(["IDENTIFIER", "RPAR"]),
        AcceptSequence(["IDENTIFIER", "LSQB"]),
        AcceptSequence(["IDENTIFIER", "WS", "COMMA"]),
        AcceptSequence(["WS"]),
    ]
    pr = ParseResult(
        accept_sequences=set(seqs),
        remainder=rem,
        remainder_state=RemainderState.MAYBE_COMPLETE,
    )

    # Provide runtime bits matching 3B shape for reliability check path
    runtime_bits = {"1": False, "2": True}
    reconstructed_bits = dict(runtime_bits)

    # Monkeypatch get_accept_mask / get_fsm_states for observational path
    ms.get_fsm_states = lambda r: [id_state]
    import torch

    def fake_accept_mask(r, get_list=False):
        m = torch.zeros(4, dtype=torch.bool)
        m[2] = True  # space
        return m

    ms.get_accept_mask = fake_accept_mask

    causal = build_causal_differential(
        mask_store=ms,
        parse_result=pr,
        grammar_text=gtext,
        newline_token_id=1,
        space_token_id=2,
        newline_bytes=b"\n",
        space_bytes=b" ",
        runtime_bits=runtime_bits,
        reconstructed_bits=reconstructed_bits,
        ignore_terminals=["WS", "LINE_COMMENT", "BLOCK_COMMENT"],
        current_accept_terminals=["IDENTIFIER", "LSQB"],
        next_accept_terminals=["COMMA", "LSQB", "RPAR"],
    )
    assert causal.ws_dfa_accepts.get("lf_0a") is True
    assert causal.first_differing_reason_code == "whitespace_strip_asymmetric"
    assert "remove_left_whitespace" in (causal.first_differing_field or "")
    conc, status, _ = conclude_from_causal(causal)
    assert status == "conclusive"
    assert conc == "verified_ignored_terminal_handling_defect"


def test_control_witnesses_candidate_specific_suffixes():
    prefix = EXPECTED_PREFIX
    w = control_canonical_witness(
        prefix=prefix, control_name="newline", candidate_decoded_text="\n"
    )
    assert w.canonical_lark_parse_success is True
    assert w.candidate_at_exact_boundary is True
    # comma_newline must use a suffix that continues ports, not ");..."
    w2 = control_canonical_witness(
        prefix=prefix, control_name="comma_newline", candidate_decoded_text=",\n"
    )
    assert w2.canonical_lark_parse_success is True
    assert CONTROL_WITNESS_SUFFIXES["comma_newline"] != CONTROL_WITNESS_SUFFIXES["newline"]
    w3 = control_canonical_witness(
        prefix=prefix, control_name="rpar_newline", candidate_decoded_text=")\n"
    )
    assert w3.canonical_lark_parse_success is True


def test_minimal_grammar_fixture_parses(syncode_ok):
    from app.services.verilog_validation import _load_lark_module

    lark = _load_lark_module()
    parser = lark.Lark(MINIMAL_IDENTIFIER_WS_GRAMMAR, parser="lalr")
    parser.parse("module Top (out);")
    parser.parse("module Top (out\n);")
    parser.parse("module Top (out );")


def test_fixed_k_marked_unavailable_in_causal(syncode_ok):
    # Empty causal shell
    from app.models.syncode_mask_probe import CausalDifferentialEvidence

    c = CausalDifferentialEvidence()
    assert c.fixed_k_status == "UNAVAILABLE"


def test_version_guard_failure(monkeypatch, syncode_ok):
    import app.research.syncode_mask_probe_version as vg

    monkeypatch.setattr(vg, "get_installed_syncode_version", lambda: "0.0.0")
    with pytest.raises(Exception):
        require_syncode_version(allow_unsupported=False)


def test_production_modules_do_not_import_research_3c():
    targets = [
        BACKEND_ROOT / "main.py",
        BACKEND_ROOT / "app" / "services" / "llm_service.py",
        BACKEND_ROOT / "app" / "api" / "routes" / "generate.py",
    ]
    for path in targets:
        if not path.is_file():
            continue
        src = path.read_text(encoding="utf-8")
        assert "app.research" not in src
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("app.research")


def test_unavailable_private_state_reason_code(syncode_ok):
    from syncode.parse_result import ParseResult, RemainderState, AcceptSequence
    import torch

    gtext = read_verilog_grammar()
    ms = SimpleNamespace(
        _fsms=SimpleNamespace(
            compute_fsm_states=lambda b: [],
            is_final=lambda s: False,
            initial=lambda t: SimpleNamespace(terminal=t, state_id=0),
            _terminals_to_byte_fsm={},
        ),
        _lookup_table=SimpleNamespace(_fsm_state_and_next_terminal_to_tokens={}),
        _mode="grammar_mask",
        _ignore_whitespace=True,
        get_fsm_states=lambda r: [],
        get_accept_mask=lambda r, get_list=False: torch.zeros(4, dtype=torch.bool),
        _remove_left_whitespace=lambda s, rem: rem,
    )
    pr = ParseResult(
        accept_sequences={AcceptSequence(["IDENTIFIER", "COMMA"])},
        remainder=b"out",
        remainder_state=RemainderState.MAYBE_COMPLETE,
    )
    causal = build_causal_differential(
        mask_store=ms,
        parse_result=pr,
        grammar_text=gtext,
        newline_token_id=1,
        space_token_id=2,
        newline_bytes=b"\n",
        space_bytes=b" ",
        runtime_bits={"1": False, "2": False},
        reconstructed_bits={"1": False, "2": False},
    )
    # Without IDENTIFIER state, differential cannot locate strip asymmetry
    assert causal.newline_trace is not None
    assert any(
        s.reason_code == "unavailable_private_state" for s in causal.newline_trace.sequences
    )
