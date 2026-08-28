"""Regression: SynViz must load exactly one canonical Verilog grammar."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from app.core.grammar import (
    CANONICAL_GRAMMAR_PATH,
    EXPECTED_GRAMMAR_SHA256,
    EXPECTED_GRAMMAR_SIZE_BYTES,
    get_canonical_grammar_path,
    grammar_byte_size,
    grammar_sha256,
    read_verilog_grammar,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = Path(__file__).resolve().parents[1]

_EXCLUDE_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    ".next",
    "__pycache__",
    ".pytest_cache",
    ".cache",
    "cache",
    "backups",
    "logs",
    "_context_bundle_staging",
}


def _is_excluded(path: Path) -> bool:
    return any(part in _EXCLUDE_DIR_NAMES for part in path.parts) or any(
        part.startswith(".venv") for part in path.parts
    )


def test_canonical_file_exists_with_verified_hash_and_size():
    path = get_canonical_grammar_path()
    assert path == CANONICAL_GRAMMAR_PATH.resolve() or path.resolve() == CANONICAL_GRAMMAR_PATH.resolve()
    assert path.is_file()
    assert grammar_byte_size() == EXPECTED_GRAMMAR_SIZE_BYTES
    assert grammar_sha256() == EXPECTED_GRAMMAR_SHA256
    raw = path.read_bytes()
    assert len(raw) == 18075
    assert b"\r" not in raw


def test_lark_compiles_with_production_parser_config():
    from app.services.verilog_validation import _load_lark_module

    lark_mod = _load_lark_module()
    assert lark_mod is not None
    grammar_text = read_verilog_grammar()
    parser = lark_mod.Lark(
        grammar_text,
        parser="lalr",
        maybe_placeholders=False,
        propagate_positions=False,
    )
    tree = parser.parse(
        "module m(a, y);\ninput a;\noutput y;\nassign y = a;\nendmodule\n"
    )
    assert tree is not None


def test_production_validation_uses_canonical_path():
    from app.services import verilog_validation as vv

    assert Path(vv._VERILOG_GRAMMAR_PATH).resolve() == CANONICAL_GRAMMAR_PATH.resolve()
    assert vv.read_verilog_grammar() == read_verilog_grammar()
    assert grammar_sha256() == EXPECTED_GRAMMAR_SHA256


def test_syncode_init_receives_canonical_grammar_text():
    from app.services import llm_service as ls

    assert Path(ls._VERILOG_GRAMMAR_PATH).resolve() == CANONICAL_GRAMMAR_PATH.resolve()
    assert ls._read_verilog_grammar() == read_verilog_grammar()


def test_exactly_one_lark_grammar_in_repo_source():
    found: list[Path] = []

    def _walk(base: Path) -> None:
        try:
            entries = list(base.iterdir())
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            return
        for entry in entries:
            name = entry.name
            if name in _EXCLUDE_DIR_NAMES or name.startswith(".venv"):
                continue
            if entry.is_dir():
                _walk(entry)
                continue
            if entry.is_file() and entry.suffix == ".lark":
                found.append(entry.resolve())

    _walk(REPO_ROOT)
    found_sorted = sorted(found)
    expected = [CANONICAL_GRAMMAR_PATH.resolve()]
    assert found_sorted == expected, f"expected one canonical grammar, found: {found_sorted}"

    assert not (REPO_ROOT / "verilog.lark").exists()
    assert not (BACKEND_ROOT / "verilog.lark").exists()
    assert not (BACKEND_ROOT / "verilog_old_before_update.lark").exists()


def test_no_independent_path_implementations_in_services():
    """Service modules must not hardcode joins to a non-canonical verilog.lark path."""
    services = BACKEND_ROOT / "app" / "services"
    offenders: list[str] = []
    needles = (
        'parents[1] / "verilog.lark"',
        '", "..", "verilog.lark"',
        "', '..', 'verilog.lark'",
        'join(os.path.dirname',  # old relative join style used with verilog.lark
    )
    for path in services.glob("*.py"):
        src = path.read_text(encoding="utf-8")
        if "verilog.lark" not in src:
            continue
        # Flag only constructions that locate a grammar file via relative joins.
        if any(n in src for n in needles) and "CANONICAL_GRAMMAR_PATH" not in src:
            offenders.append(path.name)
        for line in src.splitlines():
            if "verilog.lark" in line and "os.path.join" in line:
                offenders.append(f"{path.name}:{line.strip()}")
    assert offenders == [], f"independent grammar path joins remain: {offenders}"
