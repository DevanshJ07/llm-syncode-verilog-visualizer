"""
Phase 3A — structured parser analysis for complete / incomplete / invalid Verilog.

Lark-only tests. Does not load SynCode, mask stores, or models.
"""

from __future__ import annotations

from types import SimpleNamespace

from app.core.config import settings
from app.core.grammar import EXPECTED_GRAMMAR_SHA256, grammar_sha256
from app.models.normalized import NormalizedExperiment
from app.models.parser_analysis import unavailable_parser_analysis
from app.models.schemas import ExperimentResult, GenerateResponse
from app.services.parser_analysis import (
    analyze_verilog_source,
    binary_verdict_from_analysis,
    read_interactive_value_stack,
    status_disagrees_with_recorded_verdict,
)
from app.services.verilog_validation import validate_verilog_output

VALID_COMPLETE = """\
module t(input a, output b);
  assign b = a;
endmodule
"""

VALID_WITH_COMMENTS = """\
module t(input a, output b);
  // line comment before assign
  /* block
     comment spanning lines */
  assign b = a; // trailing line comment
endmodule
"""

MISSING_ENDMODULE = """\
module t(input a, output b);
  assign b = a;
"""

TRUNCATED_PORT = """\
module t(input a, output
"""

ASSIGN_INCOMPLETE = """\
module t(input e, output out);
  assign out[0] = ~e
"""

ILLEGAL_MID = """\
module t(input a, output b);
  assign b = @ a;
endmodule
"""

TRAILING_GARBAGE = """\
module t(input a, output b);
  assign b = a;
endmodule
junk leftover
"""

COMMENT_THEN_ERR = """\
module t(input a, output b);
  // comment line
  assign b = @ a;
endmodule
"""

MULTILINE_BLOCK_COMMENT_THEN_ERR = """\
module t(input a, output b);
  /* multiline
     block comment */
  assign b = @ a;
endmodule
"""

MULTILINE_ERR = """\
module t(input a, output b);
  assign b = a;
  assign c = @ x;
endmodule
"""


def _collect_ids(node):
    out = [node.id]
    for c in node.children:
        out.extend(_collect_ids(c))
    return out


def _count_nodes(node) -> int:
    return 1 + sum(_count_nodes(c) for c in node.children)


def test_model_module_has_no_lark_analysis_api():
    import app.models.parser_analysis as models_mod

    assert not hasattr(models_mod, "analyze_verilog_source")
    assert not hasattr(models_mod, "read_interactive_value_stack")
    assert not hasattr(models_mod, "_analysis_lark_parser")


def test_valid_complete_is_genuine_tree():
    a = analyze_verilog_source(VALID_COMPLETE)
    assert a.status == "complete_valid"
    assert a.representation_kind == "complete_parse_tree"
    assert a.is_complete is True
    assert a.is_partial is False
    assert a.is_recovered is False
    assert a.label == "Complete Lark parse tree"
    assert a.root is not None
    assert a.root.kind == "rule"
    assert a.root.kind != "synthetic_root"
    assert a.invalid_suffix == ""
    assert a.parsed_prefix == VALID_COMPLETE
    assert a.accepts_end is True
    assert a.grammar_sha256 == grammar_sha256()
    assert a.grammar_sha256 == EXPECTED_GRAMMAR_SHA256


def test_complete_tree_from_unstripped_source_with_line_and_block_comments():
    a = analyze_verilog_source(VALID_WITH_COMMENTS)
    assert a.status == "complete_valid"
    assert a.representation_kind == "complete_parse_tree"
    assert a.root is not None
    assert a.root.kind == "rule"
    assert a.parsed_prefix == VALID_WITH_COMMENTS
    assert a.invalid_suffix == ""
    # Source was not comment-stripped for analysis.
    assert "comment" in a.comment_handling.lower()
    assert "not comment-stripped" in a.comment_handling.lower()


def test_complete_root_children_are_lark_reductions_not_synthetic():
    a = analyze_verilog_source(VALID_WITH_COMMENTS)
    assert a.root is not None
    assert a.root.kind == "rule"
    for child in a.root.children:
        assert child.kind in ("rule", "token")
        assert child.kind != "synthetic_root"
        assert child.kind != "recovery_marker"


