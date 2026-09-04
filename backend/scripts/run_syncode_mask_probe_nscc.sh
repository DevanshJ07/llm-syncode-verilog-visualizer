#!/usr/bin/env bash
# Checkpoint 3C+ research runner template (repository copy).
# Corrected vs archived Checkpoint 3B runner:
# - calls $EVAL_ROOT/env/bin/python directly (no activate)
# - checks pydantic / pydantic-settings
# - no Bash ${VAR@Q} injected into Python
# - JSON/env for multiline data
# - no-GPU preflight; batch-job friendly; restart-safe
# - never overwrites a completed raw report
#
# Do NOT edit the archived Checkpoint 3B runner under synviz-research-outputs.
set -euo pipefail

EVAL_ROOT="${EVAL_ROOT:-/scratch/users/ntu/devansh0/syncode_nemotron_eval}"
PY="${EVAL_PYTHON:-${EVAL_ROOT}/env/bin/python}"
SYNVIZ_ROOT="${SYNVIZ_ROOT:-${HOME}/llm-syncode-verilog-visualizer}"
OUT_ROOT="${OUT_ROOT:?set OUT_ROOT to an isolated reporting directory}"
CASE_JSON="${CASE_JSON:?set CASE_JSON to the working case path outside git}"
CACHE_ROOT="${CACHE_ROOT:?set CACHE_ROOT to an isolated cache directory}"
MODE="${MODE:-fresh_isolated}"  # or preflight / existing_cache
TOKENIZER_ID="${TOKENIZER_ID:-nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B-BF16}"
TOKENIZER_REV="${TOKENIZER_REV:-2d59de1cbd51c0adf384eb906b766d1aee0e0517}"

mkdir -p "${OUT_ROOT}/reports" "${OUT_ROOT}/logs" "${CACHE_ROOT}"
export PYTHONPATH="${SYNVIZ_ROOT}/backend${PYTHONPATH:+:${PYTHONPATH}}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:-}"

if [[ ! -x "${PY}" ]]; then
  echo "FATAL: python not executable: ${PY}" >&2
  exit 2
fi

# Dependency preflight (fail closed)
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
assert md.version("syncode") == "0.4.16"
print("python", sys.version)
print("syncode_path", syncode.__file__)
PY

REPORT_DIR="${OUT_ROOT}/reports/${MODE}"
mkdir -p "${REPORT_DIR}"
# Restart-safe: do not overwrite a completed JSON report
DONE_JSON="${REPORT_DIR}/COMPLETED.ok"
if [[ -f "${DONE_JSON}" ]]; then
  echo "Refusing to overwrite completed report marker ${DONE_JSON}"
  exit 0
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
if [[ "${MODE}" == "preflight" ]]; then
  ARGS+=(--skip-mask-store)
fi
if [[ "${ALLOW_DOWNLOAD:-0}" == "1" ]]; then
  ARGS+=(--allow-download)
fi

"${PY}" "${ARGS[@]}"
touch "${DONE_JSON}"
echo "Wrote reports under ${REPORT_DIR}"
