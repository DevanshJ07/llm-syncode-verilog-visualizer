#!/usr/bin/env bash
# Checkpoint 3C/3D research runner template (repository copy).
# Corrected vs archived Checkpoint 3B runner:
# - calls $EVAL_ROOT/env/bin/python directly (no activate)
# - checks pydantic / pydantic-settings
# - no Bash ${VAR@Q} injected into Python
# - JSON/env for multiline data
# - no-GPU preflight; batch-job friendly; restart-safe
# - never overwrites a completed raw report
# - Checkpoint 3D: MODE=checkpoint3b_fresh_reuse forces existing_cache + identity
#
# Do NOT edit the archived Checkpoint 3B runner under synviz-research-outputs.
set -euo pipefail

EVAL_ROOT="${EVAL_ROOT:?set EVAL_ROOT to the SynCode eval checkout root}"
PY="${EVAL_PYTHON:-${EVAL_ROOT}/env/bin/python}"
SYNVIZ_ROOT="${SYNVIZ_ROOT:-${HOME}/llm-syncode-verilog-visualizer}"
OUT_ROOT="${OUT_ROOT:?set OUT_ROOT to an isolated reporting directory}"
CASE_JSON="${CASE_JSON:?set CASE_JSON to the working case path outside git}"
CACHE_ROOT="${CACHE_ROOT:?set CACHE_ROOT to an isolated cache directory}"
MODE="${MODE:-fresh_isolated}"  # preflight | checkpoint3b_fresh_reuse | fresh_isolated | existing_cache
TOKENIZER_ID="${TOKENIZER_ID:-nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16}"
TOKENIZER_REV="${TOKENIZER_REV:-2d59de1cbd51c0adf384eb906b766d1aee0e0517}"
EXPECTED_GRAMMAR_SHA="${EXPECTED_GRAMMAR_SHA:-1d4dc2bccf39f3e591e3dc59834c1c17b33b3f27d00a7ddd8810c795510cc4ef}"
# Optional: override reuse cache via CHECKPOINT3B_FRESH_CACHE; no absolute
# machine-specific paths belong in-repo defaults.
CHECKPOINT3B_FRESH_DEFAULT="${CHECKPOINT3B_FRESH_CACHE:-${EVAL_ROOT}/research_outputs/checkpoint3b_nemotron_newline_20260903/cache_fresh_isolated}"

mkdir -p "${OUT_ROOT}/reports" "${OUT_ROOT}/logs"
export PYTHONPATH="${SYNVIZ_ROOT}/backend${PYTHONPATH:+:${PYTHONPATH}}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-}"

if [[ ! -x "${PY}" ]]; then
  echo "FATAL: python not executable: ${PY}" >&2
  exit 2
fi

case "${MODE}" in
  preflight|fresh_isolated|existing_cache|checkpoint3b_fresh_reuse) ;;
  *)
    echo "FATAL: unknown MODE=${MODE}" >&2
    exit 2
    ;;
esac

# Refuse to write reports into known Checkpoint 3B evidence directories.
if [[ "${OUT_ROOT}" == *checkpoint3b_nemotron_newline* ]]; then
  echo "FATAL: OUT_ROOT must not be a Checkpoint 3B evidence directory: ${OUT_ROOT}" >&2
  exit 2
fi

# Dependency + SynCode provenance preflight (fail closed)
"${PY}" - <<'PY'
import importlib.metadata as md
import sys
need = ["pydantic", "pydantic-settings", "syncode", "transformers", "torch"]
missing = []
for n in need:
    try:
        print(n, md.version(n))
    except Exception as e:
        missing.append(f"{n}:{e}")
if missing:
    print("MISSING", missing, file=sys.stderr)
    sys.exit(2)
import syncode
assert md.version("syncode") == "0.4.16", md.version("syncode")
print("python", sys.version)
print("syncode_path", syncode.__file__)
PY

REPORT_MODE="${MODE}"
MASK_MODE="fresh_isolated"
SKIP_MASK=0
if [[ "${MODE}" == "preflight" ]]; then
  SKIP_MASK=1
  REPORT_MODE="preflight"
