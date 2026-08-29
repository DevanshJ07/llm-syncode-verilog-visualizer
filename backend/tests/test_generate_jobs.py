"""Tests for async POST /generate/jobs + status polling."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.models.schemas import DecodingStep, GenerateCreatedResponse, TopToken
from app.services.experiment_store import store
from app.services.generation_jobs import MAX_QUEUED_JOBS, generation_jobs
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


def _payload(**overrides):
    body = {
        "prompt": "test",
        "use_syncode": False,
        "max_new_tokens": 4,
        "top_k": 5,
        "temperature": 1.0,
    }
    body.update(overrides)
    return body


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _wait_status(client: TestClient, job_id: str, want: str, timeout: float = 5.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = client.get(f"/generate/jobs/{job_id}")
        assert r.status_code == 200, r.text
        last = r.json()
        if last["status"] == want:
            return last
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} never reached {want}; last={last}")


def test_create_job_returns_promptly_with_job_id(client):
    gate = asyncio.Event()

    async def slow_runner(request, progress=None):
        await gate.wait()
        return GenerateCreatedResponse(
            experiment_id="exp-slow",
            status="completed",
            mode="raw",
            step_count=1,
            created_at="t",
            detail_path="/experiment/exp-slow",
        )

    with patch.object(generation_jobs, "_runner", side_effect=slow_runner):
        t0 = time.perf_counter()
        r = client.post("/generate/jobs", json=_payload())
        elapsed = time.perf_counter() - t0
        assert r.status_code == 202, r.text
        body = r.json()
        assert elapsed < 1.0, f"job create too slow: {elapsed:.3f}s"
        assert body["job_id"]
        assert body["status"] in ("queued", "running")
        assert body["status_path"] == f"/generate/jobs/{body['job_id']}"
        assert "steps" not in body
        assert "generated_text" not in body
        gate.set()
        _wait_status(client, body["job_id"], "completed")


def test_job_lifecycle_completed_exposes_experiment(client):
    valid = """module m(a, y);
  input a;
  output y;
  assign y = a;
