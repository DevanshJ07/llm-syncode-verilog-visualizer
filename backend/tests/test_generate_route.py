"""Route-level tests for /generate (lightweight created response + persistence)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.models.schemas import DecodingStep, ExperimentResult, TopToken
from app.services.experiment_store import store
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


def _post_payload(**overrides):
    body = {
        "prompt": "test",
        "use_syncode": False,
        "max_new_tokens": 4,
        "top_k": 5,
        "temperature": 1.0,
    }
    body.update(overrides)
    return body


def test_generate_returns_500_on_empty_steps(client):
    with patch(
        "app.services.generation_runner.llm_service.generate",
        new_callable=AsyncMock,
        return_value=("", [], "", False),
    ):
        r = client.post("/generate", json=_post_payload())
    assert r.status_code == 500, r.text
    detail = r.json()["detail"]
    assert "zero decoding steps" in str(detail) or "generation_failed" in str(detail)


def test_generate_returns_500_on_validation_error(client):
    with patch(
        "app.services.generation_runner.llm_service.generate",
        new_callable=AsyncMock,
        side_effect=GenerationFailedError(
            "test failure",
            reasons=["zero decoding steps"],
            step_count=0,
        ),
    ):
        r = client.post("/generate", json=_post_payload())
    assert r.status_code == 500
    assert r.json()["detail"]["error"] == "generation_failed"


def test_generate_returns_lightweight_created_response(client):
    valid_verilog = """module m(a, y);
  input a;
  output y;
  assign y = a;
endmodule"""
    with patch(
        "app.services.generation_runner.llm_service.generate",
        new_callable=AsyncMock,
        return_value=(valid_verilog, [_step()], "", False),
    ):
        r = client.post("/generate", json=_post_payload(use_syncode=False))

    assert r.status_code == 200, r.text
    body = r.json()

    assert body["status"] == "completed"
    assert body["mode"] == "raw"
    assert body["step_count"] == 1
    assert body["final_parse_valid"] is True
    assert body["experiment_id"]
    assert body["detail_path"] == f"/experiment/{body['experiment_id']}"
    assert body["created_at"]

    # Lightweight: no full-trace fields.
    assert "steps" not in body
    assert "generated_text" not in body
    assert "parse_tree_text" not in body
    assert "parser_analysis" not in body
    assert "top_tokens" not in body


def test_generate_persists_full_experiment_retrievable_by_get(client):
    valid_verilog = """module m(a, y);
  input a;
  output y;
  assign y = a;
endmodule"""
    with patch(
        "app.services.generation_runner.llm_service.generate",
        new_callable=AsyncMock,
        return_value=(valid_verilog, [_step()], "parse_complete", False),
    ):
        r = client.post("/generate", json=_post_payload())

    assert r.status_code == 200, r.text
    created = r.json()
    eid = created["experiment_id"]
    assert created["early_termination"] == "parse_complete"
    assert created["step_count"] == 1

    detail = client.get(f"/experiment/{eid}")
    assert detail.status_code == 200, detail.text
    exp = detail.json()
    assert exp["experiment_id"] == eid
    assert exp["total_steps"] == 1
    assert len(exp["steps"]) == 1
    assert exp["steps"][0]["selected_token"] == "x"
    assert "module m" in exp["generated_code"]
    assert exp["final_parse_valid"] is True
    assert exp["mode"] == "raw"

    loaded = store.load(eid)
    assert loaded is not None
    assert loaded.total_steps == 1
    assert len(loaded.steps) == 1


def test_generate_passes_syncode_and_options_unchanged(client):
    valid_verilog = """module m(a, y);
  input a;
  output y;
  assign y = a;
endmodule"""
    mock_gen = AsyncMock(return_value=(valid_verilog, [_step()], "", False))
    with patch(
        "app.services.generation_runner.llm_service.generate",
        new=mock_gen,
    ):
        r = client.post(
            "/generate",
            json={
                "prompt": "write mux",
                "use_syncode": True,
                "max_new_tokens": 32,
                "top_k": 11,
                "temperature": 0.7,
                "do_sample": False,
                "top_p": 0.9,
                "repetition_penalty": 1.2,
            },
        )
    assert r.status_code == 200, r.text
    mock_gen.assert_awaited_once()
    kwargs = mock_gen.await_args.kwargs
    assert kwargs["prompt"] == "write mux"
    assert kwargs["use_syncode"] is True
    assert kwargs["max_new_tokens"] == 32
    assert kwargs["top_k"] == 11
    assert kwargs["temperature"] == 0.7
    assert kwargs["do_sample"] is False
    assert kwargs["top_p"] == 0.9
    assert kwargs["repetition_penalty"] == 1.2
    assert r.json()["mode"] == "syncode"
    assert r.json()["constraint_requested"] is True


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
        "app.services.generation_runner.llm_service.generate",
        new_callable=AsyncMock,
        return_value=(invalid, [_step()], "max_tokens_incomplete", False),
    ), patch(
        "app.services.generation_runner.llm_service._syncode",
        available=True,
    ):
        r = client.post("/generate", json=_post_payload(use_syncode=True))

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["final_parse_valid"] is False
    assert body["mode"] == "syncode"
    assert body["early_termination"] == "max_tokens_incomplete"
    assert body["step_count"] == 1
    assert "steps" not in body
    assert "generated_text" not in body
    assert "parser_analysis" not in body

    detail = client.get(f"/experiment/{body['experiment_id']}").json()
    assert detail["final_parse_valid"] is False
    assert len(detail["unsupported_constructs_detected"]) > 0
    assert detail["constraint_applied"] is False
    assert "parser_analysis" in detail
    assert detail["parser_analysis"]["status"] in (
        "incomplete_prefix",
        "invalid_input",
        "unavailable",
    )


def test_existing_saved_experiment_json_still_loads(tmp_path):
    """Full ExperimentResult on disk remains the GET/store contract."""
    from app.services.experiment_store import ExperimentStore

    s = ExperimentStore(base_dir=str(tmp_path))
    exp = ExperimentResult(
        experiment_id="legacy-load-test",
        prompt="p",
        mode="syncode",
        model_name="test-model",
        generated_code="module m; endmodule",
        steps=[_step()],
        total_steps=1,
        created_at="2026-01-01T00:00:00+00:00",
        final_parse_valid=False,
        syncode_stopped_reason="max_tokens_incomplete",
    )
    s.save(exp)
    loaded = s.load("legacy-load-test")
    assert loaded is not None
    assert loaded.total_steps == 1
    assert len(loaded.steps) == 1
    assert loaded.mode == "syncode"
    assert loaded.syncode_stopped_reason == "max_tokens_incomplete"
