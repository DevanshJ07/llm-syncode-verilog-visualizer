#!/usr/bin/env python3
"""
CLI: run the SynCode mask diagnostic probe (Checkpoint 3A).

Research-only. Does not import llm_service or touch production caches unless
an explicit existing_cache path is supplied.

Tokenizer loading defaults to local/cache-only. Network download requires
an explicit ``--allow-download`` flag (recorded in provenance).
``--trust-remote-code`` is also explicit and recorded.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SynCode mask diagnostic probe")
    parser.add_argument("--case", required=True, help="Path to case JSON")
    parser.add_argument("--output-dir", required=True, help="Directory for JSON+MD")
    parser.add_argument(
        "--cache-root",
        required=True,
        help="Isolated SYNCODE_CACHE root (fresh or existing)",
    )
    parser.add_argument("--tokenizer-id", default=None)
    parser.add_argument("--tokenizer-revision", default=None)
    parser.add_argument(
        "--trust-remote-code",
        action="store_true",
        help="Pass trust_remote_code=True to AutoTokenizer (recorded in provenance)",
    )
    parser.add_argument(
        "--allow-download",
        action="store_true",
        help=(
            "Allow Hugging Face / network download when tokenizer is missing "
            "locally. Default is local_files_only=True (no network)."
        ),
    )
    parser.add_argument(
        "--skip-mask-store",
        action="store_true",
        help="Tokenizer/parser/witness only (no DFA build)",
    )
    parser.add_argument(
        "--tokenizer-from",
        default=None,
        help="Optional path to a pickled/custom loader (advanced)",
    )
    args = parser.parse_args(argv)

    from app.research.syncode_mask_probe import run_probe, write_probe_outputs
    from app.research.syncode_mask_probe_prefix import load_case_spec

    case_path = Path(args.case)
    case = load_case_spec(case_path)
    if args.tokenizer_id:
        case.tokenizer_model_id = args.tokenizer_id
    if args.tokenizer_revision:
        case.tokenizer_revision = args.tokenizer_revision
    if args.trust_remote_code:
        case.trust_remote_code = True

    placeholders = ("<FILL>", "TODO", "REQUIRED")
    if not args.skip_mask_store:
        for field in (
            case.tokenizer_model_id,
            case.source_trace_path,
        ):
            if field is None or any(p in str(field) for p in placeholders):
                print(
                    "Refusing to run: fill tokenizer_model_id / source_trace_path "
                    "in the case JSON (or pass --tokenizer-id). "
                    "Use --skip-mask-store only for offline unit harnesses.",
                    file=sys.stderr,
                )
                return 2

    if case.tokenizer_model_id is None or any(
        p in str(case.tokenizer_model_id) for p in placeholders
    ):
        print(
            "tokenizer_model_id is required (do not guess Nemotron IDs).",
            file=sys.stderr,
        )
        return 2

    # Never request model weights — tokenizer only.
    from transformers import AutoTokenizer

    local_files_only = not bool(args.allow_download)
    tok_kwargs: dict = {
        "trust_remote_code": bool(case.trust_remote_code),
        "local_files_only": local_files_only,
    }
    if case.tokenizer_revision and not any(
        p in case.tokenizer_revision for p in placeholders
    ):
        tok_kwargs["revision"] = case.tokenizer_revision

    try:
        tokenizer = AutoTokenizer.from_pretrained(
            case.tokenizer_model_id, **tok_kwargs
        )
    except Exception as exc:  # noqa: BLE001
        print(
            f"Tokenizer load failed (local_files_only={local_files_only}): {exc}",
            file=sys.stderr,
        )
        return 2

    result = run_probe(
        case,
        tokenizer=tokenizer,
        cache_root=Path(args.cache_root),
        skip_mask_store=bool(args.skip_mask_store),
        case_file=case_path,
        allow_download=bool(args.allow_download),
        local_files_only=local_files_only,
    )
    json_path, md_path = write_probe_outputs(result, Path(args.output_dir))
    print(
        json.dumps(
            {
                "json": str(json_path),
                "markdown": str(md_path),
                "report_status": result.report_status,
                "failure_stage": result.failure_stage,
            },
            indent=2,
        )
    )
    if result.report_status != "complete" or result.errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
