from __future__ import annotations

import asyncio
import logging
import os
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

import torch
import torch.nn.functional as F

from app.core.config import settings
from app.core.grammar import (
    CANONICAL_GRAMMAR_PATH,
    read_verilog_grammar,
)

# Compatibility aliases — single implementation lives in app.core.grammar.
_VERILOG_GRAMMAR_PATH: str = str(CANONICAL_GRAMMAR_PATH)


def _read_verilog_grammar() -> str:
    """Read canonical grammar text via ``app.core.grammar.read_verilog_grammar``."""
    content = read_verilog_grammar()
    logging.getLogger(__name__).info(
        "[grammar] Loaded Verilog grammar from: %s", _VERILOG_GRAMMAR_PATH
    )
    return content
from app.models.schemas import (
    DecodingStep,
    MaskedTokenEntry,
    TokenCandidate,
    TopMaskedTokenEntry,
    TopToken,
)
from app.services.grammar_firewall import PrefixOracle, select_valid_token
from app.services.generation_validation import (
    GenerationFailedError,
    SyncodeUnavailableError,
    validate_generation_result,
)
from app.services.grammar_diagnostics import (
    clear_verilog_validation_syncode_stub,
    ensure_syncode_evaluation_metadata,
    get_grammar_diagnostics,
    log_grammar_diagnostics,
    run_grammar_diagnostics,
)
from app.services.verilog_validation import parse_with_verilog_grammar
from app.models.syncode_parser_evidence import (
    SyncodeParserEvidence,
    failed_syncode_parser_evidence,
    unavailable_syncode_parser_evidence,
)
from app.services.syncode_parser_evidence import (
    format_legacy_accept_sequences,
    serialize_parse_result,
    syncode_package_version,
)

if TYPE_CHECKING:
    from transformers import PreTrainedModel, PreTrainedTokenizerBase

log = logging.getLogger(__name__)

# Single worker — Qwen2ForCausalLM is not safe for concurrent forward passes.
_executor = ThreadPoolExecutor(max_workers=1)

# Consecutive whitespace tokens before graceful stop.
_WHITESPACE_STALL_THRESHOLD: int = 10

# Max masked tokens sent per step (sorted by pre-mask prob server-side).
_TOP_MASKED_TOKENS_LIMIT: int = 50

# ---------------------------------------------------------------------------
# Debug trace buffer — stores the last completed generation's step-level
# diagnostic data.  Protected by a lock because the executor thread writes
# it while the event loop may read it concurrently.
# ---------------------------------------------------------------------------
_trace_lock = threading.Lock()
_last_trace: dict = {
    "generation_id": None,
    "prompt": "",
    "mode": "raw",
    "effective_syncode": False,
    "steps": [],
    "summary": {},
}


# ---------------------------------------------------------------------------
# Sampling helpers
# ---------------------------------------------------------------------------

def _apply_repetition_penalty(
    logits: torch.Tensor,
    past_ids: list[int],
    penalty: float,
) -> torch.Tensor:
    """
    Apply repetition penalty (Keskar et al. 2019) to raw logits in-place.

    Tokens that already appeared in past_ids have their logit divided (if
    positive) or multiplied (if negative) by *penalty*, making them less
    likely to be re-selected.  penalty=1.0 is a no-op.
    """
    if penalty == 1.0 or not past_ids:
        return logits
    logits = logits.clone()
    for token_id in set(past_ids):
        if token_id < logits.size(0):
            val = logits[token_id]
            logits[token_id] = val / penalty if val >= 0 else val * penalty
    return logits


def _nucleus_sample(probs: torch.Tensor, top_p: float) -> int:
    """
    Top-p (nucleus) sampling (Holtzman et al. 2020).

    Keeps the smallest set of tokens whose cumulative probability mass exceeds
    *top_p*, then re-normalises and samples.  Degrades to uniform sampling
    over the entire distribution when top_p >= 1.0.
    """
    if top_p >= 1.0:
        return int(torch.multinomial(probs.clamp(min=0.0), num_samples=1).item())

    sorted_probs, sorted_indices = torch.sort(probs, descending=True)
    cumulative_probs = torch.cumsum(sorted_probs, dim=-1)

    # Shift cumulative probs right so the first token is always included,
    # then zero out tokens past the nucleus boundary.
    to_remove = (cumulative_probs - sorted_probs) > top_p
    filtered = sorted_probs.clone()
    filtered[to_remove] = 0.0

    total = filtered.sum()
    if total <= 0.0:
        # Degenerate: fall back to the highest-probability token.
        return int(sorted_indices[0].item())

    filtered = filtered / total
    sampled_local_idx = int(torch.multinomial(filtered, num_samples=1).item())
    return int(sorted_indices[sampled_local_idx].item())


def _is_whitespace_token(token_str: str) -> bool:
    """Return True when a decoded token contains only whitespace/newline chars."""
    return bool(token_str) and not token_str.strip()


