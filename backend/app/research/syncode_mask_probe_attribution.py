"""
Per-accept-sequence mask attribution adapter for SynCode 0.4.16.

Calls the installed MaskStore.get_accept_mask once for the runtime mask, then
reconstructs per-sequence contributions via the same private lookup helpers
used by MaskStore._lookup_next_tokens — without monkeypatching production
singletons or mutating lookup tables.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from app.models.syncode_mask_probe import (
    AcceptSequenceAttribution,
    MaskAttributionEvidence,
    SequenceCandidateAttribution,
)
from app.services.syncode_parser_evidence import classify_accept_sequence


def _seq_terminals(seq: Any) -> list[str]:
    terms = getattr(seq, "accept_terminals", None)
    if terms is not None:
        return [str(t) for t in list(terms)]
    return [str(t) for t in list(seq)]


def _mask_bit(mask: Any, token_id: int) -> Optional[bool]:
    try:
        return bool(mask[token_id].item() if hasattr(mask[token_id], "item") else mask[token_id])
    except Exception:  # noqa: BLE001
        return None


def _lookup_mask_for_single_sequence(
    mask_store: Any,
    fsm_states: Iterable[Any],
    remainder_state: Any,
    accept_sequence: Any,
) -> tuple[Any, bool, str]:
    """
    Reproduce MaskStore._lookup_next_tokens for exactly one accept sequence.

    Returns ``(mask_tensor, overapprox_used, detail)``.
    """
    from syncode.parse_result import RemainderState

    table = mask_store._lookup_table
    accept_token_mask = table._get_default_mask()
    overapprox = False
    detail = "ok"
    seq = accept_sequence

    for fsm_state in fsm_states:
        if len(seq) == 0:
            continue
        if seq[0] == "$END":
            accept_token_mask[mask_store.eos_token_id] = 1
        if fsm_state.terminal != seq[0]:
            continue

        if remainder_state == RemainderState.COMPLETE:
            accept_token_mask |= table.complete_case_lookup(fsm_state)
        elif remainder_state == RemainderState.INCOMPLETE:
            accept_token_mask |= table.incomplete_case_lookup(fsm_state)
            if getattr(mask_store, "_mode", "") == "grammar_mask":
                overapprox = True
        elif remainder_state == RemainderState.MAYBE_COMPLETE:
            if len(seq) == 1:
                accept_token_mask |= table.incomplete_case_lookup(fsm_state)
                if getattr(mask_store, "_mode", "") == "grammar_mask":
                    overapprox = True
            elif len(seq) == 2:
                accept_token_mask |= mask_store._lookup_next_tokens_for_fsm_state(
                    fsm_state, seq[1]
                )
            elif len(seq) == 3:
                if mask_store._fsms.is_final(fsm_state):
                    ignore_init_state = mask_store._fsms.initial(seq[1])
                    accept_token_mask |= mask_store._lookup_next_tokens_for_fsm_state(
                        ignore_init_state, seq[2]
                    )
            else:
                detail = f"invalid accept sequence length {len(seq)}"
        else:
            detail = f"unhandled remainder_state={remainder_state}"

    return accept_token_mask, overapprox, detail


def attribute_mask(
    mask_store: Any,
    parse_result_for_mask: Any,
    *,
    candidate_token_ids: list[int],
    byte_tokenizer: Any = None,
    current_accept_terminals: Optional[list[str]] = None,
    next_accept_terminals: Optional[list[str]] = None,
    ignore_terminals: Optional[list[str]] = None,
) -> MaskAttributionEvidence:
    warnings: list[str] = []

    # ONE runtime call — do not advance IncrementalParser here.
    runtime_mask = mask_store.get_accept_mask(parse_result_for_mask)
    runtime_bits = {
        str(tid): bool(_mask_bit(runtime_mask, tid)) for tid in candidate_token_ids
    }

    rem_state = getattr(parse_result_for_mask, "remainder_state", None)
    rem_name = rem_state.name if hasattr(rem_state, "name") else str(rem_state)
    fsm_states = list(mask_store.get_fsm_states(parse_result_for_mask))
    fsm_ids = [f"{getattr(s, 'terminal', '?')}:{getattr(s, 'state_id', '?')}" for s in fsm_states]

    sequences = list(getattr(parse_result_for_mask, "accept_sequences", None) or [])
    per_seq: list[AcceptSequenceAttribution] = []
    # Reconstruct union starting from default mask (same as SynCode).
    reconstructed = mask_store._lookup_table._get_default_mask().clone()

    for seq in sequences:
        terms = _seq_terminals(seq)
        kind, has_ignore = classify_accept_sequence(
            terms,
            remainder_state=rem_name,
            ignore_terminals=ignore_terminals,
            current_accept_terminals=current_accept_terminals,
            next_accept_terminals=next_accept_terminals,
        )
        try:
            seq_mask, overapprox, detail = _lookup_mask_for_single_sequence(
                mask_store, fsm_states, rem_state, seq
            )
            reconstructed |= seq_mask
            lookup_status = "VERIFIED"
        except Exception as exc:  # noqa: BLE001
            seq_mask = None
            overapprox = None
            detail = f"{type(exc).__name__}: {exc}"
            lookup_status = "UNAVAILABLE"
            warnings.append(f"sequence {terms} lookup failed: {detail}")

        cand_attrs: list[SequenceCandidateAttribution] = []
        for tid in candidate_token_ids:
            hex_bytes = ""
            if byte_tokenizer is not None:
                try:
                    b = byte_tokenizer.decode([tid], skip_special_tokens=False)
                    if isinstance(b, (bytes, bytearray)):
                        hex_bytes = bytes(b).hex()
                except Exception:  # noqa: BLE001
                    pass
            bit = _mask_bit(seq_mask, tid) if seq_mask is not None else None
            cand_attrs.append(
                SequenceCandidateAttribution(
                    token_id=tid,
                    contributed_bit=bit,
                    syncode_bytes_hex=hex_bytes,
                    status="VERIFIED" if bit is not None else "UNAVAILABLE",  # type: ignore[arg-type]
                    detail="" if bit is not None else detail,
                )
            )

        per_seq.append(
            AcceptSequenceAttribution(
                terminals=terms,
                construction_kind=kind,
                contains_ignored_terminal=has_ignore,
                remainder_state=rem_name,
                fsm_state_ids=list(fsm_ids),
                overapprox_path_used=overapprox,
                lookup_status=lookup_status,  # type: ignore[arg-type]
                lookup_detail=detail,
                candidates=cand_attrs,
            )
        )

    reconstructed_bits = {
        str(tid): bool(_mask_bit(reconstructed, tid)) for tid in candidate_token_ids
    }

    # Full-vocab equality check when tensors comparable.
    equal: Optional[bool] = None
    differing: Optional[int] = None
    try:
        import torch

        if isinstance(runtime_mask, torch.Tensor) and isinstance(reconstructed, torch.Tensor):
            neq = runtime_mask.bool() != reconstructed.bool()
            differing = int(neq.sum().item())
            equal = differing == 0
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"full-mask equality check failed: {exc}")
        # Fall back to candidate-bit comparison only.
        equal = all(
            runtime_bits[str(t)] == reconstructed_bits[str(t)] for t in candidate_token_ids
        )
        differing = sum(
            1
            for t in candidate_token_ids
            if runtime_bits[str(t)] != reconstructed_bits[str(t)]
        )

    cand_differ = {
        str(t): runtime_bits[str(t)] != reconstructed_bits[str(t)]
        for t in candidate_token_ids
    }
    reliable = bool(equal is True)
    if not reliable:
        warnings.append(
            "reconstructed union mask differs from runtime get_accept_mask; "
            "per-sequence attribution marked unreliable and must not be treated "
            "as factual explanation"
        )

    return MaskAttributionEvidence(
        runtime_mask_bits=runtime_bits,
        reconstructed_union_bits=reconstructed_bits,
        reconstructed_union_equal_runtime=equal,
        differing_bit_count=differing,
        candidate_bits_differ=cand_differ,
        attribution_reliable=reliable,
        per_sequence=per_seq,
        warnings=warnings,
    )
