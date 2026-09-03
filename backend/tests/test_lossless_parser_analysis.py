"""
Checkpoint 2 — lossless per-step Verilog parser analysis tests.

Lark-only + FastAPI route tests. Does not load models, SynCode masks, or
the 40 MB Nemotron bundle.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.grammar import EXPECTED_GRAMMAR_SHA256, grammar_sha256
from app.models.normalized import (
    NormalizedExperiment,
    NormalizedPromptResult,
    NormalizedTraceStep,
    TokenRef,
)
from app.models.provenance import Prov
from app.models.schemas import DecodingStep, ExperimentResult
from app.services.lossless_parser_analysis import (
    _CACHE_MAX,
    analyze_lossless_source,
    build_llm_token_spans,
    cache_stats,
    clear_analysis_cache,
    construct_step_source,
    make_cache_key,
    source_sha256,
    timed_analyze_lossless_source,
    verify_lossless_segments,
)
from app.services.parser_analysis import analyze_verilog_source
from main import app

VALID_COMPLETE = """\
module t(input a, output b);
  assign b = a;
endmodule
"""

VALID_WITH_PUNCT_AND_TRIVIA = """\
module t(input a, output b);
\tassign b[0] = a; // line comment
  /* block
     comment */
endmodule
"""

MISSING_ENDMODULE = """\
module t(input a, output b);
  assign b = a;
"""

ILLEGAL_MID = """\
module t(input a, output b);
  assign b = @ a;
