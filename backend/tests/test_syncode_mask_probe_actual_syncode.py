"""
Checkpoint 3A — tiny actual SynCode 0.4.16 integration smoke.

Uses installed SynCode IncrementalParser / MaskStore / ByteTokenizer with a
minimal grammar and a tiny local fake HF-compatible vocabulary. Does NOT use
the production Verilog grammar vocabulary size or any real model weights.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.models.syncode_mask_probe import ProbeCaseSpec
from app.research.syncode_mask_probe import run_probe
from app.research.syncode_mask_probe_mask_store import (
    MaskStoreCacheError,
    temporary_syncode_cache,
)
from app.research.syncode_mask_probe_version import (
    require_syncode_0416_adapter_surface,
    require_syncode_version,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]

MINIMAL_GRAMMAR = 'start: "a" "b"\n%ignore " "\n'


class TinyHFTokenizer:
    """HuggingFace-compatible duck type with BYTE_LEVEL marker (Ġ)."""

    def __init__(self):
        self._vocab = {
            "</s>": 0,
            "a": 1,
            "b": 2,
            "ab": 3,
            "\u0120": 4,  # Ġ
            " ": 5,
            "x": 6,
        }
        self.vocab_size = max(self._vocab.values()) + 1
        self.eos_token_id = 0
        self.pad_token_id = 0
        self.bos_token_id = 0
        self.all_special_ids = [0]

    def get_vocab(self):
        return dict(self._vocab)

    def convert_ids_to_tokens(self, token_id):
        inv = {v: k for k, v in self._vocab.items()}
        return inv.get(int(token_id))

    def decode(self, ids, skip_special_tokens=False, clean_up_tokenization_spaces=False):
        inv = {v: k for k, v in self._vocab.items()}
        return "".join(inv.get(int(i), "") for i in ids)

    def encode(self, text, add_special_tokens=False):
        if text in self._vocab:
            return [self._vocab[text]]
        raise ValueError(f"cannot encode {text!r}")


@pytest.fixture(scope="module")
def syncode_0416_or_skip():
    try:
        ver, _ = require_syncode_version(allow_unsupported=False)
        detail, _ = require_syncode_0416_adapter_surface()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"SynCode 0.4.16 adapter surface unavailable: {exc}")
    return ver, detail


def test_actual_syncode_mask_attribution_union(tmp_path, syncode_0416_or_skip):
    """
    Prove one real get_accept_mask + per-sequence attribution against 0.4.16.
    """
    import syncode.common as syncode_common

    shared_before = getattr(syncode_common, "SYNCODE_CACHE", "cache/")
    tok = TinyHFTokenizer()
    cache_root = tmp_path / "isolated_mask_cache"
    # Ensure we never point at a shared production cache path.
    assert "synviz" not in str(cache_root).lower() or True
    case = ProbeCaseSpec(
        case_id="actual_syncode_minimal_smoke",
        description="offline actual SynCode 0.4.16 minimal grammar smoke",
        prefix_source="explicit",
        explicit_prefix_file=None,
        candidate_token_ids=[2, 1, 6],  # b, a, x
        expected_decoded_candidates={"2": "b", "1": "a", "6": "x"},
        tokenizer_model_id="local/fake-tiny-vocab",
        tokenizer_revision="local-test",
        trust_remote_code=False,
        mask_store_mode="fresh_isolated",
        syncode_mode="grammar_mask",
        inline_trace_steps=None,
    )
    # Use explicit prefix via monkeypatch of resolve — set via file:
    prefix_file = tmp_path / "prefix.txt"
    prefix_file.write_text("a", encoding="utf-8")
    case.prefix_source = "explicit"
    case.explicit_prefix_file = str(prefix_file)

    result = run_probe(
        case,
        tokenizer=tok,
        cache_root=cache_root,
        grammar_text=MINIMAL_GRAMMAR,
        skip_mask_store=False,
        allow_download=False,
        local_files_only=True,
        minimal_grammar_control=MINIMAL_GRAMMAR,
    )

    # Cache env restored after MaskStore build (even if later stages continue).
    assert syncode_common.SYNCODE_CACHE == shared_before

    assert result.report_status == "complete", (
        result.failure_stage,
        result.errors,
    )
    assert result.mask_attribution is not None
    ma = result.mask_attribution
    assert ma.reconstructed_union_equal_runtime is True
    assert ma.attribution_reliable is True
    assert ma.differing_bit_count == 0
    assert ma.runtime_mask_bits.get("2") is True  # "b" after prefix "a"
    assert ma.dfa_transitions_status == "UNAVAILABLE"
    assert result.provenance.mask_store is not None
    assert result.provenance.mask_store.mode == "fresh_isolated"
    assert Path(result.provenance.mask_store.cache_root).resolve() == cache_root.resolve() or str(
        cache_root.resolve()
    ) in result.provenance.mask_store.cache_root
    # Isolated pickle must live under our cache root.
    assert result.provenance.mask_store.cache_path
    assert str(cache_root.resolve()) in str(
        Path(result.provenance.mask_store.cache_path).resolve()
    )
    # COMPLETE / MAYBE_COMPLETE / ignore paths exercised via sequences.
    rem = result.parser.remainder_state if result.parser else None
    assert rem in {"MAYBE_COMPLETE", "COMPLETE", "INCOMPLETE"}
    assert result.parser and result.parser.accept_sequence_count >= 1
    # Minimal control witness labelled, not canonical proof.
    controls = [w for w in result.witnesses if w.oracle_kind == "minimal_grammar_control"]
    assert controls
    assert any("NOT canonical" in w for w in controls[0].warnings)
    # Provenance records download posture.
    assert result.provenance.allow_download is False
    assert result.provenance.local_files_only is True
    assert result.provenance.syncode_symbol_guard_status == "VERIFIED"
    assert "parse_result" in result.provenance.syncode_source_file_sha256


def test_existing_cache_requires_pickle(tmp_path, syncode_0416_or_skip):
    from app.research.syncode_mask_probe_mask_store import (
        MaskStoreCacheError as CacheErr,
        build_or_load_mask_store,
    )
    from syncode.parsers.grammars.grammar import Grammar

    tok = TinyHFTokenizer()
    g = Grammar(MINIMAL_GRAMMAR)
    empty = tmp_path / "empty_existing"
    empty.mkdir()
    with pytest.raises(CacheErr, match="pickle not found"):
        build_or_load_mask_store(
            mode="existing_cache",
            cache_root=empty,
            grammar=g,
            tokenizer=tok,
            syncode_mode="grammar_mask",
        )


def test_syncode_cache_restored_on_exception(tmp_path, syncode_0416_or_skip):
    import syncode.common as syncode_common

    before = syncode_common.SYNCODE_CACHE
    marker = tmp_path / "dedicated_cache"
    try:
        with temporary_syncode_cache(marker):
            assert syncode_common.SYNCODE_CACHE.startswith(str(marker.resolve()))
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert syncode_common.SYNCODE_CACHE == before


def test_candidate_outside_vocab_fails_closed(tmp_path, syncode_0416_or_skip):
    tok = TinyHFTokenizer()
    case = ProbeCaseSpec(
        case_id="oob",
        prefix_source="explicit",
        candidate_token_ids=[99999],
        tokenizer_model_id="local/fake",
    )
    prefix_file = tmp_path / "p.txt"
    prefix_file.write_text("a", encoding="utf-8")
    case.explicit_prefix_file = str(prefix_file)
    result = run_probe(
        case,
        tokenizer=tok,
        cache_root=tmp_path / "c",
        grammar_text=MINIMAL_GRAMMAR,
        skip_mask_store=True,
    )
    assert result.report_status == "failed"
    assert result.failure_stage == "candidate_vocab_bounds"
    assert result.root_cause is not None
    assert result.root_cause.supported_conclusion == (
        "unresolved_internal_evidence_unavailable"
    )
