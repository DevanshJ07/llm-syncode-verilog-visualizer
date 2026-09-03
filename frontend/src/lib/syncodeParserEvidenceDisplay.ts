/**
 * Display helpers for structured SynCode parser evidence (Phase 4B).
 * Labels only — no invention of missing mask / EOS / terminal data.
 */

import { escapeTokenForDisplay } from "@/lib/importedTrace";
import { provenanceLabel, type ProvenanceKind } from "@/types/provenance";
import type {
  AcceptSequenceConstructionKind,
  EvidenceOrigin,
  MaskEosObservation,
  RemainderKind,
  RemainderRepresentation,
  RemainderStateName,
  SemanticsProvenance,
  SyncodeParserEvidence,
  SyncodeParserEvidenceStatus,
} from "@/types/syncodeParserEvidence";
import {
  isStructurallyAvailable,
  normalizeEvidenceStatus,
} from "@/types/syncodeParserEvidence";

export function evidenceStatusLabel(
  status: SyncodeParserEvidenceStatus | string | undefined | null
): "Available" | "Unavailable" | "Failed" {
  const n = normalizeEvidenceStatus(status);
  if (n === "available") return "Available";
  if (n === "failed") return "Failed";
  return "Unavailable";
}

export function evidenceOriginLabel(origin: EvidenceOrigin | string | undefined | null): string {
  switch (origin) {
    case "live_mask_runtime":
      return "Live mask runtime";
    case "import_recomputed_parser_only":
      return "Imported parser-only recomputation";
    case "import_recorded_bundle":
      return "Recorded imported bundle";
    case "none":
      return "None";
    default:
      return origin ? String(origin) : "None";
  }
}

export function evidenceTimingLabel(
  timing: string | undefined | null
): string {
  if (timing === "before_selected_token") return "Before selected token";
  if (timing == null || timing === "") return "Unavailable";
  return String(timing);
}

export function remainderStateLabel(
  state: RemainderStateName | string | null | undefined
): string {
  switch (state) {
    case "COMPLETE":
      return "Complete";
    case "MAYBE_COMPLETE":
      return "Maybe complete";
    case "INCOMPLETE":
      return "Incomplete";
    default:
      return "Unavailable";
  }
}

export function remainderStateExplanation(
  state: RemainderStateName | string | null | undefined
): string {
  switch (state) {
    case "COMPLETE":
      return "No unfinished lexical remainder is being carried.";
    case "MAYBE_COMPLETE":
      return "The final terminal is complete but may also be the prefix of a longer terminal.";
    case "INCOMPLETE":
      return "The current lexical remainder is incomplete.";
    default:
      return "";
  }
}

export function formatAcceptSequenceTerminals(terminals: string[]): string {
  if (terminals.length === 0) return "(empty terminal chain)";
  return terminals.join(" → ");
}

export function constructionKindLabel(
  kind: AcceptSequenceConstructionKind | string | null | undefined
): string {
  switch (kind) {
    case "current_terminal":
      return "current terminal";
    case "next_terminal":
      return "next terminal";
    case "final_then_next":
      return "final → next";
    case "final_ignore_next":
      return "final → ignore → next";
    case "ignore_only":
      return "ignore only";
    case "unknown":
      return "unknown";
    default:
      return "Unavailable";
  }
}

export function semanticsProvenanceLabel(
  kind: SemanticsProvenance | string | null | undefined
): string {
  switch (kind) {
    case "recorded":
      return "Recorded";
    case "recomputed":
      return "Recomputed";
    case "derived_from_version":
      return "Derived from version";
    case "unavailable":
      return "Unavailable";
    default:
      return "Unavailable";
  }
}

/**
 * Display core lookahead without inventing Recorded semantics.
 * Missing fields on historical evidence → Unavailable, or Derived from
 * SynCode 0.4.16 when only the version string is known.
 */