endmodule"""
    with patch(
        "app.services.generation_runner.llm_service.generate",
        new_callable=AsyncMock,
        return_value=(valid, [_step()], "parse_complete", False),
    ):
        r = client.post("/generate/jobs", json=_payload(use_syncode=False))
        assert r.status_code == 202, r.text
        job_id = r.json()["job_id"]
        done = _wait_status(client, job_id, "completed", timeout=10.0)

    assert done["experiment_id"]
    assert done["error"] is None
    assert done["step_count"] == 1
    assert done["mode"] == "raw"
    assert done["detail_path"] == f"/experiment/{done['experiment_id']}"
    assert done["early_termination"] == "parse_complete"

    detail = client.get(f"/experiment/{done['experiment_id']}")
    assert detail.status_code == 200
    exp = detail.json()
    assert len(exp["steps"]) == 1
    assert exp["total_steps"] == 1
    loaded = store.load(done["experiment_id"])
    assert loaded is not None
    assert len(loaded.steps) == 1


def test_job_failure_from_generation_exception(client):
    async def boom(request, progress=None):
        raise GenerationFailedError(
            "boom",
            reasons=["test"],
            step_count=0,
        )

    with patch.object(generation_jobs, "_runner", side_effect=boom):
        r = client.post("/generate/jobs", json=_payload())
        job_id = r.json()["job_id"]
        failed = _wait_status(client, job_id, "failed")
    assert failed["experiment_id"] is None
    assert failed["error_code"] == "generation_failed"
    assert "boom" in (failed["error"] or "")


def test_save_failure_does_not_report_completed(client):
    async def save_fail(request, progress=None):
        raise RuntimeError("Generation succeeded but experiment save failed: disk full")

    with patch.object(generation_jobs, "_runner", side_effect=save_fail):
        r = client.post("/generate/jobs", json=_payload())
        job_id = r.json()["job_id"]
        failed = _wait_status(client, job_id, "failed")
    assert failed["status"] == "failed"
    assert failed["experiment_id"] is None
    assert "save failed" in (failed["error"] or "").lower()


def test_second_job_queued_while_first_runs(client):
    """Documented rule: extra jobs are queued FIFO (not rejected until cap)."""
    release_first = asyncio.Event()
    order: list[str] = []

    async def gated_runner(request, progress=None):
        order.append("start:" + request.prompt)
        if request.prompt == "first":
            await release_first.wait()
        order.append("end:" + request.prompt)
        return GenerateCreatedResponse(
            experiment_id=f"exp-{request.prompt}",
            status="completed",
            mode="raw",
            step_count=1,
            created_at="t",
            detail_path=f"/experiment/exp-{request.prompt}",
        )

    with patch.object(generation_jobs, "_runner", side_effect=gated_runner):
        r1 = client.post("/generate/jobs", json=_payload(prompt="first"))
        r2 = client.post("/generate/jobs", json=_payload(prompt="second"))
        assert r1.status_code == 202 and r2.status_code == 202
        id1, id2 = r1.json()["job_id"], r2.json()["job_id"]

        # Allow first to be picked up
        time.sleep(0.15)
        s1 = client.get(f"/generate/jobs/{id1}").json()
        s2 = client.get(f"/generate/jobs/{id2}").json()
        assert s1["status"] == "running"
        assert s2["status"] == "queued"

        release_first.set()
        _wait_status(client, id1, "completed")
        _wait_status(client, id2, "completed")

    assert order == ["start:first", "end:first", "start:second", "end:second"]


def test_queue_overflow_returns_429(client):
    gate = asyncio.Event()

    async def forever(request, progress=None):
        await gate.wait()
        return GenerateCreatedResponse(
            experiment_id="x",
            status="completed",
            mode="raw",
            step_count=0,
            created_at="t",
            detail_path="/experiment/x",
        )

    with patch.object(generation_jobs, "_runner", side_effect=forever):
        # One running + MAX_QUEUED waiting
        ids = []
        for i in range(MAX_QUEUED_JOBS + 1):
            r = client.post("/generate/jobs", json=_payload(prompt=f"p{i}"))
            assert r.status_code == 202, r.text
            ids.append(r.json()["job_id"])
        overflow = client.post("/generate/jobs", json=_payload(prompt="overflow"))
        assert overflow.status_code == 429, overflow.text
        assert overflow.json()["detail"]["error"] == "generation_queue_full"
        gate.set()
        for jid in ids:
            _wait_status(client, jid, "completed", timeout=10.0)


def test_status_responsive_while_job_blocks(client):
    gate = asyncio.Event()

    async def slow(request, progress=None):
        await gate.wait()
        return GenerateCreatedResponse(
            experiment_id="exp",
            status="completed",
            mode="raw",
            step_count=1,
            created_at="t",
            detail_path="/experiment/exp",
        )

    with patch.object(generation_jobs, "_runner", side_effect=slow):
        r = client.post("/generate/jobs", json=_payload())
        job_id = r.json()["job_id"]
        t0 = time.perf_counter()
        health = client.get("/health")
        status = client.get(f"/generate/jobs/{job_id}")
        elapsed = time.perf_counter() - t0
        assert health.status_code == 200
        assert status.status_code == 200
        assert elapsed < 1.0
        assert status.json()["status"] in ("queued", "running")
        gate.set()
        _wait_status(client, job_id, "completed")


def test_unknown_job_returns_404(client):
    r = client.get("/generate/jobs/00000000-0000-0000-0000-000000000099")
    assert r.status_code == 404
    assert r.json()["detail"]["error"] == "job_not_found"


def test_malformed_job_id_returns_400(client):
    r = client.get("/generate/jobs/not-a-uuid")
    assert r.status_code == 400


def test_job_passes_syncode_options_unchanged(client):
    seen = {}

    async def capture(request, progress=None):
        seen.update(request.model_dump())
        return GenerateCreatedResponse(
            experiment_id="e",
            status="completed",
            mode="syncode",
            step_count=1,
            created_at="t",
            detail_path="/experiment/e",
            constraint_requested=True,
        )

    with patch.object(generation_jobs, "_runner", side_effect=capture):
        r = client.post(
            "/generate/jobs",
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
        job_id = r.json()["job_id"]
        _wait_status(client, job_id, "completed")

    assert seen["prompt"] == "write mux"
    assert seen["use_syncode"] is True
    assert seen["max_new_tokens"] == 32
    assert seen["top_k"] == 11
    assert seen["temperature"] == 0.7
    assert seen["do_sample"] is False
    assert seen["top_p"] == 0.9
    assert seen["repetition_penalty"] == 1.2
