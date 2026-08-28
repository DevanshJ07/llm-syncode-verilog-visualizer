/**
 * Imported-trace helpers — Phase 2B.2.
 *
 * Derive view models from NormalizedTraceStep without inventing live-only
 * fields (probabilities, entropy, accept sequences, etc.).
 */

import type { NormalizedPromptResult, NormalizedTraceStep, TokenRef } from "@/types/normalized";
import { isUnavailable, type Prov, type ProvenanceKind } from "@/types/provenance";

/** Escape token text so whitespace stays visually distinguishable. */
export function escapeTokenForDisplay(token: string | null | undefined): string {
  if (token === null || token === undefined) return "Unavailable";
  // Preserve exact content; escape common whitespace for research clarity.
  const escaped = token
    .replace(/\\/g, "\\\\")
    .replace(/\r/g, "\\r")
    .replace(/\n/g, "\\n")
    .replace(/\t/g, "\\t")
    .replace(/ /g, "·"); // middle-dot marks ordinary spaces without collapsing
  return `"${escaped}"`;
}

export function tokenRefLabel(ref: Prov<TokenRef> | null | undefined): {
  tokenDisplay: string;
  idDisplay: string;
  unavailable: boolean;
  kind: ProvenanceKind;
} {
  if (!ref || isUnavailable(ref) || !ref.value) {
    return {
      tokenDisplay: "Unavailable",
      idDisplay: "Unavailable",
      unavailable: true,
      kind: "unavailable",
    };
  }
  const tok = ref.value.token;
  const id = ref.value.token_id;
  return {
    tokenDisplay:
      tok === null || tok === undefined
        ? "Unavailable"
        : escapeTokenForDisplay(tok),
    idDisplay: id === null || id === undefined ? "Unavailable" : String(id),
    unavailable: false,
    kind: ref.provenance.kind,
  };
}

export type StepMarkerKind = "ordinary" | "intervention" | "parse_failed" | "both";

export function getBlockedFlag(
  step: NormalizedTraceStep,
  key: "raw_argmax_blocked" | "selected_equals_constrained_argmax" | "constrained_argmax_finite"
): { value: boolean | null; kind: ProvenanceKind } {
  if (isUnavailable(step.blocked_token_info) || !step.blocked_token_info.value) {
    // Fallback for raw_argmax_blocked via masking_changed_selection
    if (key === "raw_argmax_blocked" && !isUnavailable(step.masking_changed_selection)) {
      return {
        value: Boolean(step.masking_changed_selection.value),
        kind: step.masking_changed_selection.provenance.kind,
      };
    }
    return { value: null, kind: "unavailable" };
  }
  const raw = step.blocked_token_info.value[key];
  if (raw === null || raw === undefined) {
    if (key === "raw_argmax_blocked" && !isUnavailable(step.masking_changed_selection)) {
      return {
        value: Boolean(step.masking_changed_selection.value),
        kind: step.masking_changed_selection.provenance.kind,
      };
    }
    return { value: null, kind: "unavailable" };
  }
  return {
    value: Boolean(raw),
    kind: step.blocked_token_info.provenance.kind,
  };
}

/** True only when intervention evidence is recorded as true — never invent false. */
export function isRecordedIntervention(step: NormalizedTraceStep): boolean {
  const flag = getBlockedFlag(step, "raw_argmax_blocked");
  return flag.value === true;
}

export function isRecordedParseFailed(step: NormalizedTraceStep): boolean {
  if (isUnavailable(step.parser_info) || !step.parser_info.value) return false;
  return step.parser_info.value.syncode_parse_failed === true;
}

export function stepMarkerKind(step: NormalizedTraceStep): StepMarkerKind {
  const intervention = isRecordedIntervention(step);
  const parseFailed = isRecordedParseFailed(step);
  if (intervention && parseFailed) return "both";
  if (intervention) return "intervention";
  if (parseFailed) return "parse_failed";
  return "ordinary";
}

export function getGeneratedPrefixTokenCount(
  step: NormalizedTraceStep
): { value: number | null; kind: ProvenanceKind } {
  if (isUnavailable(step.parser_info) || !step.parser_info.value) {
    return { value: null, kind: "unavailable" };
  }
  const v = step.parser_info.value.generated_prefix_tokens;
  if (v === null || v === undefined) return { value: null, kind: "unavailable" };
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return { value: null, kind: "unavailable" };
  return { value: n, kind: step.parser_info.provenance.kind };
}

export interface ParsedTopRawToken {
  token: string | null;
  tokenId: number | null;
  logit: number | null;
  /** Recorded allowed-after-SynCode flag when present; null = unavailable. */
  allowedAfterSyncode: boolean | null;
  raw: unknown;
}

