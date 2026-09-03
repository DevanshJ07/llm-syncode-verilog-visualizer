"""SynCode version + private-symbol signature guard (research-only)."""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import inspect
from pathlib import Path
from typing import Any, Callable

from app.models.syncode_mask_probe import SUPPORTED_SYNCODE_VERSION

# Modules whose on-disk SHA-256 we record for reproducibility.
_SYNCODE_SOURCE_TARGETS = (
    ("parse_result", "syncode.parse_result"),
    ("incremental_parser", "syncode.parsers.incremental_parser"),
    ("mask_store", "syncode.mask_store.mask_store"),
    ("lookup_table", "syncode.mask_store.lookup_table"),
    ("byte_tokenizer", "syncode.mask_store.byte_tokenizer"),
    ("fsm_set", "syncode.mask_store.fsm_set"),
)

# Expected parameter names for SynCode 0.4.16 private/public adapter surface.
# Fail closed if any required symbol is missing or parameter set differs.
_REQUIRED_SIGNATURES: dict[str, tuple[str, frozenset[str]]] = {
    "ParseResult.from_accept_terminals": (
        "syncode.parse_result.ParseResult.from_accept_terminals",
        frozenset(
            {
                "cur_accept_terminals",
                "next_accept_terminals",
                "remainder",
                "remainder_state",
                "next_ac_indents",
                "final_terminal",
                "ignore_terminals",
            }
        ),
    ),
    "IncrementalParser.get_acceptable_next_terminals": (
        "syncode.parsers.incremental_parser.IncrementalParser.get_acceptable_next_terminals",
        frozenset({"self", "partial_code"}),
    ),
    "MaskStore.get_accept_mask": (
        "syncode.mask_store.mask_store.MaskStore.get_accept_mask",
        frozenset({"self", "r", "get_list"}),
    ),
    "MaskStore.get_fsm_states": (
        "syncode.mask_store.mask_store.MaskStore.get_fsm_states",
        frozenset({"self", "r"}),
    ),
    "MaskStore._lookup_next_tokens": (
        "syncode.mask_store.mask_store.MaskStore._lookup_next_tokens",
        frozenset({"self", "fsm_states", "remainder_state", "accept_sequences"}),
    ),
    "MaskStore._lookup_next_tokens_for_fsm_state": (
        "syncode.mask_store.mask_store.MaskStore._lookup_next_tokens_for_fsm_state",
        frozenset({"self", "fsm_state", "next_terminal"}),
    ),
    "MaskStore.init_mask_store": (
        "syncode.mask_store.mask_store.MaskStore.init_mask_store",
        frozenset({"grammar", "tokenizer", "use_cache", "mode", "indent"}),
    ),
    "LookupTable.complete_case_lookup": (
        "syncode.mask_store.lookup_table.LookupTable.complete_case_lookup",
        frozenset({"self", "fsm_state"}),
    ),
    "LookupTable.incomplete_case_lookup": (
        "syncode.mask_store.lookup_table.LookupTable.incomplete_case_lookup",
        frozenset({"self", "fsm_state"}),
    ),
    "LookupTable.fsm_state_and_next_terminal_to_tokens": (
        "syncode.mask_store.lookup_table.LookupTable.fsm_state_and_next_terminal_to_tokens",
        frozenset({"self", "fsm_state", "next_terminal"}),
    ),
    "LookupTable._get_default_mask": (
        "syncode.mask_store.lookup_table.LookupTable._get_default_mask",
        frozenset({"self"}),
    ),
    "ByteTokenizer.decode": (
        "syncode.mask_store.byte_tokenizer.ByteTokenizer.decode",
        frozenset({"self", "token_ids", "skip_special_tokens"}),
    ),
}


class SyncodeVersionError(RuntimeError):
    """Fail-closed when installed SynCode is not the pinned research version."""


class SyncodeAdapterError(RuntimeError):
    """Fail-closed when required SynCode symbols/signatures differ."""