def _extract_module_block(text: str) -> str:
    """Return the first module...endmodule block, or *text* unchanged."""
    import re  # noqa: PLC0415

    match = re.search(r"(module\b.+?endmodule)", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return text


def _generated_text_is_parse_valid(text: str) -> bool:
    """
    True when *text* (or its first module...endmodule block) parses under
    the tested Verilog grammar.  Used for early-stop during SynCode decoding.
    """
    if not text or not text.strip():
        return False
    if "endmodule" not in text.lower():
        return False
    candidate = _extract_module_block(text)
    ok, _err = parse_with_verilog_grammar(candidate)
    return ok


def _compute_top_masked_tokens(
    probs_raw: torch.Tensor,
    masked_flag: torch.Tensor,
    tokenizer: "PreTrainedTokenizerBase",
    limit: int = _TOP_MASKED_TOKENS_LIMIT,
) -> list[TopMaskedTokenEntry]:
    """
    Return up to *limit* grammar-masked tokens sorted by pre-mask probability.

    Scans the full vocabulary masked set (not just top-k) but only serialises
    the highest-probability masked tokens to the frontend.
    """
    if masked_flag is None or not bool(masked_flag.any()):
        return []

    masked_ids = masked_flag.nonzero(as_tuple=True)[0]
    if masked_ids.numel() == 0:
        return []

    masked_probs = probs_raw[masked_ids]
    k = min(int(limit), int(masked_ids.numel()))
    top_probs, local_idx = torch.topk(masked_probs, k=k)
    top_ids = masked_ids[local_idx]

    entries: list[TopMaskedTokenEntry] = []
    for i in range(k):
        tid = int(top_ids[i].item())
        entries.append(
            TopMaskedTokenEntry(
                token=tokenizer.decode(
                    [tid],
                    skip_special_tokens=False,
                    clean_up_tokenization_spaces=False,
                ),
                token_id=tid,
                pre_mask_prob=float(top_probs[i].item()),
                status="masked by SynCode",
            )
        )
    return entries


# ---------------------------------------------------------------------------
# Syncode grammar constraint wrapper
# ---------------------------------------------------------------------------

class _SyncodeConstraint:
    """
    Wrapper around syncode 0.4.x SyncodeLogitsProcessor.

    Real API (confirmed against syncode 0.4.16):
        from syncode import Grammar, SyncodeLogitsProcessor
        grammar   = Grammar(read_verilog_grammar())  # canonical backend/grammar/verilog.lark
        processor = SyncodeLogitsProcessor(
                        grammar, tokenizer,
                        use_cache=True,          # caches DFA mask store to disk
                        parse_output_only=True,  # skip prompt tokens in parse state
                        mode='grammar_mask',
                    )
        processor.reset()                 # MUST call before every new generation
        masked = processor(all_input_ids, logits.unsqueeze(0))  # [1, vocab]

    The DFA mask store is cached to disk by syncode, so only the first run
    for a given (grammar, tokenizer) pair is slow (~30 s for Verilog grammar).
    """

    def __init__(
        self,
        tokenizer: "PreTrainedTokenizerBase",
        grammar: str = _VERILOG_GRAMMAR_PATH,
    ) -> None:
        self._processor = None
        self._available = False
        self._tokenizer = tokenizer
        self._grammar_name = grammar
        self._init_error: str = ""
        self._init_traceback: str = ""
        # Whitespace token IDs — precomputed once for fast per-step checking.
        self._whitespace_ids: frozenset[int] = self._build_whitespace_ids(tokenizer)
        log.info(
            "Syncode: %d whitespace-only token IDs in vocabulary",
            len(self._whitespace_ids),
        )

        clear_verilog_validation_syncode_stub()
        ensure_syncode_evaluation_metadata()

        try:
            # High-level SynCode API (installed package exposes these at top level).
            # Do NOT import syncode.grammar_decoder — that path does not exist here.
            from syncode import Syncode, SyncodeLogitsProcessor  # noqa: PLC0415, F401
            from syncode.parsers.grammars.grammar import Grammar  # noqa: PLC0415

            grammar_content = _read_verilog_grammar()
            log.info(
                "Initializing Syncode Verilog-grammar processor "
                "(first run compiles DFA mask store — may take ~30 s). "
                "Grammar path: %s  content_len: %d chars",
                grammar,
                len(grammar_content),
            )
            gram_obj = Grammar(grammar_content)
            self._processor = SyncodeLogitsProcessor(
                grammar=gram_obj,
                tokenizer=tokenizer,
                use_cache=True,
                parse_output_only=True,
                num_samples=1,
                mode="grammar_mask",
            )
            self._available = True
            log.info("Syncode ready (Verilog grammar, path=%s)", grammar)

            # Forensic instrumentation is optional — must not break masking.
            try:
                self._install_forensic_patch()
                self._forensic_init_probe()
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "Syncode forensic patch skipped (masking still active): %s", exc
                )

        except ImportError as exc:
            self._available = False
            self._processor = None
            self._init_error = f"{type(exc).__name__}: {exc}"
            self._init_traceback = traceback.format_exc()
            log.error(
                "syncode package import failed — SynCode masking unavailable. %s",
                self._init_error,
            )
        except Exception as exc:
            self._available = False
            self._processor = None
            self._init_error = f"{type(exc).__name__}: {exc}"
            self._init_traceback = traceback.format_exc()
            log.error(
                "Syncode initialization failed — SynCode masking unavailable. %s\n%s",
                self._init_error,
                self._init_traceback,
            )

    @property
    def init_error(self) -> str:
        return self._init_error

    @property
    def init_traceback(self) -> str:
        return self._init_traceback

    @property
    def mask_store_loaded(self) -> bool:
        return self._available

    @staticmethod
    def _build_whitespace_ids(tokenizer: "PreTrainedTokenizerBase") -> frozenset[int]:
        """
        Scan the vocabulary and collect IDs whose decoded string is pure
        whitespace (spaces, newlines, tabs).  Capped at vocab_size to avoid
        iterating into special-token ranges that may be sparse.
        """
        ws_ids: set[int] = set()
        vocab_size = getattr(tokenizer, "vocab_size", None) or 0
        for token_id in range(vocab_size):
            try:
                decoded = tokenizer.decode(
                    [token_id],
                    skip_special_tokens=True,
                    clean_up_tokenization_spaces=False,
                )
                if decoded and not decoded.strip():
                    ws_ids.add(token_id)
            except Exception:
                pass
        return frozenset(ws_ids)

    @property
    def available(self) -> bool:
        return self._available

    # ------------------------------------------------------------------
    # Forensic instrumentation
    # ------------------------------------------------------------------

    def _install_forensic_patch(self) -> None:
        """
        Monkey-patch grammar_engine.mask_scores so that every call to
        SyncodeLogitsProcessor.__call__ → GrammarConstrainer.mask_scores
        is wrapped with step-level forensic logging.

        Interception points inside mask_scores:
          • ge._parse_partial_output  → captures skip flag and partial_output
          • ge.dfa_mask_store.get_accept_mask → captures the exact ParseResult
            used to build the token mask (Phase 4A.1 structured evidence) plus
            accept_mask stats

        This is a pure read-side patch: the original logic runs unchanged;
        we only observe inputs and outputs at each internal call.  The original
        ``get_accept_mask`` is invoked exactly once per call; capture failures
        never alter the returned mask.
        """
        if self._processor is None:
            self._patch_status: dict = {"installed": False, "reason": "processor is None"}
            return
        ge = self._processor.grammar_engine

        # Step counter shared across the closure (reset each generation).
        self._forensic_step: list[int] = [0]
        forensic_log: list[dict] = []
        self._forensic_log = forensic_log
        self._parser_evidence_log: list[dict] = []

        original_mask_scores = ge.mask_scores
        original_get_accept_mask = ge.dfa_mask_store.get_accept_mask
        original_parse_partial = ge._parse_partial_output
        original_get_terms = ge.inc_parser.get_acceptable_next_terminals

        ws_ids = self._whitespace_ids
        _syn_version = syncode_package_version()
        _tok = getattr(ge, "tokenizer", None)
        _raw_eos = getattr(_tok, "eos_token_id", None) if _tok is not None else None
        if isinstance(_raw_eos, (list, tuple)):
            _app_eos_ids = [int(x) for x in _raw_eos]
            _syn_eos_id = int(_raw_eos[0]) if _raw_eos else None
        elif _raw_eos is not None:
            _app_eos_ids = [int(_raw_eos)]
            _syn_eos_id = int(_raw_eos)
        else:
            _app_eos_ids = []
            _syn_eos_id = None

        def patched_mask_scores(input_ids: "torch.Tensor", scores: "torch.Tensor") -> "torch.Tensor":
            step = self._forensic_step[0]
            self._forensic_step[0] += 1

            # Unconditional sentinel — proves this function was invoked.
            # Must be the very first operation; nothing above can raise.
            forensic_log.append({"_sentinel": True, "step": step})

            try:
                return _patched_mask_scores_impl(step, input_ids, scores)
            except Exception as _outer_exc:
                forensic_log[-1]["_outer_exception"] = (
                    f"{type(_outer_exc).__name__}: {_outer_exc}"
                )
                raise  # re-raise so syncode.mask()'s except clause handles it

        def _patched_mask_scores_impl(step: int, input_ids: "torch.Tensor", scores: "torch.Tensor") -> "torch.Tensor":
            # --- capture partial_output -----------------------------------------
            # NOTE: ge.start_from may still be None here (set inside original_mask_scores).
            # We attempt _get_partial_outputs anyway; it handles None gracefully.
            try:
                partial_list = ge._get_partial_outputs(input_ids)
                partial_str = partial_list[0][0] if partial_list else ""
                remainder_b = partial_list[0][1] if partial_list else b""
            except Exception as _pe:
                partial_str = f"<get_partial_outputs failed: {_pe}>"
                remainder_b = b""

            # --- intercept inc_parser.get_acceptable_next_terminals -------------
            # This is the method inside _parse_partial_output that actually raises
            # on a grammar parse error.  By patching it we capture the exception
            # before _parse_partial_output's except clause swallows it.
            _parse_exceptions: list[str] = []
            _parse_inputs: list[str] = []

            def patched_get_terms(partial_code: str):
                _parse_inputs.append(partial_code[:80])
                try:
                    result = original_get_terms(partial_code)
                    return result
                except Exception as _exc:
                    _parse_exceptions.append(
                        f"{type(_exc).__name__}: {str(_exc)[:120]}"
                    )
                    # Re-raise so _parse_partial_output's except clause handles it normally
                    raise

            # --- intercept _parse_partial_output --------------------------------
            _skip_results: list[bool] = []
            _accept_seqs: list = []
            _rem_state: list = []

            def patched_parse_partial(
                idx: int,
                partial_output: str,
                remainder_bytes: bytes,
                accepted_generation: bool = True,
            ) -> "tuple":
                res, skip = original_parse_partial(
                    idx, partial_output, remainder_bytes, accepted_generation
                )
                _skip_results.append(skip)
                if res is not None:
                    _accept_seqs.extend(
                        [str(s) for s in list(getattr(res, "accept_sequences", []))[:6]]
                    )
                    _rem_state.append(str(getattr(res, "remainder_state", "?")))
                return res, skip

            # --- intercept get_accept_mask --------------------------------------
            # Capture the exact ParseResult passed into get_accept_mask — this is
            # the parser result that builds the token mask (Phase 4A.1).
            _mask_stats: list[dict] = []
            _structured_evidence: list = []

            def patched_get_accept_mask(res: object) -> "torch.Tensor":
                # Call the original exactly once; never replace its result.
                mask = original_get_accept_mask(res)
                try:
                    prefix_for_hash = (
                        partial_str
                        if isinstance(partial_str, str)
                        and not partial_str.startswith("<get_partial_outputs failed")
                        else None
                    )
                    evidence = serialize_parse_result(
                        res,
                        mask_call_index=step,
                        generated_token_count_before_selection=step,
                        generated_prefix=prefix_for_hash,
                        syncode_version=_syn_version,
                        accept_mask=mask,
                        syncode_tokenizer_eos_token_id=_syn_eos_id,
                        application_eos_token_ids=_app_eos_ids,
                    )
                    _structured_evidence.append(evidence)
                except Exception as _cap_exc:
                    # Capture failures must not alter masking.
                    _structured_evidence.append(
                        failed_syncode_parser_evidence(
                            error=f"{type(_cap_exc).__name__}: {_cap_exc}",
                            warnings=[
                                "capture serialization failure; mask path unaffected"
                            ],
                            mask_call_index=step,
                            syncode_version=_syn_version,
                        )
                    )

                n_acc = int(mask.sum())
                total = int(mask.numel())
                all_v = bool(mask.all())
                all_inv = not bool(mask.any())

                # Are whitespace token IDs inside the accept window?
                ws_valid = 0
                ws_invalid = 0
                sample_ws_valid: list[int] = []
                sample_ws_invalid: list[int] = []
                for wid in sorted(ws_ids):
                    if wid >= total:
                        continue
                    if mask[wid]:
                        ws_valid += 1
                        if len(sample_ws_valid) < 5:
                            sample_ws_valid.append(wid)
                    else:
                        ws_invalid += 1
                        if len(sample_ws_invalid) < 5:
                            sample_ws_invalid.append(wid)

                first_valid = mask.nonzero(as_tuple=True)[0][:10].tolist()

                _mask_stats.append({
                    "n_accepted": n_acc,
                    "vocab_len": total,
                    "pct": round(n_acc / total * 100, 2) if total else 0.0,
                    "all_valid": all_v,
                    "all_invalid": all_inv,
                    "ws_valid": ws_valid,
                    "ws_invalid": ws_invalid,
                    "sample_ws_valid_ids": sample_ws_valid,
                    "sample_ws_invalid_ids": sample_ws_invalid,
                    "first_valid_ids": first_valid,
                    "accept_seqs_at_mask": [
                        str(s) for s in list(getattr(res, "accept_sequences", []) or [])[:6]
                    ],
                })
                return mask

            # Patch, run, unpatch ------------------------------------------------
            ge._parse_partial_output = patched_parse_partial
            ge.dfa_mask_store.get_accept_mask = patched_get_accept_mask
            ge.inc_parser.get_acceptable_next_terminals = patched_get_terms
            # Clone BEFORE calling original so we can compare before/after.
            # GrammarConstrainer.mask_scores modifies scores IN PLACE, meaning
            # result IS scores after the call and naive (result != scores) == 0.
            scores_before = scores.clone()
            try:
                result = original_mask_scores(input_ids, scores)
            finally:
                ge._parse_partial_output = original_parse_partial
                ge.dfa_mask_store.get_accept_mask = original_get_accept_mask
                ge.inc_parser.get_acceptable_next_terminals = original_get_terms

            # --- analyse result -------------------------------------------------
            n_changed = int((result != scores_before).sum())
            n_newly_inf = int(
                ((result == float("-inf")) & (scores_before > float("-inf"))).sum()
            )

            # Root-cause diagnosis --------------------------------------------------
            skip_flag = bool(_skip_results[0]) if _skip_results else None
            if _parse_exceptions:
                diagnosis = (
                    f"PARSE_EXCEPTION -> {_parse_exceptions[0]} "
                    f"[input: {(_parse_inputs[0] if _parse_inputs else '?')!r}]"
                )
            elif skip_flag is True:
                diagnosis = "PARSE_FAILED_SKIP -> scores returned unchanged (exception in earlier step)"
            elif _mask_stats and _mask_stats[0]["all_invalid"]:
                diagnosis = "ACCEPT_MASK_ALL_ZEROS -> no valid tokens -> scores returned unchanged"
            elif _mask_stats and _mask_stats[0]["all_valid"]:
                diagnosis = "ACCEPT_MASK_ALL_ONES -> grammar accepts FULL vocab -> no masking"
            elif n_newly_inf == 0 and n_changed == 0:
                diagnosis = "NO_CHANGE: logits identical to input (cause unknown)"
            else:
                diagnosis = f"MASKING_APPLIED: {n_newly_inf} tokens newly -inf"

            if _structured_evidence:
                structured_ev = _structured_evidence[0]
            else:
                structured_ev = unavailable_syncode_parser_evidence(
                    reason=(
                        "get_accept_mask was not called for this mask_scores "
                        "invocation (parse skip or no accept mask)"
                    ),
                    warnings=[
                        "no ParseResult captured at get_accept_mask; "
                        "not a recomputed substitute"
                    ],
                    mask_call_index=step,
                )

            if structured_ev.status == "recorded":
                legacy_accept_seqs = format_legacy_accept_sequences(
                    structured_ev, max_entries=6
                )
            else:
                legacy_accept_seqs = _accept_seqs[:6]

            try:
                self._parser_evidence_log.append(structured_ev.model_dump())
            except Exception:
                pass

            step_record: dict = {
                "step": step,
                "partial_output": partial_str[:100],
                "remainder_bytes": repr(remainder_b[:20]),
                "ge_start_from": ge.start_from,
                "ge_parse_failed": ge.parse_failed,
                "ge_ignore_whitespace": ge._ignore_whitespace,
                "skip": skip_flag,
                "parse_input": _parse_inputs[:2],
                "parse_exception": _parse_exceptions[:2],
                "accept_seqs": legacy_accept_seqs,
                "remainder_state": (
                    [structured_ev.remainder_state]
                    if structured_ev.remainder_state
                    else _rem_state[:1]
                ),
                "syncode_parser_evidence": structured_ev.model_dump(),
                "mask_stats": _mask_stats[:1],
                "n_changed": n_changed,
                "n_newly_inf": n_newly_inf,
                "diagnosis": diagnosis,
            }
            forensic_log.append(step_record)

            # Always print first 10 steps; print afterwards only when masking changes
            if step < 10 or n_newly_inf > 0 or _parse_exceptions:
                ms = _mask_stats[0] if _mask_stats else {}
                exc_str = _parse_exceptions[0][:80] if _parse_exceptions else "none"
                try:
                    print(
                        f"[FORENSIC step {step:>3}]"
                        f"  partial={partial_str[:40]!r}"
                        f"  start_from={ge.start_from}"
                        f"  skip={skip_flag}"
                        f"  n_accepted={ms.get('n_accepted','?')}/{ms.get('vocab_len','?')}"
                        f"  ({ms.get('pct','?')}%)"
                        f"  all_valid={ms.get('all_valid','?')}"
                        f"  ws_valid={ms.get('ws_valid','?')}"
                        f"  n_newly_inf={n_newly_inf}"
                        f"  parse_exc={exc_str!r}"
                        f"  -> {diagnosis}",
                        flush=True,
                    )
                except UnicodeEncodeError:
                    # Windows cp1252 consoles must not abort SynCode masking.
                    pass

            return result

        ge.mask_scores = patched_mask_scores
        self._patch_status = {
            "installed": True,
            "ge_id": id(ge),
            "ge_dict_has_mask_scores": "mask_scores" in ge.__dict__,
            "patched_fn_name": ge.__dict__.get("mask_scores", object).__name__
            if "mask_scores" in ge.__dict__ else "NOT_FOUND",
        }
        print(
            f"[FORENSIC] Installed forensic patch on grammar_engine.mask_scores "
            f"(grammar=verilog, path={self._grammar_name})",
            flush=True,
        )

    def _forensic_init_probe(self) -> None:
        """
        Cold probe: immediately after processor init, call
        get_acceptable_next_terminals('') to characterise what the grammar
        accepts at the initial (empty-output) state and how many vocab tokens
        pass the DFA mask.  Resets the parser afterward.
        """
        if self._processor is None:
            return
        ge = self._processor.grammar_engine

        print(
            f"\n[FORENSIC INIT PROBE] grammar=verilog"
            f"  ignore_whitespace={ge._ignore_whitespace}"
            f"  parse_output_only={ge.parse_output_only}"
            f"  whitespace_ids={len(self._whitespace_ids)}",
            flush=True,
        )

        try:
            ip = ge.inc_parser

            # Probe 1: empty partial output (step 0 condition)
            res = ip.get_acceptable_next_terminals("")
            accept_seqs = [str(s) for s in list(res.accept_sequences)[:8]]
            rem_state = str(res.remainder_state)

            accept_mask = ge.dfa_mask_store.get_accept_mask(res)
            n_acc = int(accept_mask.sum())
            total = int(accept_mask.numel())
            all_v = bool(accept_mask.all())
            first_valid = accept_mask.nonzero(as_tuple=True)[0][:20].tolist()

            ws_valid = sum(
                1 for wid in self._whitespace_ids
                if wid < total and accept_mask[wid]
            )
            ws_invalid = sum(
                1 for wid in self._whitespace_ids
                if wid < total and not accept_mask[wid]
            )

            print(
                f"[FORENSIC INIT PROBE] empty input ->"
                f"  remainder_state={rem_state}"
                f"  accept_seqs={accept_seqs}"
                f"  n_accepted={n_acc}/{total} ({n_acc/total*100:.1f}%)"
                f"  all_valid={all_v}"
                f"  ws_valid={ws_valid}  ws_invalid={ws_invalid}"
                f"  first_valid_ids={first_valid}",
                flush=True,
            )

            if all_v:
                print(
                    "[FORENSIC INIT PROBE] *** ROOT CAUSE CANDIDATE: "
                    "grammar accepts ALL tokens at initial state (overapproximation) "
                    "-> no masking will ever occur ***",
                    flush=True,
                )
            elif ws_valid > 0 and ws_invalid == 0:
                print(
                    "[FORENSIC INIT PROBE] *** NOTE: ALL whitespace tokens are "
                    "grammar-valid at initial state -> whitespace loop cannot be "
                    "prevented by grammar masking alone ***",
                    flush=True,
                )

            ip.reset()
            print("[FORENSIC INIT PROBE] Parser reset after probe.", flush=True)

        except Exception as exc:
            print(
                f"[FORENSIC INIT PROBE] probe failed: {type(exc).__name__}: {exc}",
                flush=True,
            )
            import traceback  # noqa: PLC0415
            traceback.print_exc()

    @property
    def forensic_log(self) -> list:
        """Return accumulated per-step forensic records (since last patch install)."""
        return getattr(self, "_forensic_log", [])

    def forensic_summary(self) -> dict:
        """Summarise the forensic log for the last generation."""
        log_entries = self.forensic_log
        if not log_entries:
            return {"steps": 0, "note": "no forensic data yet"}

        n_skip = sum(1 for e in log_entries if e.get("skip") is True)
        n_all_valid = sum(
            1 for e in log_entries
            if e.get("mask_stats") and e["mask_stats"][0].get("all_valid")
        )
        n_all_invalid = sum(
            1 for e in log_entries
            if e.get("mask_stats") and e["mask_stats"][0].get("all_invalid")
        )
        n_masking_applied = sum(1 for e in log_entries if e.get("n_newly_inf", 0) > 0)
        n_parse_exception = sum(1 for e in log_entries if e.get("parse_exception"))
        diagnoses = list({e.get("diagnosis", "?") for e in log_entries})

        # Find the FIRST step where a parse exception was thrown
        first_exc_step = next(
            (e for e in log_entries if e.get("parse_exception")), None
        )

        return {
            "total_steps": len(log_entries),
            "skip_steps": n_skip,
            "parse_exception_steps": n_parse_exception,
            "all_valid_mask_steps": n_all_valid,
            "all_invalid_mask_steps": n_all_invalid,
            "masking_applied_steps": n_masking_applied,
            "unique_diagnoses": diagnoses,
            "first_parse_exception_step": first_exc_step,
            "first_step": log_entries[0] if log_entries else None,
            "last_step": log_entries[-1] if log_entries else None,
        }

    def reset(self) -> None:
        """Reset parse state — MUST be called before each new generation."""
        if self._processor is not None:
            try:
                self._processor.reset()
            except Exception as exc:
                log.debug("Syncode reset failed: %s", exc)

    def mask(
        self,
        all_input_ids: torch.Tensor,  # [1, seq_len] — full context (prompt + generated)
        logits: torch.Tensor,          # [vocab_size]
        step_idx: int = 0,
    ) -> "tuple[torch.Tensor | None, str | None, dict]":
        """
        Returns (masked_logits, error_message, diagnostics).

        masked_logits:
            torch.Tensor [vocab_size] with -inf for grammar-invalid tokens, or
            None if Syncode is unavailable / the processor raised an exception.
        error_message:
            None on success; a string describing what went wrong otherwise.
        diagnostics:
            {
                "grammar_masked_count":   int   # tokens newly set to -inf by Syncode
                "logits_changed":         bool  # any element differs from raw?
                "whitespace_tokens_masked": bool  # any whitespace-only token masked?
                "whitespace_tokens_accepted": int # whitespace IDs still valid after mask
                "valid_token_count":      int
            }

        On failure (unavailable processor or exception), returns
        ``(None, error_message, diag)``.  The caller must STOP generation —
        never continue from raw unconstrained logits.
        """
        diag: dict = {
            "grammar_masked_count": 0,
            "logits_changed": False,
            "whitespace_tokens_masked": False,
            "whitespace_tokens_accepted": 0,
            "valid_token_count": int(logits.size(0)),
            "syncode_processed": False,
        }
        if not self._available or self._processor is None:
            return None, "SynCode processor unavailable", diag

        # Track total mask() calls for API-accessible diagnostics.
        self._mask_call_count = getattr(self, "_mask_call_count", 0) + 1

        # ── Forensic patch health-check (step 0 only) ──────────────────────
        if step_idx == 0:
            ge = getattr(self._processor, "grammar_engine", None)
            if ge is not None:
                ms = ge.__dict__.get("mask_scores")
                ms_name = getattr(ms, "__name__", type(ms).__name__) if ms else "NOT_IN_DICT"
                flog_len = len(getattr(self, "_forensic_log", []))
                print(
                    f"[DIAG step 0] ge.mask_scores in __dict__={ms is not None}"
                    f"  name={ms_name}"
                    f"  forensic_log_len={flog_len}"
                    f"  ge.start_from={ge.start_from}"
                    f"  ge.parse_failed={ge.parse_failed}",
                    flush=True,
                )

        try:
            # Clone BEFORE calling _processor: GrammarConstrainer.mask_scores
            # modifies the scores tensor IN PLACE and returns the same object.
            # Without this clone, `logits` == `out` (same tensor after the call),
            # so all before/after comparisons would be trivially zero.
            logits_snapshot = logits.clone()

            # Pass the exact current sequence as LongTensor; call positionally
            # to match SyncodeLogitsProcessor.__call__(input_ids, scores).
            ids = all_input_ids
            if not torch.is_tensor(ids):
                raise TypeError(f"all_input_ids must be a Tensor, got {type(ids)}")
            if ids.dim() == 1:
                ids = ids.unsqueeze(0)
            ids = ids.long()
            scores = logits.unsqueeze(0)  # [1, vocab_size] — may be modified in place

            out: torch.Tensor = self._processor(ids, scores)
            out = out.squeeze(0)  # [vocab_size]

            # ── Diagnostics ────────────────────────────────────────────────
            # Tokens that were finite in the raw logits but become -inf after masking.
            newly_masked: torch.Tensor = (
                (out == float("-inf")) & (logits_snapshot > float("-inf"))
            )
            n_newly_masked = int(newly_masked.sum())
            logits_changed = bool((out != logits_snapshot).any())

            # Whitespace coverage — are whitespace tokens masked or accepted?
            ws_ids_tensor = torch.tensor(
                list(self._whitespace_ids), dtype=torch.long
            ) if self._whitespace_ids else torch.tensor([], dtype=torch.long)

            ws_masked = False
            ws_accepted = 0
            if ws_ids_tensor.numel() > 0:
                valid_ws = ws_ids_tensor[ws_ids_tensor < out.size(0)]
                if valid_ws.numel() > 0:
                    ws_out = out[valid_ws]
                    ws_masked = bool((ws_out == float("-inf")).any())
                    ws_accepted = int((ws_out > float("-inf")).sum())

            valid_count = int((out > float("-inf")).sum())

            diag.update({
                "grammar_masked_count": n_newly_masked,
                "logits_changed": logits_changed,
                "whitespace_tokens_masked": ws_masked,
                "whitespace_tokens_accepted": ws_accepted,
                "valid_token_count": valid_count,
                "syncode_processed": True,
            })

            log.debug(
                "Syncode step %d: newly_masked=%d logits_changed=%s "
                "ws_masked=%s ws_accepted=%d valid=%d",
                step_idx + 1, n_newly_masked, logits_changed,
                ws_masked, ws_accepted, valid_count,
            )
            return out, None, diag

        except Exception as exc:
            msg = f"{type(exc).__name__}: {exc}"
            log.warning(
                "Syncode mask step failed (%s) — not continuing unconstrained",
                msg,
            )
            return None, msg, diag


