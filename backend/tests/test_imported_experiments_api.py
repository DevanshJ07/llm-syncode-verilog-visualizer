"""API tests for Phase 2A.2 imported-experiment endpoints (no Torch/SynCode)."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.models.schemas import GenerateRequest
from app.services.imported_experiment_store import ImportedExperimentStore
from main import app

VALID_SV = b"module m(a, y);\n  input a;\n  output y;\n  assign y = a;\nendmodule\n"


def _build_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _minimal_zip(*, experiment: str = "focused_four_qwen_512") -> bytes:
    problem = "Prob004_vector2"
    root = f"results/{experiment}"
    text = VALID_SV.decode()
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
                        "model_revision": "rev1",
                        "device": "cpu",
                        "trust_remote_code": False,
                        "enable_thinking": False,
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


@pytest.fixture
def client(tmp_path: Path):
    store = ImportedExperimentStore(base_dir=tmp_path / "imported")
    with patch(
        "app.api.routes.imported_experiments.imported_store", store
    ), patch(
        "app.services.imported_experiment_store.imported_store", store
    ):
        yield TestClient(app), store


def test_import_list_detail_flow(client):
    http, store = client
    raw = _minimal_zip()
    r = http.post(
        "/import/bundle",
        files={"file": ("bundle.zip", raw, "application/zip")},
        data={"recompute_with_current_grammar": "false"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    # Lightweight created response — no full prompt_results/steps payload.
    assert "prompt_results" not in body
    assert "steps" not in body
    assert body["experiment_name"] == "focused_four_qwen_512"
    assert body["prompt_count"] >= 1
    assert body["recompute_with_current_grammar"] is False
    assert body["recompute_syncode_parser_evidence"] is False
    eid = body["experiment_id"]
    assert store.load(eid) is not None

    # Also available under /api
    r_api = http.post(
        "/api/import/bundle",
        files={
            "file": (
                "bundle.zip",
                _minimal_zip(experiment="focused_four_nemotron_512"),
                "application/zip",
            )
        },
        data={"recompute_with_current_grammar": "false"},
    )
    assert r_api.status_code == 201, r_api.text
    assert "prompt_results" not in r_api.json()

    listing = http.get("/imported-experiments")
    assert listing.status_code == 200
    rows = listing.json()
    assert len(rows) >= 2
    for row in rows:
        assert "steps" not in row
        assert "prompt_results" not in row
        assert "prompt_count" in row
        assert "experiment_id" in row

    detail = http.get(f"/imported-experiment/{eid}")
    assert detail.status_code == 200
    full = detail.json()
    assert full["source_type"] == "imported"
    assert full["experiment_id"] == eid
    assert "prompt_results" in full
    assert "steps" in full["prompt_results"][0]
    assert len(full["prompt_results"][0]["steps"]) > 0
    assert full["prompt_results"][0]["generated_output"]["value"] == VALID_SV.decode()

    detail_api = http.get(f"/api/imported-experiment/{eid}")
    assert detail_api.status_code == 200


def test_unknown_id_404(client):
    http, _ = client
    r = http.get("/imported-experiment/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert r.status_code == 404


def test_malformed_id_rejected(client):
    http, _ = client
    r = http.get("/imported-experiment/not-a-uuid")
    assert r.status_code == 400
    assert "malformed" in r.json()["detail"].lower()
    r2 = http.get("/imported-experiment/still_not_valid_id")
    assert r2.status_code == 400


def test_compressed_upload_size_limit(client):
    http, _ = client
    raw = _minimal_zip()
    reads = {"bytes": 0}

    class _CountingUpload:
        filename = "bundle.zip"
        content_type = "application/zip"

        def __init__(self, data: bytes):
            self._data = data
            self._pos = 0

        async def read(self, n: int = -1) -> bytes:
            if self._pos >= len(self._data):
                return b""
            end = len(self._data) if n < 0 else min(len(self._data), self._pos + n)
            chunk = self._data[self._pos : end]
            self._pos = end
            reads["bytes"] += len(chunk)
            return chunk

    import asyncio

    from app.api.routes.imported_experiments import _read_upload_limited
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as ei:
        asyncio.run(_read_upload_limited(_CountingUpload(raw), max_bytes=32))
    assert ei.value.status_code == 413
    # Stopped while streaming — did not buffer the entire oversized upload
    assert reads["bytes"] <= 33
    assert reads["bytes"] < len(raw)

    with patch(
        "app.api.routes.imported_experiments.settings.max_import_upload_bytes",
        32,
    ):
        r = http.post(
            "/import/bundle",
            files={"file": ("bundle.zip", raw, "application/zip")},
        )
    assert r.status_code == 413
    assert "limit" in r.json()["detail"].lower()


def test_unsafe_zip_returns_4xx(client):
    http, _ = client
    r = http.post(
        "/import/bundle",
        files={"file": ("bad.zip", b"not-a-zip", "application/zip")},
    )
    assert r.status_code == 400


def test_setup_only_returns_422(client):
    http, _ = client
    raw = _build_zip({"README.md": b"setup only\n", "run.py": b"print(1)\n"})
    r = http.post(
        "/import/bundle",
        files={"file": ("setup.zip", raw, "application/zip")},
    )
    assert r.status_code == 422


def test_recompute_form_default_false(client):
    http, _ = client
    r = http.post(
        "/import/bundle",
        files={"file": ("bundle.zip", _minimal_zip(), "application/zip")},
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["recompute_with_current_grammar"] is False
    detail = http.get(f"/imported-experiment/{created['experiment_id']}")
    pr = detail.json()["prompt_results"][0]
    assert pr["recomputed_grammar_verdict"]["provenance"]["kind"] == "unavailable"


def test_recompute_form_true(client):
    http, store = client
    r = http.post(
        "/import/bundle",
        files={"file": ("bundle.zip", _minimal_zip(), "application/zip")},
        data={"recompute_with_current_grammar": "true"},
    )
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["recompute_with_current_grammar"] is True
    assert "prompt_results" not in created
    detail = http.get(f"/imported-experiment/{created['experiment_id']}")
    assert detail.status_code == 200
    pr = detail.json()["prompt_results"][0]
    assert pr["recomputed_grammar_verdict"]["provenance"]["kind"] == "recomputed"
    assert pr["recomputed_grammar_verdict"]["provenance"]["grammar_sha256"]
    assert store.load(created["experiment_id"]) is not None


def test_syncode_recompute_form_default_false(client):
    http, _ = client
    r = http.post(
        "/import/bundle",
        files={"file": ("bundle.zip", _minimal_zip(), "application/zip")},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["recompute_syncode_parser_evidence"] is False
    detail = http.get(f"/imported-experiment/{body['experiment_id']}")
    step0 = detail.json()["prompt_results"][0]["steps"][0]
    assert step0["syncode_parser_evidence"]["provenance"]["kind"] == "unavailable"


def test_syncode_recompute_form_true_independent(client):
    http, _ = client
    r = http.post(
        "/import/bundle",
        files={"file": ("bundle.zip", _minimal_zip(), "application/zip")},
        data={
            "recompute_with_current_grammar": "false",
            "recompute_syncode_parser_evidence": "true",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["recompute_syncode_parser_evidence"] is True
    assert body["recompute_with_current_grammar"] is False
    assert "prompt_results" not in body
    detail = http.get(f"/imported-experiment/{body['experiment_id']}")
    pr = detail.json()["prompt_results"][0]
    assert pr["recomputed_grammar_verdict"]["provenance"]["kind"] == "unavailable"
    step0 = pr["steps"][0]
    assert step0["syncode_parser_evidence"]["provenance"]["kind"] == "recomputed"
    assert (
        step0["syncode_parser_evidence"]["value"]["origin"]
        == "import_recomputed_parser_only"
    )
    assert step0["syncode_parser_evidence"]["value"]["mask_eos_observation"] is None


def test_both_recomputations_and_lightweight_response(client):
    http, _ = client
    r = http.post(
        "/import/bundle",
        files={"file": ("bundle.zip", _minimal_zip(), "application/zip")},
        data={
            "recompute_with_current_grammar": "true",
            "recompute_syncode_parser_evidence": "true",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert set(body.keys()) >= {
        "experiment_id",
        "experiment_name",
        "created_at",
        "prompt_count",
        "import_warnings",
        "recompute_with_current_grammar",
        "recompute_syncode_parser_evidence",
    }
    assert body["recompute_with_current_grammar"] is True
    assert body["recompute_syncode_parser_evidence"] is True
    # Must stay small — never serialize full traces on create.
    assert len(r.content) < 8_000
    detail = http.get(f"/imported-experiment/{body['experiment_id']}")
    pr = detail.json()["prompt_results"][0]
    assert pr["recomputed_grammar_verdict"]["provenance"]["kind"] == "recomputed"
    assert pr["steps"][0]["syncode_parser_evidence"]["provenance"]["kind"] == "recomputed"


def test_live_schema_and_generate_route_still_importable(client):
    """Existing live schemas/API remain wired (smoke, no model run)."""
    http, _ = client
    req = GenerateRequest(prompt="hello", use_syncode=False)
    assert req.prompt == "hello"
    # OpenAPI still lists generate
    schema = http.get("/openapi.json").json()
    paths = schema["paths"]
    assert "/generate" in paths
    assert "/import/bundle" in paths
    assert "/imported-experiments" in paths
    assert "/api/import/bundle" in paths