def test_missing_endmodule_incomplete_partial_forest():
    a = analyze_verilog_source(MISSING_ENDMODULE)
    assert a.status == "incomplete_prefix"
    assert a.representation_kind == "partial_parse_forest"
    assert a.is_partial is True
    assert a.is_complete is False
    assert "Partial parser stack" in a.label
    assert a.invalid_suffix == ""
    assert a.parsed_prefix == MISSING_ENDMODULE
    assert a.accepts_end is False
    assert a.root is not None
    assert a.root.kind == "synthetic_root"
    assert "ENDMODULE" in a.expected_next_terminals or "endmodule" in [
        t.lower() for t in a.expected_next_terminals
    ]


def test_truncated_port_incomplete():
    a = analyze_verilog_source(TRUNCATED_PORT)
    assert a.status == "incomplete_prefix"
    assert a.representation_kind == "partial_parse_forest"
    assert a.is_complete is False
    assert a.invalid_suffix == ""


def test_assign_out_incomplete():
    a = analyze_verilog_source(ASSIGN_INCOMPLETE)
    assert a.status == "incomplete_prefix"
    assert a.representation_kind == "partial_parse_forest"
    assert a.is_complete is False
    assert a.invalid_suffix == ""


def test_illegal_token_invalid_recovered():
    a = analyze_verilog_source(ILLEGAL_MID)
    assert a.status == "invalid_input"
    assert a.representation_kind == "recovered_prefix_forest"
    assert a.is_recovered is True
    assert a.is_complete is False
    assert "Recovered parser stack" in a.label
    assert a.invalid_suffix.startswith("@") or "@" in a.invalid_suffix
    assert a.parsed_prefix + a.invalid_suffix == ILLEGAL_MID
    assert a.unexpected_token_or_char in ("@", "@ a") or "@" in a.unexpected_token_or_char
    assert a.error_offset is not None
    assert a.error_offset == ILLEGAL_MID.index("@")


def test_trailing_garbage_invalid_recovered():
    a = analyze_verilog_source(TRAILING_GARBAGE)
    assert a.status == "invalid_input"
    assert a.representation_kind == "recovered_prefix_forest"
    assert "endmodule" in a.parsed_prefix
    assert a.invalid_suffix.lstrip().startswith("junk")
    assert a.parsed_prefix + a.invalid_suffix == TRAILING_GARBAGE
    assert a.accepts_end is True or "$END" in a.expected_next_terminals


def test_empty_source_incomplete():
    a = analyze_verilog_source("")
    assert a.status == "incomplete_prefix"
    assert a.representation_kind == "partial_parse_forest"
    assert a.is_complete is False
    assert a.invalid_suffix == ""


def test_whitespace_only_incomplete():
    a = analyze_verilog_source("   \n\t  ")
    assert a.status == "incomplete_prefix"
    assert a.is_complete is False
    assert a.invalid_suffix == ""


def test_comments_preserve_error_coordinates():
    a = analyze_verilog_source(COMMENT_THEN_ERR)
    assert a.status == "invalid_input"
    assert a.error_line == 3
    assert a.error_column == 14
    assert a.error_offset == COMMENT_THEN_ERR.index("@")


def test_multiline_block_comment_preserves_error_coordinates():
    a = analyze_verilog_source(MULTILINE_BLOCK_COMMENT_THEN_ERR)
    assert a.status == "invalid_input"
    assert a.error_offset == MULTILINE_BLOCK_COMMENT_THEN_ERR.index("@")
    assert a.error_line == 4
    assert a.error_column == 14
    assert a.parsed_prefix + a.invalid_suffix == MULTILINE_BLOCK_COMMENT_THEN_ERR


def test_multiline_error_line_column():
    a = analyze_verilog_source(MULTILINE_ERR)
    assert a.status == "invalid_input"
    assert a.error_line == 3
    assert a.error_column is not None and a.error_column > 0
    assert a.parsed_prefix + a.invalid_suffix == MULTILINE_ERR


def test_prefix_plus_suffix_preserves_source():
    for src in (ILLEGAL_MID, TRAILING_GARBAGE, COMMENT_THEN_ERR, MISSING_ENDMODULE):
        a = analyze_verilog_source(src)
        assert a.parsed_prefix + a.invalid_suffix == src