# ---------------------------------------------------------------------------
# LLMService
# ---------------------------------------------------------------------------

class LLMService:
    """
    Singleton wrapper around Qwen/Qwen2.5-Coder-1.5B-Instruct.

    Thread-safe lazy loading: the model is downloaded and initialised on the
    first generate() call, then reused for every subsequent request.

    When settings.syncode_enabled is True the service also initialises a
    _SyncodeConstraint (Verilog grammar) that can be activated per-request via
    the use_syncode flag.
    """

    def __init__(self) -> None:
        self._model: "PreTrainedModel | None" = None
        self._tokenizer: "PreTrainedTokenizerBase | None" = None
        self._loaded: bool = False
        self._syncode: "_SyncodeConstraint | None" = None
        self._lock: threading.Lock = threading.Lock()

    # ------------------------------------------------------------------
    # Read-only accessors
    # ------------------------------------------------------------------

    @property
    def model(self) -> "PreTrainedModel | None":
        return self._model

    @property
    def tokenizer(self) -> "PreTrainedTokenizerBase | None":
        return self._tokenizer

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    # ------------------------------------------------------------------
    # load_model
    # ------------------------------------------------------------------

    def load_model(self) -> None:
        """
        Load AutoTokenizer and AutoModelForCausalLM for Qwen2.5-Coder-Instruct.

        Safe to call multiple times — a threading.Lock ensures only one
        thread performs the actual load; all others block until it finishes.

        Model is loaded in fp32 on CPU.  low_cpu_mem_usage=True streams
        weight shards so peak RAM stays near the final footprint (~3 GB).

        If settings.syncode_enabled is True the Syncode Verilog-grammar processor
        is also initialised here (once, cached globally).
        """
        if self._loaded:
            return

        with self._lock:
            if self._loaded:
                return

            from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: PLC0415

            log.info("Loading tokenizer: %s", settings.model_name)
            self._tokenizer = AutoTokenizer.from_pretrained(
                settings.model_name,
                use_fast=True,
            )

            # Qwen2.5 has no dedicated pad token; reuse EOS to satisfy any
            # batching helpers that require pad_token_id to be set.
            if self._tokenizer.pad_token_id is None:  # type: ignore[union-attr]
                raw_eos = self._tokenizer.eos_token_id  # type: ignore[union-attr]
                self._tokenizer.pad_token_id = (  # type: ignore[union-attr]
                    raw_eos if isinstance(raw_eos, int) else raw_eos[0]
                )

            log.info("Loading model: %s (fp32, CPU)", settings.model_name)
            self._model = AutoModelForCausalLM.from_pretrained(
                settings.model_name,
                torch_dtype=torch.float32,
                low_cpu_mem_usage=True,
            )
            self._model.eval()

            n_params = sum(p.numel() for p in self._model.parameters())
            log.info("Model ready — %.0fM parameters", n_params / 1e6)

            # Lark compile diagnostics (SynCode mask store probed below when enabled).
            diag = run_grammar_diagnostics(build_mask_store=False)

            if settings.syncode_enabled:
                self._syncode = _SyncodeConstraint(
                    self._tokenizer, grammar=_VERILOG_GRAMMAR_PATH
                )
                if self._syncode.available:
                    diag.syncode_grammar_ok = True
                    diag.syncode_mask_store_ok = True
                    print("[generate] SynCode mask store loaded", flush=True)
                elif self._syncode.init_error:
                    print(
                        f"[generate] SynCode unavailable: {self._syncode.init_error}",
                        flush=True,
                    )
                    diag.syncode_init_error = (
                        self._syncode.init_traceback or self._syncode.init_error
                    )
                    diag.syncode_mask_store_error = self._syncode.init_error
                    if "Grammar" not in self._syncode.init_error:
                        diag.syncode_grammar_ok = True
            else:
                log.info(
                    "Syncode disabled (SYNCODE_ENABLED=false). "
                    "Set to true and restart to enable Verilog grammar masking."
                )

            log_grammar_diagnostics(diag)

            self._loaded = True

    # ------------------------------------------------------------------
    # generate_step
    # ------------------------------------------------------------------

    def generate_step(
        self,
        logits: torch.Tensor,           # [vocab_size] raw logits for the current position
        step_idx: int,                  # 0-based; stored as step_idx+1 in the log
        context_ids: list[int],         # generated IDs so far → decoded to context string
        top_k: int,
        temperature: float,
        masked_logits: "torch.Tensor | None" = None,  # [vocab_size] or None for raw mode
        parser_error_msg: "str | None" = None,        # set when grammar parser threw
        fallback_used: bool = False,                  # True when raw was used as fallback
        # Sampling parameters
        do_sample: bool = True,
        top_p: float = 0.95,
        repetition_penalty: float = 1.1,
        past_generated_ids: "list[int] | None" = None,
        # Whitespace stall metadata (tracked externally, attached here for the step log)
        consecutive_whitespace: int = 0,
        whitespace_stall_detected: bool = False,
        whitespace_stall_step: "int | None" = None,
        # Pipeline integrity diagnostics (passed from generation loop)
        syncode_grammar_changed: bool = False,   # Syncode's pure grammar mask changed logits
        syncode_diag: "dict | None" = None,      # diagnostics from _SyncodeConstraint.mask()
    ) -> tuple[DecodingStep, int]:
        """
        Compute per-step decoding data from raw (and optionally Syncode-masked) logits.

        Raw mode  (masked_logits is None):
            probs = softmax(penalized_logits / T)
            selected = nucleus_sample(probs, top_p)  or argmax when do_sample=False
            top_tokens filled; Syncode fields empty.

        Syncode mode  (masked_logits provided):
            probs_raw    = softmax(logits / T)                → entropy_before, top_tokens_before_syncode
            probs_masked = softmax(masked_logits / T)         → entropy_after, valid_tokens_after_syncode
            selected     = nucleus_sample(penalized_masked_probs, top_p)
            masked_tokens = top-k IDs that were -inf in masked_logits

        Repetition penalty is applied to the logits used for *selection* only;
        visualisation distributions (entropy, top_tokens) use the raw/masked
        logits without penalty so the charts reflect the model's true output.

        Pipeline assertions (active at all log levels):
          • When do_sample=False AND Syncode masked 0 tokens AND logits are
            identical: selected_id must equal raw_argmax_id.
          • When fallback_used=True: selection_source must start with "fallback".

        generate_step is intentionally decoupled from the generation loop so
        Syncode can inject masked_logits without touching any probability maths.
        """
        _past = past_generated_ids or []
        _diag = syncode_diag or {}

        # ── RAW distribution (always computed — used for visualisation) ─────
        scaled_raw: torch.Tensor = logits / temperature
        probs_raw: torch.Tensor = F.softmax(scaled_raw, dim=-1)

        entropy_before: float = float(
            -(probs_raw * torch.log(probs_raw.clamp(min=1e-12))).sum()
        )

        k = min(top_k, probs_raw.size(0))
        topk_raw_probs, topk_raw_ids = torch.topk(probs_raw, k=k)
        _topk_raw_id_list: list[int] = topk_raw_ids.tolist()

        # Always compute raw greedy argmax for diagnostic comparison.
        raw_argmax_id: int = int(torch.argmax(probs_raw))

        # ── Resolve masked distribution ─────────────────────────────────────
        use_syncode = masked_logits is not None

        if use_syncode and masked_logits is not None:
            # All-masked is a hard constraint failure — never select from raw.
            if bool((masked_logits == float("-inf")).all()):
                raise GenerationFailedError(
                    f"SynCode constraint error at step {step_idx + 1}: "
                    "all tokens masked; refusing unconstrained raw selection.",
                    reasons=[
                        f"syncode_all_tokens_masked_at_step_{step_idx + 1}",
                    ],
                    early_termination=(
                        f"syncode_constraint_error_at_step_{step_idx + 1}"
                    ),
                    step_count=step_idx,
                )

        if use_syncode and masked_logits is not None:
            # ── Constrained distribution ────────────────────────────────────
            # probs_masked is derived EXCLUSIVELY from masked_logits (Syncode
            # output), NOT from the raw logits.  Any divergence between
            # probs_masked and probs_raw proves the grammar mask is active.
            scaled_masked = masked_logits / temperature
            probs_masked: torch.Tensor = F.softmax(scaled_masked, dim=-1)

            entropy_after: float | None = float(
                -(probs_masked * torch.log(probs_masked.clamp(min=1e-12))).sum()
            )

            # Constrained greedy argmax — always computed even when sampling,
            # so we can assert the greedy invariant and report the rank.
            # NOTE: argmax(masked_logits) == argmax(probs_masked) because
            # softmax is a strictly monotone transform.
            constrained_argmax_id: int = int(torch.argmax(masked_logits))

            # Constrained top-k — extracted from probs_masked, NOT probs_raw.
            # This is what valid_tokens_after_syncode MUST be built from.
            topk_masked_probs, topk_masked_ids = torch.topk(probs_masked, k=k)
            _topk_con_id_list: list[int] = topk_masked_ids.tolist()

            # ── Selection ───────────────────────────────────────────────────
            if do_sample:
                pen_masked = _apply_repetition_penalty(masked_logits, _past, repetition_penalty)
                sel_probs_masked = F.softmax(pen_masked / temperature, dim=-1)
                selected_id: int = _nucleus_sample(sel_probs_masked, top_p)
                _sel_source = "fallback_sampled" if fallback_used else "constrained_sampled"
            else:
                # GREEDY PATH: selected_id MUST equal constrained_argmax_id.
                selected_id = constrained_argmax_id
                _sel_source = "fallback_greedy" if fallback_used else "constrained_greedy"

            # ── Hard greedy assertion ────────────────────────────────────────
            # assert selected_token_id == constrained_logits.argmax().item()
            # This fires regardless of whether Syncode changed anything — it
            # verifies the fundamental invariant that greedy always picks the
            # constrained argmax, not the raw argmax.
            if not do_sample:
                _expected_greedy = int(torch.argmax(masked_logits))
                if selected_id != _expected_greedy:
                    _amsg = (
                        f"[GREEDY ASSERTION FAILED] step={step_idx+1}: "
                        f"selected_id={selected_id} ({self._tokenizer.decode([selected_id], skip_special_tokens=False)!r}) "  # type: ignore[union-attr]
                        f"!= constrained_logits.argmax()={_expected_greedy} "
                        f"({self._tokenizer.decode([_expected_greedy], skip_special_tokens=False)!r}) — "  # type: ignore[union-attr]
                        f"greedy selection must always equal constrained_logits.argmax()!"
                    )
                    log.error(_amsg)
                    print(_amsg, flush=True)

            # ── Boolean mask ────────────────────────────────────────────────
            # grammar-invalid = finite in raw logits but -inf after masking.
            # Captures grammar masking AND the special-token suppression layer.
            masked_flag: torch.Tensor = (
                (masked_logits == float("-inf")) & (logits > float("-inf"))
            )
            num_masked_total: int = int(masked_flag.sum())
            vocab_sz: int = int(logits.size(0))
            valid_cnt: int = vocab_sz - num_masked_total

            # ── Pipeline diagnostics ────────────────────────────────────────
            _logits_diverge = bool((masked_logits != logits).any())
            # constraint_active / syncode_active: True iff SynCode successfully
            # processed this decision (returned constrained logits).  Do NOT
            # require logits_changed — a no-op mask is still an enforced step.
            _syncode_processed = bool(_diag.get("syncode_processed", True))
            _syncode_active = bool(_syncode_processed) and not fallback_used
            _grammar_masked = _diag.get("grammar_masked_count", num_masked_total)
            _ws_masked = _diag.get("whitespace_tokens_masked", False)

            # ── Rank of selected token in each distribution ─────────────────
            _sel_rank_raw = (
                _topk_raw_id_list.index(selected_id)
                if selected_id in _topk_raw_id_list else -1
            )
            _sel_rank_con = (
                _topk_con_id_list.index(selected_id)
                if selected_id in _topk_con_id_list else -1
            )

            # ── Assertion: when fallback_used, source tag must say "fallback"
            if fallback_used and not _sel_source.startswith("fallback"):
                log.error(
                    "ASSERTION FAILED step %d: fallback_used=True but "
                    "selection_source=%r — source tagging inconsistency!",
                    step_idx + 1, _sel_source,
                )

            # ── Verify AFTER-SYNCODE visualization uses constrained logits ──
            # valid_tokens_after_syncode is built from topk_masked_probs which
            # comes from probs_masked = softmax(masked_logits / T).
            # Log top-1 of each distribution so the console makes it explicit.
            _raw_top1_prob = float(topk_raw_probs[0]) if k > 0 else 0.0
            _con_top1_prob = float(topk_masked_probs[0]) if k > 0 else 0.0
            _raw_top1_id = int(topk_raw_ids[0]) if k > 0 else -1
            _con_top1_id = int(topk_masked_ids[0]) if k > 0 else -1

            # Verify: if logits diverge, the constrained top-1 prob at the
            # constrained argmax must differ from raw prob at the same ID.
            _con_prob_at_argmax = float(probs_masked[constrained_argmax_id])
            _raw_prob_at_con_argmax = float(probs_raw[constrained_argmax_id])
            _after_syncode_uses_constrained = (
                not _logits_diverge  # identical logits → both probs the same
                or abs(_con_prob_at_argmax - _raw_prob_at_con_argmax) > 1e-9
            )
            if _logits_diverge and not _after_syncode_uses_constrained:
                _vmsg = (
                    f"[VISUALIZATION BUG] step={step_idx+1}: logits diverged but "
                    f"probs_masked[constrained_argmax]={_con_prob_at_argmax:.6f} "
                    f"== probs_raw[constrained_argmax]={_raw_prob_at_con_argmax:.6f} "
                    f"— valid_tokens_after_syncode may be using wrong logits!"
                )
                log.error(_vmsg)
                print(_vmsg, flush=True)

            # Probability mass removed = Σ raw_prob of all masked tokens.
            prob_mass_removed: float = float(probs_raw[masked_flag].sum())

            # top_tokens_before_syncode — raw top-k (BEFORE masking) with
            # is_masked annotation to show which tokens were masked.
            # Source: probs_raw  (topk_raw_probs, topk_raw_ids)
            top_tokens_before_syncode: list[TokenCandidate] = [
                TokenCandidate(
                    token_id=int(topk_raw_ids[i]),
                    token_str=self._tokenizer.decode(  # type: ignore[union-attr]
                        [int(topk_raw_ids[i])],
                        skip_special_tokens=False,
                        clean_up_tokenization_spaces=False,
                    ),
                    probability=float(topk_raw_probs[i]),
                    is_masked=bool(masked_flag[int(topk_raw_ids[i])]),
                    is_selected=int(topk_raw_ids[i]) == selected_id,
                )
                for i in range(k)
            ]

            # masked_tokens — top-k tokens rejected by grammar, with raw probs.
            masked_in_topk: list[MaskedTokenEntry] = [
                MaskedTokenEntry(
                    token_id=int(topk_raw_ids[i]),
                    token=self._tokenizer.decode(  # type: ignore[union-attr]
                        [int(topk_raw_ids[i])],
                        skip_special_tokens=False,
                        clean_up_tokenization_spaces=False,
                    ),
                    raw_prob=float(topk_raw_probs[i]),
                )
                for i in range(k)
                if masked_flag[int(topk_raw_ids[i])]
            ]

            # top_masked_tokens — top 50 masked across full vocab by pre-mask prob.
            top_masked_list: list[TopMaskedTokenEntry] = _compute_top_masked_tokens(
                probs_raw,
                masked_flag,
                self._tokenizer,  # type: ignore[arg-type]
            )

            # valid_tokens_after_syncode — top-k AFTER masking.
            # Source: probs_masked = softmax(masked_logits / T)
            # IMPORTANT: probabilities here are from the CONSTRAINED distribution,
            # NOT from probs_raw.  This is what the "AFTER SYNCODE" panel must use.
            valid_tokens_after_syncode: list[TokenCandidate] = [
                TokenCandidate(
                    token_id=int(topk_masked_ids[i]),
                    token_str=self._tokenizer.decode(  # type: ignore[union-attr]
                        [int(topk_masked_ids[i])],
                        skip_special_tokens=False,
                        clean_up_tokenization_spaces=False,
                    ),
                    probability=float(topk_masked_probs[i]),  # from probs_masked ✓
                    is_masked=False,   # survived masking by definition
                    is_selected=int(topk_masked_ids[i]) == selected_id,
                )
                for i in range(k)
                if float(topk_masked_probs[i]) > 1e-12
            ]

        else:
            # Raw mode — sample (or greedy) from the unmasked distribution.
            constrained_argmax_id = raw_argmax_id  # no constraint; same as raw
            _topk_con_id_list = _topk_raw_id_list  # same distribution
            if do_sample:
                pen_raw = _apply_repetition_penalty(logits, _past, repetition_penalty)
                sel_probs_raw = F.softmax(pen_raw / temperature, dim=-1)
                selected_id = _nucleus_sample(sel_probs_raw, top_p)
                _sel_source = "raw_sampled"
            else:
                selected_id = raw_argmax_id
                _sel_source = "raw_greedy"
            entropy_after = None
            top_tokens_before_syncode = []
            valid_tokens_after_syncode = []
            masked_in_topk = []
            top_masked_list = []
            num_masked_total = 0
            vocab_sz = int(logits.size(0))
            valid_cnt = vocab_sz
            prob_mass_removed = 0.0
            _logits_diverge = False
            _syncode_active = False
            _grammar_masked = 0
            _ws_masked = False
            _sel_rank_raw = (
                _topk_raw_id_list.index(selected_id)
                if selected_id in _topk_raw_id_list else -1
            )
            _sel_rank_con = _sel_rank_raw  # raw mode: same distribution
            _raw_top1_id = int(topk_raw_ids[0]) if k > 0 else -1
            _raw_top1_prob = float(topk_raw_probs[0]) if k > 0 else 0.0
            _con_top1_id = _raw_top1_id
            _con_top1_prob = _raw_top1_prob

        # ── Decode strings ──────────────────────────────────────────────────
        def _tok(tid: int) -> str:
            return self._tokenizer.decode(  # type: ignore[union-attr]
                [tid],
                skip_special_tokens=False,
                clean_up_tokenization_spaces=False,
            )

        selected_str: str = _tok(selected_id)
        raw_argmax_str: str = _tok(raw_argmax_id)
        constrained_argmax_str: str = _tok(constrained_argmax_id)

        context: str = self._tokenizer.decode(  # type: ignore[union-attr]
            context_ids,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=True,
        )

        # ── Side-by-side top-k verification print ───────────────────────────
        # Always printed to stdout so the console shows which logits are used
        # for each distribution without requiring DEBUG log level.
        _raw_top3_str = "  ".join(
            f"{_tok(int(topk_raw_ids[i]))!r}@{float(topk_raw_probs[i]):.4f}"
            for i in range(min(3, k))
        )
        if use_syncode:
            _con_top3_str = "  ".join(
                f"{_tok(int(topk_masked_ids[i]))!r}@{float(topk_masked_probs[i]):.4f}"  # type: ignore[possibly-undefined]
                for i in range(min(3, k))
            )
            _after_src = "probs_masked=softmax(masked_logits/T)"
        else:
            _con_top3_str = "(raw mode — no constraint)"
            _after_src = "probs_raw=softmax(logits/T)"

        print(
            f"[VERIFY step {step_idx+1:>3}]"
            f"  RAW  top3: {_raw_top3_str}"
            f"  |  CONSTRAINED top3 [{_after_src}]: {_con_top3_str}"
            f"  |  selected={selected_str!r}(id={selected_id}) src={_sel_source}"
            f"  rank_raw={_sel_rank_raw} rank_con={_sel_rank_con}"
            f"  |  grammar_masked={_grammar_masked}"
            f"  logits_diverge={_logits_diverge}"
            f"  raw_top1_prob={_raw_top1_prob:.4f}"
            f"  con_top1_prob={_con_top1_prob:.4f}"
            f"  con_top1{'==raw_top1' if _raw_top1_id == _con_top1_id else '!=raw_top1'}",
            flush=True,
        )

        # DEBUG-level structured log for log aggregators.
        log.debug(
            "Step %d | sel=%r (id=%d src=%s rank_raw=%d rank_con=%d) | "
            "raw_argmax=%r (id=%d) | constrained_argmax=%r (id=%d) | "
            "grammar_masked=%d logits_diverge=%s syncode_active=%s "
            "ws_masked=%s fallback=%s",
            step_idx + 1,
            selected_str, selected_id, _sel_source, _sel_rank_raw, _sel_rank_con,
            raw_argmax_str, raw_argmax_id,
            constrained_argmax_str, constrained_argmax_id,
            _grammar_masked, _logits_diverge,
            _syncode_active, _ws_masked, fallback_used,
        )

        # top_tokens — raw top-k always present (used by raw-mode charts)
        top_tokens: list[TopToken] = [
            TopToken(
                token=self._tokenizer.decode(  # type: ignore[union-attr]
                    [int(topk_raw_ids[i])],
                    skip_special_tokens=False,
                    clean_up_tokenization_spaces=False,
                ),
                probability=float(topk_raw_probs[i]),
                token_id=int(topk_raw_ids[i]),
            )
            for i in range(k)
        ]

        masked_pct: float = (
            round(num_masked_total / vocab_sz * 100, 2) if vocab_sz > 0 else 0.0
        )

        step = DecodingStep(
            step=step_idx + 1,
            context=context,
            top_tokens=top_tokens,
            selected_token=selected_str,
            selected_token_id=selected_id,
            entropy_before=round(entropy_before, 4),
            # Syncode fields — empty in raw mode, populated in Syncode mode
            top_tokens_before_syncode=top_tokens_before_syncode,
            masked_tokens=masked_in_topk,
            top_masked_tokens=top_masked_list,
            valid_tokens_after_syncode=valid_tokens_after_syncode,
            entropy_after=round(entropy_after, 4) if entropy_after is not None else None,
            num_masked=num_masked_total,
            # Masking statistics
            vocab_size=vocab_sz,
            valid_token_count=valid_cnt,
            masked_token_count=num_masked_total,
            masked_percentage=masked_pct,
            probability_mass_removed=round(prob_mass_removed, 6),
            # Parser recovery metadata
            parser_error=parser_error_msg is not None,
            parser_error_message=parser_error_msg or "",
            fallback_used=fallback_used,
            # Whitespace stall metadata
            consecutive_whitespace_count=consecutive_whitespace,
            whitespace_stall_detected=whitespace_stall_detected,
            whitespace_stall_step=whitespace_stall_step,
            # Pipeline integrity diagnostics
            syncode_active=_syncode_active,
            logits_diverge=_logits_diverge,
            raw_argmax_token_id=raw_argmax_id,
            raw_argmax_token=raw_argmax_str,
            constrained_argmax_token_id=constrained_argmax_id,
            constrained_argmax_token=constrained_argmax_str,
            selection_source=_sel_source,
            grammar_masked_count=_grammar_masked,
            whitespace_tokens_masked=_ws_masked,
            # Rank of selected token in each distribution
            selected_rank_raw=_sel_rank_raw,
            selected_rank_constrained=_sel_rank_con,
        )
        return step, selected_id

    # ------------------------------------------------------------------
    # generate  (async public API)
    # ------------------------------------------------------------------

    async def generate(
        self,
        prompt: str,
        max_new_tokens: int,
        top_k: int,
        temperature: float,
        use_syncode: bool = False,
        do_sample: bool = True,
        top_p: float = 0.95,
        repetition_penalty: float = 1.1,
    ) -> tuple[str, list[DecodingStep], str, bool]:
        """
        Async entry point for FastAPI route handlers.

        Returns (generated_text, steps, early_termination, eos_allowed_at_completion).

        Dispatches the blocking CPU work to the single-worker
        ThreadPoolExecutor so the asyncio event loop stays free.
        """
        import functools  # noqa: PLC0415
        loop = asyncio.get_running_loop()
        fn = functools.partial(
            self._run_generate_sync,
            prompt,
            max_new_tokens,
            top_k,
            temperature,
            use_syncode,
            do_sample,
            top_p,
            repetition_penalty,
        )
        return await loop.run_in_executor(_executor, fn)

    # ------------------------------------------------------------------
    # _run_generate_sync  (blocking, runs inside executor)
    # ------------------------------------------------------------------

    @torch.no_grad()
    def _run_generate_sync(
        self,
        prompt: str,
        max_new_tokens: int,
        top_k: int,
        temperature: float,
        use_syncode: bool = False,
        do_sample: bool = True,
        top_p: float = 0.95,
        repetition_penalty: float = 1.1,
    ) -> tuple[str, list[DecodingStep], str, bool]:
        """
        Token-by-token generation with nucleus sampling and whitespace stall
        protection.  Synchronous / blocking — always called through the
        ThreadPoolExecutor, never from async context.

        Returns
        -------
        (generated_text, steps, early_termination, eos_allowed_at_completion)

        Steps
        -----
        1. Lazy-load model (and Syncode if enabled) if needed.
        2. Apply Qwen chat template to the prompt.
        3. Tokenise → input_ids [1, prompt_len].
        4. Full forward pass over the prompt to prime the KV cache.
        5. Loop up to max_new_tokens:
             a. If use_syncode: get grammar mask from _SyncodeConstraint.
             b. Call generate_step() with sampling params and stall metadata.
             c. Break on EOS or whitespace stall (>_WHITESPACE_STALL_THRESHOLD
                consecutive whitespace-only tokens).
             d. Incremental forward pass with new token + cached KV state.
             e. Update all_input_ids (for Syncode context tracking).
        6. Decode and return generated text + step list.
        """
        if not self._loaded:
            print("[generate] loading model...", flush=True)
            self.load_model()
            print("[generate] model ready", flush=True)

        if use_syncode and settings.syncode_enabled:
            print("[generate] loading SynCode...", flush=True)

        # Determine if Syncode is actually usable for this request.
        effective_syncode = (
            use_syncode
            and self._syncode is not None
            and self._syncode.available
        )
        if use_syncode and not effective_syncode:
            init_err = ""
            init_tb = ""
            if self._syncode is not None:
                init_err = self._syncode.init_error
                init_tb = self._syncode.init_traceback
            diag = get_grammar_diagnostics()
            detail = init_tb or init_err or diag.syncode_init_error or diag.syncode_mask_store_error or diag.syncode_grammar_error or "SynCode mask store unavailable"
            log.error(
                "use_syncode=True but SynCode mask store is unavailable — "
                "fail-closed, not generating unconstrained output. %s",
                init_err or detail[:200],
            )
            raise SyncodeUnavailableError(
                "SynCode unavailable: updated grammar failed to compile for SynCode/mask store.",
                init_error=init_err or detail,
                init_traceback=init_tb or diag.syncode_init_error,
            )

        # Reset Syncode parse state so this generation starts from a clean
        # grammar state (the processor is reused across requests).
        if effective_syncode and self._syncode is not None:
            self._syncode.reset()
            # Reset forensic step counter and clear accumulated log for this run.
            if hasattr(self._syncode, "_forensic_step"):
                self._syncode._forensic_step[0] = 0
            if hasattr(self._syncode, "_forensic_log"):
                self._syncode._forensic_log.clear()
            if hasattr(self._syncode, "_parser_evidence_log"):
                self._syncode._parser_evidence_log.clear()
            # Prevent mask-call counter leaking across generations.
            if hasattr(self._syncode, "_mask_call_count"):
                self._syncode._mask_call_count = 0
            log.debug("Syncode parse state reset for new generation")

        # ── Format prompt ───────────────────────────────────────────────────
        # In Syncode/Verilog-grammar mode we MUST NOT use the chat template
        # (it adds <|im_start|>/<|im_end|> control tokens that the Verilog
        # grammar cannot parse).
        #
        # Prompt format — "/* prompt */\n":
        #   The Verilog grammar's start rule is `description*` which expects
        #   a `module` keyword as the first real token.  With parse_output_only=True
        #   Syncode only parses the GENERATED tokens; the prompt itself is never
        #   fed to the grammar parser.  Using a block comment wraps the prompt
        #   cleanly: the model sees a completed documentation comment and
        #   naturally generates a full `module ... endmodule` definition.
        #   MULTILINE_COMMENT is `%ignore`d in the Verilog grammar, so if any
        #   comment tokens leak into the generated stream they are silently skipped.
        #
        # Guard against embedded */ in the prompt that would close the comment early.
        if effective_syncode:
            _safe_prompt = prompt.replace("*/", "* /")
            formatted_prompt: str = f"/* {_safe_prompt} */\n"
        elif callable(getattr(self._tokenizer, "apply_chat_template", None)):
            formatted_prompt = self._tokenizer.apply_chat_template(  # type: ignore[union-attr]
                [{"role": "user", "content": prompt}],
                tokenize=False,
                add_generation_prompt=True,
            )
        else:
            formatted_prompt = prompt

        encoded = self._tokenizer(  # type: ignore[misc]
            formatted_prompt,
            return_tensors="pt",
            add_special_tokens=True,
        )
        input_ids: torch.Tensor = encoded["input_ids"]  # [1, prompt_len]

        # Track the full sequence (prompt + generated) for Syncode context.
        # The model uses past_key_values so only needs the new token each step,
        # but Syncode's LogitsProcessor needs the full sequence to parse state.
        all_input_ids: torch.Tensor = input_ids

        # Build EOS set — Qwen2.5 returns a list [151645, 151643].
        raw_eos = self._tokenizer.eos_token_id  # type: ignore[union-attr]
        eos_ids: set[int] = (
            set(raw_eos) if isinstance(raw_eos, list) else {raw_eos}
        )

        steps: list[DecodingStep] = []
        generated_ids: list[int] = []

        # ── Prime KV cache ─────────────────────────────────────────────────
        outputs = self._model(input_ids=input_ids, use_cache=True)  # type: ignore[misc]
        past_key_values = outputs.past_key_values
        last_logits: torch.Tensor = outputs.logits[0, -1, :]  # [vocab_size]

        # Precompute special/chat token IDs that must never appear in
        # grammar-constrained output.  EOS is intentionally EXCLUDED: it is
        # mapped to the Lark $END acceptance condition separately so a
        # complete module can terminate cleanly via tokenizer.eos_token.
        syncode_suppress_ids: set[int] = set()
        if effective_syncode:
            vocab_dim = int(last_logits.size(0))
            syncode_suppress_ids = {
                sid for sid in self._tokenizer.all_special_ids  # type: ignore[union-attr]
                if sid < vocab_dim and sid not in eos_ids
            }
            log.debug(
                "Syncode special-token suppression covers %d IDs "
                "(vocab_dim=%d, eos_ids=%s excluded)",
                len(syncode_suppress_ids), vocab_dim, sorted(eos_ids),
            )

        log.debug(
            "Starting generation: max_new_tokens=%d top_k=%d T=%.2f "
            "do_sample=%s top_p=%.2f rep_penalty=%.2f syncode=%s",
            max_new_tokens, top_k, temperature,
            do_sample, top_p, repetition_penalty, effective_syncode,
        )

        # Whitespace stall state — reset per generation.
        consecutive_whitespace_count: int = 0
        whitespace_stall_step_num: int | None = None

        # Per-step trace records — written to _last_trace at the end.
        import uuid as _uuid  # noqa: PLC0415
        _generation_id = str(_uuid.uuid4())[:8]
        _trace_steps: list[dict] = []

        early_termination: str | None = None
        # True when the finalization step (prefix already parse-valid) offered
        # model EOS because Lark $END was accepted.  Surfaced in the evidence panel.
        eos_allowed_at_completion: bool = False

        log.info(
            "[GEN loop entry] gen_id=%s mode=%s max_new_tokens=%d top_k=%d "
            "T=%.2f do_sample=%s top_p=%.2f rep_pen=%.2f syncode=%s prompt_len=%d",
            _generation_id,
            "syncode" if effective_syncode else "raw",
            max_new_tokens,
            top_k,
            temperature,
            do_sample,
            top_p,
            repetition_penalty,
            effective_syncode,
            len(prompt),
        )
        print(
            f"[TRACE start] gen_id={_generation_id} "
            f"mode={'syncode' if effective_syncode else 'raw'} "
            f"max_new_tokens={max_new_tokens} top_k={top_k} T={temperature} "
            f"do_sample={do_sample} top_p={top_p} rep_pen={repetition_penalty}",
            flush=True,
        )
        # SynCode research mode: allow an extra completion budget past the
        # normal display limit so incomplete-but-constrained prefixes can
        # finish into a grammar-valid module (and yield a parser tree).
        # Raw mode keeps the caller-requested max_new_tokens only.
        normal_max = max_new_tokens
        if effective_syncode:
            absolute_max = min(
                normal_max + settings.completion_extra_tokens,
                settings.absolute_max_tokens,
            )
        else:
            absolute_max = normal_max

        print(
            f"[generation] backend_mode: {'syncode' if effective_syncode else 'raw'}",
            flush=True,
        )
        print(f"[generation] grammar: verilog", flush=True)
        print(f"[generation] max_tokens: {normal_max}", flush=True)
        print(f"[generation] absolute_max_tokens: {absolute_max}", flush=True)
        print(
            f"[generation] raw fallback allowed: {settings.allow_syncode_fallback}",
            flush=True,
        )

        print("[generate] starting generation...", flush=True)
        try:
            for step_idx in range(absolute_max):
                # ── Optional Syncode mask ───────────────────────────────────
                masked_logits: torch.Tensor | None = None
                step_parser_error: str | None = None
                step_fallback_used: bool = False

                # Diagnostics from _SyncodeConstraint.mask() — populated below.
                step_syncode_grammar_changed: bool = False
                step_syncode_diag: dict = {}

                # Prefix parse check BEFORE selecting the next token.
                # When the already-generated text is grammar-valid, Lark would
                # accept $END — so we unmask model EOS and treat this as a
                # finalization step (reject non-EOS continuations).
                prefix_text = ""
                prefix_parse_valid = False
                step_eos_allowed = False
                if effective_syncode and generated_ids:
                    prefix_text = self._tokenizer.decode(  # type: ignore[union-attr]
                        generated_ids,
                        skip_special_tokens=True,
                        clean_up_tokenization_spaces=True,
                    )
                    prefix_parse_valid = _generated_text_is_parse_valid(prefix_text)
                    step_eos_allowed = prefix_parse_valid
                    if prefix_parse_valid:
                        eos_allowed_at_completion = True

                if effective_syncode and self._syncode is not None:
                    # GrammarConstrainer.mask_scores modifies scores IN PLACE via the
                    # logits.unsqueeze(0) view, so after mask() returns:
                    #   • last_logits has been corrupted with -inf on masked tokens
                    #   • masked_logits is a view of last_logits (same storage)
                    # Strategy:
                    #   1. Snapshot raw logits before the call.
                    #   2. After mask(), clone masked_logits before restoring last_logits.
                    #   3. Pass raw snapshot as `logits` and the clone as `masked_logits`
                    #      to generate_step so the raw vs. masked comparison is valid.
                    raw_logits_snapshot = last_logits.clone()
                    masked_logits, step_parser_error, step_syncode_diag = (
                        self._syncode.mask(all_input_ids, last_logits, step_idx)
                    )
                    # Clone masked_logits now (before restoring last_logits) so it
                    # keeps the -inf values independent of last_logits.
                    if masked_logits is not None:
                        masked_logits = masked_logits.clone()
                    # Restore last_logits to the original raw values so the next
                    # iteration and the raw-side visualization are unaffected.
                    last_logits.copy_(raw_logits_snapshot)

                    # True when Syncode's grammar mask (before special-token
                    # suppression) actually changed at least one logit value.
                    step_syncode_grammar_changed = step_syncode_diag.get(
                        "logits_changed", False
                    )

                    # SynCode failed to return constrained logits — stop.
                    # Never continue unconstrained from raw model logits.
                    if masked_logits is None:
                        step_fallback_used = True
                        step_syncode_grammar_changed = False
                        err = step_parser_error or "SynCode returned no constrained logits"
                        if prefix_parse_valid:
                            early_termination = "parse_complete"
                            eos_allowed_at_completion = True
                            print(
                                f"[generation] parse_complete at step "
                                f"{step_idx + 1} — prefix valid; SynCode mask "
                                f"failed ({err!r}); not continuing raw",
                                flush=True,
                            )
                        else:
                            early_termination = (
                                f"syncode_constraint_error_at_step_{step_idx + 1}"
                            )
                            print(
                                f"[generation] SynCode constraint error at step "
                                f"{step_idx + 1}: {err!r}",
                                flush=True,
                            )
                            print(
                                "[generation] stopping — refusing unconstrained "
                                "raw-logit continuation",
                                flush=True,
                            )
                        log.warning(
                            "[GEN fail-fast] gen_id=%s stopping at step %d: "
                            "reason=%s Error: %s",
                            _generation_id,
                            step_idx + 1,
                            early_termination,
                            err,
                        )
                        break

                    # Suppress chat/special tokens (EOS excluded from this set).
                    for sid in syncode_suppress_ids:
                        masked_logits[sid] = float("-inf")

                    # Map Lark $END → model EOS (tokenizer.eos_token_id).
                    # Do NOT add <|endoftext|> to the Verilog grammar; this is
                    # a mask-layer mapping only.
                    for eid in eos_ids:
                        if eid >= masked_logits.size(0):
                            continue
                        if step_eos_allowed:
                            # Restore raw logit so EOS remains selectable.
                            masked_logits[eid] = raw_logits_snapshot[eid]
                        else:
                            masked_logits[eid] = float("-inf")

                    print(
                        f"[generation] step {step_idx + 1} eos_allowed="
                        f"{step_eos_allowed} (prefix_parse_valid={prefix_parse_valid})",
                        flush=True,
                    )
                    step_syncode_diag["eos_allowed"] = step_eos_allowed
                    step_syncode_diag["prefix_parse_valid"] = prefix_parse_valid
                    step_syncode_diag["syncode_processed"] = True

                    # SynCode parse_failed ⇒ unconstrained skip inside the library.
                    # Refuse to continue — never append tokens from that state.
                    ge = self._syncode._processor.grammar_engine
                    if bool(getattr(ge, "parse_failed", False)):
                        early_termination = (
                            f"syncode_constraint_error_at_step_{step_idx + 1}"
                        )
                        step_parser_error = (
                            "SynCode parse_failed — refusing unconstrained continuation"
                        )
                        print(
                            f"[generation] {step_parser_error} at step "
                            f"{step_idx + 1}; stopping",
                            flush=True,
                        )
                        log.warning(
                            "[GEN fail-fast] gen_id=%s parse_failed at step %d",
                            _generation_id,
                            step_idx + 1,
                        )
                        break

                    # If every token is masked, stop — never select from raw.
                    if (masked_logits == float("-inf")).all():
                        step_fallback_used = True
                        step_syncode_grammar_changed = False
                        if prefix_parse_valid:
                            early_termination = "parse_complete"
                            eos_allowed_at_completion = True
                            print(
                                f"[generation] parse_complete at step "
                                f"{step_idx + 1} — prefix valid; all other "
                                f"tokens masked",
                                flush=True,
                            )
                        else:
                            early_termination = (
                                f"syncode_constraint_error_at_step_{step_idx + 1}"
                            )
                            print(
                                f"[generation] all tokens masked at step "
                                f"{step_idx + 1}; stopping "
                                f"(refusing unconstrained raw selection)",
                                flush=True,
                            )
                        log.warning(
                            "[GEN fail-fast] gen_id=%s all tokens masked "
                            "at step %d — reason=%s",
                            _generation_id,
                            step_idx + 1,
                            early_termination,
                        )
                        break

                    # Lazy selected-token parser guard (not exhaustive vocab scan).
                    # Validates only successive SynCode argmax candidates.
                    oracle = PrefixOracle(
                        processor=self._syncode._processor,
                        tokenizer=self._tokenizer,
                    )
                    prompt_len = int(all_input_ids.size(1) - len(generated_ids))
                    guard = select_valid_token(
                        masked_logits,
                        oracle=oracle,
                        generated_prefix=prefix_text,
                        prompt_len=prompt_len,
                        max_rejects=32,
                        eos_ids=eos_ids,
                    )
                    masked_logits = guard.logits
                    step_syncode_diag["parser_guard_validations"] = guard.validations
                    step_syncode_diag["parser_guard_rejected"] = list(guard.rejected_ids)
                    if guard.error or guard.selected_id is None:
                        early_termination = "constraint_no_valid_token"
                        step_parser_error = (
                            f"parser guard: no valid SynCode token after "
                            f"{len(guard.rejected_ids)} rejects "
                            f"({guard.validations} validations)"
                        )
                        print(
                            f"[generation] {early_termination} at step "
                            f"{step_idx + 1}: {step_parser_error}",
                            flush=True,
                        )
                        log.warning(
                            "[GEN fail-fast] gen_id=%s %s at step %d",
                            _generation_id,
                            early_termination,
                            step_idx + 1,
                        )
                        break
                    step_syncode_diag["parser_guard_selected_id"] = int(guard.selected_id)

                elif effective_syncode:
                    # SynCode mode requested but processor missing mid-loop.
                    early_termination = (
                        f"syncode_constraint_error_at_step_{step_idx + 1}"
                    )
                    step_parser_error = "SynCode processor unavailable during generation"
                    print(
                        f"[generation] {step_parser_error} at step "
                        f"{step_idx + 1}; stopping",
                        flush=True,
                    )
                    break

                # Determine if this step is already in a stall (stall fired
                # on a previous step; we annotate this step too for clarity).
                step_stall_detected = (
                    whitespace_stall_step_num is not None
                )

                guard_selected_id: int | None = None
                if effective_syncode and self._syncode is not None:
                    _gid = step_syncode_diag.get("parser_guard_selected_id")
                    if isinstance(_gid, int):
                        guard_selected_id = _gid

                step, selected_id = self.generate_step(
                    logits=raw_logits_snapshot if (effective_syncode and self._syncode is not None) else last_logits,
                    step_idx=step_idx,
                    context_ids=generated_ids,
                    top_k=top_k,
                    temperature=temperature,
                    masked_logits=masked_logits,
                    parser_error_msg=step_parser_error,
                    fallback_used=step_fallback_used,
                    do_sample=do_sample,
                    top_p=top_p,
                    repetition_penalty=repetition_penalty,
                    past_generated_ids=generated_ids,
                    consecutive_whitespace=consecutive_whitespace_count,
                    whitespace_stall_detected=step_stall_detected,
                    whitespace_stall_step=whitespace_stall_step_num,
                    syncode_grammar_changed=step_syncode_grammar_changed,
                    syncode_diag=step_syncode_diag,
                )
                # Always commit the parser-guard validated token, never an
                # unvalidated sample from remaining SynCode-finite logits.
                if guard_selected_id is not None:
                    selected_id = int(guard_selected_id)
                    step.selected_token_id = selected_id
                    step.selected_token = self._tokenizer.decode(  # type: ignore[union-attr]
                        [selected_id],
                        skip_special_tokens=False,
                        clean_up_tokenization_spaces=False,
                    )
                    step.selection_source = "parser_guard_argmax"
                steps.append(step)

                # For a finalization step (prefix already parse-valid), only
                # commit EOS into generated_ids.  Non-EOS continuations are
                # kept in the step trace for diagnostics but not emitted.
                if not (effective_syncode and prefix_parse_valid):
                    generated_ids.append(selected_id)
                elif selected_id in eos_ids:
                    generated_ids.append(selected_id)

                if step_idx == 0:
                    log.info(
                        "[GEN first token] gen_id=%s token=%r id=%d source=%s",
                        _generation_id,
                        step.selected_token,
                        selected_id,
                        step.selection_source,
                    )

                # ── Build per-step trace record ─────────────────────────────
                trace_step: dict = {  # noqa: RUF012
                    "step": step_idx + 1,
                    "selected_token": step.selected_token,
                    "selected_token_id": selected_id,
                    "selection_source": step.selection_source,
                    "raw_argmax_token": step.raw_argmax_token,
                    "raw_argmax_token_id": step.raw_argmax_token_id,
                    "constrained_argmax_token": step.constrained_argmax_token,
                    "constrained_argmax_token_id": step.constrained_argmax_token_id,
                    "syncode_active": step.syncode_active,
                    "logits_diverge": step.logits_diverge,
                    "grammar_masked_count": step.grammar_masked_count,
                    "num_masked_total": step.num_masked,
                    "masked_percentage": step.masked_percentage,
                    "whitespace_tokens_masked": step.whitespace_tokens_masked,
                    "whitespace_tokens_accepted": step_syncode_diag.get(
                        "whitespace_tokens_accepted", 0
                    ),
                    "entropy_before": step.entropy_before,
                    "entropy_after": step.entropy_after,
                    "fallback_used": step.fallback_used,
                    "parser_error": step.parser_error,
                    "parser_error_message": step.parser_error_message,
                    "consecutive_whitespace_count": step.consecutive_whitespace_count,
                    # Rank of selected token in each distribution — key invariants:
                    #   greedy+constrained → rank_constrained must be 0
                    #   greedy+raw         → rank_raw must be 0
                    "selected_rank_raw": step.selected_rank_raw,
                    "selected_rank_constrained": step.selected_rank_constrained,
                    # Top-3 from each distribution (source labelled)
                    "raw_top3": [
                        {"token": t.token, "prob": round(t.probability, 6), "id": t.token_id}
                        for t in step.top_tokens[:3]
                    ],
                    # valid_tokens_after_syncode uses probs_masked (constrained);
                    # top_tokens_before_syncode uses probs_raw (raw) with masking flags
                    "constrained_top3_after_syncode": [
                        {
                            "token": t.token_str,
                            "prob": round(t.probability, 6),
                            "id": t.token_id,
                            "source": "probs_masked",
                        }
                        for t in step.valid_tokens_after_syncode[:3]
                    ],
                    "raw_top3_before_syncode": [
                        {
                            "token": t.token_str,
                            "prob": round(t.probability, 6),
                            "id": t.token_id,
                            "is_masked": t.is_masked,
                            "source": "probs_raw",
                        }
                        for t in step.top_tokens_before_syncode[:3]
                    ],
                }
                _trace_steps.append(trace_step)
                # INFO-level print for immediate console visibility even when
                # the server log level is set above DEBUG.
                print(
                    f"[TRACE step {step_idx+1:>3}] "
                    f"sel={step.selected_token!r:>12} (id={selected_id}) "
                    f"src={step.selection_source:<22} "
                    f"raw_top1={step.raw_argmax_token!r} "
                    f"con_top1={step.constrained_argmax_token!r} "
                    f"grammar_masked={step.grammar_masked_count:>5} "
                    f"logits_diverge={step.logits_diverge} "
                    f"ws_masked={step.whitespace_tokens_masked} "
                    f"fallback={step.fallback_used}",
                    flush=True,
                )
                print(
                    f"[generation] step {step_idx+1} selected token: "
                    f"{step.selected_token!r}  "
                    f"constraint active: {step.syncode_active}",
                    flush=True,
                )

                # Finalization step: prefix was already grammar-valid ($END ok).
                # Trace above includes this step so EOS unmasking is visible in
                # top-masked diagnostics.  Non-EOS tokens are not committed.
                if effective_syncode and prefix_parse_valid:
                    if selected_id in eos_ids:
                        early_termination = "eos_parse_complete"
                        print(
                            f"[generation] eos_parse_complete at step "
                            f"{step_idx + 1} — EOS selected with $END accepted",
                            flush=True,
                        )
                    else:
                        early_termination = "parse_complete"
                        print(
                            f"[generation] parse_complete at step {step_idx + 1} "
                            f"— rejected non-EOS continuation "
                            f"{step.selected_token!r} after valid module",
                            flush=True,
                        )
                    log.info(
                        "[GEN early termination] gen_id=%s reason=%s step=%d",
                        _generation_id,
                        early_termination,
                        step_idx + 1,
                    )
                    break

                # After appending a newly generated token: if the output is now
                # grammar-valid, the NEXT iteration becomes the finalization
                # step (EOS unmasked / non-EOS rejected).  Log crossing the
                # normal display limit while still incomplete.
                if effective_syncode:
                    partial_text = self._tokenizer.decode(  # type: ignore[union-attr]
                        generated_ids,
                        skip_special_tokens=True,
                        clean_up_tokenization_spaces=True,
                    )
                    if _generated_text_is_parse_valid(partial_text):
                        print(
                            f"[generation] prefix became parse-valid at step "
                            f"{step_idx + 1} — next step is finalization "
                            f"(EOS↔$END)",
                            flush=True,
                        )
                    elif step_idx + 1 == normal_max and absolute_max > normal_max:
                        print(
                            f"[generation] normal_max={normal_max} reached but "
                            f"output not yet parse-valid — continuing constrained "
                            f"generation up to absolute_max={absolute_max}",
                            flush=True,
                        )

                if selected_id in eos_ids:
                    # EOS while prefix was not yet parse-valid (should be rare
                    # because EOS is masked until $END is accepted).
                    early_termination = f"eos_at_step_{step_idx + 1}"
                    log.info(
                        "[GEN early termination] gen_id=%s reason=%s",
                        _generation_id,
                        early_termination,
                    )
                    print(f"[TRACE] EOS at step {step_idx + 1}", flush=True)
                    break

                # ── Whitespace stall detection ──────────────────────────────
                # Count consecutive tokens that contain only whitespace/newlines.
                # If the count exceeds the threshold we stop gracefully to avoid
                # infinite grammar-valid whitespace loops in constrained mode.
                if _is_whitespace_token(step.selected_token):
                    consecutive_whitespace_count += 1
                    if (
                        consecutive_whitespace_count > _WHITESPACE_STALL_THRESHOLD
                        and whitespace_stall_step_num is None
                    ):
                        whitespace_stall_step_num = step_idx + 1
                        early_termination = (
                            f"whitespace_stall_at_step_{whitespace_stall_step_num}"
                        )
                        log.warning(
                            "[GEN early termination] gen_id=%s reason=%s "
                            "(%d consecutive whitespace tokens)",
                            _generation_id,
                            early_termination,
                            consecutive_whitespace_count,
                        )
                        break
                else:
                    consecutive_whitespace_count = 0

                # ── Incremental forward pass ────────────────────────────────
                next_input = torch.tensor([[selected_id]])  # [1, 1]
                outputs = self._model(  # type: ignore[misc]
                    input_ids=next_input,
                    past_key_values=past_key_values,
                    use_cache=True,
                )
                past_key_values = outputs.past_key_values
                last_logits = outputs.logits[0, -1, :]

                # Extend full sequence for Syncode context tracking.
                all_input_ids = torch.cat([all_input_ids, next_input], dim=1)

        except Exception as exc:
            log.error(
                "[GEN loop exception] gen_id=%s step=%d/%d steps_so_far=%d: %s",
                _generation_id,
                step_idx + 1 if "step_idx" in locals() else 0,
                absolute_max,
                len(steps),
                exc,
                exc_info=True,
            )
            raise GenerationFailedError(
                f"Generation loop raised {type(exc).__name__}: {exc}",
                reasons=[f"exception: {type(exc).__name__}: {exc}"],
                generation_id=_generation_id,
                early_termination="exception_in_loop",
                step_count=len(steps),
                trace_step_count=len(_trace_steps),
            ) from exc

        if early_termination is None and len(steps) >= absolute_max:
            early_termination = "max_tokens_incomplete"

        # If we hit the absolute budget but the final text happens to parse,
        # upgrade the stop reason so the tree path is clearly successful.
        generated_text: str = self._tokenizer.decode(  # type: ignore[union-attr]
            generated_ids,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=True,
        )
        if (
            effective_syncode
            and early_termination == "max_tokens_incomplete"
            and _generated_text_is_parse_valid(generated_text)
        ):
            early_termination = "parse_complete"
            eos_allowed_at_completion = True

        # ── Attach SynCode parser evidence + legacy accept_sequences ────────
        # Structured syncode_parser_evidence is authoritative (Phase 4A.1).
        # Legacy accept_sequences is populated from it for backward compatibility.
        if effective_syncode and self._syncode is not None:
            forensic_entries = self._syncode.forensic_log
            # Build map: 0-based forensic step index → record (skip sentinels)
            forensic_map: dict[int, dict] = {}
            mask_call_count = 0
            for entry in forensic_entries:
                if entry.get("_sentinel"):
                    continue
                mask_call_count += 1
                fidx = entry.get("step")
                if isinstance(fidx, int) and fidx not in forensic_map:
                    forensic_map[fidx] = entry

            cardinality_warning: str | None = None
            if mask_call_count != len(steps):
                cardinality_warning = (
                    f"SynCode mask-call count ({mask_call_count}) diverges from "
                    f"generated step count ({len(steps)})"
                )
                print(
                    f"[SYNCODE EVIDENCE] WARNING: {cardinality_warning}",
                    flush=True,
                )

            for i, step_obj in enumerate(steps):
                fentry = forensic_map.get(i)
                if fentry:
                    raw_ev = fentry.get("syncode_parser_evidence")
                    if isinstance(raw_ev, dict):
                        try:
                            ev = SyncodeParserEvidence.model_validate(raw_ev)
                        except Exception as _ev_exc:
                            ev = unavailable_syncode_parser_evidence(
                                reason=(
                                    f"invalid syncode_parser_evidence payload: "
                                    f"{type(_ev_exc).__name__}"
                                ),
                                mask_call_index=i,
                            )
                    else:
                        ev = unavailable_syncode_parser_evidence(
                            reason="syncode_parser_evidence missing from forensic log",
                            mask_call_index=i,
                        )
                    if cardinality_warning:
                        ev = ev.model_copy(
                            update={
                                "warnings": list(ev.warnings) + [cardinality_warning]
                            }
                        )
                    step_obj.syncode_parser_evidence = ev
                    if ev.status == "recorded":
                        step_obj.accept_sequences = format_legacy_accept_sequences(ev)
                    else:
                        # Preserve any debug strings; do not invent sequences.
                        step_obj.accept_sequences = list(
                            fentry.get("accept_seqs", []) or []
                        )[:8]
                elif cardinality_warning:
                    step_obj.syncode_parser_evidence = (
                        unavailable_syncode_parser_evidence(
                            reason="no forensic mask entry for this step",
                            warnings=[cardinality_warning],
                            mask_call_index=i,
                        )
                    )
                step_obj.constraint_applied = step_obj.syncode_active

        # ── Write debug trace ────────────────────────────────────────────────
        n_steps = len(_trace_steps)
        n_syncode_active = sum(1 for s in _trace_steps if s["syncode_active"])
        n_fallback = sum(1 for s in _trace_steps if s["fallback_used"])
        n_logits_diverge = sum(1 for s in _trace_steps if s["logits_diverge"])
        n_grammar_masked_any = sum(
            1 for s in _trace_steps if s["grammar_masked_count"] > 0
        )
        n_ws_masked = sum(1 for s in _trace_steps if s["whitespace_tokens_masked"])
        n_ws_stall = sum(
            1 for s in _trace_steps if s["consecutive_whitespace_count"] > 0
        )

        summary = {
            "generation_id": _generation_id,
            "total_steps": n_steps,
            "syncode_active_steps": n_syncode_active,
            "fallback_steps": n_fallback,
            "logits_diverge_steps": n_logits_diverge,
            "grammar_masked_any_steps": n_grammar_masked_any,
            "whitespace_tokens_masked_steps": n_ws_masked,
            "whitespace_stall_steps": n_ws_stall,
            "whitespace_stall_step_num": whitespace_stall_step_num,
            "generated_text_preview": generated_text[:120],
        }

        print(
            f"[TRACE end] gen_id={_generation_id} steps={n_steps} "
            f"syncode_active={n_syncode_active}/{n_steps} "
            f"fallback={n_fallback}/{n_steps} "
            f"logits_diverge={n_logits_diverge}/{n_steps} "
            f"grammar_masked_any={n_grammar_masked_any}/{n_steps} "
            f"ws_masked={n_ws_masked}/{n_steps} "
            f"ws_stall={n_ws_stall}/{n_steps}",
            flush=True,
        )

        with _trace_lock:
            _last_trace.update({
                "generation_id": _generation_id,
                "prompt": prompt,
                "mode": "syncode" if effective_syncode else "raw",
                "effective_syncode": effective_syncode,
                "steps": _trace_steps,
                "summary": summary,
                "early_termination": early_termination,
            })

        log.info(
            "[GEN complete] gen_id=%s final_step_count=%d trace_steps=%d "
            "early_termination=%s generated_text_len=%d preview=%r",
            _generation_id,
            len(steps),
            len(_trace_steps),
            early_termination,
            len(generated_text),
            generated_text[:80],
        )

        validate_generation_result(
            generated_text,
            steps,
            _trace_steps,
            generation_id=_generation_id,
            early_termination=early_termination,
        )

        print(
            f"[parser-tree] parsing final output only (len={len(generated_text)})",
            flush=True,
        )
        print(
            f"[generation] stopped_reason={early_termination!r} "
            f"eos_allowed_at_completion={eos_allowed_at_completion}",
            flush=True,
        )
        return (
            generated_text,
            steps,
            early_termination or "",
            eos_allowed_at_completion,
        )


# Module-level singleton — import this in route handlers.
# Never instantiate LLMService() elsewhere.
llm_service = LLMService()
