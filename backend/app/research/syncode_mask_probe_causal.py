"""
Checkpoint 3C — research-only SynCode 0.4.16 causal tracer.

Observational by default: reads MaskStore / FSMSet / LookupTable private
structures and replays construction logic without mutating stored masks.

Optional temporary hooks around ``get_accept_mask`` are restored in ``finally``.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Optional

from app.models.syncode_mask_probe import (
    ByteTransitionStep,
    CandidateCausalTrace,
    CausalDifferentialEvidence,
    CausalReasonCode,
    ConstructionReplayStep,
    SequenceCausalTrace,
)
from app.services.syncode_parser_evidence import classify_accept_sequence

PRIVATE_FUNCTIONS_INSPECTED = [
    "syncode.mask_store.mask_store.MaskStore.get_accept_mask",
    "syncode.mask_store.mask_store.MaskStore._lookup_next_tokens",
    "syncode.mask_store.mask_store.MaskStore._lookup_next_tokens_for_fsm_state",
    "syncode.mask_store.mask_store.MaskStore._process_regular_tokens",
    "syncode.mask_store.mask_store.MaskStore._process_complete_case",
    "syncode.mask_store.mask_store.MaskStore._remove_left_whitespace",
    "syncode.mask_store.mask_store.MaskStore._store_token_masks",
    "syncode.mask_store.lookup_table.LookupTable.complete_case_lookup",
    "syncode.mask_store.lookup_table.LookupTable.incomplete_case_lookup",
    "syncode.mask_store.lookup_table.LookupTable.fsm_state_and_next_terminal_to_tokens",
    "syncode.mask_store.fsm_set.FSMSet.compute_fsm_states",
    "syncode.mask_store.fsm_set.FSMSet.consume_prefix",
    "syncode.mask_store.fsm_set.FSMSet.is_final",
    "syncode.mask_store.byte_fsm.ByteFSM.accepts",
    "syncode.mask_store.byte_fsm.ByteFSM.get_next_state",
]


def extract_ws_terminal_definition(grammar_text: str) -> tuple[str, str]:
    """
    Return ``(verbatim_ws_block, regexp)`` for the WS terminal used by SynCode.

    Canonical Verilog grammar uses ``%import common.WS``. SynCode/larkm resolves
    that to ``common.lark``: ``WS: /[ \\t\\f\\r\\n]/+/``.
    """
    from syncode.parsers.grammars.grammar import Grammar
    from syncode.parsers import create_base_parser

    grammar = Grammar(grammar_text)
    base = create_base_parser(grammar)
    for terminal in base.terminals:
        if terminal.name == "WS":
            regexp = terminal.pattern.to_regexp()
            # Verbatim from syncode.larkm grammars/common.lark (installed 0.4.16).
            verbatim = "WS: /[ \\t\\f\\r\\n]/+/"
            return verbatim, regexp
    raise RuntimeError("WS terminal not found in grammar")


def ws_dfa_accepts_bytes(grammar_text: str, samples: dict[str, bytes]) -> dict[str, bool]:
    from syncode.mask_store.byte_fsm import ByteFSM

    _verbatim, regexp = extract_ws_terminal_definition(grammar_text)
    fsm = ByteFSM(regexp)
    return {label: bool(fsm.accepts(data)) for label, data in samples.items()}


def lark_ws_lexer_accepts(samples: dict[str, bytes]) -> dict[str, Optional[bool]]:
    """Run strings through a tiny Lark grammar that only ignores WS."""
    from app.services.verilog_validation import _load_lark_module

    lark = _load_lark_module()
    if lark is None:
        return {k: None for k in samples}
    # Import the same common.WS definition style as the canonical grammar.
    g = '%import common.WS\n%ignore WS\nstart: "X"\n'
    try:
        parser = lark.Lark(g, parser="lalr")
    except Exception:  # noqa: BLE001
        return {k: None for k in samples}
    out: dict[str, Optional[bool]] = {}
    for label, data in samples.items():
        text = data.decode("utf-8")
        try:
            parser.parse("X" + text)  # trailing WS ignored after X? better: WS around
            out[label] = True
        except Exception:  # noqa: BLE001
            try:
                parser.parse(text + "X")
                out[label] = True
            except Exception:  # noqa: BLE001
                out[label] = False
    return out


def _bytes_hex(data: bytes) -> str:
    return data.hex()


def _mask_bit(mask: Any, token_id: int) -> Optional[bool]:
    try:
        v = mask[token_id]
        return bool(v.item() if hasattr(v, "item") else v)
    except Exception:  # noqa: BLE001
        return None


def _seq_terminals(seq: Any) -> list[str]:
    terms = getattr(seq, "accept_terminals", None)
    if terms is not None:
        return [str(t) for t in list(terms)]
    return [str(t) for t in list(seq)]


def simulate_remove_left_whitespace(
    mask_store: Any, fsm_state: Any, remainder: bytes
) -> bytes:
    """Call the real MaskStore._remove_left_whitespace (read-only)."""
    return mask_store._remove_left_whitespace(fsm_state, remainder)


def replay_construction_for_candidate(
    mask_store: Any,
    *,
    fsm_state: Any,
    token_bytes: bytes,
    next_terminal: str,
) -> list[ConstructionReplayStep]:
    """
    Replay ``_process_complete_case`` + incomplete branch of
    ``_process_regular_tokens`` for one candidate without mutating the store.
    """
    steps: list[ConstructionReplayStep] = []
    rem0 = token_bytes.replace(b"\t", b"    ")

    # COMPLETE path
    rem_c = simulate_remove_left_whitespace(mask_store, fsm_state, rem0)
    ok_c, rem_c2 = mask_store._fsms.consume_prefix(fsm_state, rem_c)
    store_exact = bool(ok_c and len(rem_c2) == 0)
    steps.append(
        ConstructionReplayStep(
            stage="process_complete_case",
            detail="strip-left-ws then consume_prefix on current terminal",
            remainder_before=repr(rem0),
            remainder_after=repr(rem_c2),
            is_valid=bool(ok_c),
            would_store_token=store_exact,
            reason_code=(
                "whitespace_strip_asymmetric"
                if rem0 != rem_c and store_exact
                else ("stored_mask_bit_false" if not store_exact else "unknown")
            ),
        )
    )

    # INCOMPLETE / next-terminal path (same as _process_regular_tokens)
    rem = rem0
    ok, rem_after = mask_store._fsms.consume_prefix(fsm_state, rem)
    steps.append(
        ConstructionReplayStep(
            stage="consume_prefix_current_terminal",
            detail=f"terminal={getattr(fsm_state, 'terminal', None)}",
            remainder_before=repr(rem),
            remainder_after=repr(rem_after),
            is_valid=bool(ok),
            would_store_token=None,
            reason_code="unknown",
        )
    )
    would_store_next = False
    reason: CausalReasonCode = "unknown"
    if ok:
        if len(rem_after) == 0:
            would_store_next = True
            reason = "unknown"
            steps.append(
                ConstructionReplayStep(
                    stage="live_on_current_add_all_next",
                    detail="empty remainder after current terminal → store for all next",
                    remainder_after=repr(rem_after),
                    is_valid=True,
                    would_store_token=True,
                    reason_code="unknown",
                )
            )
        else:
            rem_stripped = simulate_remove_left_whitespace(
                mask_store, fsm_state, rem_after
            )
            strip_changed = rem_stripped != rem_after
            steps.append(
                ConstructionReplayStep(
                    stage="remove_left_whitespace",
                    detail=(
                        "MaskStore._remove_left_whitespace only lstrip(b' ') / "
                        "lstrip(' ') when ignore_whitespace and state is initial/final"
                    ),
                    remainder_before=repr(rem_after),
                    remainder_after=repr(rem_stripped),
                    is_valid=True,
                    would_store_token=None,
                    reason_code=(
                        "whitespace_strip_asymmetric"
                        if strip_changed
                        else "ignored_terminal_transition_missing"
                    ),
                )
            )
            init = mask_store._fsms.initial(next_terminal)
            ok_n, rem_n = mask_store._fsms.consume_prefix(init, rem_stripped)
            # grammar_mask overapprox: any valid consume stores
            would_store_next = bool(ok_n)
            if not ok_n:
                reason = "next_terminal_lookup_missing"
            elif getattr(mask_store, "_mode", "") == "grammar_strict" and len(rem_n) != 0:
                would_store_next = False
                reason = "remainder_not_finalized"
            steps.append(
                ConstructionReplayStep(
                    stage="consume_prefix_next_terminal",
                    detail=f"next_terminal={next_terminal}",
                    remainder_before=repr(rem_stripped),
                    remainder_after=repr(rem_n),
                    is_valid=bool(ok_n),
                    would_store_token=would_store_next,
                    reason_code=reason,
                )
            )
    else:
        reason = "terminal_dfa_rejects_byte"
        steps.append(
            ConstructionReplayStep(
                stage="current_terminal_reject",
                detail="consume_prefix failed on current terminal",
                is_valid=False,
                would_store_token=False,
                reason_code=reason,
            )
        )

    steps.append(
        ConstructionReplayStep(
            stage="construction_result",
            detail=f"would_store_on_({fsm_state.terminal}->{next_terminal})",
            would_store_token=bool(would_store_next or store_exact),
            reason_code=reason if not (would_store_next or store_exact) else "unknown",
        )
    )
    return steps


def walk_byte_transitions(
    byte_fsm: Any, start_state_id: Any, data: bytes
) -> list[ByteTransitionStep]:
    steps: list[ByteTransitionStep] = []
    cur = start_state_id
    for b in data:
        nxt = byte_fsm.get_next_state(cur, b)
        steps.append(
            ByteTransitionStep(
                byte_value=int(b),
                byte_hex=f"{int(b):02x}",
                state_before=str(cur),
                state_after=None if nxt is None else str(nxt),
                transition_exists=nxt is not None,
                reason_code="transition_missing" if nxt is None else "unknown",
            )
        )
        if nxt is None:
            break
        cur = nxt
    return steps


def _lookup_branch_name(remainder_state: Any, seq_len: int) -> str:
    from syncode.parse_result import RemainderState

    if remainder_state == RemainderState.COMPLETE:
        return "complete_case_lookup"
    if remainder_state == RemainderState.INCOMPLETE:
        return "incomplete_case_lookup"
    if remainder_state == RemainderState.MAYBE_COMPLETE:
        if seq_len == 1:
            return "maybe_complete_len1_incomplete_case_lookup"
        if seq_len == 2:
            return "maybe_complete_len2_fsm_state_and_next_terminal"
        if seq_len == 3:
            return "maybe_complete_len3_ignore_init_then_next"
        return f"maybe_complete_len{seq_len}_invalid"
    return f"unhandled_{remainder_state}"


@contextmanager
def temporary_accept_mask_hook(mask_store: Any) -> Iterator[dict[str, Any]]:
    """
    Optional temporary monkeypatch on ``get_accept_mask``; always restored.

    Observational primary tracing does not require this. Tests prove restoration.
    """
    probe: dict[str, Any] = {"calls": 0, "restored": False}
    original = mask_store.get_accept_mask

    def wrapped(r, get_list=False):
        probe["calls"] += 1
        return original(r, get_list=get_list)

    mask_store.get_accept_mask = wrapped  # type: ignore[method-assign]
    try:
        yield probe
    finally:
        mask_store.get_accept_mask = original  # type: ignore[method-assign]
        probe["restored"] = True


# Exact grammar whitespace byte set from common.WS: space tab FF CR LF
GRAMMAR_WS_BYTES = bytes([0x20, 0x09, 0x0C, 0x0D, 0x0A])


@contextmanager
def temporary_full_ws_strip_counterfactual(mask_store: Any) -> Iterator[dict[str, Any]]:
    """
    EXPERIMENTAL research-only counterfactual.

    Temporarily replaces MaskStore._remove_left_whitespace so that, when
    ignore_whitespace applies, leading bytes in {0x20,0x09,0x0c,0x0d,0x0a}
    are stripped — matching the ignored WS terminal character class.

    Does NOT modify installed SynCode on disk. Always restores the original
    bound method in ``finally``. Not a production fix.
    """
    probe: dict[str, Any] = {
        "restored": False,
        "experimental": True,
        "label": "full_grammar_ws_byte_strip_counterfactual",
        "ws_bytes_hex": GRAMMAR_WS_BYTES.hex(),
    }
    original = mask_store._remove_left_whitespace

    def counterfactual(self, fsm_state, remainder):
        # Compatible with class-method patching (self bound) and direct calls.
        if (
            self._fsms.initial(fsm_state.terminal) == fsm_state
            or self._fsms.is_final(fsm_state)
        ) and self._ignore_whitespace:
            if isinstance(remainder, bytes):
                return remainder.lstrip(GRAMMAR_WS_BYTES)
            if isinstance(remainder, str):
                return remainder.lstrip(" \t\f\r\n")
        return remainder

    mask_store._remove_left_whitespace = counterfactual  # type: ignore[method-assign]
    try:
        yield probe
    finally:
        mask_store._remove_left_whitespace = original  # type: ignore[method-assign]
        probe["restored"] = True


def record_call_path_for_candidate(
    mask_store: Any,
    *,
    fsm_state: Any,
    token_bytes: bytes,
    next_terminal: str,
) -> dict[str, Any]:
    """
    Exact call-path evidence for one candidate through construction replay.

    Path: _process_regular_tokens → consume_prefix(current) →
    _remove_left_whitespace → consume_prefix(next_terminal).
    """
    rem0 = token_bytes.replace(b"\t", b"    ")
    ok1, rem_after_id = mask_store._fsms.consume_prefix(fsm_state, rem0)
    before_strip = rem_after_id
    after_strip = rem_after_id
    next_ok: Optional[bool] = None
    rem_after_next = None
    first_failed = None
    would_store_next = False

    if not ok1:
        first_failed = "consume_prefix_current_terminal"
    elif len(rem_after_id or b"") == 0:
        would_store_next = True
    else:
        after_strip = mask_store._remove_left_whitespace(fsm_state, rem_after_id)
        init = mask_store._fsms.initial(next_terminal)
        next_ok, rem_after_next = mask_store._fsms.consume_prefix(init, after_strip)
        would_store_next = bool(next_ok)
        if bytes(before_strip) == bytes(after_strip) and not next_ok:
            first_failed = (
                "remove_left_whitespace_did_not_strip_then_next_terminal_failed"
            )
        elif not next_ok:
            first_failed = "consume_prefix_next_terminal"

    rem_c = mask_store._remove_left_whitespace(fsm_state, rem0)
    ok_c, rem_c2 = mask_store._fsms.consume_prefix(fsm_state, rem_c)
    store_exact = bool(ok_c and len(rem_c2) == 0)
    return {
        "input_bytes_hex": token_bytes.hex(),
        "after_tab_expand_hex": rem0.hex(),
        "consume_current_ok": bool(ok1),
        "remainder_before_strip_hex": None
        if before_strip is None
        else bytes(before_strip).hex(),
        "remainder_after_strip_hex": None
        if after_strip is None
        else bytes(after_strip).hex(),
        "next_terminal": next_terminal,
        "lookup_fsm_after_strip": f"{fsm_state.terminal}:{fsm_state.state_id}",
        "next_consume_ok": next_ok,
        "remainder_after_next_hex": None
        if rem_after_next is None
        else bytes(rem_after_next).hex(),
        "would_store_exact_complete_case": store_exact,
        "would_store_next_terminal_path": bool(store_exact or would_store_next),
        "first_failed_operation": first_failed,
    }


def build_causal_differential(
    *,
    mask_store: Any,
    parse_result: Any,
    grammar_text: str,
    newline_token_id: int,
    space_token_id: int,
    newline_bytes: bytes,
    space_bytes: bytes,
    runtime_bits: dict[str, bool],
    reconstructed_bits: dict[str, bool],
    ignore_terminals: Optional[list[str]] = None,
    current_accept_terminals: Optional[list[str]] = None,
    next_accept_terminals: Optional[list[str]] = None,
    use_hooks: bool = False,
) -> CausalDifferentialEvidence:
    """
    Compare newline vs space at the same parser/mask state.

    Does not mutate lookup tables. Optional hooks are restored in ``finally``.
    """
    from syncode.parse_result import RemainderState

    warnings: list[str] = []
    samples = {
        "space_20": b" ",
        "lf_0a": b"\n",
        "cr_0d": b"\r",
        "tab_09": b"\t",
        "lf_lf_0a0a": b"\n\n",
        "crlf_0d0a": b"\r\n",
    }
    try:
        ws_verbatim, ws_regexp = extract_ws_terminal_definition(grammar_text)
        ws_accepts = ws_dfa_accepts_bytes(grammar_text, samples)
    except Exception as exc:  # noqa: BLE001
        ws_verbatim, ws_regexp = "", ""
        ws_accepts = {k: None for k in samples}  # type: ignore[misc]
        warnings.append(f"WS DFA probe failed: {exc}")

    lark_accepts = lark_ws_lexer_accepts(samples)

    rem_state = getattr(parse_result, "remainder_state", None)
    rem_name = rem_state.name if hasattr(rem_state, "name") else str(rem_state)
    rem_bytes = getattr(parse_result, "remainder", b"") or b""
    if isinstance(rem_bytes, str):
        rem_bytes = rem_bytes.encode("utf-8")

    fsm_states = list(mask_store.get_fsm_states(parse_result))
    sequences = list(getattr(parse_result, "accept_sequences", None) or [])

    hooks_restored: Optional[bool] = None
    traced_equals: Optional[bool] = None
    if use_hooks:
        with temporary_accept_mask_hook(mask_store) as probe:
            runtime = mask_store.get_accept_mask(parse_result)
            traced_equals = True
            for tid in (newline_token_id, space_token_id):
                if _mask_bit(runtime, tid) != runtime_bits.get(str(tid)):
                    traced_equals = False
        hooks_restored = probe.get("restored")
    else:
        runtime = mask_store.get_accept_mask(parse_result)
        traced_equals = True
        for tid in (newline_token_id, space_token_id):
            if _mask_bit(runtime, tid) != runtime_bits.get(str(tid)):
                traced_equals = False
                warnings.append("traced get_accept_mask bit differs from provided runtime_bits")

    def trace_one(tid: int, raw: bytes, decode: str) -> CandidateCausalTrace:
        id_states = [s for s in fsm_states if getattr(s, "terminal", None) == "IDENTIFIER"]
        fsm = id_states[0] if id_states else (fsm_states[0] if fsm_states else None)
        seq_traces: list[SequenceCausalTrace] = []
        first_reason: CausalReasonCode = "unknown"
        first_detail = ""

        for seq in sequences:
            terms = _seq_terminals(seq)
            kind, has_ignore = classify_accept_sequence(
                terms,
                remainder_state=rem_name,
                ignore_terminals=ignore_terminals,
                current_accept_terminals=current_accept_terminals,
                next_accept_terminals=next_accept_terminals,
            )
            branch = _lookup_branch_name(rem_state, len(terms))
            matching = [
                s for s in fsm_states if getattr(s, "terminal", None) == terms[0]
            ]
            st = matching[0] if matching else None
            stored_bit: Optional[bool] = None
            lookup_key = None
            key_exists: Optional[bool] = None
            byte_steps: list[ByteTransitionStep] = []
            construction: list[ConstructionReplayStep] = []
            reason: CausalReasonCode = "unknown"
            detail = ""

            if st is None:
                reason = "unavailable_private_state"
                detail = f"no FSM state for terminal {terms[0]!r}"
            else:
                # Byte walk on current terminal FSM from remainder-derived state
                bf = mask_store._fsms._terminals_to_byte_fsm.get(st.terminal)
                if bf is not None:
                    byte_steps = walk_byte_transitions(bf, st.state_id, raw)

                if rem_state == RemainderState.MAYBE_COMPLETE and len(terms) == 2:
                    lookup_key = f"({st.terminal}:{st.state_id}, {terms[1]})"
                    table = mask_store._lookup_table._fsm_state_and_next_terminal_to_tokens
                    key = (st, terms[1])
                    # After conversion values are tensors; missing → defaultdict list []
                    if key in table:
                        key_exists = True
                        stored_bit = _mask_bit(table[key], tid)
                    else:
                        key_exists = False
                        stored_bit = False
                        reason = "lookup_key_missing"
                    construction = replay_construction_for_candidate(
                        mask_store,
                        fsm_state=st,
                        token_bytes=raw,
                        next_terminal=terms[1],
                    )
                    if stored_bit is False and any(
                        s.stage == "remove_left_whitespace"
                        and s.remainder_before != s.remainder_after
                        for s in construction
                    ):
                        # Space path typically strips; newline does not.
                        pass
                    if stored_bit is False:
                        # Prefer construction reason from last construction_result
                        for s in reversed(construction):
                            if s.stage == "construction_result" and not s.would_store_token:
                                reason = s.reason_code
                                detail = s.detail
                                break
                        if reason == "unknown":
                            reason = "stored_mask_bit_false"
                elif rem_state == RemainderState.MAYBE_COMPLETE and len(terms) == 3:
                    if mask_store._fsms.is_final(st):
                        ign = mask_store._fsms.initial(terms[1])
                        lookup_key = f"({ign.terminal}:{ign.state_id}, {terms[2]})"
                        table = (
                            mask_store._lookup_table._fsm_state_and_next_terminal_to_tokens
                        )
                        key = (ign, terms[2])
                        if key in table:
                            key_exists = True
                            stored_bit = _mask_bit(table[key], tid)
                        else:
                            key_exists = False
                            stored_bit = False
                            reason = "lookup_key_missing"
                    else:
                        reason = "remainder_not_finalized"
                        detail = "len-3 path requires final FSM state"
                elif rem_state == RemainderState.MAYBE_COMPLETE and len(terms) == 1:
                    branch = "maybe_complete_len1_incomplete_case_lookup"
                    try:
                        mask = mask_store._lookup_table.incomplete_case_lookup(st)
                        stored_bit = _mask_bit(mask, tid)
                        key_exists = True
                        lookup_key = f"overapprox[{st.terminal}:{st.state_id}]"
                    except Exception as exc:  # noqa: BLE001
                        reason = "unavailable_private_state"
                        detail = str(exc)
                else:
                    reason = "unavailable_private_state"
                    detail = f"branch {branch} not fully instrumented"

            if (
                first_reason == "unknown"
                and stored_bit is False
                and terms[:2] in (["IDENTIFIER", "COMMA"], ["IDENTIFIER", "RPAR"], ["IDENTIFIER", "LSQB"])
            ):
                first_reason = reason if reason != "unknown" else "stored_mask_bit_false"
                first_detail = detail or f"sequence {terms} stored bit false"

            seq_traces.append(
                SequenceCausalTrace(
                    terminals=terms,
                    construction_kind=kind,
                    contains_ignored_terminal=has_ignore,
                    lookup_branch=branch,
                    lookup_key=lookup_key,
                    lookup_key_exists=key_exists,
                    remainder_state=rem_name,
                    remainder_bytes_hex=_bytes_hex(bytes(rem_bytes)),
                    fsm_state=(
                        None
                        if st is None
                        else f"{st.terminal}:{st.state_id}"
                    ),
                    fsm_is_final=(
                        None if st is None else bool(mask_store._fsms.is_final(st))
                    ),
                    candidate_bytes_hex=_bytes_hex(raw),
                    byte_transitions=byte_steps,
                    construction_replay=construction,
                    stored_per_sequence_bit=stored_bit,
                    reason_code=reason,
                    detail=detail,
                )
            )

        # Construction differential on IDENTIFIER→COMMA specifically
        would_store = None
        if fsm is not None:
            creplay = replay_construction_for_candidate(
                mask_store, fsm_state=fsm, token_bytes=raw, next_terminal="COMMA"
            )
            for s in creplay:
                if s.stage == "construction_result":
                    would_store = s.would_store_token

        return CandidateCausalTrace(
            token_id=tid,
            decode_text=decode,
            bytes_hex=_bytes_hex(raw),
            runtime_union_bit=runtime_bits.get(str(tid)),
            reconstructed_union_bit=reconstructed_bits.get(str(tid)),
            construction_would_store_on_identifier_next=would_store,
            direct_ws_dfa_accepts=ws_accepts.get("lf_0a") if tid == newline_token_id else ws_accepts.get("space_20"),
            sequences=seq_traces,
            first_reject_reason=first_reason,
            first_reject_detail=first_detail,
        )

    newline_trace = trace_one(newline_token_id, newline_bytes, "\n")
    space_trace = trace_one(space_token_id, space_bytes, " ")

    # First differing field between construction replays on IDENTIFIER→COMMA
    first_field = None
    first_reason: CausalReasonCode = "unknown"
    first_detail = ""
    id_states = [s for s in fsm_states if getattr(s, "terminal", None) == "IDENTIFIER"]
    if id_states:
        st = id_states[0]
        nl_steps = replay_construction_for_candidate(
            mask_store, fsm_state=st, token_bytes=newline_bytes, next_terminal="COMMA"
        )
        sp_steps = replay_construction_for_candidate(
            mask_store, fsm_state=st, token_bytes=space_bytes, next_terminal="COMMA"
        )
        # Document strip asymmetry
        strip_map = {}
        for label, raw in (("newline_0a", newline_bytes), ("space_20", space_bytes)):
            before = raw.replace(b"\t", b"    ")
            # After consume_prefix remainder simulation for strip stage:
            ok, rem_after = mask_store._fsms.consume_prefix(st, before)
            if ok and rem_after:
                stripped = simulate_remove_left_whitespace(mask_store, st, rem_after)
                strip_map[label] = f"{rem_after!r} -> {stripped!r}"
            else:
                strip_map[label] = f"consume ok={ok} rem={rem_after!r}"
        # Find first stage where would_store / remainder differs
        for a, b in zip(nl_steps, sp_steps):
            if (
                a.remainder_after != b.remainder_after
                or a.would_store_token != b.would_store_token
            ):
                first_field = a.stage
                # Both process_complete_case and remove_left_whitespace stages
                # invoke _remove_left_whitespace; classify the asymmetry there.
                if a.stage in (
                    "process_complete_case",
                    "remove_left_whitespace",
                ) or (
                    a.remainder_before == b.remainder_before
                    and a.remainder_after != b.remainder_after
                ):
                    first_reason = "whitespace_strip_asymmetric"
                    first_field = (
                        "remove_left_whitespace"
                        if a.stage == "remove_left_whitespace"
                        else "process_complete_case/_remove_left_whitespace"
                    )
                    first_detail = (
                        "MaskStore._remove_left_whitespace lstrip only ASCII space "
                        f"(0x20); newline (0x0a) retained. strip_map={strip_map}"
                    )
                elif a.stage == "consume_prefix_next_terminal":
                    first_reason = "next_terminal_lookup_missing"
                    first_detail = (
                        f"after asymmetric strip, next terminal COMMA: "
                        f"newline ok={a.is_valid} space ok={b.is_valid}"
                    )
                else:
                    first_reason = a.reason_code
                    first_detail = f"newline={a} space={b}"
                break
        remove_map = strip_map
    else:
        remove_map = {}
        warnings.append("IDENTIFIER FSM state unavailable for differential")

    reliable = bool(traced_equals is True) and not any(
        "differs" in w for w in warnings
    )

    return CausalDifferentialEvidence(
        tracing_mode="temporary_hooks" if use_hooks else "observational",
        tracing_reliable=reliable,
        hooks_restored=hooks_restored,
        traced_mask_equals_runtime=traced_equals,
        ws_grammar_definition_verbatim=ws_verbatim,
        ws_regexp=ws_regexp,
        ws_dfa_accepts={k: bool(v) if v is not None else None for k, v in ws_accepts.items()},
        lark_ws_lexer_accepts=lark_accepts,
        remove_left_whitespace_strips=remove_map,
        first_differing_field=first_field,
        first_differing_reason_code=first_reason,
        first_differing_detail=first_detail,
        newline_trace=newline_trace,
        space_trace=space_trace,
        private_functions_inspected=list(PRIVATE_FUNCTIONS_INSPECTED),
        warnings=warnings,
    )


def conclude_from_causal(
    causal: CausalDifferentialEvidence,
) -> tuple[str, str, list[str]]:
    """
    Return ``(supported_conclusion, causal_conclusion_status, unresolved_reasons)``.

    Only returns a verified_* conclusion when the differential is reliable and
    the first differing stage is identified.
    """
    unresolved: list[str] = []
    if not causal.tracing_reliable:
        unresolved.append("tracing marked unreliable")
    if causal.traced_mask_equals_runtime is not True:
        unresolved.append("traced mask bits do not match runtime bits")
    # WS DFA accepts LF; construction strip does not treat LF as whitespace.
    if causal.first_differing_reason_code == "whitespace_strip_asymmetric":
        if causal.ws_dfa_accepts.get("lf_0a") is True:
            return (
                "verified_ignored_terminal_handling_defect",
                "conclusive",
                [
                    "Byte-level DFA transition traces beyond construction replay "
                    "remain limited to public ByteFSM walks",
                    "existing_cache comparison may still be UNAVAILABLE",
                ],
            )
    if causal.first_differing_reason_code == "next_terminal_lookup_missing":
        return (
            "verified_stored_mask_construction_defect",
            "conclusive",
            unresolved
            or [
                "Root asymmetry may still be ignored-terminal whitespace stripping"
            ],
        )
    unresolved.append(
        f"first_differing_reason_code={causal.first_differing_reason_code}"
    )
    return "unresolved_internal_evidence_unavailable", "unresolved", unresolved
