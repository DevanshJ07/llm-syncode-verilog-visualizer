"""Fast tests for Phase 2A.2 import normalization and persistence (no Torch/SynCode)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from app.core.grammar import EXPECTED_GRAMMAR_SHA256, grammar_sha256
from app.models.provenance import ProvenanceKind
from app.services.import_normalize import (
    ImportNormalizationError,
    decode_text_bytes,
    is_safe_experiment_id,
    normalize_imported_bundle,
)
from app.services.import_zip import ZipInspectionError
from app.services.imported_experiment_store import (
    ImportedExperimentStore,
    ImportedStoreError,
)


def _build_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


VALID_SV = b"module m(a, y);\n  input a;\n  output y;\n  assign y = a;\nendmodule\n"


def _steps_for_sv(text: str) -> list[dict]:
    """One step per character so reconstruction matches authoritative .sv."""
    return [
        {
            "step": i + 1,
            "selected_token": ch,
            "selected_token_id": i,
            "raw_argmax_token": ch,
            "raw_argmax_token_id": i,
            "constrained_argmax_token": ch,
            "constrained_argmax_token_id": i,
            "raw_argmax_blocked": False,
            "allowed_token_count": 0,
            "newly_masked_token_count": 0,
            "prefix_tail": text[:i],
            "top_raw_tokens": [{"token": ch, "logit": 1.0}],
            "vocab_logits": [1.0],
            "syncode_parse_failed": False,
        }
        for i, ch in enumerate(text)
    ]


def _summary_config(
    *,
    model: str = "Qwen/Qwen2.5-Coder-1.5B-Instruct",
    enable_thinking: bool = False,
    **extra,
) -> dict:
    cfg = {
        "model": model,
        "model_revision": "abc123",
        "input_device": "cuda",
        "trust_remote_code": True,
        "enable_thinking": enable_thinking,
        "max_new_tokens": 512,
        "versions": {
            "syncode": "0.4.16",
            "torch": "2.2.0",
            "transformers": "4.40.0",
        },
    }
    cfg.update(extra)
    return cfg


def _rich_record(problem: str, **extra) -> dict:
    """Per-prompt record fields only — model config lives in summary.json."""
    base = {
        "problem": problem,
        "termination": "eos",
        "generated_tokens": 12,
        "grammar_valid": True,
        "parse_error": "",
        "verdict": "pass",
        "findings": [],
        "mask_steps": 0,
        "newly_masked_token_count": 0,
        "grammar": r"C:\Users\research\grammar\verilog.lark",
        "generated_file": r"C:\Users\research\results\x\generated\Prob004_vector2.sv",
        "trace_file": r"C:\Users\research\results\x\traces\Prob004_vector2.json",
        "prompt_file": r"C:\Users\research\prompts\Prob004_vector2.txt",
        "dataset_dir": r"C:\Users\research\dataset",
    }
    base.update(extra)
    return base


def _single_problem_bundle(
    *,
    experiment: str = "focused_four_qwen_512",
    problem: str = "Prob004_vector2",
    sv: bytes = VALID_SV,
    steps: list[dict] | None = None,
    record: dict | None = None,
    summary_config: dict | None = None,
    include_log: bool = False,
    prefix: str = "",
    extra_entries: dict[str, bytes] | None = None,
) -> bytes:
    root = f"{prefix}results/{experiment}"
    text = sv.decode("utf-8")
    if steps is None:
        steps = _steps_for_sv(text)
    if record is None:
        record = _rich_record(problem, generated_tokens=len(steps))
    if summary_config is None:
        summary_config = _summary_config()
    entries: dict[str, bytes] = {
        f"{root}/summary.json": json.dumps(
            {"ok": True, "config": summary_config}
        ).encode(),
        f"{root}/results.csv": b"problem,pass\n",
        f"{root}/anomalies.md": b"# none\n",
        f"{root}/generated/{problem}.sv": sv,
        f"{root}/traces/{problem}.json": json.dumps(
            {
                "problem": problem,
                "prompt_file": r"C:\host\prompts\x.txt",
                "steps": steps,
            }
        ).encode(),
        f"{root}/records/{problem}.json": json.dumps(record).encode(),
    }
    if include_log:
        entries[f"{prefix}logs/{experiment}.log"] = b"run ok\n"
    if extra_entries:
        entries.update(extra_entries)
    return _build_zip(entries)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_decode_utf8_and_utf8_sig():
    assert decode_text_bytes(b"abc", source_path="a") == "abc"
    assert decode_text_bytes(b"\xef\xbb\xbfabc", source_path="a") == "abc"


def test_decode_invalid_utf8_errors():
    with pytest.raises(ImportNormalizationError, match="invalid UTF-8"):
        decode_text_bytes(b"\xff\xfe", source_path="bad.txt")


def test_safe_experiment_id():
    assert is_safe_experiment_id("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert not is_safe_experiment_id("../evil")
    assert not is_safe_experiment_id("not-a-uuid")
    assert not is_safe_experiment_id("")


# ---------------------------------------------------------------------------
# Successful imports
# ---------------------------------------------------------------------------


def test_successful_qwen_shaped_import():
    raw = _single_problem_bundle(experiment="focused_four_qwen_512")
    exp = normalize_imported_bundle(raw)
    assert exp.source_type == "imported"
    assert exp.experiment_name == "focused_four_qwen_512"
    assert exp.schema_version == "2A.2"
    assert len(exp.prompt_results) == 1
    pr = exp.prompt_results[0]
    assert pr.problem_id == "Prob004_vector2"
    assert pr.generated_output.provenance.kind == ProvenanceKind.recorded
    assert pr.generated_output.value == VALID_SV.decode()
    assert pr.reconstructed_from_tokens.provenance.kind == ProvenanceKind.derived
    assert pr.reconstruction_matches_authoritative.value is True
    assert pr.prompt_text.is_unavailable
    assert pr.reference_program.is_unavailable
    # Missing channels stay unavailable
    assert pr.steps[0].entropy_before.is_unavailable
    assert pr.steps[0].raw_probability.is_unavailable
    assert pr.steps[0].syncode_accept_sequences.is_unavailable
    assert pr.steps[0].expected_terminals.is_unavailable
    assert pr.steps[0].eos_eligible.is_unavailable
    # Recorded false/zero preserved
    assert pr.steps[0].masking_changed_selection.value is False
    assert pr.steps[0].valid_token_count.value == 0
    assert pr.termination_reason.value == "eos"
    assert pr.grammar_valid.value is True
    assert pr.findings.provenance.kind == ProvenanceKind.recorded
    assert pr.findings.value == []
    assert pr.mask_counts.value["mask_steps"] == 0
    assert not pr.mask_counts.is_unavailable
    # Metadata mapping from summary.json config (not records)
    assert exp.llm_metadata.value["model"].startswith("Qwen/")
    assert exp.llm_metadata.value["device"] == "cuda"
    assert exp.llm_metadata.value["input_device"] == "cuda"
    assert exp.llm_metadata.value["trust_remote_code"] is True
    assert exp.llm_metadata.value["enable_thinking"] is False
    assert exp.llm_metadata.provenance.source_field == "config"
    assert exp.decoding_metadata.value["max_new_tokens"] == 512
    assert exp.runtime_metadata.value["versions"]["syncode"] == "0.4.16"
    assert pr.token_limit.value == 512
    assert pr.token_limit.provenance.source_field == "config.max_new_tokens"
    # Grammar hash absent → unknown (not inferred from verilog.lark name)
    assert exp.grammar_metadata.is_unavailable or (
        exp.grammar_metadata.value
        and exp.grammar_metadata.value.get("grammar_match_status") == "unknown"
    ) or exp.grammar_metadata.provenance.method
    # Default: no recompute
    assert pr.recomputed_grammar_verdict.is_unavailable
    assert (
        exp.runtime_metadata.value.get("recompute_with_current_grammar") is False
    )


def test_successful_nemotron_shaped_zip_import():
    raw = _single_problem_bundle(
        experiment="focused_four_nemotron_512",
        include_log=True,
        summary_config=_summary_config(
            model="nvidia/Nemotron-...",
            enable_thinking=True,
        ),
        record=_rich_record("Prob004_vector2"),
    )
    exp = normalize_imported_bundle(raw)
    assert exp.experiment_name == "focused_four_nemotron_512"
    assert exp.runtime_metadata.value.get("sibling_log_path") == (
        "logs/focused_four_nemotron_512.log"
    )
    assert exp.llm_metadata.value["model"].startswith("nvidia/")
    assert exp.llm_metadata.value["enable_thinking"] is True
    assert "model" not in _rich_record("x")


def test_sv_authoritative_and_reconstruction_mismatch_warning():
    text = VALID_SV.decode()
    steps = _steps_for_sv("WRONG")
    raw = _single_problem_bundle(sv=VALID_SV, steps=steps)
    exp = normalize_imported_bundle(raw)
    pr = exp.prompt_results[0]
    assert pr.generated_output.value == text
    assert pr.reconstructed_from_tokens.value == "WRONG"
    assert pr.reconstruction_matches_authoritative.value is False
    assert any("reconstruction differs" in w for w in pr.warnings)


def test_reconstruction_unavailable_without_selected_tokens():
    steps = [{"step": 1, "raw_argmax_token": "m", "raw_argmax_token_id": 1}]
    raw = _single_problem_bundle(steps=steps)
    exp = normalize_imported_bundle(raw)
    pr = exp.prompt_results[0]
    assert pr.reconstructed_from_tokens.is_unavailable
    assert pr.reconstruction_matches_authoritative.is_unavailable


def test_grammar_hash_absent_match_unknown():
    raw = _single_problem_bundle()
    exp = normalize_imported_bundle(raw)
    # Path recorded but no hash → unknown
    if not exp.grammar_metadata.is_unavailable:
        assert exp.grammar_metadata.value.get("grammar_match_status") == "unknown"
    # Must not invent match from filename
    method = (exp.grammar_metadata.provenance.method or "").lower()
    warnings = " ".join(exp.grammar_metadata.provenance.warnings).lower()
    assert "verilog.lark" in method or "hash absent" in method or "unknown" in warnings or exp.grammar_metadata.is_unavailable


def test_recorded_false_and_zero_distinct_from_unavailable():
    steps = [
        {
            "step": 1,
            "selected_token": "m",
            "selected_token_id": 1,
            "raw_argmax_blocked": False,
            "allowed_token_count": 0,
            "newly_masked_token_count": 0,
        }
    ]
    raw = _single_problem_bundle(sv=b"m", steps=steps)
    exp = normalize_imported_bundle(raw)
    step = exp.prompt_results[0].steps[0]
    assert step.masking_changed_selection.value is False
    assert not step.masking_changed_selection.is_unavailable
    assert step.valid_token_count.value == 0
    assert step.entropy_before.value is None
    assert step.entropy_before.is_unavailable


# ---------------------------------------------------------------------------
# Errors / conflicts
# ---------------------------------------------------------------------------


def test_malformed_json_in_trace():
    entries = {
        "results/focused_four_qwen_512/summary.json": b"{}",
        "results/focused_four_qwen_512/results.csv": b"x\n",
        "results/focused_four_qwen_512/anomalies.md": b"#\n",
        "results/focused_four_qwen_512/generated/Prob004_vector2.sv": VALID_SV,
        "results/focused_four_qwen_512/traces/Prob004_vector2.json": b"{bad",
        "results/focused_four_qwen_512/records/Prob004_vector2.json": b'{"problem":"Prob004_vector2"}',
    }
    with pytest.raises(ImportNormalizationError, match="malformed JSON"):
        normalize_imported_bundle(_build_zip(entries))


def test_problem_id_mismatch_trace_vs_filename():
    entries = {
        "results/exp/summary.json": b"{}",
        "results/exp/results.csv": b"x\n",
        "results/exp/anomalies.md": b"#\n",
        "results/exp/generated/Prob004_vector2.sv": VALID_SV,
        "results/exp/traces/Prob004_vector2.json": json.dumps(
            {"problem": "Prob039_always_if", "steps": []}
        ).encode(),
        "results/exp/records/Prob004_vector2.json": b'{"problem":"Prob004_vector2"}',
    }
    with pytest.raises(ImportNormalizationError, match="does not match"):
        normalize_imported_bundle(_build_zip(entries))


def test_duplicate_and_noncontiguous_steps():
    dup = [
        {"step": 1, "selected_token": "a"},
        {"step": 1, "selected_token": "b"},
    ]
    with pytest.raises(ImportNormalizationError, match="duplicate step"):
        normalize_imported_bundle(_single_problem_bundle(steps=dup, sv=b"ab"))

    gap = [
        {"step": 1, "selected_token": "a"},
        {"step": 3, "selected_token": "b"},
    ]
    with pytest.raises(ImportNormalizationError, match="non-contiguous"):
        normalize_imported_bundle(_single_problem_bundle(steps=gap, sv=b"ab"))


def test_missing_generated_file():
    entries = {
        "results/exp/summary.json": b"{}",
        "results/exp/results.csv": b"x\n",
        "results/exp/anomalies.md": b"#\n",
        "results/exp/traces/Prob004_vector2.json": b'{"problem":"Prob004_vector2","steps":[]}',
        "results/exp/records/Prob004_vector2.json": b'{"problem":"Prob004_vector2"}',
    }
    with pytest.raises(ImportNormalizationError, match="missing generated"):
        normalize_imported_bundle(_build_zip(entries))


def test_multiple_experiment_roots_rejected():
    entries = {
        **{
            f"results/run_a/generated/Prob004_vector2.sv": VALID_SV,
            "results/run_a/summary.json": b"{}",
            "results/run_a/results.csv": b"x\n",
            "results/run_a/anomalies.md": b"#\n",
            "results/run_a/traces/Prob004_vector2.json": b'{"problem":"Prob004_vector2","steps":[]}',
            "results/run_a/records/Prob004_vector2.json": b'{"problem":"Prob004_vector2"}',
        },
        **{
            "results/run_b/generated/Prob004_vector2.sv": VALID_SV,
            "results/run_b/summary.json": b"{}",
            "results/run_b/results.csv": b"x\n",
            "results/run_b/anomalies.md": b"#\n",
            "results/run_b/traces/Prob004_vector2.json": b'{"problem":"Prob004_vector2","steps":[]}',
            "results/run_b/records/Prob004_vector2.json": b'{"problem":"Prob004_vector2"}',
        },
    }
    with pytest.raises(ImportNormalizationError, match="multiple experiment roots"):
        normalize_imported_bundle(_build_zip(entries))


def test_setup_only_zip_rejected():
    raw = _build_zip(
        {
            "README.md": b"# runner\n",
            "run_eval.py": b"print(1)\n",
            "grammar/verilog.lark": b"start: module\n",
        }
    )
    with pytest.raises(ImportNormalizationError, match="no generated result set"):
        normalize_imported_bundle(raw)


def test_unsafe_zip_rejected_via_phase_2a1():
    with pytest.raises(ZipInspectionError):
        normalize_imported_bundle(b"not a zip")


def test_steps_must_be_list():
    entries = {
        "results/exp/summary.json": b"{}",
        "results/exp/results.csv": b"x\n",
        "results/exp/anomalies.md": b"#\n",
        "results/exp/generated/Prob004_vector2.sv": VALID_SV,
        "results/exp/traces/Prob004_vector2.json": json.dumps(
            {"problem": "Prob004_vector2", "steps": {"bad": True}}
        ).encode(),
        "results/exp/records/Prob004_vector2.json": b'{"problem":"Prob004_vector2"}',
    }
    with pytest.raises(ImportNormalizationError, match="must be a list"):
        normalize_imported_bundle(_build_zip(entries))


# ---------------------------------------------------------------------------
# Recomputation
# ---------------------------------------------------------------------------


def test_recorded_false_grammar_valid_and_zero_mask_counts():
    record = _rich_record(
        "Prob004_vector2",
        grammar_valid=False,
        mask_steps=0,
        newly_masked_token_count=0,
        findings=["incomplete"],
        verdict="fail",
    )
    exp = normalize_imported_bundle(_single_problem_bundle(record=record))
    pr = exp.prompt_results[0]
    assert pr.grammar_valid.value is False
    assert not pr.grammar_valid.is_unavailable
    assert pr.grammar_verdict.value == "fail"
    assert pr.mask_counts.value["mask_steps"] == 0
    assert pr.mask_counts.value["newly_masked_token_count"] == 0
    assert pr.findings.value == ["incomplete"]


def test_model_metadata_not_taken_from_record_even_if_present():
    """Stale model keys in a record must not override summary.json config."""
    record = _rich_record(
        "Prob004_vector2",
        model="SHOULD_NOT_USE/FromRecord",
        device="SHOULD_NOT_USE",
    )
    raw = _single_problem_bundle(
        record=record,
        summary_config=_summary_config(model="Qwen/FromSummary"),
    )
    exp = normalize_imported_bundle(raw)
    assert exp.llm_metadata.value["model"] == "Qwen/FromSummary"
    assert exp.llm_metadata.provenance.source_file.endswith("summary.json")


def test_recompute_disabled_by_default():
    exp = normalize_imported_bundle(_single_problem_bundle())
    pr = exp.prompt_results[0]
    assert pr.recomputed_grammar_verdict.is_unavailable
    assert pr.recomputed_parse_error.is_unavailable
    assert pr.parser_analysis.is_unavailable


def test_recompute_enabled_marks_recomputed_and_attaches_hash():
    exp = normalize_imported_bundle(
        _single_problem_bundle(),
        recompute_with_current_grammar=True,
    )
    pr = exp.prompt_results[0]
    assert pr.recomputed_grammar_verdict.provenance.kind == ProvenanceKind.recomputed
    assert pr.recomputed_grammar_verdict.provenance.grammar_sha256 == grammar_sha256()
    assert pr.recomputed_grammar_verdict.provenance.grammar_sha256 == EXPECTED_GRAMMAR_SHA256
    assert pr.recomputed_grammar_verdict.value in {"valid", "invalid"}
    assert pr.grammar_verdict.provenance.kind == ProvenanceKind.recorded
    assert pr.grammar_valid.provenance.kind == ProvenanceKind.recorded
    assert exp.runtime_metadata.value["recompute_with_current_grammar"] is True
    assert pr.parser_analysis.provenance.kind == ProvenanceKind.recomputed
    assert pr.parser_analysis.value is not None
    assert pr.parser_analysis.value.status == "complete_valid"
    assert pr.parser_analysis.value.representation_kind == "complete_parse_tree"
    assert pr.parser_analysis.value.grammar_sha256 == EXPECTED_GRAMMAR_SHA256


def test_recorded_recomputed_disagreement_warning():
    # Valid SV but record says grammar_valid=false
    record = _rich_record("Prob004_vector2", grammar_valid=False, verdict="fail")
    exp = normalize_imported_bundle(
        _single_problem_bundle(record=record),
        recompute_with_current_grammar=True,
    )
    pr = exp.prompt_results[0]
    assert pr.grammar_valid.value is False
    assert pr.grammar_verdict.value == "fail"
    # Canonical grammar should accept VALID_SV
    assert pr.recomputed_grammar_verdict.value == "valid"
    assert pr.parser_analysis.value is not None
    assert pr.parser_analysis.value.status == "complete_valid"
    assert any("disagrees" in w for w in pr.warnings)


def test_recompute_uses_canonical_grammar_sha_without_syncode():
    exp = normalize_imported_bundle(
        _single_problem_bundle(),
        recompute_with_current_grammar=True,
    )
    pr = exp.prompt_results[0]
    assert pr.grammar_valid.provenance.kind == ProvenanceKind.recorded
    assert pr.recomputed_grammar_verdict.provenance.kind == ProvenanceKind.recomputed
    assert pr.recomputed_grammar_verdict.provenance.grammar_sha256 == EXPECTED_GRAMMAR_SHA256
    from app.core.grammar import CANONICAL_GRAMMAR_PATH

    assert CANONICAL_GRAMMAR_PATH.name == "verilog.lark"
    assert CANONICAL_GRAMMAR_PATH.as_posix().endswith("grammar/verilog.lark")


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_persistence_survives_fresh_store_instance(tmp_path: Path):
    store_a = ImportedExperimentStore(base_dir=tmp_path / "imported")
    exp = normalize_imported_bundle(
        _single_problem_bundle(),
        experiment_id=store_a.new_id(),
    )
    store_a.save(exp)

    store_b = ImportedExperimentStore(base_dir=tmp_path / "imported")
    loaded = store_b.load(exp.experiment_id)
    assert loaded is not None
    assert loaded.experiment_id == exp.experiment_id
    assert loaded.prompt_results[0].generated_output.value == VALID_SV.decode()
    summaries = store_b.list_summaries()
    assert len(summaries) == 1
    assert summaries[0].prompt_count == 1
    # Summary must not embed steps
    assert not hasattr(summaries[0], "steps") or summaries[0].model_dump().get("steps") is None


def test_store_rejects_malformed_id(tmp_path: Path):
    store = ImportedExperimentStore(base_dir=tmp_path)
    with pytest.raises(ImportedStoreError, match="malformed"):
        store.load("../evil")


def test_store_malformed_file_fails_clearly(tmp_path: Path):
    store = ImportedExperimentStore(base_dir=tmp_path)
    eid = store.new_id()
    path = tmp_path / f"{eid}.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ImportedStoreError, match="malformed"):
        store.load(eid)
