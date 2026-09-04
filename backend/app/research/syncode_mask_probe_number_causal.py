"""
Checkpoint 3D — research-only NUMBER / based-literal causal analysis.

Observational by default. Optional ByteFSM.consume_prefix counterfactual is
restored in ``finally`` and never writes installed SynCode.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Optional

from app.models.syncode_mask_probe import (
    BasedNumberCausalEvidence,
    ByteTransitionStep,
    CandidateCausalTrace,
    CausalReasonCode,
    ConstructionReplayStep,
    NumberFsmWalkStep,
    NumberTerminalFsmEvidence,
    SequenceCausalTrace,
)
from app.research.minimal_based_number_grammar import CANONICAL_NUMBER_REGEXP
from app.research.syncode_mask_probe_causal import (
    PRIVATE_FUNCTIONS_INSPECTED,
    _lookup_branch_name,
    _mask_bit,
    _seq_terminals,
    replay_construction_for_candidate,
    walk_byte_transitions,
)
from app.services.syncode_parser_evidence import classify_accept_sequence

PRIVATE_FUNCTIONS_INSPECTED_3D = PRIVATE_FUNCTIONS_INSPECTED + [
    "syncode.mask_store.byte_fsm.ByteFSM.consume_prefix",
    "syncode.mask_store.byte_fsm.ByteFSM.islive",
    "syncode.mask_store.byte_fsm.ByteFSM.accepts",
]


def extract_number_terminal_definition(grammar_text: str) -> tuple[str, str]:
    """Return ``(verbatim_line_or_block, regexp)`` for the NUMBER terminal."""
    from syncode.parsers.grammars.grammar import Grammar
    from syncode.parsers import create_base_parser

    grammar = Grammar(grammar_text)
    base = create_base_parser(grammar)
    for terminal in base.terminals:
        if terminal.name == "NUMBER":
            regexp = terminal.pattern.to_regexp()
            verbatim = f"NUMBER: /{regexp}/"
            return verbatim, regexp
    raise RuntimeError("NUMBER terminal not found in grammar")


def _hex(data: Optional[bytes]) -> Optional[str]:
    if data is None:
        return None
    return bytes(data).hex()


def _digit_outgoing(byte_fsm: Any, state_id: Any) -> bool:
    """True if state has a transition on a hex digit (e.g. ``a`` = 0x61)."""
    for b in (0x61, 0x30, 0x41):  # a, 0, A
        if byte_fsm.get_next_state(state_id, b) is not None:
            return True
    return False


def walk_number_bytes(
    byte_fsm: Any, start_state: Any, data: bytes, *, label_prefix: str
) -> tuple[list[NumberFsmWalkStep], Optional[Any]]:
    steps: list[NumberFsmWalkStep] = []
    cur = start_state
    steps.append(
        NumberFsmWalkStep(
            label=f"{label_prefix}_start",
            state_id=str(cur),
            is_final=bool(cur in byte_fsm.finals),
            is_live=bool(byte_fsm.islive(cur)),
            transition_exists=True,
        )
    )
    for b in data:
        nxt = byte_fsm.get_next_state(cur, b)
        steps.append(
            NumberFsmWalkStep(
                label=f"{label_prefix}_byte",
                byte_value=int(b),
                byte_hex=f"{int(b):02x}",
                state_id=None if nxt is None else str(nxt),
                is_final=None if nxt is None else bool(nxt in byte_fsm.finals),
                is_live=None if nxt is None else bool(byte_fsm.islive(nxt)),
                transition_exists=nxt is not None,
            )
        )
        if nxt is None:
            return steps, None
        cur = nxt
    return steps, cur


def analyze_number_terminal_fsm(
    *,
    grammar_text: str,
    remainder_text: str = "16",
    short_bytes: bytes = b"'h",
    long_bytes: bytes = b"'ha",
) -> NumberTerminalFsmEvidence:
    """Trace SynCode's real NUMBER ByteFSM for remainder + short/long extensions."""
    from syncode.mask_store.byte_fsm import ByteFSM

    _verbatim, regexp = extract_number_terminal_definition(grammar_text)
    _ = CANONICAL_NUMBER_REGEXP  # documented invariant for reviewers
    fsm = ByteFSM(regexp)
    rem = remainder_text.encode("utf-8")
    state = fsm.initial
    for b in rem:
        nxt = fsm.get_next_state(state, b)
        if nxt is None:
            return NumberTerminalFsmEvidence(
                regexp=regexp,
                remainder_text=remainder_text,
                remainder_bytes_hex=rem.hex(),
                classification="terminal_fsm_transition_missing",
                detail=f"NUMBER FSM cannot consume remainder {remainder_text!r}",
            )
        state = nxt

    walk_s, after_s = walk_number_bytes(fsm, state, short_bytes, label_prefix="short")
    walk_l, after_l = walk_number_bytes(fsm, state, long_bytes, label_prefix="long")
    ok_s, rem_s = fsm.consume_prefix(short_bytes, state)
    ok_l, rem_l = fsm.consume_prefix(long_bytes, state)

    classification = "unknown"
    detail = ""
    if after_s is not None and after_s not in fsm.finals and fsm.islive(after_s):
        if ok_s and rem_s == short_bytes:
            classification = "viable_nonfinal_discarded_by_consume_prefix"
            detail = (
                "Direct walk after remainder+'h' ends in a live non-final NUMBER "
                "state with outgoing digit transitions, but ByteFSM.consume_prefix "
                "returns the full token as unconsumed remainder because the start "
                "state was already final (longest_accept_index=0)."
            )
        elif ok_s and rem_s == b"":
            classification = "viable_nonfinal_preserved"
            detail = "consume_prefix preserved live non-final extension"
        else:
            classification = "other"
            detail = f"short consume_prefix ok={ok_s} rem={rem_s!r}"
    elif after_s is None:
        classification = "dfa_rejects_short"
        detail = "NUMBER FSM has no transition path for short extension"
    elif after_s in fsm.finals:
        classification = "dfa_accepts_short_as_final"
        detail = "short extension reaches an accepting NUMBER state"

    if after_l is not None and after_l in fsm.finals and ok_l and rem_l == b"":
        if classification == "viable_nonfinal_discarded_by_consume_prefix":
            detail += (
                " Long token 'ha reaches an accepting NUMBER state and "
                "consume_prefix returns empty remainder."
            )

    return NumberTerminalFsmEvidence(
        regexp=regexp,
        remainder_text=remainder_text,
        remainder_bytes_hex=rem.hex(),
        state_after_remainder=str(state),
        state_after_remainder_is_final=bool(state in fsm.finals),
        state_after_remainder_is_live=bool(fsm.islive(state)),
        walk_short=walk_s,
        walk_long=walk_l,
        consume_prefix_short_ok=bool(ok_s),
        consume_prefix_short_remainder_hex=_hex(rem_s),
        consume_prefix_long_ok=bool(ok_l),
        consume_prefix_long_remainder_hex=_hex(rem_l),
        state_after_short=None if after_s is None else str(after_s),
        state_after_short_is_final=(
            None if after_s is None else bool(after_s in fsm.finals)
        ),
        state_after_short_is_live=(
            None if after_s is None else bool(fsm.islive(after_s))
        ),
        state_after_short_has_digit_transitions=(
            None if after_s is None else _digit_outgoing(fsm, after_s)
        ),
        state_after_long=None if after_l is None else str(after_l),
        state_after_long_is_final=(
            None if after_l is None else bool(after_l in fsm.finals)
        ),
        state_after_long_is_live=(
            None if after_l is None else bool(fsm.islive(after_l))
        ),
        accepts_remainder_plus_short=bool(fsm.accepts(rem + short_bytes)),
        accepts_remainder_plus_long=bool(fsm.accepts(rem + long_bytes)),
        classification=classification,
        detail=detail,
    )