/** Best-effort parse of recorded top_raw_tokens entries (flexible field names). */
export function parseTopRawTokens(
  step: NormalizedTraceStep
): { items: ParsedTopRawToken[]; kind: ProvenanceKind; unavailable: boolean } {
  if (isUnavailable(step.recorded_top_raw_tokens) || !step.recorded_top_raw_tokens.value) {
    return {
      items: [],
      kind: "unavailable",
      unavailable: true,
    };
  }
  const items: ParsedTopRawToken[] = [];
  for (const entry of step.recorded_top_raw_tokens.value) {
    if (entry === null || entry === undefined) {
      items.push({
        token: null,
        tokenId: null,
        logit: null,
        allowedAfterSyncode: null,
        raw: entry,
      });
      continue;
    }
    if (typeof entry !== "object") {
      items.push({
        token: String(entry),
        tokenId: null,
        logit: null,
        allowedAfterSyncode: null,
        raw: entry,
      });
      continue;
    }
    const o = entry as Record<string, unknown>;
    const token =
      typeof o.token === "string"
        ? o.token
        : typeof o.token_str === "string"
          ? o.token_str
          : o.token != null
            ? String(o.token)
            : null;
    const idRaw = o.token_id ?? o.id ?? o.tokenId;
    let tokenId: number | null = null;
    if (typeof idRaw === "number" && Number.isFinite(idRaw)) tokenId = idRaw;
    else if (typeof idRaw === "string" && idRaw.trim() !== "" && !Number.isNaN(Number(idRaw))) {
      tokenId = Number(idRaw);
    }
    const logitRaw = o.logit ?? o.raw_logit ?? o.score;
    let logit: number | null = null;
    if (typeof logitRaw === "number" && Number.isFinite(logitRaw)) logit = logitRaw;
    else if (typeof logitRaw === "string" && logitRaw.trim() !== "" && !Number.isNaN(Number(logitRaw))) {
      logit = Number(logitRaw);
    }
    const allowedRaw =
      o.allowed_after_syncode ??
      o.allowed_after_SynCode ??
      o.allowed ??
      o.is_allowed ??
      o.allowed_by_syncode;
    let allowedAfterSyncode: boolean | null = null;
    if (typeof allowedRaw === "boolean") allowedAfterSyncode = allowedRaw;
    items.push({ token, tokenId, logit, allowedAfterSyncode, raw: entry });
  }
  return {
    items,
    kind: step.recorded_top_raw_tokens.provenance.kind,
    unavailable: false,
  };
}

/** Vocab-logit count from recorded vocab_logits length when it is a list. */
export function vocabLogitCount(
  step: NormalizedTraceStep
): { value: number | null; kind: ProvenanceKind } {
  if (isUnavailable(step.recorded_vocab_logits) || step.recorded_vocab_logits.value == null) {
    return { value: null, kind: "unavailable" };
  }
  const v = step.recorded_vocab_logits.value;
  if (Array.isArray(v)) {
    return { value: v.length, kind: step.recorded_vocab_logits.provenance.kind };
  }
  if (typeof v === "object" && v !== null && "length" in (v as object)) {
    const len = Number((v as { length: unknown }).length);
    if (Number.isFinite(len)) {
      return { value: len, kind: step.recorded_vocab_logits.provenance.kind };
    }
  }
  // Scalar / opaque payload — count unavailable (do not invent).
  return { value: null, kind: "unavailable" };
}

/**
 * Reconstruct prefix from selected_token strings only.
 * before: concat of steps[0..indexExclusive-1]
 * Returns unavailable if any selected token string is missing.
 */
export function derivePrefixFromSelected(
  steps: NormalizedTraceStep[],
  indexExclusive: number
): Prov<string> {
  if (indexExclusive < 0) {
    return {
      value: null,
      provenance: {
        kind: "unavailable",
        method: "invalid prefix index",
      },
    };
  }
  if (indexExclusive === 0) {
    return {
      value: "",
      provenance: {
        kind: "derived",
        method: "concatenate selected_token strings (empty prefix)",
      },
    };
  }
  const parts: string[] = [];
  for (let i = 0; i < indexExclusive; i++) {
    const step = steps[i];
    if (!step || isUnavailable(step.selected) || !step.selected.value) {
      return {
        value: null,
        provenance: {
          kind: "unavailable",
          method: "selected_token missing; cannot reconstruct prefix",
        },
      };
    }
    const tok = step.selected.value.token;
    if (tok === null || tok === undefined) {
      return {
        value: null,
        provenance: {
          kind: "unavailable",
          method: "selected_token string missing; cannot reconstruct prefix",
        },
      };
    }
    parts.push(tok);
  }
  return {
    value: parts.join(""),
    provenance: {
      kind: "derived",
      method: "concatenate selected_token strings",
      source_field: "steps[].selected.token",
    },
  };
}

export interface TraceOverview {
  totalSteps: number;
  interventionCount: number;
  parseFailedCount: number;
  firstInterventionIndex: number | null; // 0-based array index
  interventionEvidenceAvailable: boolean;
}