export function coreLookaheadDisplay(
  ev: SyncodeParserEvidence | null | undefined
): { label: string; provenanceNote: string } {
  if (!ev) {
    return { label: "Unavailable", provenanceNote: "Unavailable" };
  }
  if (
    typeof ev.core_lookahead_k === "number" &&
    ev.core_lookahead_unit === "grammar_terminals"
  ) {
    const note = semanticsProvenanceLabel(ev.semantics_provenance ?? null);
    return {
      label: `k=${ev.core_lookahead_k} grammar terminals`,
      provenanceNote: note,
    };
  }
  if (
    typeof ev.core_lookahead_k === "number" &&
    ev.core_lookahead_unit
  ) {
    return {
      label: `k=${ev.core_lookahead_k} ${ev.core_lookahead_unit}`,
      provenanceNote: semanticsProvenanceLabel(ev.semantics_provenance ?? null),
    };
  }
  const ver = (ev.syncode_version || "").trim();
  if (ver === "0.4.16" || ver.startsWith("0.4.16")) {
    return {
      label: "k=2 grammar terminals",
      provenanceNote: "Derived from SynCode 0.4.16",
    };
  }
  return { label: "Unavailable", provenanceNote: "Unavailable" };
}

export function ignoreTerminalsDisplay(
  terminals: string[] | null | undefined
): string {
  if (terminals == null) return "Unavailable";
  if (terminals.length === 0) return "(none recorded)";
  return terminals.join(", ");
}

export function formatRemainderDisplay(
  rem: RemainderRepresentation | null | undefined
): {
  kindLabel: string;
  textDisplay: string;
  hexDisplay: string | null;
  emptyDistinct: boolean;
  unavailable: boolean;
} {
  const kind: RemainderKind = rem?.kind ?? "unavailable";
  if (kind === "unavailable") {
    return {
      kindLabel: "unavailable",
      textDisplay: "Unavailable",
      hexDisplay: rem?.bytes_hex ?? null,
      emptyDistinct: false,
      unavailable: true,
    };
  }
  if (kind === "empty") {
    return {
      kindLabel: "empty",
      textDisplay: "(empty remainder)",
      hexDisplay: rem?.bytes_hex ?? null,
      emptyDistinct: true,
      unavailable: false,
    };
  }
  if (kind === "bytes_hex") {
    // Never invent UTF-8 from hex — show hex only; text only if backend stored it.
    return {
      kindLabel: "bytes_hex",
      textDisplay:
        rem?.text != null ? escapeTokenForDisplay(rem.text) : "Unavailable",
      hexDisplay: rem?.bytes_hex ?? null,
      emptyDistinct: false,
      unavailable: false,
    };
  }
  const rawText = rem?.text;
  return {
    kindLabel: kind,
    textDisplay:
      rawText != null ? escapeTokenForDisplay(rawText) : "Unavailable",
    hexDisplay: rem?.bytes_hex ?? null,
    emptyDistinct: false,
    unavailable: false,
  };
}

export function hasMaskEosObservation(
  obs: MaskEosObservation | null | undefined
): boolean {
  if (!obs) return false;
  return (
    obs.syncode_tokenizer_eos_token_id != null ||
    (obs.application_eos_token_ids?.length ?? 0) > 0 ||
    (obs.syncode_eos_allowed_by_accept_mask !== null &&
      obs.syncode_eos_allowed_by_accept_mask !== undefined) ||
    Object.keys(obs.application_eos_allowed_by_accept_mask ?? {}).length > 0
  );
}

/** True when live runtime recorded an EOS mask observation object. */
export function shouldShowEosMaskSection(ev: SyncodeParserEvidence): boolean {
  if (ev.origin === "import_recomputed_parser_only") return false;
  return hasMaskEosObservation(ev.mask_eos_observation);
}

export function boolOrUnavailable(
  v: boolean | null | undefined
): "True" | "False" | "Unavailable" {
  if (v === true) return "True";
  if (v === false) return "False";
  return "Unavailable";
}

/** EOS mask allowance — False must not read as Unavailable. */
export function eosAllowedLabel(
  v: boolean | null | undefined
): "Allowed" | "Blocked (False)" | "Unavailable" {
  if (v === true) return "Allowed";
  if (v === false) return "Blocked (False)";
  return "Unavailable";
}