def viable_nonfinal_consume_prefix(
    self: Any,
    data: Any,
    current_state: Any = None,
) -> tuple[bool, Optional[bytes]]:
    """Counterfactual ByteFSM.consume_prefix preserving live non-final extensions."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    cur_state = self.initial if current_state is None else current_state
    longest_accept_index = 0 if cur_state in self.finals else -1
    is_final = self.finals.__contains__
    data_len = len(data)
    if data_len == 0:
        if cur_state is not None and self.islive(cur_state):
            return True, b""
        return False, None

    i = 0
    while i < data_len:
        state_transitions = self.transitions.get(cur_state, {})
        if not state_transitions:
            break
        category = self._get_category(data[i])
        if category is not None and category in state_transitions:
            cur_state = state_transitions[category]
        else:
            cur_state = None
            break
        if is_final(cur_state):
            longest_accept_index = i + 1
        i += 1

    if (
        i == data_len
        and cur_state is not None
        and self.islive(cur_state)
        and longest_accept_index != -1
        and longest_accept_index < data_len
    ):
        return True, b""

    if longest_accept_index != -1:
        return True, data[longest_accept_index:]
    if cur_state is not None and self.islive(cur_state):
        return True, b""
    return False, None


@contextmanager
def temporary_viable_nonfinal_extension_counterfactual() -> Iterator[dict[str, Any]]:
    """EXPERIMENTAL research-only counterfactual; always restores in finally."""
    from syncode.mask_store.byte_fsm import ByteFSM

    probe: dict[str, Any] = {
        "restored": False,
        "experimental": True,
        "label": "viable_nonfinal_number_extension_counterfactual",
    }
    original = ByteFSM.consume_prefix
    ByteFSM.consume_prefix = viable_nonfinal_consume_prefix  # type: ignore[method-assign]
    try:
        yield probe
    finally:
        ByteFSM.consume_prefix = original  # type: ignore[method-assign]
        probe["restored"] = True


def _trace_candidate_on_number(
    mask_store: Any,
    *,
    parse_result: Any,
    token_id: int,
    token_bytes: bytes,
    decode: str,
    runtime_bits: dict[str, bool],
    reconstructed_bits: dict[str, bool],
    ignore_terminals: Optional[list[str]],
    current_accept_terminals: Optional[list[str]],
    next_accept_terminals: Optional[list[str]],
) -> CandidateCausalTrace:
    from syncode.parse_result import RemainderState

    rem_state = getattr(parse_result, "remainder_state", None)
    rem_name = rem_state.name if hasattr(rem_state, "name") else str(rem_state)
    rem_bytes = getattr(parse_result, "remainder", b"") or b""
    if isinstance(rem_bytes, str):
        rem_bytes = rem_bytes.encode("utf-8")

    fsm_states = list(mask_store.get_fsm_states(parse_result))
    sequences = list(getattr(parse_result, "accept_sequences", None) or [])
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
        matching = [s for s in fsm_states if getattr(s, "terminal", None) == terms[0]]
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
            bf = mask_store._fsms._terminals_to_byte_fsm.get(st.terminal)
            if bf is not None:
                byte_steps = walk_byte_transitions(bf, st.state_id, token_bytes)

            if rem_state == RemainderState.MAYBE_COMPLETE and len(terms) == 2:
                lookup_key = f"({st.terminal}:{st.state_id}, {terms[1]})"
                table = mask_store._lookup_table._fsm_state_and_next_terminal_to_tokens
                key = (st, terms[1])
                if key in table:
                    key_exists = True
                    stored_bit = _mask_bit(table[key], token_id)
                else:
                    key_exists = False
                    stored_bit = False
                    reason = "lookup_key_missing"
                construction = replay_construction_for_candidate(
                    mask_store,
                    fsm_state=st,
                    token_bytes=token_bytes,
                    next_terminal=terms[1],
                )
                ok, rem_after = mask_store._fsms.consume_prefix(st, token_bytes)
                if (
                    stored_bit is False
                    and ok
                    and rem_after == token_bytes
                    and getattr(st, "terminal", None) == "NUMBER"
                    and mask_store._fsms.is_final(st)
                ):
                    reason = "viable_nonfinal_state_discarded"
                    detail = (
                        "consume_prefix from final NUMBER state returned the full "
                        "token as remainder (prior accept preferred over live "
                        "non-final extension); next-terminal path then rejects"
                    )
                elif stored_bit is False:
                    for s in reversed(construction):
                        if s.stage == "construction_result" and not s.would_store_token:
                            reason = s.reason_code
                            detail = s.detail
                            break
                    if reason == "unknown":
                        reason = "stored_mask_bit_false"
            elif rem_state == RemainderState.MAYBE_COMPLETE and len(terms) == 1:
                try:
                    mask = mask_store._lookup_table.incomplete_case_lookup(st)
                    stored_bit = _mask_bit(mask, token_id)
                    key_exists = True
                    lookup_key = f"overapprox[{st.terminal}:{st.state_id}]"
                    if stored_bit is False and getattr(st, "terminal", None) == "NUMBER":
                        ok, rem_after = mask_store._fsms.consume_prefix(st, token_bytes)
                        if ok and rem_after == token_bytes:
                            reason = "viable_nonfinal_state_discarded"
                            detail = (
                                "incomplete_case path: live non-final NUMBER "
                                "extension discarded by consume_prefix"
                            )
                        elif stored_bit is False:
                            reason = "incomplete_terminal_token_not_stored"
                except Exception as exc:  # noqa: BLE001
                    reason = "unavailable_private_state"
                    detail = str(exc)
            else:
                reason = "unavailable_private_state"
                detail = f"branch {branch} not fully instrumented for NUMBER"

        if first_reason == "unknown" and stored_bit is False:
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
                remainder_bytes_hex=bytes(rem_bytes).hex(),
                fsm_state=None if st is None else f"{st.terminal}:{st.state_id}",
                fsm_is_final=(
                    None if st is None else bool(mask_store._fsms.is_final(st))
                ),
                candidate_bytes_hex=token_bytes.hex(),
                byte_transitions=byte_steps,
                construction_replay=construction,
                stored_per_sequence_bit=stored_bit,
                reason_code=reason,
                detail=detail,
            )
        )

    would_store = None
    num_states = [s for s in fsm_states if getattr(s, "terminal", None) == "NUMBER"]
    if num_states and sequences:
        terms0 = _seq_terminals(sequences[0])
        nxt = terms0[1] if len(terms0) > 1 else None
        if nxt and nxt != "$END":
            try:
                creplay = replay_construction_for_candidate(
                    mask_store,
                    fsm_state=num_states[0],
                    token_bytes=token_bytes,
                    next_terminal=nxt,
                )
                for s in creplay:
                    if s.stage == "construction_result":
                        would_store = s.would_store_token
            except Exception:  # noqa: BLE001
                would_store = None

    return CandidateCausalTrace(
        token_id=token_id,
        decode_text=decode,
        bytes_hex=token_bytes.hex(),
        runtime_union_bit=runtime_bits.get(str(token_id)),
        reconstructed_union_bit=reconstructed_bits.get(str(token_id)),
        construction_would_store_on_identifier_next=would_store,
        sequences=seq_traces,
        first_reject_reason=first_reason,
        first_reject_detail=first_detail,
    )


def build_based_number_causal(
    *,
    mask_store: Any,
    parse_result: Any,
    grammar_text: str,
    short_token_id: int,
    long_token_id: int,
    short_bytes: bytes,
    long_bytes: bytes,
    short_decode: str,
    long_decode: str,
    runtime_bits: dict[str, bool],
    reconstructed_bits: dict[str, bool],
    remainder_text: str = "16",
    ignore_terminals: Optional[list[str]] = None,
    current_accept_terminals: Optional[list[str]] = None,
    next_accept_terminals: Optional[list[str]] = None,
    digit_token_id: Optional[int] = None,
    digit_bytes: Optional[bytes] = None,
) -> BasedNumberCausalEvidence:
    warnings: list[str] = []
    number_fsm = analyze_number_terminal_fsm(
        grammar_text=grammar_text,
        remainder_text=remainder_text,
        short_bytes=short_bytes,
        long_bytes=long_bytes,
    )

    short_trace = _trace_candidate_on_number(
        mask_store,
        parse_result=parse_result,
        token_id=short_token_id,
        token_bytes=short_bytes,
        decode=short_decode,
        runtime_bits=runtime_bits,
        reconstructed_bits=reconstructed_bits,
        ignore_terminals=ignore_terminals,
        current_accept_terminals=current_accept_terminals,
        next_accept_terminals=next_accept_terminals,
    )
    long_trace = _trace_candidate_on_number(
        mask_store,
        parse_result=parse_result,
        token_id=long_token_id,
        token_bytes=long_bytes,
        decode=long_decode,
        runtime_bits=runtime_bits,
        reconstructed_bits=reconstructed_bits,
        ignore_terminals=ignore_terminals,
        current_accept_terminals=current_accept_terminals,
        next_accept_terminals=next_accept_terminals,
    )

    first_field = "ByteFSM.consume_prefix"
    first_reason: CausalReasonCode = "unknown"
    first_detail = number_fsm.detail
    if number_fsm.classification == "viable_nonfinal_discarded_by_consume_prefix":
        first_reason = "viable_nonfinal_state_discarded"
        first_detail = number_fsm.detail
    elif number_fsm.classification == "dfa_rejects_short":
        first_reason = "terminal_fsm_transition_missing"
    elif number_fsm.classification == "dfa_accepts_short_as_final":
        first_reason = "other"
        first_detail = "short token already accepting; unexpected for this case"

    separate = None
    if digit_token_id is not None and digit_bytes is not None:
        from syncode.mask_store.byte_fsm import ByteFSM

        _, regexp = extract_number_terminal_definition(grammar_text)
        fsm = ByteFSM(regexp)
        rem = remainder_text.encode("utf-8")
        st = fsm.initial
        for b in rem + short_bytes:
            st = fsm.get_next_state(st, b)
        digit_ok = (
            st is not None and fsm.get_next_state(st, digit_bytes[0]) is not None
        )
        separate = {
            "hypothesis": "short_token_plus_separate_digit_token",
            "digit_token_id": digit_token_id,
            "digit_bytes_hex": digit_bytes.hex(),
            "dfa_allows_digit_after_short": digit_ok,
            "note": (
                "A valid NUMBER can be formed across tokenizer boundaries "
                "('h then a...), but MaskStore admits only tokens that "
                "survive consume_prefix from the current NUMBER state."
            ),
        }

    reliable = (
        number_fsm.classification == "viable_nonfinal_discarded_by_consume_prefix"
        and runtime_bits.get(str(short_token_id)) is False
        and runtime_bits.get(str(long_token_id)) is True
    )

    return BasedNumberCausalEvidence(
        tracing_reliable=reliable,
        short_token_id=short_token_id,
        long_token_id=long_token_id,
        short_decode=short_decode,
        long_decode=long_decode,
        short_bytes_hex=short_bytes.hex(),
        long_bytes_hex=long_bytes.hex(),
        number_fsm=number_fsm,
        short_trace=short_trace,
        long_trace=long_trace,
        first_differing_field=first_field,
        first_differing_reason_code=first_reason,
        first_differing_detail=first_detail,
        original_short_mask_bit=runtime_bits.get(str(short_token_id)),
        original_long_mask_bit=runtime_bits.get(str(long_token_id)),
        separate_digit_token_hypothesis=separate,
        private_functions_inspected=list(PRIVATE_FUNCTIONS_INSPECTED_3D),
        warnings=warnings,
    )


def conclude_from_number_causal(
    evidence: BasedNumberCausalEvidence,
) -> tuple[str, str, list[str], dict[str, str]]:
    """
    Return ``(supported_conclusion, status, unresolved, scope_fields)``.

    ``scope_fields`` always separates minimal-control from original-Nemotron
    conclusions so a local tiny-vocab reproduction cannot silently promote the
    full Nemotron case.
    """
    unresolved: list[str] = []
    scope: dict[str, str] = {
        "minimal_control_conclusion": "unresolved_internal_evidence_unavailable",
        "original_nemotron_conclusion": "awaiting_full_runtime_verification",
        "conclusion_scope": "mixed_pending_nscc",
    }
    if not evidence.tracing_reliable:
        unresolved.append("number causal tracing marked unreliable")
    nf = evidence.number_fsm
    if nf is None:
        return (
            "unresolved_internal_evidence_unavailable",
            "unresolved",
            unresolved,
            scope,
        )

    mechanism_ok = (
        evidence.first_differing_reason_code == "viable_nonfinal_state_discarded"
        and nf.state_after_short_is_live is True
        and nf.state_after_short_is_final is False
        and nf.state_after_short_has_digit_transitions is True
        and nf.state_after_long_is_final is True
        and evidence.original_short_mask_bit is False
        and evidence.original_long_mask_bit is True
    )

    if mechanism_ok:
        scope["minimal_control_conclusion"] = (
            "verified_viable_nonfinal_number_state_discarded"
        )
        if evidence.full_vocab_runtime_verified:
            scope["original_nemotron_conclusion"] = (
                "verified_viable_nonfinal_number_state_discarded"
            )
            scope["conclusion_scope"] = "original_nemotron_full"
            return (
                "verified_viable_nonfinal_number_state_discarded",
                "conclusive",
                unresolved,
                scope,
            )
        scope["original_nemotron_conclusion"] = "awaiting_full_runtime_verification"
        scope["conclusion_scope"] = "mixed_pending_nscc"
        unresolved.extend(
            [
                "Fixed-k remains independently UNAVAILABLE",
                "original_nemotron_conclusion=awaiting_full_runtime_verification "
                "until NSCC full-vocab HF decode, ByteTokenizer bytes, exact "
                "prefix/witness, runtime bit, union equality and causal replay",
            ]
        )
        # Top-level remains the verified minimal mechanism, but scoped fields
        # prevent treating the original Nemotron case as conclusive.
        return (
            "verified_viable_nonfinal_number_state_discarded",
            "conclusive",
            unresolved,
            scope,
        )

    if nf.classification == "dfa_rejects_short":
        scope["minimal_control_conclusion"] = "verified_number_terminal_dfa_defect"
        return (
            "verified_number_terminal_dfa_defect",
            "conclusive",
            unresolved,
            scope,
        )

    if evidence.original_short_mask_bit is True:
        scope["minimal_control_conclusion"] = "no_completeness_violation"
        return "no_completeness_violation", "conclusive", unresolved, scope

    unresolved.append(f"classification={nf.classification}")
    return (
        "unresolved_internal_evidence_unavailable",
        "unresolved",
        unresolved,
        scope,
    )
