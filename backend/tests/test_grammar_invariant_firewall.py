"""Focused tests: selected-token parser guard (not exhaustive firewall)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest
import torch

from app.services.grammar_firewall import (
    RESERVED_KEYWORDS,
    PrefixOracle,
    select_valid_token,
)
from app.core.grammar import CANONICAL_GRAMMAR_PATH

MODEL_NAME = "Qwen/Qwen2.5-Coder-1.5B-Instruct"
GRAMMAR_PATH = CANONICAL_GRAMMAR_PATH

PREFIX_PORT = "module mux2to1("
MUX_REF = (
    "module mux2to1(sel, a, b, y);\n"
    "input sel;\n"
    "input a;\n"
    "input b;\n"
    "output y;\n"
    "assign y = sel ? a : b;\n"
    "endmodule"
)


def _is_masked(v: float) -> bool:
    return not math.isfinite(v) or v == float("-inf")


@pytest.fixture(scope="module")
def bundle():
    pytest.importorskip("syncode")
    from transformers import AutoTokenizer
    from syncode import SyncodeLogitsProcessor
    from syncode.parsers.grammars.grammar import Grammar

    tok = AutoTokenizer.from_pretrained(MODEL_NAME, local_files_only=True)
    grammar = Grammar(GRAMMAR_PATH.read_text(encoding="utf-8"))
    processor = SyncodeLogitsProcessor(
        grammar=grammar,
        tokenizer=tok,
        use_cache=True,
        parse_output_only=True,
        num_samples=1,
        mode="grammar_mask",
    )
    return {"processor": processor, "tok": tok, "grammar": grammar}


def _encode_one(tok, text: str) -> int | None:
    ids = tok.encode(text, add_special_tokens=False)
    return int(ids[0]) if len(ids) == 1 else None


def _syncode_mask(bundle, prefix: str) -> torch.Tensor:
    processor = bundle["processor"]
    tok = bundle["tok"]
    processor.reset()
    ge = processor.grammar_engine
    ge.start_from = 0
    ge.parse_failed = False
    ids = tok(prefix, return_tensors="pt", add_special_tokens=False).input_ids
    if ids.numel() == 0:
        ids = torch.zeros((1, 0), dtype=torch.long)
    scores = torch.zeros((1, len(tok)), dtype=torch.float32)
    # Boost a few candidates so argmax is meaningful under zeros.
    for s, boost in (("input", 5.0), (" a", 4.0), ("a", 3.5), ("sel", 3.0), ("x", 2.5)):
        tid = _encode_one(tok, s)
        if tid is not None:
            scores[0, tid] = boost
    out = processor(ids, scores.clone()).squeeze(0).clone()
    return out


# --- A. Lexer contract -------------------------------------------------------------

def test_A_reserved_keywords_lex_as_keyword_terminals():
    from app.services.verilog_validation import read_verilog_grammar, _load_lark_module

    g = read_verilog_grammar()
    L = _load_lark_module()
    parser = L.Lark(g, parser="lalr", lexer="basic", maybe_placeholders=False)
    expected = {
        "module": "MODULE", "endmodule": "ENDMODULE", "input": "INPUT",
        "output": "OUTPUT", "inout": "INOUT", "wire": "WIRE", "reg": "REG",
        "assign": "ASSIGN",
    }
    assert set(expected) <= RESERVED_KEYWORDS
    for word, term in expected.items():
        toks = list(parser.lex(word))
        assert len(toks) == 1 and toks[0].type == term, (word, toks[0].type)


# --- B. Selected-token guard -------------------------------------------------------

def test_B_input_rejected_at_port_list(bundle):
    """`input` must not be appendable after module mux2to1(."""
    tok = bundle["tok"]
    processor = bundle["processor"]
    tid = _encode_one(tok, "input")
    assert tid is not None
    oracle = PrefixOracle(processor=processor, tokenizer=tok)
    ok, sig, rem, _, _ = oracle.prefix_status(PREFIX_PORT)
    assert ok, rem
    assert not oracle.candidate_valid(
        generated_prefix=PREFIX_PORT, token_id=tid, prefix_sig=sig
    )


def test_B_identifier_a_accepted_at_port_list(bundle):
    tok = bundle["tok"]
    processor = bundle["processor"]
    tid = _encode_one(tok, "a")
    assert tid is not None
    oracle = PrefixOracle(processor=processor, tokenizer=tok)
    ok, sig, rem, _, _ = oracle.prefix_status(PREFIX_PORT)
    assert ok, rem
    assert oracle.candidate_valid(
        generated_prefix=PREFIX_PORT, token_id=tid, prefix_sig=sig
    )


def test_B_select_valid_token_skips_input_chooses_identifier(bundle):
    tok = bundle["tok"]
    processor = bundle["processor"]
    masked = _syncode_mask(bundle, PREFIX_PORT)
    input_id = _encode_one(tok, "input")
    a_id = _encode_one(tok, "a")
    assert input_id is not None and a_id is not None

    oracle = PrefixOracle(processor=processor, tokenizer=tok)
    # Make input the highest score when SynCode left it finite.
    if torch.isfinite(masked[input_id]):
        masked[input_id] = float(torch.nan_to_num(masked, nan=0.0).max().item()) + 10.0
    if torch.isfinite(masked[a_id]):
        top = float(torch.nan_to_num(masked, nan=0.0).max().item())
        masked[a_id] = top - 1.0

    guard = select_valid_token(
        masked,
        oracle=oracle,
        generated_prefix=PREFIX_PORT,
        prompt_len=0,
        max_rejects=32,
        eos_ids=set(),
    )
    assert guard.error is None, guard
    assert guard.selected_id is not None
    assert guard.selected_id != input_id
    if torch.isfinite(_syncode_mask(bundle, PREFIX_PORT)[input_id]):
        assert input_id in guard.rejected_ids
    ok, sig, _, _, _ = oracle.prefix_status(PREFIX_PORT)
    assert oracle.candidate_valid(
        generated_prefix=PREFIX_PORT,
        token_id=int(guard.selected_id),
        prefix_sig=sig,
    )
    assert 1 <= guard.validations <= 33


def test_C_mux_reference_token_by_token(bundle):
    """Valid non-ANSI mux accepted token-by-token via the oracle."""
    tok = bundle["tok"]
    processor = bundle["processor"]
    oracle = PrefixOracle(processor=processor, tokenizer=tok)
    ids = tok.encode(MUX_REF, add_special_tokens=False)
    prefix = ""
    for i, tid in enumerate(ids):
        ok, sig, rem, _, _ = oracle.prefix_status(prefix)
        assert ok, f"prefix invalid before token {i}: {prefix!r} rem={rem}"
        assert oracle.candidate_valid(
            generated_prefix=prefix, token_id=int(tid), prefix_sig=sig
        ), (
            f"gold token {i} id={tid} text={tok.decode([tid])!r} "
            f"rejected at prefix={prefix!r}"
        )
        prefix += tok.decode([tid], skip_special_tokens=False)
    from app.services.verilog_validation import parse_with_verilog_grammar

    ok, err = parse_with_verilog_grammar(MUX_REF)
    assert ok, err


def test_D_no_raw_fallback_and_no_exhaustive_firewall_in_service():
    backend = CANONICAL_GRAMMAR_PATH.resolve().parents[1]  # backend/
    svc = (backend / "app" / "services" / "llm_service.py").read_text(encoding="utf-8")
    assert "using raw selection" not in svc
    assert "falling back to raw logits" not in svc
    assert "continue with raw logits" not in svc
    assert "masked_logits = last_logits.clone()" not in svc
    assert "select_valid_token" in svc
    assert "apply_strict_firewall" not in svc
    assert "constraint_no_valid_token" in svc
    fw = (backend / "app" / "services" / "grammar_firewall.py").read_text(encoding="utf-8")
    assert "select_valid_token" in fw
    assert "max_rejects: int = 32" in fw
