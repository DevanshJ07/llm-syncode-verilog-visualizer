#!/usr/bin/env python3
"""
Compare two SynCode mask probe JSON reports (e.g. existing_cache vs fresh_isolated).

Reports whether candidate decisions differ and whether provenance/environment
incompatibility could explain the difference (rather than cache mode alone).

Only cache mode/path should intentionally differ in an existing-versus-fresh
comparison. Incompatible provenance must not be attributed solely to cache state.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


# Volatile fields intentionally ignored for environment compatibility.
_IGNORE_PROVENANCE_KEYS = frozenset(
    {
        "timestamp_utc",
        "host",
        "repository_dirty",
        "case_file_sha256",
        "trace_file_sha256",
        "mask_store",  # compared separately (mode/path expected to differ)
    }
)

_ENV_KEYS = [
    "probe_schema_version",
    "syncode_version",
    "transformers_version",
    "syncode_larkm_version",
    "torch_version",
    "tokenizer_model_id",
    "tokenizer_revision",
    "tokenizer_class",
    "vocabulary_size",
    "grammar_sha256",
    "parser_mode",
    "syncode_mode",
    "python_version",
    "trust_remote_code",
    "allow_download",
    "local_files_only",
]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _source_module_hashes(prov: dict) -> dict:
    raw = prov.get("syncode_source_file_sha256") or {}
    # Compare digest keys only (ignore *__path absolute paths).
    return {k: v for k, v in raw.items() if not str(k).endswith("__path")}


def _candidate_bytes(report: dict) -> dict:
    out = {}
    for tc in report.get("tokenizer_candidates") or []:
        tid = str(tc.get("token_id"))
        out[tid] = {
            "utf8_bytes": tc.get("utf8_bytes"),
            "decode": tc.get("decode_cleanup_disabled"),
            "codepoints": tc.get("unicode_codepoints"),
        }
    return out


def compare_probes(a: dict, b: dict) -> dict:
    pa = a.get("provenance") or {}
    pb = b.get("provenance") or {}

    env_mismatches = {
        k: {"a": pa.get(k), "b": pb.get(k)}
        for k in _ENV_KEYS
        if pa.get(k) != pb.get(k)
    }

    sha_a = _source_module_hashes(pa)
    sha_b = _source_module_hashes(pb)
    source_hash_mismatches = {
        k: {"a": sha_a.get(k), "b": sha_b.get(k)}
        for k in sorted(set(sha_a) | set(sha_b))
        if sha_a.get(k) != sha_b.get(k)
    }

    ma = (a.get("mask_attribution") or {}).get("runtime_mask_bits") or {}
    mb = (b.get("mask_attribution") or {}).get("runtime_mask_bits") or {}
    all_ids = sorted(
        set(ma) | set(mb), key=lambda x: int(x) if str(x).isdigit() else str(x)
    )
    decision_diffs = {}
    for tid in all_ids:
        if ma.get(tid) != mb.get(tid):
            decision_diffs[tid] = {"a": ma.get(tid), "b": mb.get(tid)}

    cand_a = set((a.get("case") or {}).get("candidate_token_ids") or [])
    cand_b = set((b.get("case") or {}).get("candidate_token_ids") or [])
    candidate_id_mismatch = sorted(cand_a.symmetric_difference(cand_b))

    bytes_a = _candidate_bytes(a)
    bytes_b = _candidate_bytes(b)
    candidate_byte_mismatches = {
        tid: {"a": bytes_a.get(tid), "b": bytes_b.get(tid)}
        for tid in sorted(set(bytes_a) | set(bytes_b), key=lambda x: int(x) if x.isdigit() else x)
        if bytes_a.get(tid) != bytes_b.get(tid)
    }

    msa = pa.get("mask_store") or {}
    msb = pb.get("mask_store") or {}
    mask_store_param_mismatches = {}
    for key in ("syncode_mode",):
        if msa.get(key) != msb.get(key):
            mask_store_param_mismatches[key] = {"a": msa.get(key), "b": msb.get(key)}

    intentional_cache_diffs = {
        "mode": {"a": msa.get("mode"), "b": msb.get("mode")},
        "cache_root": {"a": msa.get("cache_root"), "b": msb.get("cache_root")},
        "cache_path": {"a": msa.get("cache_path"), "b": msb.get("cache_path")},
        "cache_file_sha256": {
            "a": msa.get("cache_file_sha256"),
            "b": msb.get("cache_file_sha256"),
        },
    }

    incompatible = bool(
        env_mismatches
        or source_hash_mismatches
        or candidate_id_mismatch
        or candidate_byte_mismatches
        or mask_store_param_mismatches
        or a.get("prefix_sha256_utf8") != b.get("prefix_sha256_utf8")
    )

    return {
        "candidate_decision_differences": decision_diffs,
        "environment_mismatches": env_mismatches,
        "syncode_source_hash_mismatches": source_hash_mismatches,
        "candidate_id_mismatch": candidate_id_mismatch,
        "candidate_byte_mismatches": candidate_byte_mismatches,
        "mask_store_construction_param_mismatches": mask_store_param_mismatches,
        "intentional_cache_identity_diffs": intentional_cache_diffs,
        "prefix_sha_equal": a.get("prefix_sha256_utf8") == b.get("prefix_sha256_utf8"),
        "difference_may_be_environment_not_cache_mode": incompatible
        and bool(decision_diffs),
        "compatible_for_cache_mode_attribution": (not incompatible)
        and bool(decision_diffs),
        "case_ids": [a.get("case", {}).get("case_id"), b.get("case", {}).get("case_id")],
        "mask_store_modes": [msa.get("mode"), msb.get("mode")],
        "ignored_volatile_provenance_keys": sorted(_IGNORE_PROVENANCE_KEYS),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compare SynCode mask probe reports")
    parser.add_argument("report_a")
    parser.add_argument("report_b")
    parser.add_argument("--output", default=None, help="Optional JSON output path")
    args = parser.parse_args(argv)
    result = compare_probes(_load(Path(args.report_a)), _load(Path(args.report_b)))
    text = json.dumps(result, indent=2)
    print(text)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
