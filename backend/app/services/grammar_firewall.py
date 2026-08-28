"""
Strict grammar firewall for SynViz constrained decoding.

Invariant: a token may remain finite only if appending its decoded text to the
exact current generated prefix leaves a complete or extendable path under
backend/grammar/verilog.lark.

Uses SynCode's incremental parser (basic lexer — same as MaskStore construction)
as the oracle.  Does not trust SynCode's overapproximate accept mask alone.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import torch

from app.services.syncode_eos import IM_END_ID, SYNCODE_EOS_ID, is_masked_logit

log = logging.getLogger("app.services.grammar_firewall")

RESERVED_KEYWORDS: frozenset[str] = frozenset({
    "module", "endmodule", "input", "output", "inout", "wire", "reg", "assign",
    "always", "begin", "end", "if", "else",
})


@dataclass
class FirewallStats:
    raw_finite: int = 0
    after_syncode: int = 0
    after_special: int = 0
    after_firewall: int = 0
    removed_by_firewall: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    elapsed_ms: float = 0.0
    syncode_overapprox_caught: int = 0


@dataclass
class FirewallResult:
    logits: torch.Tensor
    stats: FirewallStats
    parser_state_sig: str = ""
    remainder_state: str = ""
    prefix_hash: str = ""
    prefix_len: int = 0
    end_immediate: bool = False
    end_reachable: bool = False


@dataclass
class PrefixOracle:
    """Exact-prefix grammar oracle (SynCode incremental parser, basic lexer)."""

    processor: Any
    tokenizer: Any
    _cache: dict[tuple[str, int], bool] = field(default_factory=dict)
    _decode_cache: dict[int, str] = field(default_factory=dict)
    _keyword_token_ids: frozenset[int] | None = None

    def reset_caches(self) -> None:
        self._cache.clear()

    def keyword_token_ids(self) -> frozenset[int]:
        """
        Token IDs whose decoded text is optional whitespace + exactly one
        reserved keyword (grammar lexical contract).  Used to close the
        MaskStore IDENTIFIER-DFA overapprox gap — not prompt-specific.
        """
        if self._keyword_token_ids is not None:
            return self._keyword_token_ids
        ids: set[int] = set()
        vocab = int(getattr(self.tokenizer, "vocab_size", 0) or 0)
        # Limit scan to known specials + encode variants for each keyword.
        for kw in RESERVED_KEYWORDS:
            for variant in (kw, f" {kw}", f"\n{kw}", f"\t{kw}", f"  {kw}"):
                enc = self.tokenizer.encode(variant, add_special_tokens=False)
                if len(enc) == 1:
                    ids.add(int(enc[0]))
        self._keyword_token_ids = frozenset(ids)
        return self._keyword_token_ids

    def decode_token(self, token_id: int) -> str:
        if token_id not in self._decode_cache:
            self._decode_cache[token_id] = self.tokenizer.decode(
                [int(token_id)], skip_special_tokens=False
            )
        return self._decode_cache[token_id]


    def prefix_hash(self, prefix: str) -> str:
        return hashlib.sha256(prefix.encode("utf-8")).hexdigest()[:16]

    def _parse_prefix(self, prefix: str) -> tuple[Any | None, str, str]:
        """
        Return (ParseResult|None, remainder_name, signature).
        None result means the prefix is not extendable (parse error).
        """
        ge = self.processor.grammar_engine
        ge.inc_parser.reset()
        try:
            res = ge.inc_parser.get_acceptable_next_terminals(prefix)
        except Exception as exc:  # noqa: BLE001
            return None, "INVALID", f"INVALID|{type(exc).__name__}"
        rem = getattr(res.remainder_state, "name", str(res.remainder_state))
        seqs = tuple(
            tuple(str(t) for t in getattr(s, "accept_terminals", s))
            for s in list(res.accept_sequences or [])[:48]
        )
        remainder = res.remainder
        if isinstance(remainder, str):
            remainder_repr = remainder.encode("utf-8", errors="replace")
        else:
            remainder_repr = remainder
        sig = f"{rem}|{remainder_repr!r}|{seqs}"
        return res, rem, sig

    def prefix_status(self, prefix: str) -> tuple[bool, str, str, bool, bool]:
        """
        Returns (ok, sig, rem, end_immediate, end_reachable).
        ok True => empty, complete, or extendable.
        """
        if prefix == "":
            return True, "EMPTY", "EMPTY", False, False
        res, rem, sig = self._parse_prefix(prefix)
        if res is None:
            return False, sig, rem, False, False
        end_imm = False
        end_reach = False
        for seq in res.accept_sequences or []:
            terms = [str(t) for t in getattr(seq, "accept_terminals", seq)]
            if not terms:
                continue
            if terms[0] == "$END":
                end_imm = True
                end_reach = True
            elif len(terms) >= 2 and terms[1] == "$END":
                end_reach = True
        return True, sig, rem, end_imm, end_reach

    def candidate_valid(
        self,
        *,
        generated_prefix: str,
        token_id: int,
        prefix_sig: str,
        stats: FirewallStats | None = None,
    ) -> bool:
        tid = int(token_id)
        cache_key = (prefix_sig, tid)
        if cache_key in self._cache:
            if stats is not None:
                stats.cache_hits += 1
            return self._cache[cache_key]
        if stats is not None:
            stats.cache_misses += 1

        if tid == IM_END_ID:
            self._cache[cache_key] = False
            return False

        if tid == SYNCODE_EOS_ID:
            # EOS only when the current prefix is a complete successful parse.
            valid = _prefix_fully_parses(generated_prefix)
            self._cache[cache_key] = valid
            return valid

        piece = self.decode_token(tid)
        # Special tokens other than registered EOS never extend Verilog source.
        if piece.startswith("<|") and piece.endswith("|>") and tid != SYNCODE_EOS_ID:
            self._cache[cache_key] = False
            return False

        new_prefix = generated_prefix + piece
        valid = self._prefix_extendable(new_prefix)
        self._cache[cache_key] = valid
        return valid

    def _prefix_extendable(self, text: str) -> bool:
        """
        Exact extendability under backend/grammar/verilog.lark (basic lexer), including
        valid-incomplete remainders.  Rejects keyword/IDENTIFIER mismatches
        that SynCode's IDENTIFIER DFA overapprox leaves finite.
        """
        import syncode.larkm as lark  # noqa: PLC0415

        keyword_literals = {
            "MODULE": "module",
            "ENDMODULE": "endmodule",
            "INPUT": "input",
            "OUTPUT": "output",
            "INOUT": "inout",
            "WIRE": "wire",
            "REG": "reg",
            "ASSIGN": "assign",
            "ALWAYS": "always",
            "BEGIN": "begin",
            "END": "end",
            "IF": "if",
            "ELSE": "else",
        }

        ge = self.processor.grammar_engine
        base = ge.inc_parser.base_parser
        interactive = base.parse_interactive(text)
        lexer_state = interactive.lexer_thread.state
        tokens: list = []
        lexing_incomplete = False
        try:
            while lexer_state.line_ctr.char_pos < len(lexer_state.text):
                tok = interactive.lexer_thread.lexer.next_token(lexer_state)
                tokens.append(tok)
        except lark.exceptions.UnexpectedCharacters:
            lexing_incomplete = True
        except EOFError:
            pass

        for i, tok in enumerate(tokens):
            accepts_before = interactive.accepts()
            try:
                interactive.feed_token(tok)
            except lark.exceptions.UnexpectedToken:
                # Non-final unexpected token ⇒ hard invalid.
                if i != len(tokens) - 1:
                    return False
                # Final token rejected: allow valid-incomplete when its text is a
                # proper prefix of an *expected* keyword (e.g. "end" → endmodule).
                val = str(getattr(tok, "value", "") or "")
                for term in accepts_before:
                    name = str(term)
                    lit = keyword_literals.get(name)
                    if lit is not None and lit.startswith(val) and len(val) < len(lit):
                        return True
                return False

        if lexing_incomplete:
            return bool(interactive.accepts())
        return True

    def restore_processor_parse_state(self, prompt_len: int) -> None:
        """Reset incremental parser; latch start_from for parse_output_only."""
        ge = self.processor.grammar_engine
        self.processor.reset()
        ge.start_from = int(prompt_len)
        ge.parse_failed = False


@dataclass
class SelectedTokenGuardResult:
    """Result of lazy selected-token validation (not exhaustive vocab scan)."""

    logits: torch.Tensor
    selected_id: int | None
    rejected_ids: list[int] = field(default_factory=list)
    validations: int = 0
    error: str | None = None


def _prefix_fully_parses(text: str) -> bool:
    """True when *text* is a complete successful parse under the canonical grammar."""
    if not text or not str(text).strip():
        return False
    try:
        from app.services.verilog_validation import parse_with_verilog_grammar
    except Exception:  # noqa: BLE001
        return False
    ok, _err = parse_with_verilog_grammar(text)
    return bool(ok)


def select_valid_token(
    logits: torch.Tensor,
    *,
    oracle: PrefixOracle,
    generated_prefix: str,
    prompt_len: int,
    max_rejects: int = 32,
    eos_ids: set[int] | frozenset[int] | None = None,
) -> SelectedTokenGuardResult:
    """
    Lazy selected-token guard: validate only argmax candidates from SynCode
    logits (at most *max_rejects* + 1 exact checks).  Never scans the full
    vocabulary.  Rejected candidates are set to -inf on the returned logits.
    """
    work = logits.clone()
    eos = {int(x) for x in (eos_ids or ())}
    rejected: list[int] = []
    validations = 0

    ok, sig, _rem, _end_imm, _end_reach = oracle.prefix_status(generated_prefix)
    if generated_prefix != "" and not ok:
        work[:] = float("-inf")
        oracle.restore_processor_parse_state(prompt_len)
        return SelectedTokenGuardResult(
            logits=work,
            selected_id=None,
            rejected_ids=rejected,
            validations=0,
            error="constraint_no_valid_token",
        )

    selected: int | None = None
    for _ in range(int(max_rejects) + 1):
        if not bool(torch.isfinite(work).any()):
            break
        cand = int(torch.argmax(work).item())
        if not torch.isfinite(work[cand]):
            break
        validations += 1
        if cand in eos:
            valid = _prefix_fully_parses(generated_prefix)
        else:
            valid = oracle.candidate_valid(
                generated_prefix=generated_prefix,
                token_id=cand,
                prefix_sig=sig,
            )
        if valid:
            selected = cand
            break
        work[cand] = float("-inf")
        rejected.append(cand)
        log.info(
            "parser guard rejected token_id=%d text=%r (reject %d/%d)",
            cand,
            oracle.decode_token(cand)[:40],
            len(rejected),
            max_rejects,
        )

    oracle.restore_processor_parse_state(prompt_len)
    if selected is None:
        return SelectedTokenGuardResult(
            logits=work,
            selected_id=None,
            rejected_ids=rejected,
            validations=validations,
            error="constraint_no_valid_token",
        )
    return SelectedTokenGuardResult(
        logits=work,
        selected_id=selected,
        rejected_ids=rejected,
        validations=validations,
        error=None,
    )


def count_finite(logits: torch.Tensor) -> int:
    return int(torch.isfinite(logits).sum().item())


def apply_strict_firewall(
    logits: torch.Tensor,
    *,
    oracle: PrefixOracle,
    generated_prefix: str,
    prompt_len: int,
) -> FirewallResult:
    """
    Mask every finite token that fails the exact candidate-validity oracle.
    Evaluates all finite positions (not top-k).
    """
    t0 = time.perf_counter()
    stats = FirewallStats(after_special=count_finite(logits))
    out = logits.clone()

    ok, sig, rem, end_imm, end_reach = oracle.prefix_status(generated_prefix)
    if generated_prefix != "" and not ok:
        out[:] = float("-inf")
        stats.after_firewall = 0
        stats.removed_by_firewall = stats.after_special
        stats.elapsed_ms = (time.perf_counter() - t0) * 1000.0
        oracle.restore_processor_parse_state(prompt_len)
        return FirewallResult(
            logits=out,
            stats=stats,
            parser_state_sig=sig,
            remainder_state=rem,
            prefix_hash=oracle.prefix_hash(generated_prefix),
            prefix_len=len(generated_prefix),
            end_immediate=False,
            end_reachable=False,
        )

    finite_idx = torch.nonzero(torch.isfinite(out), as_tuple=False).view(-1)

    # Phase 1: close IDENTIFIER-DFA keyword overapprox using grammar keywords.
    res_now = None
    first_terms: set[str] = set()
    if generated_prefix != "":
        res_now, rem_now, _ = oracle._parse_prefix(generated_prefix)
        if res_now is not None:
            for seq in res_now.accept_sequences or []:
                terms = [str(t) for t in getattr(seq, "accept_terminals", seq)]
                if terms:
                    first_terms.add(terms[0])
    keyword_terms = {
        "MODULE", "ENDMODULE", "INPUT", "OUTPUT", "INOUT", "WIRE", "REG",
        "ASSIGN", "ALWAYS", "BEGIN", "END", "IF", "ELSE",
    }
    expecting_keyword = bool(first_terms & keyword_terms)
    if not expecting_keyword and first_terms:
        for kid in oracle.keyword_token_ids():
            if kid < out.size(0) and torch.isfinite(out[kid]):
                out[kid] = float("-inf")

    finite_idx = torch.nonzero(torch.isfinite(out), as_tuple=False).view(-1)

    # Phase 2: exact parse oracle.  For COMPLETE remainder whose only word
    # terminal is IDENTIFIER (plus punctuation/WS), SynCode's complete-case
    # IDENTIFIER DFA + keyword filter is exact — skip O(|V|) reparse.
    simple_terms = frozenset({"IDENTIFIER", "WS", "RPAR", "COMMA", "$END", "SEMICOLON"})
    skip_full_reparse = (
        rem == "COMPLETE"
        and bool(first_terms)
        and first_terms <= simple_terms
        and not expecting_keyword
    )

    removed = 0
    if not skip_full_reparse:
        for tid in finite_idx.tolist():
            if not oracle.candidate_valid(
                generated_prefix=generated_prefix,
                token_id=int(tid),
                prefix_sig=sig,
                stats=stats,
            ):
                out[int(tid)] = float("-inf")
                removed += 1
    else:
        # Still verify EOS via oracle (never trust IDENTIFIER DFA for $END).
        if SYNCODE_EOS_ID < out.size(0) and torch.isfinite(out[SYNCODE_EOS_ID]):
            if not oracle.candidate_valid(
                generated_prefix=generated_prefix,
                token_id=SYNCODE_EOS_ID,
                prefix_sig=sig,
                stats=stats,
            ):
                out[SYNCODE_EOS_ID] = float("-inf")
                removed += 1

    stats.removed_by_firewall = removed
    stats.syncode_overapprox_caught = removed
    stats.after_firewall = count_finite(out)
    stats.elapsed_ms = (time.perf_counter() - t0) * 1000.0

    # Restore processor so the next mask()/generation step is clean.
    oracle.restore_processor_parse_state(prompt_len)

    return FirewallResult(
        logits=out,
        stats=stats,
        parser_state_sig=sig,
        remainder_state=rem,
        prefix_hash=oracle.prefix_hash(generated_prefix),
        prefix_len=len(generated_prefix),
        end_immediate=end_imm,
        end_reachable=end_reach,
    )