def test_expected_terminals_parser_derived():
    a = analyze_verilog_source(MISSING_ENDMODULE)
    assert isinstance(a.expected_next_terminals, list)
    assert len(a.expected_next_terminals) > 0
    assert all(not t.isdigit() for t in a.expected_next_terminals)


def test_partial_not_labelled_complete():
    a = analyze_verilog_source(MISSING_ENDMODULE)
    assert a.representation_kind != "complete_parse_tree"
    assert a.is_complete is False


def test_recovered_not_labelled_complete():
    a = analyze_verilog_source(ILLEGAL_MID)
    assert a.representation_kind != "complete_parse_tree"
    assert a.is_complete is False


def test_stable_deterministic_node_ids():
    a1 = analyze_verilog_source(VALID_COMPLETE)
    a2 = analyze_verilog_source(VALID_COMPLETE)
    assert a1.root is not None and a2.root is not None
    assert _collect_ids(a1.root) == _collect_ids(a2.root)
    assert _collect_ids(a1.root)[0] == "n0"
    assert len(_collect_ids(a1.root)) == len(set(_collect_ids(a1.root)))


def test_token_values_preserve_whitespace_where_present():
    a = analyze_verilog_source(MISSING_ENDMODULE)
    assert a.root is not None

    def walk(n):
        if n.kind == "token" and n.token_value is not None:
            yield n
        for c in n.children:
            yield from walk(c)

    tokens = list(walk(a.root))
    assert any(t.token_value == "module" for t in tokens)
    assert any(t.token_value == "t" for t in tokens)
    for t in tokens:
        assert isinstance(t.token_value, str)


def test_safety_limits_truncate_with_warning_and_deterministic_ids(monkeypatch):
    monkeypatch.setattr(settings, "parser_analysis_max_nodes", 3)
    a1 = analyze_verilog_source(VALID_COMPLETE)
    a2 = analyze_verilog_source(VALID_COMPLETE)
    assert a1.truncated is True
    assert any("truncat" in w.lower() for w in a1.warnings)
    assert a1.root is not None
    assert _count_nodes(a1.root) <= 3 + 2  # root + markers may add a few
    # Hard ceiling: serialized tree must not grow without bound past the limit
    # by more than the recovery markers the builder attaches at the cut.
    assert a1.node_count <= 8
    assert _collect_ids(a1.root) == _collect_ids(a2.root)
    assert _collect_ids(a1.root)[0] == "n0"


def test_depth_limit_truncates_with_warning(monkeypatch):
    monkeypatch.setattr(settings, "parser_analysis_max_depth", 1)
    a = analyze_verilog_source(VALID_COMPLETE)
    assert a.truncated is True or any("depth" in w.lower() for w in a.warnings)
    assert any("truncat" in w.lower() or "depth" in w.lower() for w in a.warnings)


def test_existing_valid_references_match_final_verdict():
    val = validate_verilog_output(VALID_COMPLETE)
    a = analyze_verilog_source(VALID_COMPLETE)
    assert val.final_parse_valid is True
    assert binary_verdict_from_analysis(a) == "valid"

    val2 = validate_verilog_output(MISSING_ENDMODULE)
    a2 = analyze_verilog_source(MISSING_ENDMODULE)
    assert val2.final_parse_valid is False
    assert binary_verdict_from_analysis(a2) == "invalid"


def test_old_persisted_imported_json_without_parser_analysis_loads():
    payload = {
        "schema_version": "2A.2",
        "experiment_id": "00000000-0000-0000-0000-000000000099",
        "source_type": "imported",
        "experiment_name": "legacy",
        "created_at": "2026-01-01T00:00:00+00:00",
        "prompt_results": [
            {
                "problem_id": "ProbA",
                "steps": [],
                "warnings": [],
                "source_files": [],
            }
        ],
        "import_warnings": [],
    }
    exp = NormalizedExperiment.model_validate(payload)
    assert exp.prompt_results[0].parser_analysis.is_unavailable
    assert exp.prompt_results[0].parser_analysis.value is None