export interface EvidenceCompareResult {
  acceptSequencesEqual: boolean | null;
  remainderStateEqual: boolean | null;
  grammarEndEqual: boolean | null;
}

/**
 * Derived comparison between recorded and recomputed evidence.
 * Does not declare either source correct.
 */
export function compareSyncodeEvidence(
  a: SyncodeParserEvidence | null | undefined,
  b: SyncodeParserEvidence | null | undefined
): EvidenceCompareResult {
  if (!isStructurallyAvailable(a) || !isStructurallyAvailable(b)) {
    return {
      acceptSequencesEqual: null,
      remainderStateEqual: null,
      grammarEndEqual: null,
    };
  }
  const seqA = JSON.stringify(
    (a!.accept_sequences ?? []).map((s) => s.terminals ?? [])
  );
  const seqB = JSON.stringify(
    (b!.accept_sequences ?? []).map((s) => s.terminals ?? [])
  );
  return {
    acceptSequencesEqual: seqA === seqB,
    remainderStateEqual: (a!.remainder_state ?? null) === (b!.remainder_state ?? null),
    grammarEndEqual:
      Boolean(a!.grammar_end_marker_present) ===
      Boolean(b!.grammar_end_marker_present),
  };
}

export function resolveDisplayProvenance(
  provenanceKind: ProvenanceKind | undefined,
  evidence: SyncodeParserEvidence | null | undefined
): ProvenanceKind {
  if (provenanceKind) return provenanceKind;
  if (!evidence) return "unavailable";
  if (evidence.origin === "import_recomputed_parser_only") return "recomputed";
  if (normalizeEvidenceStatus(evidence.status) === "unavailable") {
    return "unavailable";
  }
  if (evidence.origin === "live_mask_runtime" || evidence.origin === "import_recorded_bundle") {
    return "recorded";
  }
  return "recorded";
}

export function provenanceOriginSummary(
  provenanceKind: ProvenanceKind,
  origin: EvidenceOrigin | string | undefined
): string {
  return `${provenanceLabel(provenanceKind)} · ${evidenceOriginLabel(origin)}`;
}

