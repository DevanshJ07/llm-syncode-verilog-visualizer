"""
Grammar/runtime conformance: token-by-token SynCode mask vs canonical grammar.

Tests the constraint contract (not NL prompts). Does not scan full vocab.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import torch

from app.core.grammar import CANONICAL_GRAMMAR_PATH

GRAMMAR_PATH = CANONICAL_GRAMMAR_PATH
MODEL_NAME = "Qwen/Qwen2.5-Coder-1.5B-Instruct"

# Representative programs covering major supported productions.
REF_PROGRAMS: dict[str, str] = {
    "module_ports": """module m(a, b, y);
input a;
input b;
output y;
assign y = a;
endmodule""",
    "wire_decl": """module m(a, y);
input a;
output y;
wire w;
assign w = a;
assign y = w;
endmodule""",
    "continuous_assign": """module m(a, b, y);
input a;
input b;
output y;
assign y = a;
endmodule""",
    "arith_expr": """module m(a, b, y);
input a;
input b;
output y;
assign y = a + b - a * b;
endmodule""",
    "logic_ternary": """module m(a, b, c, y);
input a;
input b;
input c;
output y;
assign y = a && b || c ? a : b;
endmodule""",
    "endmodule": """module m;
endmodule""",
}

# At selected prefixes, these single-token strings must stay finite (if they
# encode as exactly one tokenizer id).
MUST_ALLOW: dict[str, list[tuple[str, str]]] = {
    # (prefix_before_token, token_text)
    "module_ports": [
        ("", "module"),
        ("module ", "m"),
        ("module m", "("),
    ],
    "wire_decl": [
        ("module m(a, y);\ninput a;\noutput y;\n", "wire"),
    ],
    "continuous_assign": [
        ("module m(a, b, y);\ninput a;\ninput b;\noutput y;\n", "assign"),
    ],
    "endmodule": [
        ("module m;\n", "endmodule"),
    ],
}

# Obvious invalid next pieces (single-id if possible) that must be masked.
MUST_MASK: dict[str, list[tuple[str, str]]] = {
    "module_ports": [
        ("module m(", "input"),  # ANSI port decl not supported; keyword in port list
        ("module m(a, b, y);\n", "assign"),  # need decl or endmodule first is OK actually assign can be module_item
    ],
    "wire_decl": [
        ("module m(a, y);\n", "wire"),  # need input/output first? wire is valid module_item - skip
    ],
}


def _lark_parse_ok(src: str) -> bool:
    from app.services.verilog_validation import read_verilog_grammar, _load_lark_module

    g = read_verilog_grammar()
    L = _load_lark_module()
    parser = L.Lark(g, parser="lalr", lexer="basic", maybe_placeholders=False)
    try:
        parser.parse(src)
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def tok_and_processor():
    pytest.importorskip("syncode")
    from transformers import AutoTokenizer
    from syncode import SyncodeLogitsProcessor
    from syncode.parsers.grammars.grammar import Grammar

    tok = AutoTokenizer.from_pretrained(MODEL_NAME, local_files_only=True)
    grammar = Grammar(GRAMMAR_PATH.read_text(encoding="utf-8"))
    assert str(grammar.hash()) == "9039611849", (
        f"unexpected grammar hash {grammar.hash()} — MaskStore mismatch risk"
    )
    processor = SyncodeLogitsProcessor(
        grammar=grammar,
        tokenizer=tok,
        use_cache=True,
        parse_output_only=True,
        num_samples=1,
        mode="grammar_mask",
    )
    return tok, processor, grammar


def _encode_one(tok, text: str) -> int | None:
    ids = tok.encode(text, add_special_tokens=False)
    if len(ids) != 1:
        return None
    return int(ids[0])


def _mask_at(processor, tok, prefix: str) -> torch.Tensor:
    processor.reset()
    ge = processor.grammar_engine
    ge.start_from = 0
    ids = tok(prefix, return_tensors="pt", add_special_tokens=False).input_ids
    if ids.numel() == 0:
        # Empty prefix: use a dummy BOS-less empty batch of shape [1,0] if supported
        ids = torch.zeros((1, 0), dtype=torch.long)
    scores = torch.zeros((1, len(tok)), dtype=torch.float32)
    out = processor(ids, scores.clone())
    return out.squeeze(0)


def test_A_keywords_lex_distinct_from_identifier():
    from app.services.verilog_validation import read_verilog_grammar, _load_lark_module

    g = read_verilog_grammar()
    L = _load_lark_module()
    parser = L.Lark(g, parser="lalr", lexer="basic", maybe_placeholders=False)
    expected = {
        "module": "MODULE",
        "endmodule": "ENDMODULE",
        "input": "INPUT",
        "output": "OUTPUT",
        "inout": "INOUT",
        "wire": "WIRE",
        "reg": "REG",
        "assign": "ASSIGN",
        "always": "ALWAYS",
        "begin": "BEGIN",
        "end": "END",
        "if": "IF",
        "else": "ELSE",
    }
    for word, term in expected.items():
        toks = list(parser.lex(word))
        assert len(toks) == 1 and toks[0].type == term, (word, toks)
    for word in ("input_sig", "wire_x", "reg0", "module_en"):
        toks = list(parser.lex(word))
        assert len(toks) == 1 and toks[0].type == "IDENTIFIER", word


def test_B_reference_programs_parse():
    for name, src in REF_PROGRAMS.items():
        assert _lark_parse_ok(src), f"Lark reject: {name}"


def test_C_token_replay_keeps_gold_finite(tok_and_processor):
    tok, processor, _grammar = tok_and_processor
    for name, src in REF_PROGRAMS.items():
        processor.reset()
        ge = processor.grammar_engine
        ge.start_from = 0
        # Replay by growing character prefixes at token boundaries.
        ids = tok.encode(src, add_special_tokens=False)
        assert ids, name
        for i in range(len(ids)):
            prefix_ids = ids[:i]
            next_id = ids[i]
            if prefix_ids:
                prefix_tensor = torch.tensor([prefix_ids], dtype=torch.long)
            else:
                prefix_tensor = torch.zeros((1, 0), dtype=torch.long)
            scores = torch.zeros((1, len(tok)), dtype=torch.float32)
            out = processor(prefix_tensor, scores.clone()).squeeze(0)
            assert torch.isfinite(out[next_id]), (
                f"{name}: gold token id={next_id} "
                f"text={tok.decode([next_id])!r} masked at prefix_len={i} "
                f"prefix={tok.decode(prefix_ids)!r}"
            )
            # Commit token into SynCode state by advancing ids for next call —
            # processor is stateful via full input_ids each time.
        # Final full source must still Lark-parse.
        assert _lark_parse_ok(src), name


def test_D_invalid_punctuation_in_port_list_masked(tok_and_processor):
    """SynCode must mask tokens that cannot extend IDENTIFIER / port syntax."""
    tok, processor, _ = tok_and_processor
    prefix = "module m("
    out = _mask_at(processor, tok, prefix)
    for bad in (";", "=", "+", ")", "endmodule"):
        tid = _encode_one(tok, bad)
        if tid is None:
            continue
        # ')' can be valid for empty port list "module m()" — skip.
        if bad == ")":
            continue
        assert not torch.isfinite(out[tid]), (
            f"expected {bad!r} masked after {prefix!r}, tid={tid}"
        )
    # Ordinary identifier port should remain possible (single-token forms).
    for good in ("a", "x"):
        tid = _encode_one(tok, good)
        if tid is None:
            continue
        assert torch.isfinite(out[tid]), f"expected {good!r} finite after {prefix!r}"


def test_D2_keyword_overapprox_documented(tok_and_processor):
    """
    Document SynCode IDENTIFIER-DFA overapprox: exact keyword strings still
    match the IDENTIFIER regex, so tokens like 'input' stay finite when the
    parser expects IDENTIFIER (port name). Lark would re-lex them as INPUT.
    This is the universal grammar/MaskStore gap (no firewall in this stack).
    """
    tok, processor, _ = tok_and_processor
    prefix = "module m("
    out = _mask_at(processor, tok, prefix)
    tid = _encode_one(tok, "input")
    if tid is None:
        pytest.skip("tokenizer does not encode 'input' as one token")
    # Expected current behavior: finite (overapprox). If this ever becomes
    # masked, keyword/IDENTIFIER separation in the MaskStore improved.
    assert torch.isfinite(out[tid]), (
        "unexpected: 'input' masked in port list — update this doc-test"
    )

def test_E_after_decls_assign_or_endmodule_allowed(tok_and_processor):
    tok, processor, _ = tok_and_processor
    prefix = "module m(a, y);\ninput a;\noutput y;\n"
    out = _mask_at(processor, tok, prefix)
    for good in ("assign", " wire", "endmodule", "\n"):
        tid = _encode_one(tok, good)
        if tid is None:
            continue
        assert torch.isfinite(out[tid]), f"{good!r} should be finite"


def test_F_no_raw_logit_selection_helpers_in_service():
    """Static guard: llm_service must not re-enable raw selection after SynCode."""
    text = (
        Path(__file__).resolve().parents[1]
        / "app"
        / "services"
        / "llm_service.py"
    ).read_text(encoding="utf-8")
    assert "using raw selection" not in text
    assert "falling back to raw logits" not in text
    assert "continue with raw logits" not in text
    assert "masked_logits = last_logits.clone()" not in text