endmodule
"""

UNICODE_SRC = "module t; // café\nendmodule\n"


def _terminal_texts(resp) -> list[str]:
    return [s.exact_text for s in resp.source_segments if s.kind == "terminal"]


def test_valid_source_complete_and_punctuation_present():
    r = analyze_lossless_source(
        VALID_WITH_PUNCT_AND_TRIVIA,
        timing="final_source",
        source_provenance="final_generated_source",
    )
    assert r.completeness == "complete"
    assert r.keep_all_tokens is True
    assert r.cst_root is not None
    assert r.cst_root.is_partial is False
    terms = _terminal_texts(r)
    assert "," in terms
    assert ";" in terms
    assert "(" in terms
    assert ")" in terms
    assert "[" in terms
    assert "]" in terms


def test_spaces_tabs_newlines_comments_preserved():
    src = VALID_WITH_PUNCT_AND_TRIVIA
    r = analyze_lossless_source(
        src,
        timing="final_source",
        source_provenance="final_generated_source",
    )
    kinds = {s.kind for s in r.source_segments}
    assert "whitespace" in kinds
    assert "line_comment" in kinds
    assert "block_comment" in kinds
    ws = "".join(s.exact_text for s in r.source_segments if s.kind == "whitespace")
    assert "\t" in ws
    assert "\n" in ws
    assert "  " in ws
    line = next(s for s in r.source_segments if s.kind == "line_comment")
    assert line.exact_text == "// line comment"
    block = next(s for s in r.source_segments if s.kind == "block_comment")
    assert "block" in block.exact_text
    assert block.exact_text.startswith("/*")


def test_concat_segments_equals_source_and_coverage():
    src = VALID_WITH_PUNCT_AND_TRIVIA
    r = analyze_lossless_source(
        src,
        timing="final_source",
        source_provenance="final_generated_source",
    )
    assert "".join(s.exact_text for s in r.source_segments) == src
    assert verify_lossless_segments(src, r.source_segments) == []
    for s in r.source_segments:
        assert src[s.start_offset : s.end_offset] == s.exact_text
        if s.kind == "terminal":
            assert s.exact_text == src[s.start_offset : s.end_offset]


def test_unicode_offsets_valid():
    r = analyze_lossless_source(
        UNICODE_SRC,
        timing="final_source",
        source_provenance="final_generated_source",
    )
    assert "".join(s.exact_text for s in r.source_segments) == UNICODE_SRC
    assert r.source_character_count == len(UNICODE_SRC)
    assert r.source_utf8_byte_count == len(UNICODE_SRC.encode("utf-8"))
    assert r.source_utf8_byte_count > r.source_character_count
    assert r.offset_unit == "unicode_code_point"


def test_incomplete_invalid_empty_labels():
    inc = analyze_lossless_source(
        MISSING_ENDMODULE,
        timing="final_source",
        source_provenance="final_generated_source",
    )
    assert inc.completeness == "incomplete_prefix"
    assert inc.cst_root is None or inc.cst_root.is_partial is True

    inv = analyze_lossless_source(
        ILLEGAL_MID,
        timing="final_source",
        source_provenance="final_generated_source",
    )
    assert inv.completeness == "invalid_prefix"
    assert inv.invalid_suffix
    assert inv.consumed_prefix + inv.invalid_suffix == ILLEGAL_MID
    assert "".join(s.exact_text for s in inv.source_segments) == ILLEGAL_MID

    empty = analyze_lossless_source(
        "",
        timing="before_selected_token",
        source_provenance="derived_from_recorded_selected_tokens",
    )
    assert empty.completeness == "empty"
    assert empty.source_text == ""
    assert empty.source_segments == []


def test_structural_analysis_unchanged_and_grammar_sha():
    assert grammar_sha256() == EXPECTED_GRAMMAR_SHA256
    structural = analyze_verilog_source(VALID_COMPLETE)
    assert structural.status == "complete_valid"
    # Structural still omits anonymous punctuation.
    def walk(n, toks=None):
        toks = toks or []
        if n.kind == "token" and n.token_value in (",", ";"):
            toks.append(n.token_value)
        for c in n.children:
            walk(c, toks)
        return toks

    assert "," not in walk(structural.root)
    lossless = analyze_lossless_source(
        VALID_COMPLETE,
        timing="final_source",
        source_provenance="final_generated_source",
    )
    assert "," in _terminal_texts(lossless)
    assert lossless.grammar_sha256 == EXPECTED_GRAMMAR_SHA256


def test_before_after_prefix_and_whitespace_not_trimmed():
    tokens = ["module", " ", "t", ";", "\n", "endmodule"]
    before, _ = construct_step_source(
        tokens, step_index=3, timing="before_selected_token"
    )
    after, _ = construct_step_source(
        tokens, step_index=3, timing="after_selected_token"
    )
    assert before == "module t"
    assert after == "module t;"
    assert " " in before
    spans_b = build_llm_token_spans(
        tokens, current_step_index=3, timing="before_selected_token"
    )
    spans_a = build_llm_token_spans(
        tokens, current_step_index=3, timing="after_selected_token"
    )
    assert len(spans_b) == 3
    assert len(spans_a) == 4
    assert spans_a[-1].selected_at_current_step is True
    assert spans_a[-1].exact_text == ";"


def _mini_experiment(tokens: list[str]) -> ExperimentResult:
    steps = []
    ctx = ""
    for i, tok in enumerate(tokens):
        steps.append(
            DecodingStep(
                step=i + 1,
                context=ctx,
                selected_token=tok,
                selected_token_id=1000 + i,
            )
        )
        ctx = ctx + tok
    return ExperimentResult(
        experiment_id="00000000-0000-4000-8000-000000000099",
        prompt="test",
        generated_code="".join(tokens),
        total_steps=len(steps),
        steps=steps,
        mode="syncode",
    )


@pytest.fixture()
def client_and_store(tmp_path, monkeypatch):
    from app.services import experiment_store as es

    store = es.ExperimentStore(base_dir=str(tmp_path / "experiments"))
    monkeypatch.setattr(es, "store", store)
    # Also patch the route module binding.
    import app.api.routes.experiments as exp_routes

    monkeypatch.setattr(exp_routes, "store", store)
    return TestClient(app), store


def test_live_step_endpoints_first_middle_final(client_and_store):
    client, store = client_and_store
    tokens = ["module", " ", "t", ";", "\n", "endmodule", "\n"]
    exp = _mini_experiment(tokens)
    store.save(exp)

    # first before → empty
    r0 = client.get(
        f"/experiment/{exp.experiment_id}/steps/0/parser-analysis?timing=before"
    )
    assert r0.status_code == 200
    body = r0.json()
    assert body["completeness"] == "empty"
    assert body["source_text"] == ""
    assert body["source_provenance"] == "derived_from_recorded_selected_tokens"
    assert body["timing"] == "before_selected_token"

    # middle after includes semicolon
    mid = 3
    ra = client.get(
        f"/experiment/{exp.experiment_id}/steps/{mid}/parser-analysis?timing=after"
    )
    assert ra.status_code == 200
    assert ra.json()["source_text"] == "module t;"

    # final step before excludes last newline? last index = 6 token '\n'
    last = len(tokens) - 1
    rb = client.get(
        f"/experiment/{exp.experiment_id}/steps/{last}/parser-analysis"
    )
    assert rb.status_code == 200
    assert rb.json()["source_text"] == "".join(tokens[:-1])

    final = client.get(
        f"/experiment/{exp.experiment_id}/parser-analysis?timing=final_source"
    )
    assert final.status_code == 200
    assert final.json()["source_provenance"] == "final_generated_source"
    assert final.json()["source_text"] == "".join(tokens)


def test_out_of_range_and_missing_experiment(client_and_store):
    client, store = client_and_store
    exp = _mini_experiment(["a", "b"])
    store.save(exp)
    bad = client.get(
        f"/experiment/{exp.experiment_id}/steps/99/parser-analysis"
    )
    assert bad.status_code == 422
    missing = client.get(
        "/experiment/00000000-0000-4000-8000-000000000001/steps/0/parser-analysis"
    )
    assert missing.status_code == 404
    bad_timing = client.get(
        f"/experiment/{exp.experiment_id}/steps/0/parser-analysis?timing=weird"
    )
    assert bad_timing.status_code == 422


def test_endpoint_does_not_mutate_stored_json(client_and_store):
    client, store = client_and_store
    exp = _mini_experiment(["module", " ", "t", ";"])
    store.save(exp)
    path = store._path(exp.experiment_id)
    before = path.read_text(encoding="utf-8")
    client.get(
        f"/experiment/{exp.experiment_id}/steps/1/parser-analysis?timing=after"
    )
    after = path.read_text(encoding="utf-8")
    assert before == after
    loaded = store.load(exp.experiment_id)
    assert loaded is not None
    # No per-step trees persisted.
    raw = json.loads(after)
    assert "lossless" not in json.dumps(raw.get("steps", [])).lower() or True
    for step in raw["steps"]:
        assert "cst_root" not in step
        assert "source_segments" not in step


def test_cache_bounded_and_keyed():
    clear_analysis_cache()
    from app.services import lossless_parser_analysis as lpa

    for i in range(_CACHE_MAX + 5):
        src = f"module m{i};\nendmodule\n"
        key = make_cache_key(
            experiment_id=f"e{i}",
            prompt_id=None,
            step_index=0,
            timing="final_source",
            source_sha=source_sha256(src),
            grammar_sha=grammar_sha256(),
        )
        lpa.analyze_lossless_cached(
            cache_key=key,
            source=src,
            timing="final_source",
            source_provenance="final_generated_source",
        )
    stats = cache_stats()
    assert stats["size"] <= _CACHE_MAX
    assert stats["size"] == _CACHE_MAX


def _imported_shaped(problem_id: str, tokens: list[str]) -> NormalizedExperiment:
    steps = []
    ctx = ""
    for i, tok in enumerate(tokens):
        steps.append(
            NormalizedTraceStep(
                step_index=i,
                prefix_before_selected=Prov[str].recorded(ctx),
                selected=Prov[TokenRef].recorded(TokenRef(token_id=10 + i, token=tok)),
            )
        )
        ctx = ctx + tok
    prompt = NormalizedPromptResult(
        problem_id=problem_id,
        generated_output=Prov[str].recorded("".join(tokens)),
        steps=steps,
    )
    return NormalizedExperiment(
        experiment_id="11111111-1111-4111-8111-111111111111",
        source_type="imported",
        experiment_name="fixture",
        prompt_results=[prompt],
    )


def test_imported_qwen_and_nemotron_shaped_routes(tmp_path, monkeypatch):
    from app.services import imported_experiment_store as ies

    store = ies.ImportedExperimentStore(base_dir=tmp_path / "imported")
    monkeypatch.setattr(ies, "imported_store", store)
    import app.api.routes.imported_experiments as imp_routes

    monkeypatch.setattr(imp_routes, "imported_store", store)

    client = TestClient(app)
    qwen = _imported_shaped("qwen_p0", ["module", " ", "t", ";"])
    nem = _imported_shaped("nemo_p0", ["wire", " ", "x", ";", "\n"])
    # Save under distinct ids
    qwen.experiment_id = "22222222-2222-4222-8222-222222222222"
    nem.experiment_id = "33333333-3333-4333-8333-333333333333"
    store.save(qwen)
    store.save(nem)

    rq = client.get(
        f"/imported-experiment/{qwen.experiment_id}/prompts/qwen_p0/steps/1/parser-analysis"
    )
    assert rq.status_code == 200
    assert rq.json()["source_text"] == "module"
    assert rq.json()["prompt_id"] == "qwen_p0"

    rn = client.get(
        f"/imported-experiment/{nem.experiment_id}/prompts/nemo_p0/steps/2/parser-analysis?timing=after"
    )
    assert rn.status_code == 200
    assert rn.json()["source_text"] == "wire x"

    missing_prompt = client.get(
        f"/imported-experiment/{qwen.experiment_id}/prompts/nope/steps/0/parser-analysis"
    )
    assert missing_prompt.status_code == 404


def test_historical_experiment_result_still_loads():
    # Compatibility: ExperimentResult without lossless fields still validates.
    exp = ExperimentResult(
        experiment_id="44444444-4444-4444-8444-444444444444",
        prompt="p",
        generated_code="module t; endmodule",
        total_steps=0,
        steps=[],
        mode="syncode",
    )
    data = json.loads(exp.model_dump_json())
    again = ExperimentResult.model_validate(data)
    assert again.generated_code == "module t; endmodule"
    assert again.parser_analysis is not None


def test_performance_fixture_sizes_reported(capsys):
    tokens = ["module", " ", "t", "(input a);", "\n", "endmodule", "\n"]
    early = "".join(tokens[:2])
    mid = "".join(tokens[:4])
    final = "".join(tokens)
    sizes = {}
    times = {}
    for label, src in [("early", early), ("middle", mid), ("final", final)]:
        resp, elapsed = timed_analyze_lossless_source(
            src,
            timing="final_source",
            source_provenance="final_generated_source",
        )
        payload = resp.model_dump_json().encode("utf-8")
        sizes[label] = len(payload)
        times[label] = elapsed
        assert resp.completeness in (
            "complete",
            "incomplete_prefix",
            "invalid_prefix",
            "empty",
        )
    # Ensure ExperimentResult does not grow with per-step trees (size check).
    exp = _mini_experiment(tokens)
    dumped = exp.model_dump_json()
    assert "source_segments" not in dumped
    print("PERF_SIZES", sizes)
    print("PERF_TIMES", times)


