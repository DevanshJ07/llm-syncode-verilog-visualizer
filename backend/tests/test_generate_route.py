"""Route-level tests for strict /generate failure handling."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.models.schemas import DecodingStep, TopToken
from app.services.generation_validation import GenerationFailedError
from main import app


def _step() -> DecodingStep:
    return DecodingStep(
        step=1,
        context="",
        top_tokens=[TopToken(token="x", probability=0.9, token_id=1)],
        selected_token="x",
        selected_token_id=1,
        entropy_before=0.5,
    )


@pytest.fixture
def client():
    return TestClient(app)


def test_generate_returns_500_on_empty_steps(client):
    # generate() returns (text, steps, early_termination, eos_allowed_at_completion)
    with patch(
        "app.api.routes.generate.llm_service.generate",
        new_callable=AsyncMock,
        return_value=("", [], "", False),
    ):
        r = client.post(
            "/generate",
            json={
                "prompt": "test",
                "use_syncode": False,
                "max_new_tokens": 4,
                "top_k": 5,
                "temperature": 1.0,
            },
        )
    assert r.status_code == 500, r.text
    detail = r.json()["detail"]
    assert "zero decoding steps" in str(detail) or "generation_failed" in str(detail)


def test_generate_returns_500_on_validation_error(client):
    with patch(
        "app.api.routes.generate.llm_service.generate",
        new_callable=AsyncMock,
        side_effect=GenerationFailedError(
            "test failure",
            reasons=["zero decoding steps"],
            step_count=0,
        ),
    ):
        r = client.post(
            "/generate",
            json={
                "prompt": "test",
                "use_syncode": False,
                "max_new_tokens": 4,
                "top_k": 5,
                "temperature": 1.0,
            },
        )
    assert r.status_code == 500
    assert r.json()["detail"]["error"] == "generation_failed"


def test_generate_returns_200_on_success(client):
    valid_verilog = """module m(a, y);
  input a;
  output y;
  assign y = a;
endmodule"""
    with patch(
        "app.api.routes.generate.llm_service.generate",
        new_callable=AsyncMock,
        return_value=(valid_verilog, [_step()], "", False),
    ):
        r = client.post(
            "/generate",
            json={
                "prompt": "test",
                "use_syncode": False,
                "max_new_tokens": 4,
                "top_k": 5,
                "temperature": 1.0,
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_steps"] == 1
    assert len(body["steps"]) == 1
    assert "module m" in body["generated_text"]
    assert body["final_parse_valid"] is True


def test_generate_returns_200_with_invalid_verilog_flagged(client):
    # Must be invalid under the canonical grammar (always/reg/@ are legal now).
    invalid = """module bad(a, y);
  input a;
  output y;
  generate
    if (1) begin
      assign y = a;
    end
  endgenerate
endmodule"""
    with patch(
        "app.api.routes.generate.llm_service.generate",
        new_callable=AsyncMock,
        return_value=(invalid, [_step()], "max_tokens_incomplete", False),
    ), patch(
        "app.api.routes.generate.llm_service._syncode",
        available=True,
    ):
        r = client.post(
            "/generate",
            json={
                "prompt": "test",
                "use_syncode": True,
                "max_new_tokens": 4,
                "top_k": 5,
                "temperature": 1.0,
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["final_parse_valid"] is False
    assert len(body["unsupported_constructs_detected"]) > 0
    assert body["constraint_applied"] is False
    assert body["mode"] == "syncode"
    # Phase 3A: structured analysis present without breaking the response.
    assert "parser_analysis" in body
    assert body["parser_analysis"]["status"] in (
        "incomplete_prefix",
        "invalid_input",
        "unavailable",
    )