def get_installed_syncode_version() -> str:
    try:
        return importlib.metadata.version("syncode")
    except importlib.metadata.PackageNotFoundError as exc:
        raise SyncodeVersionError("syncode package is not installed") from exc


def require_syncode_version(*, allow_unsupported: bool = False) -> tuple[str, bool]:
    version = get_installed_syncode_version()
    if version == SUPPORTED_SYNCODE_VERSION:
        return version, False
    if allow_unsupported:
        return version, True
    raise SyncodeVersionError(
        f"SynCode {version!r} is not supported by this probe "
        f"(required {SUPPORTED_SYNCODE_VERSION}). Pass "
        f"allow_unsupported_syncode_version=true only with explicit awareness; "
        f"the override is recorded in the report."
    )


def _resolve_attr(dotted: str) -> Any:
    parts = dotted.split(".")
    # module path until we can import, then attributes
    for i in range(len(parts), 0, -1):
        mod_name = ".".join(parts[:i])
        try:
            mod = importlib.import_module(mod_name)
        except ImportError:
            continue
        obj: Any = mod
        for attr in parts[i:]:
            obj = getattr(obj, attr)
        return obj
    raise SyncodeAdapterError(f"cannot resolve symbol {dotted!r}")


def require_syncode_0416_adapter_surface() -> tuple[str, dict[str, str]]:
    """
    Verify required SynCode 0.4.16 symbols exist with expected parameter names.

    Returns ``(detail, resolved_signatures)``. Raises SyncodeAdapterError on mismatch.
    No silent compatibility fallback.
    """
    resolved: dict[str, str] = {}
    for label, (dotted, expected_params) in _REQUIRED_SIGNATURES.items():
        try:
            fn = _resolve_attr(dotted)
        except Exception as exc:  # noqa: BLE001
            raise SyncodeAdapterError(
                f"required symbol missing: {label} ({dotted}): {exc}"
            ) from exc
        try:
            params = frozenset(inspect.signature(fn).parameters.keys())
        except Exception as exc:  # noqa: BLE001
            raise SyncodeAdapterError(
                f"cannot inspect signature for {label}: {exc}"
            ) from exc
        if params != expected_params:
            raise SyncodeAdapterError(
                f"signature mismatch for {label}: got {sorted(params)} "
                f"expected {sorted(expected_params)}"
            )
        resolved[label] = str(inspect.signature(fn))
    return "all required SynCode 0.4.16 adapter symbols verified", resolved


def module_file_sha256(module_name: str) -> tuple[str, str]:
    mod = importlib.import_module(module_name)
    path = getattr(mod, "__file__", None)
    if not path:
        raise FileNotFoundError(f"module {module_name} has no __file__")
    data = Path(path).read_bytes()
    return str(Path(path).resolve()), hashlib.sha256(data).hexdigest()


def collect_syncode_source_shas() -> dict[str, str]:
    out: dict[str, str] = {}
    for label, mod_name in _SYNCODE_SOURCE_TARGETS:
        try:
            path, digest = module_file_sha256(mod_name)
            out[label] = digest
            out[f"{label}__path"] = path
        except Exception as exc:  # noqa: BLE001
            out[label] = f"UNAVAILABLE:{type(exc).__name__}"
    return out


def syncode_package_path() -> str:
    mod = importlib.import_module("syncode")
    return str(Path(getattr(mod, "__file__", "") or "").resolve())


def require_candidate_ids_in_vocab(tokenizer: Any, candidate_ids: list[int]) -> None:
    vocab_size = int(getattr(tokenizer, "vocab_size", 0) or 0)
    if vocab_size <= 0:
        raise SyncodeAdapterError("tokenizer.vocab_size missing or non-positive")
    for tid in candidate_ids:
        if tid < 0 or tid >= vocab_size:
            raise SyncodeAdapterError(
                f"candidate token id {tid} outside vocabulary [0, {vocab_size})"
            )
