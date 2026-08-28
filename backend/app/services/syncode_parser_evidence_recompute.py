"""
Phase 4A.2 — imported-trace SynCode parser-evidence recomputation (parser-only).

Reconstructs pre-token prefixes from recorded ``selected_token`` strings and
runs SynCode 0.4.16's incremental parser against the current canonical grammar.

This is **not** original live mask evidence:
  • no MaskStore / DFA accept mask
  • no tokenizer / model / Torch
  • no original byte remainder from the tokenizer
  • ``origin=import_recomputed_parser_only``
  • Prov provenance kind = recomputed

SynCode imports are lazy (inside functions) so default import paths never load
SynCode.  Relies on ``_load_lark_module`` reuse of an already-loaded
``syncode.larkm`` (Phase 4A.1 fix) to avoid split-package corruption.

Whitespace-ignore determination mirrors SynCode 0.4.16
``GrammarConstrainer._get_ignore_whitespace`` without importing that class
(which would pull Torch / MaskStore).

Failure / missing-token policy
------------------------------
* Missing ``selected_token`` string at step K: step K and all later steps are
  marked unavailable (no token-ID decoding).  Earlier successful steps kept.
* Parser exception at step K: that step is ``failed``; the incremental parser
  is ``reset()`` and each later step analyses its full reconstructed prefix
  independently (deterministic; no poisoned parser state).
* Step / prefix character limits: remaining steps unavailable with warnings;
  the imported trace itself is never truncated or discarded.

Performance
-----------
``create_parser_only_incremental()`` runs **once per prompt**.  The same
IncrementalParser instance is reused across steps with ``reset()`` before each
``get_acceptable_next_terminals`` call (and again after a parser failure).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.core.config import settings
from app.core.grammar import grammar_sha256, read_verilog_grammar
from app.models.normalized import NormalizedTraceStep
from app.models.provenance import Prov
from app.models.syncode_parser_evidence import (
    SyncodeParserEvidence,
    failed_syncode_parser_evidence,
    unavailable_syncode_parser_evidence,
)
from app.services.syncode_parser_evidence import (
    serialize_parse_result,
    syncode_package_version,
)

log = logging.getLogger(__name__)

_RECOMPUTE_WARNING = (
    "parser-only recomputation against the current canonical grammar; "
    "not the original SynCode mask-time ParseResult; "
    "no DFA mask, tokenizer byte remainder, or token-level EOS-mask observation"
)


def _max_recompute_steps() -> int:
    return int(
        getattr(settings, "syncode_parser_evidence_recompute_max_steps", 2048)
    )


def _max_prefix_chars() -> int:
    return int(
        getattr(
            settings,
            "syncode_parser_evidence_recompute_max_prefix_chars",
            200_000,
        )
    )


def detect_ignore_whitespace(grammar: Any) -> bool:
    """
    SynCode 0.4.16-compatible whitespace-ignore probe.

    Version-coupled: copies GrammarConstrainer._get_ignore_whitespace logic
    using create_base_parser only (no MaskStore / Torch).
    """
    from syncode.parsers import create_base_parser  # noqa: PLC0415
    import regex  # noqa: PLC0415

    base_parser = create_base_parser(grammar)
    terminals = base_parser.terminals
    ignore_terminals = base_parser.ignore_tokens
    for ig_name in ignore_terminals:
        for terminal in terminals:
            if terminal.name == ig_name:
                if regex.match(terminal.pattern.to_regexp(), " ") is not None:
                    return True
    return False


def create_parser_only_incremental():
    """
    Build SynCode IncrementalParser for the canonical Verilog grammar.

    Does not construct SyncodeLogitsProcessor, GrammarConstrainer, MaskStore,
    tokenizer, or model.
    """
    from syncode.parsers.grammars.grammar import Grammar  # noqa: PLC0415
    from syncode.parsers import create_parser  # noqa: PLC0415

    ebnf = read_verilog_grammar()
    grammar = Grammar(ebnf)  # name becomes 'custom'
    ignore_ws = detect_ignore_whitespace(grammar)
    return create_parser(grammar, parser="lalr", ignore_whitespace=ignore_ws), ignore_ws


def _selected_token_string(step: NormalizedTraceStep) -> Optional[str]:
    if step.selected.is_unavailable or step.selected.value is None:
        return None
    tok = step.selected.value.token
    if tok is None:
        return None
    return str(tok)


def _validate_bundle_evidence(raw: Any) -> SyncodeParserEvidence | None:
    """Validate future structured bundle evidence; None if absent/invalid."""
    from app.services.syncode_parser_evidence import (  # noqa: PLC0415
        validate_imported_structured_evidence,
    )

    return validate_imported_structured_evidence(raw)


def extract_recorded_evidence_from_raw_step(
    raw: dict[str, Any],
) -> SyncodeParserEvidence | None:
    """
    Read structured ``syncode_parser_evidence`` from a future trace step.

    Does **not** parse legacy stringified ``accept_sequences`` into terminals.
    """
    if "syncode_parser_evidence" not in raw:
        return None
    return _validate_bundle_evidence(raw.get("syncode_parser_evidence"))


def recompute_syncode_parser_evidence_for_steps(
    steps: list[NormalizedTraceStep],
    *,
    grammar_hash: str | None = None,
    source_file: str = "",
) -> tuple[list[Prov[SyncodeParserEvidence]], list[str]]:
    """
    Recompute parser-only SynCode evidence for each normalized trace step.

    Returns parallel list of Prov wrappers (same length as ``steps``) and
    experiment/prompt-level warnings.
    """
    warnings: list[str] = []
    ghash = grammar_hash or grammar_sha256()
    version = syncode_package_version()
    max_steps = _max_recompute_steps()
    max_chars = _max_prefix_chars()

    if not steps:
        return [], warnings

    try:
        inc_parser, ignore_ws = create_parser_only_incremental()
    except Exception as exc:
        log.warning("SynCode parser-only init failed: %s", exc)
        warnings.append(
            f"SynCode parser-only recomputation unavailable: "
            f"{type(exc).__name__}: {exc}"
        )
        failed = [
            Prov[SyncodeParserEvidence].unavailable(
                method=(
                    f"parser-only SynCode init failed: "
                    f"{type(exc).__name__}: {exc}"
                ),
                source_file=source_file or None,
                warnings=[_RECOMPUTE_WARNING],
            )
            for _ in steps
        ]
        return failed, warnings

    warnings.append(
        f"SynCode parser-only recompute active "
        f"(ignore_whitespace={ignore_ws}, grammar_sha256={ghash[:12]}…)"
    )

    results: list[Prov[SyncodeParserEvidence]] = []
    prefix = ""
    stop_reason: str | None = None

    for i, step in enumerate(steps):
        if stop_reason is not None:
            results.append(
                Prov[SyncodeParserEvidence].unavailable(
                    method=stop_reason,
                    source_file=source_file or None,
                    warnings=[_RECOMPUTE_WARNING],
                )
            )
            continue

        if i >= max_steps:
            stop_reason = (
                f"recompute step limit reached "
                f"(max_steps={max_steps}); remaining steps not recomputed"
            )
            warnings.append(stop_reason)
            results.append(
                Prov[SyncodeParserEvidence].unavailable(
                    method=stop_reason,
                    source_file=source_file or None,
                    warnings=[_RECOMPUTE_WARNING],
                )
            )
            continue

        if len(prefix) > max_chars:
            stop_reason = (
                f"recompute prefix character limit reached "
                f"(max_prefix_chars={max_chars}); remaining steps not recomputed"
            )
            warnings.append(stop_reason)
            results.append(
                Prov[SyncodeParserEvidence].unavailable(
                    method=stop_reason,
                    source_file=source_file or None,
                    warnings=[_RECOMPUTE_WARNING],
                )
            )
            continue

        # Analyse prefix BEFORE selected token i (empty at step 0).
        step_warnings = [_RECOMPUTE_WARNING]
        try:
            inc_parser.reset()
            parse_result = inc_parser.get_acceptable_next_terminals(prefix)
            evidence = serialize_parse_result(
                parse_result,
                mask_call_index=None,  # must not imply a live mask call
                generated_token_count_before_selection=i,
                generated_prefix=prefix,
                syncode_version=version,
                accept_mask=None,
                extra_warnings=step_warnings,
                origin="import_recomputed_parser_only",
            )
            # Defence: never attach mask EOS observation for recompute.
            if evidence.mask_eos_observation is not None:
                evidence = evidence.model_copy(
                    update={"mask_eos_observation": None}
                )
            results.append(
                Prov[SyncodeParserEvidence].recomputed(
                    evidence,
                    method=(
                        "SynCode IncrementalParser.get_acceptable_next_terminals "
                        "on reconstructed selected_token prefix"
                    ),
                    grammar_sha256=ghash,
                    source_file=source_file or None,
                    source_field="steps[].selected_token prefix",
                    warnings=list(evidence.warnings),
                )
            )
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            warnings.append(
                f"SynCode parser-only recompute failed at step_index={step.step_index}: {err}"
            )
            failed_ev = failed_syncode_parser_evidence(
                error=err,
                warnings=step_warnings
                + [
                    "parser failure; subsequent steps retry from a reset parser "
                    "on their full reconstructed prefixes"
                ],
                mask_call_index=None,
                syncode_version=version,
                origin="import_recomputed_parser_only",
            )
            failed_ev = failed_ev.model_copy(
                update={
                    "generated_token_count_before_selection": i,
                    "generated_prefix_char_count": len(prefix),
                }
            )
            results.append(
                Prov[SyncodeParserEvidence].recomputed(
                    failed_ev,
                    method="SynCode IncrementalParser failure",
                    grammar_sha256=ghash,
                    source_file=source_file or None,
                    warnings=list(failed_ev.warnings),
                )
            )
            try:
                inc_parser.reset()
            except Exception:
                pass

        # Extend prefix with this step's selected token for the next step.
        tok = _selected_token_string(step)
        if tok is None:
            stop_reason = (
                f"selected_token string missing at step_index={step.step_index}; "
                "cannot decode token IDs; subsequent prefixes unavailable"
            )
            warnings.append(stop_reason)
            continue
        prefix = prefix + tok

    assert len(results) == len(steps)
    return results, warnings
