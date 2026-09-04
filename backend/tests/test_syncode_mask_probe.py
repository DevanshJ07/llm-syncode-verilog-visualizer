"""
Checkpoint 3A — SynCode mask diagnostic probe tests.

Local fakes only. No network, no real model, no production Verilog mask store.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch

from app.core.grammar import EXPECTED_GRAMMAR_SHA256, grammar_sha256
from app.models.syncode_mask_probe import ProbeCaseSpec
from app.research.syncode_mask_probe_attribution import attribute_mask
from app.research.syncode_mask_probe_oracle import (
    constructive_canonical_witness,
    minimal_grammar_control_witness,
)
from app.research.syncode_mask_probe_prefix import (
    ProbeCaseError,
    prefix_metrics,
    reconstruct_prefix_from_selected_tokens,
    resolve_case_prefix,
    sha256_utf8,
)
from app.research.syncode_mask_probe_report import build_root_cause, render_markdown_report
from app.research.syncode_mask_probe_tokenizer import collect_tokenizer_candidate_evidence
from app.research.syncode_mask_probe_version import (
    SyncodeVersionError,
    get_installed_syncode_version,
    require_syncode_version,
)
from app.research import syncode_mask_probe as probe_mod


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _load_compare_mod():
    import importlib.util

    path = BACKEND_ROOT / "scripts" / "compare_syncode_mask_probes.py"
    spec = importlib.util.spec_from_file_location("compare_syncode_mask_probes", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


compare_mod = _load_compare_mod()


class TinyFakeTokenizer:
    """Minimal tokenizer duck-type for offline probe tests."""

    def __init__(self, vocab: dict[str, int], *, eos_id: int = 0):
        self._vocab = dict(vocab)
        self._id_to_tok = {i: t for t, i in self._vocab.items()}
        self.vocab_size = max(self._vocab.values()) + 1 if self._vocab else 0
        self.eos_token_id = eos_id
        self.pad_token_id = eos_id
        self.bos_token_id = eos_id
        self.all_special_ids = [eos_id]

    def get_vocab(self):
        return dict(self._vocab)

    def convert_ids_to_tokens(self, token_id):
        return self._id_to_tok.get(int(token_id))

    def decode(self, ids, skip_special_tokens=False, clean_up_tokenization_spaces=False):
        parts = []
        for i in ids:
            t = self._id_to_tok.get(int(i), "")
            parts.append(t)
        return "".join(parts)

    def encode(self, text, add_special_tokens=False):
        # Greedy longest-match over vocab strings.
        out = []
        i = 0
        items = sorted(self._vocab.items(), key=lambda kv: -len(kv[0]))
        while i < len(text):
            matched = False
            for tok, tid in items:
                if text.startswith(tok, i):
                    out.append(tid)
                    i += len(tok)
                    matched = True
                    break
            if not matched:
                raise ValueError(f"cannot encode at {text[i]!r}")
        return out


class FakeAcceptSequence(list):
    def __init__(self, terminals):
        self.accept_terminals = tuple(terminals)
        super().__init__(terminals)


class FakeParseResult:
    def __init__(self, accept_sequences, remainder, remainder_state, function_end=False):
        self.accept_sequences = accept_sequences
        self.remainder = remainder
        self.remainder_state = remainder_state
        self.next_ac_indents = None
        self.function_end = function_end


class FakeRemainderState:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return getattr(other, "name", other) == self.name


class FakeLookupTable:
    def __init__(self, vocab_size: int, masks: dict[str, torch.Tensor]):
        self._vocab_size = vocab_size
        self._masks = masks
        self._default = torch.zeros(vocab_size, dtype=torch.bool)

    def _get_default_mask(self):
        return self._default.clone()

    def complete_case_lookup(self, fsm_state):
        return self._masks.get("complete", self._default).clone()

    def incomplete_case_lookup(self, fsm_state):
        return self._masks.get("incomplete", self._default).clone()

    def fsm_state_and_next_terminal_to_tokens(self, fsm_state, next_terminal):
        key = f"next:{next_terminal}"
        return self._masks.get(key, self._default).clone()


class FakeFSMState:
    def __init__(self, terminal, state_id=0):
        self.terminal = terminal
        self.state_id = state_id


class FakeMaskStore:
    def __init__(self, vocab_size: int, runtime_mask: torch.Tensor, seq_masks: dict[str, torch.Tensor]):
        self.eos_token_id = 0
        self._mode = "grammar_mask"
        self._runtime = runtime_mask
        self._lookup_table = FakeLookupTable(vocab_size, seq_masks)
        self._fsms = SimpleNamespace(
            is_final=lambda s: True,
            initial=lambda term: FakeFSMState(term, 0),
        )
        self.byte_tokenizer = SimpleNamespace(
            decode=lambda ids, skip_special_tokens=False: str(ids[0]).encode("utf-8"),
            vocab_type=SimpleNamespace(name="RAW"),
        )

    def get_accept_mask(self, r):
        return self._runtime.clone()

    def get_fsm_states(self, r):
        # One state per first terminal in sequences
        seen = []
        for seq in r.accept_sequences:
            t0 = seq[0]
            if t0 not in seen:
                seen.append(t0)
        return [FakeFSMState(t) for t in seen]

    def _lookup_next_tokens_for_fsm_state(self, fsm_state, next_terminal):
        return self._lookup_table.fsm_state_and_next_terminal_to_tokens(
            fsm_state, next_terminal
        )


def test_grammar_sha_unchanged():
    assert grammar_sha256() == EXPECTED_GRAMMAR_SHA256


def test_syncode_version_guard():
    ver = get_installed_syncode_version()
    if ver == "0.4.16":
        v, override = require_syncode_version(allow_unsupported=False)
        assert v == "0.4.16" and override is False
    # Force fail-closed path via monkeypatch
    import app.research.syncode_mask_probe_version as vg

    old = vg.get_installed_syncode_version
    vg.get_installed_syncode_version = lambda: "9.9.9"
    try:
        with pytest.raises(SyncodeVersionError):
            require_syncode_version(allow_unsupported=False)
        v, override = require_syncode_version(allow_unsupported=True)
        assert v == "9.9.9" and override is True
    finally:
        vg.get_installed_syncode_version = old


def test_multiline_prefix_preservation_and_metrics():
    steps = [
        {"selected_token": "module"},
        {"selected_token": " "},
        {"selected_token": "t"},
        {"selected_token": ";\n"},
        {"selected_token": "  "},
        {"selected_token": "assign"},
    ]
    # before step 4 → through ";\n"
    prefix = reconstruct_prefix_from_selected_tokens(steps, step_index=4)
    assert prefix == "module t;\n"
    assert "\n" in prefix
    assert not prefix.endswith(" ")
    # spaces preserved at step 5 before
    prefix2 = reconstruct_prefix_from_selected_tokens(steps, step_index=5)
    assert prefix2 == "module t;\n  "
    assert prefix2.endswith("  ")
    m = prefix_metrics(prefix2)
    assert m["prefix_character_count"] == len(prefix2)
    assert m["prefix_utf8_byte_count"] == len(prefix2.encode("utf-8"))
    assert m["prefix_sha256_utf8"] == sha256_utf8(prefix2)


def test_no_trimming_of_newline_space_and_id_mismatch_fails():
    case = ProbeCaseSpec(
        case_id="t",
        step_index=1,
        prefix_source="reconstructed_from_selected_tokens",
        inline_trace_steps=[
            {"selected_token": "\n", "selected_token_id": 1},
            {"selected_token": " ", "selected_token_id": 2},
        ],
        selected_token_id=99,
    )
    with pytest.raises(ProbeCaseError, match="selected_token_id mismatch"):
        resolve_case_prefix(case)


def test_tokenizer_candidate_serialization():
    tok = TinyFakeTokenizer({"\n": 10, ",\n": 11, " ": 12}, eos_id=0)
    ev = collect_tokenizer_candidate_evidence(
        tok, 10, expected_decode="\n", original_trace_token_text="\n"
    )
    assert ev.decode_cleanup_disabled == "\n"
    assert ev.unicode_codepoints == [10]
    assert ev.utf8_bytes == [10]
    assert ev.trace_text_equals_decode is True
    assert ev.decode_repr == repr("\n")


def test_constructive_witness_success_and_boundary():
    prefix = "module t(input a, output b);\n  assign b = a"
    # missing semicolon then endmodule
    w = constructive_canonical_witness(
        prefix=prefix,
        candidate_decoded_text=";\n",
        completion_suffix="endmodule\n",
    )
    assert w.candidate_at_exact_boundary is True
    assert w.canonical_lark_parse_success is True
    assert w.grammar_sha256 == EXPECTED_GRAMMAR_SHA256
    assert w.oracle_kind == "constructive_canonical"

    bad = constructive_canonical_witness(
        prefix=prefix,
        candidate_decoded_text="@",
        completion_suffix="\nendmodule\n",
    )
    assert bad.canonical_lark_parse_success is False


def test_minimal_grammar_control_labelled():
    g = 'start: "ab"\n'
    w = minimal_grammar_control_witness(
        prefix="a",
        candidate_decoded_text="b",
        completion_suffix="",
        minimal_grammar_text=g,
    )
    assert w.oracle_kind == "minimal_grammar_control"
    assert "NOT canonical" in w.warnings[0]
    assert w.canonical_lark_parse_success is True


def test_attribution_union_equals_runtime_and_mismatch_unreliable():
    from syncode.parse_result import RemainderState

    vocab = 8
    # Sequence A admits token 3; sequence B admits token 5; union admits 3 and 5.
    mask_a = torch.zeros(vocab, dtype=torch.bool)
    mask_a[3] = True
    mask_b = torch.zeros(vocab, dtype=torch.bool)
    mask_b[5] = True
    runtime = mask_a | mask_b

    store = FakeMaskStore(
        vocab,
        runtime_mask=runtime,
        seq_masks={
            "complete": mask_a,  # used for COMPLETE seqs matching fsm
        },
    )
    # Make complete lookup return different masks per call by using incomplete path:
    # Simpler: set runtime equal to reconstructed via single COMPLETE sequence.
    seqs = [FakeAcceptSequence(["SEMICOLON"])]
    # Override: both sequences use complete_case_lookup → same mask_a; adjust runtime=mask_a
    store2 = FakeMaskStore(vocab, runtime_mask=mask_a.clone(), seq_masks={"complete": mask_a})
    pr = FakeParseResult(seqs, b"", RemainderState.COMPLETE)
    # Need fsm state terminal == SEMICOLON
    store2.get_fsm_states = lambda r: [FakeFSMState("SEMICOLON")]
    ev = attribute_mask(store2, pr, candidate_token_ids=[3, 5, 1])
    assert ev.runtime_mask_bits["3"] is True
    assert ev.runtime_mask_bits["5"] is False
    assert ev.reconstructed_union_equal_runtime is True
    assert ev.attribution_reliable is True

    # Mismatch: runtime claims token 5 but reconstruction cannot
    store3 = FakeMaskStore(
        vocab, runtime_mask=runtime.clone(), seq_masks={"complete": mask_a}
    )
    store3.get_fsm_states = lambda r: [FakeFSMState("SEMICOLON")]
    ev_bad = attribute_mask(store3, pr, candidate_token_ids=[3, 5])
    assert ev_bad.attribution_reliable is False
    assert ev_bad.reconstructed_union_equal_runtime is False
    assert any("unreliable" in w.lower() for w in ev_bad.warnings)


def test_parser_accept_sequences_not_truncated(monkeypatch):
    """Full accept-sequence list is retained (no 64-path cap)."""
    from app.research.syncode_mask_probe_parser import collect_parser_evidence
    from syncode.parse_result import RemainderState

    class FakeInc:
        def reset(self):
            return None

        def get_acceptable_next_terminals(self, prefix):
            seqs = [FakeAcceptSequence([f"T{i}"]) for i in range(80)]
            return FakeParseResult(seqs, "", RemainderState.COMPLETE)

    # extract_parser_terminal_sets may fail on FakeInc — patch it.
    import app.research.syncode_mask_probe_parser as pe

    monkeypatch.setattr(
        pe,
        "extract_parser_terminal_sets",
        lambda p: {
            "current_accept_terminals": [],
            "next_accept_terminals": [f"T{i}" for i in range(80)],
            "ignore_terminals": [],
        },
    )
    ev = collect_parser_evidence(FakeInc(), "module", syncode_version="0.4.16")
    assert ev.accept_sequence_count == 80
    assert ev.truncated_for_storage is False
    assert len(ev.accept_sequences) == 80


def test_remainder_exact_suffix_validation(monkeypatch):
    from app.research.syncode_mask_probe_parser import collect_parser_evidence
    from syncode.parse_result import RemainderState

    class FakeInc:
        def reset(self):
            return None

        def get_acceptable_next_terminals(self, prefix):
            return FakeParseResult(
                [FakeAcceptSequence(["X"])], "xyz", RemainderState.INCOMPLETE
            )

    import app.research.syncode_mask_probe_parser as pe

    monkeypatch.setattr(
        pe,
        "extract_parser_terminal_sets",
        lambda p: {
            "current_accept_terminals": ["X"],
            "next_accept_terminals": [],
            "ignore_terminals": [],
        },
    )
    # prefix does not end with remainder
    ev = collect_parser_evidence(FakeInc(), "abc", syncode_version="0.4.16")
    assert ev.fixed_prefix_status == "UNAVAILABLE"
    assert ev.fixed_prefix is None


def test_compare_probes_environment_warning():
    a = {
        "case": {"case_id": "x"},
        "prefix_sha256_utf8": "aaa",
        "provenance": {
            "syncode_version": "0.4.16",
            "tokenizer_model_id": "modelA",
            "grammar_sha256": "g",
            "mask_store": {"mode": "existing_cache"},
        },
        "mask_attribution": {"runtime_mask_bits": {"10": True}},
    }
    b = {
        "case": {"case_id": "x"},
        "prefix_sha256_utf8": "aaa",
        "provenance": {
            "syncode_version": "0.4.16",
            "tokenizer_model_id": "modelB",
            "grammar_sha256": "g",
            "mask_store": {"mode": "fresh_isolated"},
        },
        "mask_attribution": {"runtime_mask_bits": {"10": False}},
    }
    out = compare_mod.compare_probes(a, b)
    assert out["candidate_decision_differences"]["10"]["a"] is True
    assert out["difference_may_be_environment_not_cache_mode"] is True


def test_deterministic_json_markdown(tmp_path):
    from app.models.syncode_mask_probe import SyncodeMaskProbeResult

    case = ProbeCaseSpec(case_id="det_case", candidate_token_ids=[1])
    result = SyncodeMaskProbeResult(
        case=case,
        prefix_text="ab\n",
        prefix_sha256_utf8=sha256_utf8("ab\n"),
        prefix_character_count=3,
        prefix_utf8_byte_count=3,
    )
    result.root_cause = build_root_cause(result)
    j1 = result.model_dump_json(indent=2)
    j2 = result.model_dump_json(indent=2)
    assert j1 == j2
    md = render_markdown_report(result)
    assert "det_case" in md
    assert "Decision sequence" in md


def test_production_modules_do_not_import_probe():
    """Static check: production entrypoints must not import app.research."""
    targets = [
        BACKEND_ROOT / "main.py",
        BACKEND_ROOT / "app" / "services" / "llm_service.py",
        BACKEND_ROOT / "app" / "services" / "generation_runner.py",
        BACKEND_ROOT / "app" / "api" / "routes" / "generate.py",
        BACKEND_ROOT / "app" / "api" / "routes" / "experiments.py",
    ]
    for path in targets:
        if not path.is_file():
            continue
        src = path.read_text(encoding="utf-8")
        assert "app.research" not in src
        assert "syncode_mask_probe" not in src or path.name.startswith("test")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("app.research")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "research" not in alias.name.split(".")


def test_run_probe_skip_mask_store_offline(tmp_path):
    tok = TinyFakeTokenizer(
        {"module": 1, " ": 2, "t": 3, ";\n": 4, "\n": 5}, eos_id=0
    )
    case = ProbeCaseSpec(
        case_id="offline_skip",
        step_index=3,
        prefix_source="reconstructed_from_selected_tokens",
        inline_trace_steps=[
            {"selected_token": "module", "selected_token_id": 1},
            {"selected_token": " ", "selected_token_id": 2},
            {"selected_token": "t", "selected_token_id": 3},
            {"selected_token": ";\n", "selected_token_id": 4},
        ],
        candidate_token_ids=[5, 4],
        expected_decoded_candidates={"5": "\n", "4": ";\n"},
        selected_token_id=4,
        witness_completion_suffix="endmodule\n",
        tokenizer_model_id="fake/local",
    )
    result = probe_mod.run_probe(
        case,
        tokenizer=tok,
        cache_root=tmp_path / "cache",
        skip_mask_store=True,
        allow_download=False,
        local_files_only=True,
    )
    assert result.report_status == "complete"
    assert result.prefix_text == "module t"
    assert result.parser is not None
    assert result.mask_attribution is None
    assert any(w.oracle_kind == "constructive_canonical" for w in result.witnesses)
    assert result.provenance.allow_download is False
    assert result.provenance.local_files_only is True
    json_path, md_path = probe_mod.write_probe_outputs(result, tmp_path / "out")
    assert json_path.is_file() and md_path.is_file()
    loaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert loaded["case"]["case_id"] == "offline_skip"
    assert loaded["report_status"] == "complete"


def test_templates_require_fill_placeholders():
    p = BACKEND_ROOT / "research_cases" / "nemotron_newline_step24.template.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert "<FILL" in data["tokenizer_model_id"]
    assert "<FILL" in data["tokenizer_revision"]
    assert "<FILL" in data["source_trace_path"]
    assert "<FILL" in data["witness_completion_suffix"]
    assert data["prompt_id"] == "Prob004_vector2"
    assert data["step_index"] == 24
    assert data["step_index_unit"] == "zero_based"
    assert data["raw_argmax_token_id"] == 1010
    assert data["expected_decoded_candidates"]["1010"] == "\n"
    assert data["selected_token_id"] == 1520
    assert data["expected_decoded_candidates"]["1520"] == ",\n"
    p2 = BACKEND_ROOT / "research_cases" / "nemotron_base_literal_step65.template.json"
    data2 = json.loads(p2.read_text(encoding="utf-8"))
    assert "<FILL" in data2["source_trace_path"]
    assert data2["tokenizer_model_id"].startswith("nvidia/")
    assert data2["prompt_id"] == "Prob126_circuit6"
    assert data2["step_index"] == 65
    assert data2["raw_argmax_token_id"] == 6782
    assert data2["expected_decoded_candidates"]["6782"] == "'h"
    assert data2["selected_token_id"] == 56257
    assert data2["expected_decoded_candidates"]["56257"] == "'ha"
    assert "<FILL" in data2["candidate_witness_suffixes"]["6782"]


def test_backend_startup_does_not_load_research_package():
    """Production entry modules must not reference the research probe package."""
    import app.services.llm_service as llm_mod
    import main as main_mod

    for mod in (llm_mod, main_mod):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "app.research" not in src
        assert "syncode_mask_probe" not in src
    # Importing them must not require loading the probe (may already be loaded
    # by earlier tests; absence of references above is the hard gate).
    assert getattr(llm_mod, "llm_service", None) is not None
    assert getattr(main_mod, "app", None) is not None


def test_failed_probe_writes_incomplete_status(tmp_path):
    tok = TinyFakeTokenizer({"a": 1, "b": 2}, eos_id=0)
    case = ProbeCaseSpec(
        case_id="fail_oob",
        prefix_source="explicit",
        candidate_token_ids=[99],
        tokenizer_model_id="fake/local",
    )
    prefix_file = tmp_path / "p.txt"
    prefix_file.write_text("a", encoding="utf-8")
    case.explicit_prefix_file = str(prefix_file)
    result = probe_mod.run_probe(
        case, tokenizer=tok, cache_root=tmp_path / "c", skip_mask_store=True
    )
    assert result.report_status == "failed"
    assert result.failure_stage == "candidate_vocab_bounds"
    json_path, _ = probe_mod.write_probe_outputs(result, tmp_path / "out")
    loaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert loaded["report_status"] == "failed"
    assert loaded["root_cause"]["supported_conclusion"] == (
        "unresolved_internal_evidence_unavailable"
    )
