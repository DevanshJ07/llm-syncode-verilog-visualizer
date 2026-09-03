"""Mask-store cache modes for the research probe (isolated from production)."""

from __future__ import annotations

import hashlib
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

from app.models.syncode_mask_probe import MaskStoreIdentity, MaskStoreMode


class MaskStoreCacheError(RuntimeError):
    """Fail-closed cache / path errors for the research probe."""


@contextmanager
def temporary_syncode_cache(cache_root: Path) -> Iterator[str]:
    """
    Point syncode.common.SYNCODE_CACHE at an isolated directory for the duration.

    Restores the previous value in ``finally`` even on exception.
    Never deletes or overwrites a shared production cache.
    """
    import syncode.common as syncode_common

    cache_root = Path(cache_root)
    cache_root.mkdir(parents=True, exist_ok=True)
    root = str(cache_root.resolve())
    if not root.endswith(("/", "\\")):
        root = root + os.sep
    previous = getattr(syncode_common, "SYNCODE_CACHE", "cache/")
    syncode_common.SYNCODE_CACHE = root
    try:
        yield root
    finally:
        syncode_common.SYNCODE_CACHE = previous


def expected_mask_store_pickle_path(
    cache_root: str,
    *,
    tokenizer: Any,
    grammar: Any,
    mode: str,
) -> Path:
    tokenizer_name = type(tokenizer).__name__
    grammar_hash = grammar.hash()
    vocab_size = int(tokenizer.vocab_size)
    return (
        Path(cache_root)
        / "mask_stores"
        / tokenizer_name
        / f"{mode}_{grammar_hash}_{vocab_size}.pkl"
    )


def file_sha256(path: Path) -> Optional[str]:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_or_load_mask_store(
    *,
    mode: MaskStoreMode,
    cache_root: Path,
    grammar: Any,
    tokenizer: Any,
    syncode_mode: str = "grammar_mask",
) -> tuple[Any, MaskStoreIdentity]:
    """
    existing_cache: require an explicit cache root that already contains the
    matching pickle; never silently construct a new store into a shared path.
    fresh_isolated: construct under a dedicated directory; retain artifacts;
    never delete/overwrite unrelated shared caches.
    """
    from syncode.mask_store.mask_store import MaskStore

    notes: list[str] = []
    cache_root = Path(cache_root)
    if mode == "existing_cache":
        if not str(cache_root).strip():
            raise MaskStoreCacheError(
                "existing_cache requires an explicit non-empty cache_root path"
            )
        notes.append(
            "existing_cache mode: using only the explicitly supplied cache path; "
            "not claimed to be the original NSCC cache unless independently proven"
        )
    else:
        notes.append(
            "fresh_isolated mode: constructing under a dedicated directory; "
            "shared production cache is not deleted or overwritten"
        )

    with temporary_syncode_cache(cache_root) as root:
        pickle_path = expected_mask_store_pickle_path(
            root, tokenizer=tokenizer, grammar=grammar, mode=syncode_mode
        )
        if mode == "existing_cache":
            if not pickle_path.is_file():
                raise MaskStoreCacheError(
                    f"existing_cache pickle not found at {pickle_path}; "
                    "refusing to silently construct a new store"
                )
            use_cache_flag = True
        else:
            use_cache_flag = False

        t0 = time.perf_counter()
        store = MaskStore.init_mask_store(
            grammar,
            tokenizer,
            use_cache=use_cache_flag,
            mode=syncode_mode,
            indent=False,
        )
        elapsed = time.perf_counter() - t0

        identity = MaskStoreIdentity(
            mode=mode,
            syncode_mode=syncode_mode,
            cache_root=root,
            cache_path=str(pickle_path),
            cache_file_sha256=file_sha256(pickle_path),
            construction_seconds=elapsed,
            claimed_original_nscc_cache=False,
            notes=notes,
        )
        return store, identity
