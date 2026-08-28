"""Unit tests for provenance and normalized experiment schemas (Phase 2A.1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.normalized import (
    NORMALIZED_SCHEMA_VERSION,
    NormalizedExperiment,
    NormalizedPromptResult,
    NormalizedTraceStep,
)
from app.models.provenance import Prov, ProvenanceKind
from app.models.schemas import DecodingStep, ExperimentResult, GenerateRequest


def test_recorded_value_allows_false_zero_and_empty_list():
    b = Prov[bool].recorded(False, source_file="traces/x.json", source_field="flag")
    assert b.value is False
    assert b.provenance.kind == ProvenanceKind.recorded
    assert not b.is_unavailable

    z = Prov[int].recorded(0, source_field="count")
    assert z.value == 0

    empty = Prov[list[str]].recorded([], source_field="accept_sequences")
    assert empty.value == []
    assert empty.provenance.kind == ProvenanceKind.recorded


def test_derived_value():
    p = Prov[str].derived(
        "module m;",
        method="accumulate selected_token",
        source_file="traces/p.json",
    )
    assert p.value == "module m;"
    assert p.provenance.kind == ProvenanceKind.derived
    assert p.provenance.method == "accumulate selected_token"


def test_recomputed_value_with_grammar_hash():
    p = Prov[str].recomputed(
        "complete",
        method="lark_parse",
        grammar_sha256="1d4dc2bccf39f3e591e3dc59834c1c17b33b3f27d00a7ddd8810c795510cc4ef",
    )
    assert p.value == "complete"
    assert p.provenance.kind == ProvenanceKind.recomputed
    assert p.provenance.grammar_sha256.startswith("1d4dc2")


def test_unavailable_field():
    p = Prov[float].unavailable(method="entropy_before absent")
    assert p.value is None
    assert p.is_unavailable
    assert p.provenance.kind == ProvenanceKind.unavailable


def test_unavailable_rejects_non_null_value():
    with pytest.raises(ValidationError):
        Prov[float](
            value=0.0,
            provenance={"kind": "unavailable", "method": "bad"},
        )


def test_unavailable_differs_from_false_zero_and_empty_list():
    missing_bool = Prov[bool].unavailable(method="mask intervention unknown")
    recorded_false = Prov[bool].recorded(False)
    assert missing_bool.value is None
    assert recorded_false.value is False
    assert missing_bool.provenance.kind != recorded_false.provenance.kind

    missing_entropy = Prov[float].unavailable()
    recorded_zero = Prov[float].recorded(0.0)
    assert missing_entropy.value is None
    assert recorded_zero.value == 0.0

    missing_seqs = Prov[list[str]].unavailable(method="accept sequences absent")
    recorded_empty = Prov[list[str]].recorded([])
    assert missing_seqs.value is None
    assert recorded_empty.value == []


def test_optional_trace_fields_default_unavailable():
    step = NormalizedTraceStep(step_index=1)
    assert step.entropy_before.is_unavailable
    assert step.syncode_accept_sequences.is_unavailable
    assert step.expected_terminals.is_unavailable
    assert step.masking_changed_selection.is_unavailable
    assert step.eos_eligible.is_unavailable
    assert step.entropy_before.value is None


def test_normalized_experiment_and_prompt_defaults():
    exp = NormalizedExperiment(
        experiment_id="imp-1",
        source_type="imported",
        prompt_results=[
            NormalizedPromptResult(problem_id="Prob004_vector2"),
        ],
    )
    assert exp.schema_version == NORMALIZED_SCHEMA_VERSION  # 4A.2
    assert exp.source_type == "imported"
    pr = exp.prompt_results[0]
    assert pr.grammar_verdict.is_unavailable
    assert pr.generated_output.is_unavailable
    assert pr.grammar_verdict.value is None  # not False


def test_live_schemas_remain_importable():
    step = DecodingStep(step=1, context="")
    assert step.selected_token == ""
    exp = ExperimentResult(
        experiment_id="live-1",
        prompt="write a mux",
        mode="syncode",
    )
    assert exp.final_parse_valid is False  # live schema default; unchanged
    req = GenerateRequest(prompt="hello", use_syncode=True)
    assert req.prompt == "hello"