/** Plain-text export block for step analysis reports. */
export function formatSyncodeParserEvidenceReport(
  evidence: SyncodeParserEvidence | null | undefined,
  options?: {
    provenanceKind?: ProvenanceKind;
    grammarSha256?: string | null;
    heading?: string;
    legacyAcceptSequences?: string[];
  }
): string {
  const lines: string[] = [];
  const heading = options?.heading ?? "SynCode terminal accept paths";
  lines.push(heading);
  lines.push("-".repeat(heading.length));

  if (!evidence) {
    const legacy = options?.legacyAcceptSequences ?? [];
    if (legacy.length > 0) {
      lines.push("Status: Unavailable (structured evidence absent)");
      lines.push("Legacy unstructured evidence:");
      legacy.forEach((s, i) => lines.push(`  ${i + 1}. ${s}`));
    } else {
      lines.push("Status: Unavailable");
    }
    return lines.join("\n");
  }

  const status = evidenceStatusLabel(evidence.status);
  const prov = resolveDisplayProvenance(options?.provenanceKind, evidence);
  lines.push(`Status: ${status}`);
  lines.push(`Provenance: ${provenanceLabel(prov)}`);
  lines.push(`Origin: ${evidenceOriginLabel(evidence.origin)}`);
  lines.push(`Timing: ${evidenceTimingLabel(evidence.evidence_timing)}`);
  if (evidence.syncode_version) {
    lines.push(`SynCode version: ${evidence.syncode_version}`);
  }
  if (options?.grammarSha256) {
    lines.push(`Grammar SHA-256: ${options.grammarSha256}`);
  }

  if (evidence.origin === "import_recomputed_parser_only") {
    lines.push(
      "Note: Recomputed with the current canonical grammar and SynCode parser. This is not the original runtime token mask."
    );
  }

  if (normalizeEvidenceStatus(evidence.status) === "failed") {
    lines.push(`Error: ${evidence.error || "(failed)"}`);
  } else if (normalizeEvidenceStatus(evidence.status) === "unavailable") {
    lines.push(`Detail: ${evidence.error || "SynCode parser evidence unavailable."}`);
  }

  if (isStructurallyAvailable(evidence)) {
    lines.push("");
    lines.push("Accept sequences (SynCode terminal paths — not tokenizer tokens):");
    lines.push(
      `Stored ${evidence.accept_sequence_count_stored} of ${evidence.accept_sequence_count_total} total` +
        (evidence.accept_sequences_truncated ? " (truncated)" : "")
    );
    if (evidence.accept_sequences.length === 0) {
      lines.push(
        "  0 sequences (recorded/recomputed empty sequence set — not Unavailable)"
      );
    } else {
      evidence.accept_sequences.forEach((seq, i) => {
        lines.push(`  ${i + 1}. ${formatAcceptSequenceTerminals(seq.terminals ?? [])}`);
      });
    }

    lines.push("");
    lines.push(
      `Remainder state: ${evidence.remainder_state ?? "Unavailable"}`
    );
    const rem = formatRemainderDisplay(evidence.remainder);
    lines.push(`Remainder kind: ${rem.kindLabel}`);
    lines.push(`Remainder text: ${rem.textDisplay}`);
    if (rem.hexDisplay) lines.push(`Remainder bytes_hex: ${rem.hexDisplay}`);
    if (evidence.remainder?.original_type) {
      lines.push(`Remainder original type: ${evidence.remainder.original_type}`);
    }

    lines.push("");
    if (evidence.grammar_end_marker_present) {
      lines.push("Grammar-end marker present");
      lines.push(
        "This does not by itself prove that an EOS tokenizer token was allowed by the final mask."
      );
    } else {
      lines.push("Grammar-end marker: not present");
    }

    if (shouldShowEosMaskSection(evidence) && evidence.mask_eos_observation) {
      const eos = evidence.mask_eos_observation;
      lines.push("");
      lines.push("EOS mask observation (tokenizer mask — not grammar-end):");
      lines.push(
        `  SynCode tokenizer EOS token ID: ${eos.syncode_tokenizer_eos_token_id ?? "Unavailable"}`
      );
      lines.push(
        `  Application EOS token IDs: ${(eos.application_eos_token_ids ?? []).join(", ") || "Unavailable"}`
      );
      lines.push(
        `  SynCode EOS allowed by accept mask: ${eosAllowedLabel(eos.syncode_eos_allowed_by_accept_mask)}`
      );
      const appMap = eos.application_eos_allowed_by_accept_mask ?? {};
      Object.entries(appMap).forEach(([k, v]) => {
        lines.push(`  Application EOS ${k} allowed: ${eosAllowedLabel(v)}`);
      });
    }

    lines.push("");
    lines.push("Prefix alignment (evidence is for the prefix before the selected token):");
    lines.push(
      `  Generated-token count before selection: ${evidence.generated_token_count_before_selection ?? "Unavailable"}`
    );
    lines.push(
      `  Prefix character count: ${evidence.generated_prefix_char_count ?? "Unavailable"}`
    );
    lines.push(
      `  Prefix SHA-256: ${evidence.generated_prefix_sha256 ?? "Unavailable"}`
    );
    if (
      evidence.origin === "live_mask_runtime" &&
      evidence.mask_call_index != null
    ) {
      lines.push(`  Mask-call index: ${evidence.mask_call_index}`);
    }
  }

  if (evidence.warnings?.length) {
    lines.push("");
    lines.push("Warnings:");
    evidence.warnings.forEach((w) => lines.push(`  - ${w}`));
  }
  if (evidence.error && normalizeEvidenceStatus(evidence.status) !== "unavailable") {
    lines.push(`Error: ${evidence.error}`);
  }

  return lines.join("\n");
}
