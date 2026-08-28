"""
Canonical Verilog Lark grammar location for SynViz.

All production readers (Lark validation, SynCode, diagnostics, tests) must
load grammar text through this module so there is exactly one source of truth:

    backend/grammar/verilog.lark
"""

from __future__ import annotations

import hashlib
from pathlib import Path

# backend/app/core/grammar.py → parents[2] == backend/
CANONICAL_GRAMMAR_PATH: Path = (
    Path(__file__).resolve().parents[2] / "grammar" / "verilog.lark"
)

# Verified LF-normalized content from the focused Qwen/Nemotron experiment grammar.
EXPECTED_GRAMMAR_SHA256 = (
    "1d4dc2bccf39f3e591e3dc59834c1c17b33b3f27d00a7ddd8810c795510cc4ef"
)
EXPECTED_GRAMMAR_SIZE_BYTES = 18075


def get_canonical_grammar_path() -> Path:
    """Return the absolute path to the canonical grammar file.

    Raises:
        FileNotFoundError: if ``backend/grammar/verilog.lark`` is missing.
    """
    path = CANONICAL_GRAMMAR_PATH
    if not path.is_file():
        raise FileNotFoundError(
            f"Canonical Verilog grammar file not found at: {path}. "
            "Expected backend/grammar/verilog.lark."
        )
    return path


def read_verilog_grammar() -> str:
    """Read the canonical grammar as UTF-8 text."""
    return get_canonical_grammar_path().read_text(encoding="utf-8")


def grammar_sha256(*, raw_bytes: bytes | None = None) -> str:
    """Return the SHA-256 hex digest of the canonical grammar file bytes."""
    data = raw_bytes if raw_bytes is not None else get_canonical_grammar_path().read_bytes()
    return hashlib.sha256(data).hexdigest()


def grammar_byte_size() -> int:
    """Return the on-disk byte size of the canonical grammar file."""
    return get_canonical_grammar_path().stat().st_size