elif [[ "${MODE}" == "checkpoint3b_fresh_reuse" ]]; then
  MASK_MODE="existing_cache"
  REPORT_MODE="checkpoint3b_fresh_reuse"
  if [[ -z "${CACHE_ROOT}" || "${CACHE_ROOT}" == "<FILL"* ]]; then
    CACHE_ROOT="${CHECKPOINT3B_FRESH_DEFAULT}"
  fi
  # Identity gate before load (fail closed).
  CACHE_ROOT="${CACHE_ROOT}" TOKENIZER_ID="${TOKENIZER_ID}" \
  TOKENIZER_REV="${TOKENIZER_REV}" EXPECTED_GRAMMAR_SHA="${EXPECTED_GRAMMAR_SHA}" \
  SYNVIZ_ROOT="${SYNVIZ_ROOT}" \
  "${PY}" - <<'PY'
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(os.environ["SYNVIZ_ROOT"]) / "backend"))
from app.core.grammar import grammar_sha256, EXPECTED_GRAMMAR_SHA256
from app.research.syncode_mask_probe_mask_store import expected_mask_store_pickle_path

cache_root = Path(os.environ["CACHE_ROOT"])
if not cache_root.is_dir():
    print(f"FATAL: checkpoint3b_fresh_reuse cache missing: {cache_root}", file=sys.stderr)
    sys.exit(2)
# Label: never claim original generation cache
print("cache_label=checkpoint3b_fresh_reuse")
print("claimed_original_nscc_cache=False")
gsha = grammar_sha256()
exp = os.environ.get("EXPECTED_GRAMMAR_SHA") or EXPECTED_GRAMMAR_SHA256
if gsha != exp:
    print(f"FATAL: grammar SHA mismatch {gsha} != {exp}", file=sys.stderr)
    sys.exit(2)
print("grammar_sha256", gsha)
print("tokenizer_id", os.environ["TOKENIZER_ID"])
print("tokenizer_revision", os.environ["TOKENIZER_REV"])
# Pickle must already exist (existing_cache never constructs).
# Class/vocab checked after tokenizer load in the probe itself; here we only
# require a grammar_mask_*.pkl under the cache root.
pkls = list(cache_root.rglob("grammar_mask_*.pkl"))
if not pkls:
    print(f"FATAL: no grammar_mask_*.pkl under {cache_root}", file=sys.stderr)
    sys.exit(2)
print("reuse_candidates", len(pkls))
for p in pkls[:5]:
    print(" candidate", p, p.stat().st_size)
PY
elif [[ "${MODE}" == "existing_cache" ]]; then
  MASK_MODE="existing_cache"
fi

mkdir -p "${CACHE_ROOT}"
REPORT_DIR="${OUT_ROOT}/reports/${REPORT_MODE}"
mkdir -p "${REPORT_DIR}"
# Restart-safe: do not overwrite a completed JSON report in THIS 3D OUT_ROOT
DONE_JSON="${REPORT_DIR}/COMPLETED.ok"
if [[ -f "${DONE_JSON}" ]]; then
  echo "Refusing to overwrite completed report marker ${DONE_JSON}"
  exit 0
fi

# Patch case mask_store_mode for reuse modes via env-passed sidecar is avoided;
# require the working CASE_JSON to already set mask_store_mode appropriately.
if [[ "${MODE}" == "checkpoint3b_fresh_reuse" || "${MODE}" == "existing_cache" ]]; then
  "${PY}" - <<PY
import json, sys
from pathlib import Path
case = json.loads(Path("${CASE_JSON}").read_text(encoding="utf-8"))
mode = case.get("mask_store_mode")
if mode != "existing_cache":
    print(f"FATAL: CASE_JSON mask_store_mode must be existing_cache for MODE=${MODE}, got {mode!r}", file=sys.stderr)
    sys.exit(2)
print("case_mask_store_mode_ok", mode)
PY
fi

ARGS=(
  "${SYNVIZ_ROOT}/backend/scripts/run_syncode_mask_probe.py"
  --case "${CASE_JSON}"
  --output-dir "${REPORT_DIR}"
  --cache-root "${CACHE_ROOT}"
  --tokenizer-id "${TOKENIZER_ID}"
  --tokenizer-revision "${TOKENIZER_REV}"
  --trust-remote-code
)
if [[ "${SKIP_MASK}" == "1" ]]; then
  ARGS+=(--skip-mask-store)
fi
if [[ "${ALLOW_DOWNLOAD:-0}" == "1" ]]; then
  ARGS+=(--allow-download)
fi

"${PY}" "${ARGS[@]}"
touch "${DONE_JSON}"
echo "Wrote reports under ${REPORT_DIR}"
echo "MODE=${MODE} MASK_MODE=${MASK_MODE} CACHE_ROOT=${CACHE_ROOT}"
