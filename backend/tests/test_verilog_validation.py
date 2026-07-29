"""Tests for final Verilog output validation."""

from __future__ import annotations

import pytest

from app.services.verilog_validation import (
    compute_constraint_status,
    detect_unsupported_constructs,
    strip_verilog_comments,
    validate_verilog_output,
)

VALID_FA = """\
module fa_cell(a, b, carry_in, sum, carry_out);
  input a, b, carry_in;
  output sum, carry_out;

  assign sum = a ^ b ^ carry_in;
  assign carry_out = (a & b) | (b & carry_in) | (a & carry_in);
endmodule
"""

INVALID_ALWAYS = """\
module bad(a, sum);
  input a;
  output reg sum;
  always @(*) sum = a;
endmodule
"""

INVALID_INPUT_WIRE = """\
module bad(a, y);
  input wire a;
  output y;
  assign y = a;
endmodule
"""

MUX_WITH_COMMENTS = """\
module mux_cell(a, b, select, out);
  input a, b;
  input select;   // Notice the change from sel to select
  output out;
  assign out = (select)? b : a;
endmodule
// module
/* new code */ /* end new code */
"""


def test_strip_verilog_comments_removes_line_and_block():
    code = "input a; // line\nassign x = a; /* block */ endmodule"
    stripped = strip_verilog_comments(code)
    assert "//" not in stripped
    assert "/*" not in stripped
    assert "Notice" not in stripped
    assert "assign x = a;" in stripped


def test_comment_slashes_not_flagged_as_arithmetic():
    unsupported = detect_unsupported_constructs(MUX_WITH_COMMENTS)
    assert "arithmetic /" not in unsupported
    assert "always" not in unsupported


def test_mux_with_trailing_comments_not_invalid_due_to_slash():
    result = validate_verilog_output(MUX_WITH_COMMENTS)
    assert "arithmetic /" not in result.unsupported_constructs_detected
    assert result.comments_stripped_for_validation is True
    # Primary validity from Lark parser (grammar ignores comments)
    assert result.final_parse_valid is True
    assert result.unsupported_constructs_detected == []


def test_valid_full_adder_parses():
    result = validate_verilog_output(VALID_FA)
    assert result.final_parse_valid is True
    assert result.unsupported_constructs_detected == []
    assert result.final_parse_error == ""


def test_always_reg_case_detected():
    unsupported = detect_unsupported_constructs(INVALID_ALWAYS)
    assert "always" in unsupported
    assert "reg" in unsupported
    assert "@" in unsupported

    result = validate_verilog_output(INVALID_ALWAYS)
    assert result.final_parse_valid is False
    assert "always" in result.unsupported_constructs_detected


def test_input_wire_detected():
    result = validate_verilog_output(INVALID_INPUT_WIRE)
    assert result.final_parse_valid is False
    assert "input wire" in result.unsupported_constructs_detected


def test_arithmetic_division_outside_comments_is_detected():
    code = """\
module div(a, b, y);
  input a, b;
  output y;
  assign y = a / b;
endmodule
"""
    result = validate_verilog_output(code)
    assert result.final_parse_valid is True
    assert "arithmetic /" in result.unsupported_constructs_detected
    assert "arithmetic /" in result.unsupported_constructs_detected


def test_partial_constraint_not_marked_applied_when_output_invalid():
    evidence = compute_constraint_status(
        mode="syncode",
        syncode_available=True,
        total_steps=64,
        syncode_active_steps=10,
        syncode_fallback_steps=54,
        final_parse_valid=False,
        final_parse_error="unsupported constructs detected: always",
    )
    assert evidence.constraint_status == "partial"
    assert evidence.constraint_applied is False
    assert evidence.fallback_occurred is True
    assert evidence.syncode_error != ""


def test_full_constraint_only_when_all_steps_and_valid_output():
    evidence = compute_constraint_status(
        mode="syncode",
        syncode_available=True,
        total_steps=64,
        syncode_active_steps=64,
        syncode_fallback_steps=0,
        final_parse_valid=True,
        final_parse_error="",
    )
    assert evidence.constraint_status == "full"
    assert evidence.constraint_applied is True
    assert evidence.fallback_occurred is False


def test_full_steps_but_invalid_output_not_applied():
    evidence = compute_constraint_status(
        mode="syncode",
        syncode_available=True,
        total_steps=64,
        syncode_active_steps=64,
        syncode_fallback_steps=0,
        final_parse_valid=False,
        final_parse_error="parse error",
    )
    assert evidence.constraint_status == "failed"
    assert evidence.constraint_applied is False


@pytest.mark.skipif(
    not __import__("importlib").util.find_spec("lark"),
    reason="lark not installed",
)
def test_lark_parse_valid_mux():
    code = """\
module mux_cell(a, b, select, out);
  input a, b, select;
  output out;
  assign out = select ? b : a;
endmodule
"""
    result = validate_verilog_output(code)
    assert result.final_parse_valid is True
