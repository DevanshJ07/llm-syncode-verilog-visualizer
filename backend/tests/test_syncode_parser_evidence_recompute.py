"""
Phase 4A.2 — imported SynCode parser-evidence recomputation tests.

Parser-only path.  Does not build MaskStore, load models, or decode token IDs.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from unittest.mock import patch

import pytest

from app.core.grammar import EXPECTED_GRAMMAR_SHA256
from app.models.normalized import NormalizedTraceStep, TokenRef
from app.models.provenance import ProvenanceKind, Prov
from app.models.schemas import DecodingStep
from app.models.syncode_parser_evidence import SyncodeParserEvidence
from app.services.import_normalize import normalize_imported_bundle
from app.services.syncode_parser_evidence import (
    serialize_parse_result,
    validate_imported_structured_evidence,
)
from app.services.syncode_parser_evidence_recompute import (
    recompute_syncode_parser_evidence_for_steps,
)


class FakeRemainderState:
    def __init__(self, name: str):
        self.name = name


class FakeAcceptSequence:
    def __init__(self, terminals):
        self.accept_terminals = tuple(terminals)


class FakeParseResult:
    def __init__(self, accept_sequences, remainder, remainder_state, function_end=False):
        self.accept_sequences = accept_sequences
        self.remainder = remainder
        self.remainder_state = remainder_state
        self.function_end = function_end


def _step(idx: int, token: str | None) -> NormalizedTraceStep:
    if token is None:
        selected = Prov[TokenRef].unavailable(method="missing")
    else:
        selected = Prov[TokenRef].recorded(
            TokenRef(token=token, token_id=idx),
            source_field="selected_token",
        )
    return NormalizedTraceStep(step_index=idx, selected=selected)


def _build_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


VALID_SV = b"module m;\nendmodule\n"


def _minimal_bundle_bytes(*, steps: list[dict] | None = None) -> bytes:
    problem = "Prob004_vector2"
    root = "results/focused_four_qwen_512"
    text = VALID_SV.decode()
    if steps is None:
        steps = [
            {
                "step": i + 1,
                "selected_token": ch,
                "selected_token_id": i,
                "raw_argmax_blocked": False,
                "allowed_token_count": 0,
            }
            for i, ch in enumerate(text)
        ]
    return _build_zip(
        {
            f"{root}/summary.json": json.dumps(
                {
                    "ok": True,
                    "config": {
                        "model": "Qwen/Qwen2.5-Coder-1.5B-Instruct",
                        "max_new_tokens": 64,
                        "versions": {"syncode": "0.4.16"},
                    },
                }
            ).encode(),
            f"{root}/results.csv": b"problem,pass\n",
            f"{root}/anomalies.md": b"# none\n",
            f"{root}/generated/{problem}.sv": VALID_SV,
            f"{root}/traces/{problem}.json": json.dumps(
                {"problem": problem, "steps": steps}
            ).encode(),
            f"{root}/records/{problem}.json": json.dumps(
                {
                    "problem": problem,
                    "grammar_valid": True,
                    "parse_error": "",
                    "termination": "eos",
                    "generated_tokens": len(steps),
                    "mask_steps": 0,
                    "verdict": "pass",
                    "findings": [],
                }
            ).encode(),
        }
    )


def test_01_default_option_false_keeps_unavailable():
    raw = _minimal_bundle_bytes()
    exp = normalize_imported_bundle(raw, recompute_syncode_parser_evidence=False)
    step0 = exp.prompt_results[0].steps[0]
    assert step0.syncode_parser_evidence.is_unavailable
    assert step0.syncode_parser_evidence_recorded.is_unavailable


def test_02_false_path_does_not_call_parser_factory():
    raw = _minimal_bundle_bytes()
    with patch(
        "app.services.syncode_parser_evidence_recompute.create_parser_only_incremental"
    ) as factory:
        normalize_imported_bundle(raw, recompute_syncode_parser_evidence=False)
        factory.assert_not_called()


def test_03_to_06_prefix_alignment_whitespace_sha_with_fake_parser():
    steps = [_step(0, " "), _step(1, "m"), _step(2, " ")]
    seen_prefixes: list[str] = []

    class Inc:
        def reset(self):
            pass

        def get_acceptable_next_terminals(self, partial_code: str):
            seen_prefixes.append(partial_code)
            rem = (
                FakeRemainderState("INCOMPLETE")
                if partial_code
                else FakeRemainderState("COMPLETE")
            )
            return FakeParseResult(
                {FakeAcceptSequence(["MODULE"])},
                partial_code.encode() if partial_code else b"",
                rem,
            )

    with patch(
        "app.services.syncode_parser_evidence_recompute.create_parser_only_incremental",
        return_value=(Inc(), True),
    ), patch(
        "app.services.syncode_parser_evidence_recompute.syncode_package_version",
        return_value="0.4.16-test",
    ):
        provs, _warns = recompute_syncode_parser_evidence_for_steps(steps)

    assert seen_prefixes == ["", " ", " m"]
    ev0 = provs[0].value
    assert ev0 is not None
    assert ev0.generated_prefix_char_count == 0
    assert ev0.generated_prefix_sha256 == hashlib.sha256(b"").hexdigest()
    ev1 = provs[1].value
    assert ev1.generated_prefix_char_count == 1
    assert ev1.generated_prefix_sha256 == hashlib.sha256(b" ").hexdigest()


def test_07_recomputed_provenance_and_origin():
    steps = [_step(0, "m")]

    class Inc:
        def reset(self):
            pass

        def get_acceptable_next_terminals(self, partial_code: str):
            return FakeParseResult(
                {FakeAcceptSequence(["MODULE"])},
                b"",
                FakeRemainderState("COMPLETE"),
            )

    with patch(
        "app.services.syncode_parser_evidence_recompute.create_parser_only_incremental",
        return_value=(Inc(), False),
    ), patch(
        "app.services.syncode_parser_evidence_recompute.syncode_package_version",
        return_value="0.4.16",
    ):
        provs, _ = recompute_syncode_parser_evidence_for_steps(steps)

    assert provs[0].provenance.kind == ProvenanceKind.recomputed
    assert provs[0].value.origin == "import_recomputed_parser_only"
    assert any("parser-only" in w for w in provs[0].value.warnings)


def test_08_canonical_grammar_sha_on_provenance():
    steps = [_step(0, "m")]

    class Inc:
        def reset(self):
            pass

        def get_acceptable_next_terminals(self, partial_code: str):
            return FakeParseResult(set(), b"", FakeRemainderState("COMPLETE"))

    with patch(
        "app.services.syncode_parser_evidence_recompute.create_parser_only_incremental",
        return_value=(Inc(), False),
    ):
        provs, _ = recompute_syncode_parser_evidence_for_steps(
            steps, grammar_hash=EXPECTED_GRAMMAR_SHA256
        )
    assert provs[0].provenance.grammar_sha256 == EXPECTED_GRAMMAR_SHA256


@pytest.mark.parametrize("state_name", ["COMPLETE", "MAYBE_COMPLETE", "INCOMPLETE"])
def test_09_10_11_remainder_states(state_name):
    steps = [_step(0, "x")]

    class Inc:
        def reset(self):
            pass

        def get_acceptable_next_terminals(self, partial_code: str):
            return FakeParseResult(
                {FakeAcceptSequence(["IDENT"])},
                b"ab" if state_name != "COMPLETE" else b"",
                FakeRemainderState(state_name),
            )

    with patch(
        "app.services.syncode_parser_evidence_recompute.create_parser_only_incremental",
        return_value=(Inc(), False),
    ):
        provs, _ = recompute_syncode_parser_evidence_for_steps(steps)
    assert provs[0].value.remainder_state == state_name


def test_12_multi_terminal_accept_sequences():
    steps = [_step(0, "x")]

    class Inc:
        def reset(self):
            pass

        def get_acceptable_next_terminals(self, partial_code: str):
            return FakeParseResult(
                {FakeAcceptSequence(["FINAL", "WS", "IDENT"])},
                "ab",
                FakeRemainderState("MAYBE_COMPLETE"),
            )

    with patch(
        "app.services.syncode_parser_evidence_recompute.create_parser_only_incremental",
        return_value=(Inc(), False),
    ):
        provs, _ = recompute_syncode_parser_evidence_for_steps(steps)
    assert ["FINAL", "WS", "IDENT"] in [
        r.terminals for r in provs[0].value.accept_sequences
    ]


def test_13_14_grammar_end_not_mask_eos():
    steps = [_step(0, "x")]

    class Inc:
        def reset(self):
            pass

        def get_acceptable_next_terminals(self, partial_code: str):
            return FakeParseResult(
                {FakeAcceptSequence(["$END"])},
                b"",
                FakeRemainderState("COMPLETE"),
                function_end=True,
            )

    with patch(
        "app.services.syncode_parser_evidence_recompute.create_parser_only_incremental",
        return_value=(Inc(), False),
    ):
        provs, _ = recompute_syncode_parser_evidence_for_steps(steps)
    ev = provs[0].value
    assert ev.grammar_end_marker_present is True
    assert ev.mask_eos_observation is None
    assert ev.mask_call_index is None


def test_15_16_missing_selected_token_no_id_decode():
    steps = [
        _step(0, "a"),
        NormalizedTraceStep(
            step_index=1,
            selected=Prov[TokenRef].recorded(
                TokenRef(token=None, token_id=99),
                source_field="selected_token_id",
            ),
        ),
        _step(2, "c"),
    ]

    class Inc:
        def reset(self):
            pass

        def get_acceptable_next_terminals(self, partial_code: str):
            return FakeParseResult(set(), b"", FakeRemainderState("COMPLETE"))

    with patch(
        "app.services.syncode_parser_evidence_recompute.create_parser_only_incremental",
        return_value=(Inc(), False),
    ):
        provs, warns = recompute_syncode_parser_evidence_for_steps(steps)

    assert provs[0].value.status == "available"
    assert provs[2].is_unavailable
    assert any("missing" in w or "cannot decode" in w for w in warns)


def test_17_18_parser_failure_preserves_trace_and_continues():
    steps = [_step(0, "a"), _step(1, "b"), _step(2, "c")]
    calls = {"n": 0}

    class Inc:
        def reset(self):
            pass

        def get_acceptable_next_terminals(self, partial_code: str):
            calls["n"] += 1
            if partial_code == "a":
                raise RuntimeError("boom-at-a")
            return FakeParseResult(
                {FakeAcceptSequence(["X"])},
                b"",
                FakeRemainderState("COMPLETE"),
            )

    with patch(
        "app.services.syncode_parser_evidence_recompute.create_parser_only_incremental",
        return_value=(Inc(), False),
    ):
        provs, _warns = recompute_syncode_parser_evidence_for_steps(steps)

    assert steps[0].selected.value.token == "a"
    assert provs[1].value.status == "failed"
    assert "boom-at-a" in provs[1].value.error
    assert provs[2].value.status == "available"
    assert calls["n"] == 3


def test_19_step_limit_handling(monkeypatch):
    monkeypatch.setattr(
        "app.services.syncode_parser_evidence_recompute._max_recompute_steps",
        lambda: 2,
    )
    steps = [_step(i, "x") for i in range(4)]

    class Inc:
        def reset(self):
            pass

        def get_acceptable_next_terminals(self, partial_code: str):
            return FakeParseResult(set(), b"", FakeRemainderState("COMPLETE"))

    with patch(
        "app.services.syncode_parser_evidence_recompute.create_parser_only_incremental",
        return_value=(Inc(), False),
    ):
        provs, warns = recompute_syncode_parser_evidence_for_steps(steps)
    assert not provs[0].is_unavailable
    assert not provs[1].is_unavailable
    assert provs[2].is_unavailable
    assert provs[3].is_unavailable
    assert any("step limit" in w for w in warns)
    assert len(steps) == 4


def test_20_prefix_length_limit_handling(monkeypatch):
    monkeypatch.setattr(
        "app.services.syncode_parser_evidence_recompute._max_prefix_chars",
        lambda: 3,
    )
    steps = [_step(0, "ab"), _step(1, "cd"), _step(2, "ef")]

    class Inc:
        def reset(self):
            pass

        def get_acceptable_next_terminals(self, partial_code: str):
            return FakeParseResult(set(), b"", FakeRemainderState("COMPLETE"))

    with patch(
        "app.services.syncode_parser_evidence_recompute.create_parser_only_incremental",
        return_value=(Inc(), False),
    ):
        provs, warns = recompute_syncode_parser_evidence_for_steps(steps)
    assert not provs[0].is_unavailable
    assert not provs[1].is_unavailable
    assert provs[2].is_unavailable
    assert any("prefix character limit" in w for w in warns)


def test_21_existing_imported_json_still_loads():
    from app.models.normalized import NormalizedExperiment

    payload = {
        "experiment_id": "imp-old",
        "schema_version": "2A.2",
        "source_type": "imported",
        "created_at": "2026-01-01T00:00:00+00:00",
        "prompt_results": [],
    }
    exp = NormalizedExperiment.model_validate(payload)
    assert exp.prompt_results == []


def test_22_existing_live_evidence_still_loads_as_recorded():
    ev = serialize_parse_result(
        FakeParseResult(
            {FakeAcceptSequence(["MODULE"])},
            b"",
            FakeRemainderState("COMPLETE"),
        ),
        mask_call_index=0,
        generated_prefix="",
        origin="live_mask_runtime",
    )
    assert ev.status == "available"
    assert ev.origin == "live_mask_runtime"
    step = DecodingStep(step=1, context="", syncode_parser_evidence=ev)
    assert step.syncode_parser_evidence.origin == "live_mask_runtime"
    # Legacy Phase 4A.1 status="recorded" normalizes to available on load.
    old = DecodingStep.model_validate(
        {"step": 1, "context": "", "syncode_parser_evidence": {"status": "recorded"}}
    )
    assert old.syncode_parser_evidence.status == "available"
    assert old.syncode_parser_evidence.origin == "none"


def test_23_default_normalize_omits_syncode_recompute():
    raw = _minimal_bundle_bytes()
    exp = normalize_imported_bundle(raw)
    assert exp.runtime_metadata.value["recompute_syncode_parser_evidence"] is False


def test_24_normalize_accepts_syncode_recompute_true():
    raw = _minimal_bundle_bytes()

    class Inc:
        def reset(self):
            pass

        def get_acceptable_next_terminals(self, partial_code: str):
            return FakeParseResult(
                {FakeAcceptSequence(["MODULE"])},
                b"",
                FakeRemainderState("COMPLETE"),
            )

    with patch(
        "app.services.syncode_parser_evidence_recompute.create_parser_only_incremental",
        return_value=(Inc(), True),
    ):
        exp = normalize_imported_bundle(
            raw, recompute_syncode_parser_evidence=True
        )
    assert exp.runtime_metadata.value["recompute_syncode_parser_evidence"] is True
    assert not exp.prompt_results[0].steps[0].syncode_parser_evidence.is_unavailable
    assert (
        exp.prompt_results[0].steps[0].syncode_parser_evidence.provenance.kind
        == ProvenanceKind.recomputed
    )


def test_25_grammar_and_syncode_recompute_independent():
    raw = _minimal_bundle_bytes()

    class Inc:
        def reset(self):
            pass

        def get_acceptable_next_terminals(self, partial_code: str):
            return FakeParseResult(set(), b"", FakeRemainderState("COMPLETE"))

    with patch(
        "app.services.syncode_parser_evidence_recompute.create_parser_only_incremental",
        return_value=(Inc(), False),
    ):
        only_syncode = normalize_imported_bundle(
            raw,
            recompute_with_current_grammar=False,
            recompute_syncode_parser_evidence=True,
        )
        only_grammar = normalize_imported_bundle(
            raw,
            recompute_with_current_grammar=True,
            recompute_syncode_parser_evidence=False,
        )
    assert only_syncode.prompt_results[0].parser_analysis.is_unavailable
    assert not only_syncode.prompt_results[0].steps[0].syncode_parser_evidence.is_unavailable
    assert not only_grammar.prompt_results[0].parser_analysis.is_unavailable
    assert only_grammar.prompt_results[0].steps[0].syncode_parser_evidence.is_unavailable


def test_26_27_no_maskstore_torch_at_module_top_level():
    import ast
    import app.services.syncode_parser_evidence_recompute as mod

    tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
    top_imports: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            top_imports.add(node.module.split(".")[0])
    assert "syncode" not in top_imports
    assert "torch" not in top_imports
    assert "transformers" not in top_imports


def test_28_evidence_unavailable_when_recompute_false():
    raw = _minimal_bundle_bytes()
    exp = normalize_imported_bundle(raw, recompute_syncode_parser_evidence=False)
    for st in exp.prompt_results[0].steps:
        assert st.syncode_parser_evidence.is_unavailable


def test_29_future_recorded_coexists_with_recomputed():
    recorded = serialize_parse_result(
        FakeParseResult(
            {FakeAcceptSequence(["OLD"])},
            b"",
            FakeRemainderState("COMPLETE"),
        ),
        origin="import_recorded_bundle",
        generated_prefix="",
    )
    text = VALID_SV.decode()
    steps = []
    for i, ch in enumerate(text):
        entry = {
            "step": i + 1,
            "selected_token": ch,
            "selected_token_id": i,
            "raw_argmax_blocked": False,
            "allowed_token_count": 0,
        }
        if i == 0:
            entry["syncode_parser_evidence"] = recorded.model_dump()
        steps.append(entry)
    raw = _minimal_bundle_bytes(steps=steps)

    class Inc:
        def reset(self):
            pass

        def get_acceptable_next_terminals(self, partial_code: str):
            return FakeParseResult(
                {FakeAcceptSequence(["NEW"])},
                b"",
                FakeRemainderState("COMPLETE"),
            )

    with patch(
        "app.services.syncode_parser_evidence_recompute.create_parser_only_incremental",
        return_value=(Inc(), False),
    ):
        exp = normalize_imported_bundle(
            raw, recompute_syncode_parser_evidence=True
        )
    s0 = exp.prompt_results[0].steps[0]
    assert s0.syncode_parser_evidence.provenance.kind == ProvenanceKind.recomputed
    assert s0.syncode_parser_evidence.value.accept_sequences[0].terminals == ["NEW"]
    assert not s0.syncode_parser_evidence_recorded.is_unavailable
    assert s0.syncode_parser_evidence_recorded.value.accept_sequences[0].terminals == [
        "OLD"
    ]
    assert validate_imported_structured_evidence({"status": "nope"}) is None


def test_30_lark_terminals_never_copied():
    lark = ["MODULE", "IDENT"]
    steps = [_step(0, "x")]

    class Inc:
        def reset(self):
            pass

        def get_acceptable_next_terminals(self, partial_code: str):
            return FakeParseResult(
                {FakeAcceptSequence(["ONLY_SYNCODE"])},
                b"",
                FakeRemainderState("COMPLETE"),
            )

    with patch(
        "app.services.syncode_parser_evidence_recompute.create_parser_only_incremental",
        return_value=(Inc(), False),
    ):
        provs, _ = recompute_syncode_parser_evidence_for_steps(steps)
    flat = [t for r in provs[0].value.accept_sequences for t in r.terminals]
    assert flat == ["ONLY_SYNCODE"]
    assert flat != lark


def test_interface_real_parser_only_no_mask_store():
    """
    Real SynCode 0.4.16 IncrementalParser on short prefixes.

    Interface test — not an original token-mask replay.  Proves MaskStore is
    never constructed (SynCode may import the module transitively).
    """
    from app.services.syncode_parser_evidence_recompute import (
        create_parser_only_incremental,
    )

    with patch(
        "syncode.mask_store.mask_store.MaskStore.init_mask_store"
    ) as init_mask, patch(
        "syncode.mask_store.mask_store.MaskStore.__init__",
        side_effect=AssertionError("MaskStore must not be constructed"),
    ):
        inc, ignore_ws = create_parser_only_incremental()
        assert isinstance(ignore_ws, bool)
        inc.reset()
        pr0 = inc.get_acceptable_next_terminals("")
        assert pr0 is not None
        assert hasattr(pr0, "accept_sequences")
        assert hasattr(pr0, "remainder_state")
        inc.reset()
        pr1 = inc.get_acceptable_next_terminals("module")
        assert pr1 is not None
        init_mask.assert_not_called()
    assert type(inc).__name__ == "IncrementalParser"


# ---------------------------------------------------------------------------
# Phase 4A.2 refinements (limit, semantics, coexistence, reuse)
# ---------------------------------------------------------------------------


def test_default_step_limit_is_at_least_2048():
    from app.core.config import settings

    assert settings.syncode_parser_evidence_recompute_max_steps >= 2048


def test_exactly_512_steps_recomputed_without_limit_warning():
    """Cheap fake parser — proves 512-step experiments fit under the default."""
    n = 512
    tokens = ["a"] * n
    steps = [_step(i, tokens[i]) for i in range(n)]
    seen: list[str] = []
    factory_calls = {"n": 0}

    class Inc:
        def reset(self):
            pass

        def get_acceptable_next_terminals(self, partial_code: str):
            seen.append(partial_code)
            return FakeParseResult(
                {FakeAcceptSequence(["T"])},
                b"",
                FakeRemainderState("COMPLETE"),
            )

    def factory():
        factory_calls["n"] += 1
        return Inc(), False

    with patch(
        "app.services.syncode_parser_evidence_recompute.create_parser_only_incremental",
        side_effect=factory,
    ):
        provs, warns = recompute_syncode_parser_evidence_for_steps(steps)

    assert factory_calls["n"] == 1  # one parser reused per prompt
    assert len(provs) == 512
    assert all(not p.is_unavailable for p in provs)
    assert provs[511].value is not None
    assert provs[511].value.is_structurally_available()
    assert provs[511].value.generated_token_count_before_selection == 511
    assert seen[0] == ""
    assert seen[1] == "a"
    assert seen[511] == "a" * 511  # excludes token 511
    assert not any("step limit" in w for w in warns)
    assert all(p.value.mask_call_index is None for p in provs)
    assert all(p.value.mask_eos_observation is None for p in provs)
    assert all(p.provenance.kind == ProvenanceKind.recomputed for p in provs)
    assert all(p.value.origin == "import_recomputed_parser_only" for p in provs)
    # Must not present recomputation as Prov Recorded
    assert all(p.provenance.kind != ProvenanceKind.recorded for p in provs)
    assert all(p.value.status != "recorded" for p in provs)


def test_overflow_above_configured_limit_marks_remaining_unavailable(monkeypatch):
    monkeypatch.setattr(
        "app.services.syncode_parser_evidence_recompute._max_recompute_steps",
        lambda: 3,
    )
    steps = [_step(i, "x") for i in range(5)]

    class Inc:
        def reset(self):
            pass

        def get_acceptable_next_terminals(self, partial_code: str):
            return FakeParseResult(set(), b"", FakeRemainderState("COMPLETE"))

    with patch(
        "app.services.syncode_parser_evidence_recompute.create_parser_only_incremental",
        return_value=(Inc(), False),
    ):
        provs, warns = recompute_syncode_parser_evidence_for_steps(steps)
    assert all(not p.is_unavailable for p in provs[:3])
    assert provs[3].is_unavailable
    assert provs[4].is_unavailable
    assert any("step limit" in w and "max_steps=3" in w for w in warns)


def test_status_origin_prov_semantics_documented():
    """status=availability; origin=source; Prov.kind=provenance."""
    from app.models.syncode_parser_evidence import SyncodeParserEvidence

    live = serialize_parse_result(
        FakeParseResult(
            {FakeAcceptSequence(["MODULE"])},
            b"",
            FakeRemainderState("COMPLETE"),
        ),
        origin="live_mask_runtime",
        generated_prefix="",
    )
    assert live.status == "available"
    assert live.origin == "live_mask_runtime"

    steps = [_step(0, "m")]

    class Inc:
        def reset(self):
            pass

        def get_acceptable_next_terminals(self, partial_code: str):
            return FakeParseResult(
                {FakeAcceptSequence(["$END"])},
                b"",
                FakeRemainderState("COMPLETE"),
                function_end=True,
            )

    with patch(
        "app.services.syncode_parser_evidence_recompute.create_parser_only_incremental",
        return_value=(Inc(), False),
    ):
        provs, _ = recompute_syncode_parser_evidence_for_steps(steps)
    ev = provs[0].value
    assert ev.status == "available"  # outcome, not Prov Recorded
    assert ev.origin == "import_recomputed_parser_only"
    assert provs[0].provenance.kind == ProvenanceKind.recomputed
    assert ev.grammar_end_marker_present is True
    assert ev.mask_eos_observation is None  # grammar-end ≠ EOS mask allowance

    # Legacy wire value
    legacy = SyncodeParserEvidence.model_validate({"status": "recorded"})
    assert legacy.status == "available"


def test_recorded_recomputed_coexistence_keeps_both_identities():
    recorded = serialize_parse_result(
        FakeParseResult(
            {FakeAcceptSequence(["OLD"])},
            b"",
            FakeRemainderState("COMPLETE"),
        ),
        origin="import_recorded_bundle",
        syncode_version="0.4.16-bundle",
        generated_prefix="old",
    )
    text = "ab"
    steps_raw = []
    for i, ch in enumerate(text):
        entry = {
            "step": i + 1,
            "selected_token": ch,
            "selected_token_id": i,
            "raw_argmax_blocked": False,
            "allowed_token_count": 0,
            "syncode_parser_evidence": recorded.model_dump(),
        }
        steps_raw.append(entry)
    # Minimal zip with two-char SV matching steps
    problem = "Prob004_vector2"
    root = "results/focused_four_qwen_512"
    raw = _build_zip(
        {
            f"{root}/summary.json": json.dumps(
                {"ok": True, "config": {"model": "x", "versions": {}}}
            ).encode(),
            f"{root}/results.csv": b"problem,pass\n",
            f"{root}/anomalies.md": b"#\n",
            f"{root}/generated/{problem}.sv": text.encode(),
            f"{root}/traces/{problem}.json": json.dumps(
                {"problem": problem, "steps": steps_raw}
            ).encode(),
            f"{root}/records/{problem}.json": json.dumps(
                {
                    "problem": problem,
                    "grammar_valid": True,
                    "parse_error": "",
                    "termination": "eos",
                    "generated_tokens": 2,
                    "mask_steps": 0,
                    "verdict": "pass",
                    "findings": [],
                }
            ).encode(),
        }
    )

    class Inc:
        def reset(self):
            pass

        def get_acceptable_next_terminals(self, partial_code: str):
            return FakeParseResult(
                {FakeAcceptSequence(["NEW"])},
                b"",
                FakeRemainderState("INCOMPLETE"),
            )

    with patch(
        "app.services.syncode_parser_evidence_recompute.create_parser_only_incremental",
        return_value=(Inc(), False),
    ), patch(
        "app.services.syncode_parser_evidence_recompute.syncode_package_version",
        return_value="0.4.16-recompute",
    ):
        exp = normalize_imported_bundle(
            raw, recompute_syncode_parser_evidence=True
        )
    s0 = exp.prompt_results[0].steps[0]
    rec = s0.syncode_parser_evidence_recorded
    recom = s0.syncode_parser_evidence
    assert not rec.is_unavailable and not recom.is_unavailable
    assert rec.provenance.kind == ProvenanceKind.recorded
    assert recom.provenance.kind == ProvenanceKind.recomputed
    assert rec.value.origin == "import_recorded_bundle"
    assert recom.value.origin == "import_recomputed_parser_only"
    assert rec.value.accept_sequences[0].terminals == ["OLD"]
    assert recom.value.accept_sequences[0].terminals == ["NEW"]
    assert rec.value.syncode_version == "0.4.16-bundle"
    assert recom.value.syncode_version == "0.4.16-recompute"
    assert recom.provenance.grammar_sha256  # recomputed carries current hash
    # Disagreement not silently resolved — both retained
    assert rec.value.accept_sequences != recom.value.accept_sequences


def test_parser_reuse_and_reset_on_failure():
    steps = [_step(0, "a"), _step(1, "b"), _step(2, "c")]
    factory_calls = {"n": 0}
    resets = {"n": 0}

    class Inc:
        def reset(self):
            resets["n"] += 1

        def get_acceptable_next_terminals(self, partial_code: str):
            if partial_code == "a":
                raise RuntimeError("mid-fail")
            return FakeParseResult(set(), b"", FakeRemainderState("COMPLETE"))

    def factory():
        factory_calls["n"] += 1
        return Inc(), False

    with patch(
        "app.services.syncode_parser_evidence_recompute.create_parser_only_incremental",
        side_effect=factory,
    ):
        provs, _ = recompute_syncode_parser_evidence_for_steps(steps)
    assert factory_calls["n"] == 1
    assert resets["n"] >= 3  # reset before each step (+ possible post-failure)
    assert provs[1].value.status == "failed"
    assert provs[2].value.status == "available"


def test_dependency_boundary_no_mask_constrainer_processor_objects():
    """
    Recompute must not construct MaskStore / GrammarConstrainer /
    SyncodeLogitsProcessor / tokenizer / model objects.

    Note: importing SynCode's incremental parser may transitively import the
    already-installed ``torch`` *package* in this process.  That is reported
    accurately — the guarantee is no tensor/model/tokenizer/mask-store
    *objects* are constructed on the recompute path.
    """
    constructed: list[str] = []

    class Boom:
        def __init__(self, *a, **k):
            constructed.append(self.__class__.__name__)
            raise AssertionError("forbidden construction")

    steps = [_step(0, "m")]

    class Inc:
        def reset(self):
            pass

        def get_acceptable_next_terminals(self, partial_code: str):
            return FakeParseResult(
                {FakeAcceptSequence(["MODULE"])},
                b"",
                FakeRemainderState("COMPLETE"),
            )

    with patch(
        "app.services.syncode_parser_evidence_recompute.create_parser_only_incremental",
        return_value=(Inc(), False),
    ), patch(
        "syncode.mask_store.mask_store.MaskStore", Boom
    ), patch(
        "syncode.grammar_mask.grammar_constrainer.GrammarConstrainer", Boom
    ), patch(
        "syncode.grammar_mask.logits_processor.SyncodeLogitsProcessor", Boom
    ):
        provs, _ = recompute_syncode_parser_evidence_for_steps(steps)

    assert constructed == []
    assert provs[0].value.is_structurally_available()
    # Honest dependency note (this process may already have imported torch via
    # prior SynCode imports).  The guarantee under test is object construction,
    # not a clean-process "torch never loaded" claim.
    _ = "torch" in __import__("sys").modules
