"""
Startup and runtime diagnostics for the active Verilog Lark grammar.

Separates three statuses:
  A. Lark parser compile (final validation, parse tree, incremental trace)
  B. SynCode grammar + mask-store (constrained decoding)
  C. Per-step constraint application (recorded during generation)
"""

from __future__ import annotations

from app.console_safe import _safe_console_print

import importlib.util
import json
import logging
import os
import sys
import traceback
import types
from dataclasses import dataclass, field

from app.services.verilog_validation import (
    _VERILOG_GRAMMAR_PATH,
    read_verilog_grammar,
)

_log = logging.getLogger(__name__)

_STUB_ATTR = "_verilog_validation_lark_stub"


def clear_verilog_validation_syncode_stub() -> bool:
    """
    Remove the temporary ``syncode`` package stub installed by
    ``verilog_validation._load_lark_module`` so the real SynCode package can import.
    """
    mod = sys.modules.get("syncode")
    if mod is not None and getattr(mod, _STUB_ATTR, False):
        del sys.modules["syncode"]
        return True
    return False


def ensure_syncode_evaluation_metadata() -> None:
    """
    SynCode's top-level ``__init__`` imports evaluation datasets that expect
    ``metadata.json``.  Create a minimal stub when the wheel install is incomplete.
    """
    for path_entry in sys.path:
        data_py = os.path.join(
            path_entry, "syncode", "evaluation", "mxeval", "data.py"
        )
        if not os.path.isfile(data_py):
            continue
        meta_dir = os.path.join(
            path_entry, "syncode", "evaluation", "mxeval", "data", "multilingual_humaneval"
        )
        meta_path = os.path.join(meta_dir, "metadata.json")
        if os.path.isfile(meta_path):
            return
        os.makedirs(meta_dir, exist_ok=True)
        with open(meta_path, "w", encoding="utf-8") as fh:
            json.dump({"python": "dummy.jsonl"}, fh)
        _log.info("[grammar] Created SynCode evaluation metadata stub at %s", meta_path)
        return


@dataclass
class GrammarDiagnosticResult:
    grammar_path: str = ""
    lark_compile_ok: bool = False
    lark_compile_error: str = ""
    syncode_grammar_ok: bool = False
    syncode_grammar_error: str = ""
    syncode_mask_store_ok: bool = False
    syncode_mask_store_error: str = ""
    syncode_init_error: str = ""  # full traceback when mask store fails

    @property
    def lark_grammar_loaded(self) -> bool:
        return self.lark_compile_ok

    @property
    def syncode_mask_store_loaded(self) -> bool:
        return self.syncode_mask_store_ok


# Module-level cache populated at startup / first model load.
_last_diagnostics: GrammarDiagnosticResult | None = None


def get_grammar_diagnostics() -> GrammarDiagnosticResult:
    global _last_diagnostics  # noqa: PLW0603
    if _last_diagnostics is None:
        _last_diagnostics = run_grammar_diagnostics()
    return _last_diagnostics


def run_grammar_diagnostics(
    *,
    tokenizer=None,
    build_mask_store: bool = False,
) -> GrammarDiagnosticResult:
    """
    Compile the active Verilog grammar under Lark and (optionally) SynCode.

    When *build_mask_store* is True and *tokenizer* is provided, also attempts
    to construct the SynCode DFA mask store (slow on first run).
    """
    clear_verilog_validation_syncode_stub()
    ensure_syncode_evaluation_metadata()

    result = GrammarDiagnosticResult(grammar_path=_VERILOG_GRAMMAR_PATH)

    # --- A. Lark compile ---------------------------------------------------
    try:
        grammar_text = read_verilog_grammar()
        from app.services.verilog_validation import _load_lark_module  # noqa: PLC0415

        lark_mod = _load_lark_module()
        if lark_mod is None:
            raise ImportError("lark not available")
        lark_mod.Lark(  # type: ignore[attr-defined]
            grammar_text,
            parser="lalr",
            maybe_placeholders=False,
            propagate_positions=False,
        )
        result.lark_compile_ok = True
    except Exception as exc:  # noqa: BLE001
        result.lark_compile_error = f"{type(exc).__name__}: {exc}"

    # --- B. SynCode grammar + mask store -----------------------------------
    clear_verilog_validation_syncode_stub()
    ensure_syncode_evaluation_metadata()

    try:
        from syncode import Syncode, SyncodeLogitsProcessor  # noqa: PLC0415, F401
        from syncode.parsers.grammars.grammar import Grammar  # noqa: PLC0415

        grammar_text = read_verilog_grammar()
        gram_obj = Grammar(grammar_text)
        result.syncode_grammar_ok = True

        if build_mask_store and tokenizer is not None:
            SyncodeLogitsProcessor(
                grammar=gram_obj,
                tokenizer=tokenizer,
                use_cache=True,
                parse_output_only=True,
                num_samples=1,
                mode="grammar_mask",
            )
            result.syncode_mask_store_ok = True
    except Exception as exc:  # noqa: BLE001
        err_text = f"{type(exc).__name__}: {exc}"
        tb = traceback.format_exc()
        result.syncode_init_error = tb
        if result.syncode_grammar_ok:
            result.syncode_mask_store_error = err_text
        else:
            result.syncode_grammar_error = err_text

    global _last_diagnostics  # noqa: PLW0603
    _last_diagnostics = result
    return result


def log_grammar_diagnostics(diag: GrammarDiagnosticResult) -> None:
    """Print startup diagnostic lines requested for research visibility."""
    _safe_console_print(f"[grammar] active verilog grammar path: {diag.grammar_path}", flush=True)
    if diag.lark_compile_ok:
        _safe_console_print("[grammar] lark parse compile: success", flush=True)
    else:
        _safe_console_print("[grammar] lark parse compile: failure", flush=True)
        _safe_console_print(f"[grammar] error details: {diag.lark_compile_error}", flush=True)

    if diag.syncode_grammar_ok:
        _safe_console_print("[grammar] syncode grammar load: success", flush=True)
    else:
        _safe_console_print("[grammar] syncode grammar load: failure", flush=True)
        err = diag.syncode_grammar_error or diag.syncode_init_error
        _safe_console_print(f"[grammar] error details: {err}", flush=True)

    if not diag.syncode_grammar_ok:
        _safe_console_print("[grammar] syncode mask store: unavailable", flush=True)
    elif diag.syncode_mask_store_ok:
        _safe_console_print("[grammar] syncode mask store: loaded", flush=True)
    elif diag.syncode_mask_store_error:
        _safe_console_print("[grammar] syncode mask store: unavailable", flush=True)
        _safe_console_print(f"[grammar] error details: {diag.syncode_mask_store_error}", flush=True)
    else:
        _safe_console_print("[grammar] syncode mask store: not probed (awaiting tokenizer)", flush=True)