def test_live_result_schema_backward_compatible():
    payload = {
        "experiment_id": "exp-legacy",
        "prompt": "hello",
        "mode": "raw",
        "model_name": "test",
        "steps": [],
    }
    exp = ExperimentResult.model_validate(payload)
    assert exp.parser_analysis.status == "unavailable"
    resp = GenerateResponse.model_validate(
        {
            "experiment_id": "exp-legacy",
            "steps": [],
        }
    )
    assert resp.parser_analysis.status == "unavailable"


def test_unavailable_default_is_honest():
    u = unavailable_parser_analysis()
    assert u.status == "unavailable"
    assert u.representation_kind == "none"
    assert u.is_complete is False
    assert u.root is None


def test_disagreement_compares_against_complete_valid():
    a = analyze_verilog_source(VALID_COMPLETE)
    assert a.status == "complete_valid"
    assert status_disagrees_with_recorded_verdict("invalid", a) is True
    assert status_disagrees_with_recorded_verdict("fail", a) is True
    assert status_disagrees_with_recorded_verdict("valid", a) is False
    a2 = analyze_verilog_source(MISSING_ENDMODULE)
    assert a2.status != "complete_valid"
    assert status_disagrees_with_recorded_verdict("valid", a2) is True
    assert status_disagrees_with_recorded_verdict("pass", a2) is True
    assert status_disagrees_with_recorded_verdict("invalid", a2) is False


def test_parser_version_reports_syncode_larkm():
    a = analyze_verilog_source(VALID_COMPLETE)
    assert a.parser_version.startswith("syncode.larkm")
    assert "1.1.8" in a.parser_version


def test_value_stack_helper_success_on_incomplete():
    from app.services.parser_analysis import _analysis_lark_parser

    parser = _analysis_lark_parser()
    ip = parser.parse_interactive(MISSING_ENDMODULE)
    list(ip.exhaust_lexer())
    read = read_interactive_value_stack(ip)
    assert read.available is True
    assert read.warning == ""
    assert isinstance(read.values, list)
    assert len(read.values) > 0


def test_value_stack_helper_unavailable_when_missing():
    read = read_interactive_value_stack(None)
    assert read.available is False
    assert read.values == []
    assert "unavailable" in read.warning.lower()

    read2 = read_interactive_value_stack(SimpleNamespace())
    assert read2.available is False
    assert "parser_state" in read2.warning

    read3 = read_interactive_value_stack(
        SimpleNamespace(parser_state=SimpleNamespace())
    )
    assert read3.available is False
    assert "value_stack" in read3.warning.lower()


def test_value_stack_helper_failure_path_does_not_raise():
    class BoomState:
        @property
        def value_stack(self):
            raise RuntimeError("simulated incompatible Lark")

    read = read_interactive_value_stack(SimpleNamespace(parser_state=BoomState()))
    assert read.available is False
    assert read.values == []
    assert "failed" in read.warning.lower()


def test_analysis_survives_value_stack_unavailable(monkeypatch):
    import app.services.parser_analysis as svc

    def _deny(_ip):
        from app.services.parser_analysis import ValueStackRead

        return ValueStackRead(
            values=[],
            available=False,
            warning="value_stack unavailable: forced test path",
        )

    monkeypatch.setattr(svc, "read_interactive_value_stack", _deny)
    a = analyze_verilog_source(MISSING_ENDMODULE)
    assert a.status == "incomplete_prefix"
    assert a.invalid_suffix == ""
    assert a.parsed_prefix == MISSING_ENDMODULE
    assert any("value_stack unavailable" in w for w in a.warnings)
    assert a.root is not None
    assert a.root.kind == "synthetic_root"
    assert a.root.children
    assert a.root.children[0].kind == "recovery_marker"
    assert a.expected_next_terminals  # diagnostics still present


def test_unexpected_analysis_failure_returns_unavailable(monkeypatch):
    import app.services.parser_analysis as svc

    def _boom(*_a, **_k):
        raise RuntimeError("forced internal failure")

    monkeypatch.setattr(svc, "_analyze_verilog_source_impl", _boom)
    a = analyze_verilog_source(VALID_COMPLETE)
    assert a.status == "unavailable"
    assert a.representation_kind == "none"
    assert a.root is None
    assert any("unexpected parser analysis failure" in w for w in a.warnings)
    assert a.is_complete is False