export function summarizeTrace(steps: NormalizedTraceStep[]): TraceOverview {
  let interventionCount = 0;
  let parseFailedCount = 0;
  let firstInterventionIndex: number | null = null;
  let anyInterventionEvidence = false;

  steps.forEach((step, i) => {
    const blocked = getBlockedFlag(step, "raw_argmax_blocked");
    if (blocked.value !== null) anyInterventionEvidence = true;
    if (blocked.value === true) {
      interventionCount += 1;
      if (firstInterventionIndex === null) firstInterventionIndex = i;
    }
    if (isRecordedParseFailed(step)) parseFailedCount += 1;
  });

  return {
    totalSteps: steps.length,
    interventionCount,
    parseFailedCount,
    firstInterventionIndex,
    interventionEvidenceAvailable: anyInterventionEvidence,
  };
}

export type EvidenceChannelStatus = ProvenanceKind | "mixed";

export interface EvidenceChannelRow {
  channel: string;
  status: EvidenceChannelStatus;
  note?: string;
}

/** Summarize evidence availability for the active step (and common unavailable channels). */
export function evidenceChannelsForStep(step: NormalizedTraceStep): EvidenceChannelRow[] {
  const kindOf = <T,>(p: Prov<T>): ProvenanceKind =>
    isUnavailable(p) ? "unavailable" : p.provenance.kind;

  return [
    { channel: "Raw / pre-mask preferred token", status: kindOf(step.raw_preferred) },
    { channel: "Constrained argmax token", status: kindOf(step.constrained_preferred) },
    { channel: "Selected token", status: kindOf(step.selected) },
    {
      channel: "raw_argmax_blocked / intervention",
      status: getBlockedFlag(step, "raw_argmax_blocked").kind,
    },
    {
      channel: "selected_equals_constrained_argmax",
      status: getBlockedFlag(step, "selected_equals_constrained_argmax").kind,
    },
    {
      channel: "constrained_argmax_finite",
      status: getBlockedFlag(step, "constrained_argmax_finite").kind,
    },
    { channel: "Allowed-token count", status: kindOf(step.valid_token_count) },
    {
      channel: "newly_masked_token_count",
      status: kindOf(step.newly_masked_token_count),
    },
    { channel: "masked_token_count", status: kindOf(step.masked_token_count) },
    { channel: "Top raw tokens (subset)", status: kindOf(step.recorded_top_raw_tokens) },
    {
      channel: "Vocab logits",
      status: kindOf(step.recorded_vocab_logits),
      note: "Recorded logits only — probabilities not derived",
    },
    {
      channel: "prefix_tail",
      status: kindOf(step.prefix_before_selected),
      note: "May be truncated; not the complete prefix",
    },
    { channel: "Parser flags (syncode_parse_failed)", status: kindOf(step.parser_info) },
    {
      channel: "Entropy",
      status: kindOf(step.entropy_before),
      note: "Full distribution unavailable; not plotted as zero",
    },
    {
      channel: "Probabilities / ranks",
      status: kindOf(step.raw_probability),
    },
    {
      channel: "Expected Lark terminals",
      status: kindOf(step.expected_terminals),
    },
    {
      channel: "SynCode accept sequences",
      status: kindOf(step.syncode_accept_sequences),
    },
    { channel: "Remainder state", status: kindOf(step.remainder_state) },
    { channel: "EOS eligibility", status: kindOf(step.eos_eligible) },
  ];
}

export function explainIntervention(step: NormalizedTraceStep): string {
  const blocked = getBlockedFlag(step, "raw_argmax_blocked");
  const equals = getBlockedFlag(step, "selected_equals_constrained_argmax");

  if (blocked.value === true) {
    return "SynCode blocked the model’s raw argmax; the selected token differs from the unconstrained preference.";
  }
  if (blocked.value === false) {
    if (equals.value === true) {
      return "SynCode did not change the model’s preferred choice: selected equals constrained argmax, and raw argmax was not blocked.";
    }
    if (equals.value === false) {
      return "Raw argmax was not blocked, but selected does not equal constrained argmax (inspect recorded tokens).";
    }
    return "Raw argmax was not blocked (recorded). Equality with constrained argmax is unavailable.";
  }
  return "Intervention evidence (raw_argmax_blocked) is unavailable for this step — not treated as false.";
}

export function findNextIntervention(
  steps: NormalizedTraceStep[],
  fromIndex: number,
  direction: 1 | -1
): number | null {
  if (steps.length === 0) return null;
  let i = fromIndex + direction;
  while (i >= 0 && i < steps.length) {
    if (isRecordedIntervention(steps[i])) return i;
    i += direction;
  }
  return null;
}

/** Prompt-level overview helpers that read provenanced prompt fields. */
export function promptOverviewBits(prompt: NormalizedPromptResult) {
  return {
    termination: prompt.termination_reason,
    reconstructionMatch: prompt.reconstruction_matches_authoritative,
    warnings: prompt.warnings,
  };
}
