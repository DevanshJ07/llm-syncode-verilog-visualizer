/**
 * TypeScript mirrors of backend/app/models/syncode_parser_evidence.py
 * (Phase 4A.1 / 4A.2). Keep in sync with the committed Pydantic models.
 *
 * Do not conflate:
 *   status  — available | unavailable | failed (legacy "recorded" → available)
 *   origin  — where the ParseResult came from
 *   Prov.kind — Recorded / Recomputed / Unavailable / Derived (outer wrapper)
 */

export type SyncodeParserEvidenceStatus =
  | "available"
  | "unavailable"
  | "failed"
  | "recorded"; // legacy wire value; treat as available when present

export type EvidenceOrigin =
  | "live_mask_runtime"
  | "import_recomputed_parser_only"
  | "import_recorded_bundle"
  | "none";

export type EvidenceTiming = "before_selected_token";

export type RemainderStateName =
  | "COMPLETE"
  | "MAYBE_COMPLETE"
  | "INCOMPLETE";

export type RemainderKind = "text" | "bytes_hex" | "empty" | "unavailable";

export interface AcceptSequenceRecord {
  terminals: string[];
}

export interface RemainderRepresentation {
  kind: RemainderKind;
  text?: string | null;
  bytes_hex?: string | null;
  original_type?: string;
  truncated?: boolean;
  original_byte_length?: number | null;
  stored_byte_length?: number | null;
}

export interface MaskEosObservation {
  syncode_tokenizer_eos_token_id?: number | null;
  application_eos_token_ids?: number[];
  syncode_eos_allowed_by_accept_mask?: boolean | null;
  application_eos_allowed_by_accept_mask?: Record<string, boolean | null>;
}

export interface SyncodeParserEvidence {
  status: SyncodeParserEvidenceStatus;
  origin: EvidenceOrigin;
  evidence_timing: EvidenceTiming;
  syncode_version: string;
  mask_call_index?: number | null;
  generated_token_count_before_selection?: number | null;
  generated_prefix_char_count?: number | null;
  generated_prefix_sha256?: string | null;
  accept_sequences: AcceptSequenceRecord[];
  accept_sequence_count_total: number;
  accept_sequence_count_stored: number;
  accept_sequences_truncated: boolean;
  remainder_state?: RemainderStateName | null;
  remainder: RemainderRepresentation;
  function_end?: boolean | null;
  grammar_end_marker_present: boolean;
  mask_eos_observation?: MaskEosObservation | null;
  warnings: string[];
  error: string;
}

/** Normalize legacy status="recorded" to available (mirrors backend validator). */
export function normalizeEvidenceStatus(
  status: SyncodeParserEvidenceStatus | string | undefined | null
): "available" | "unavailable" | "failed" {
  if (status === "recorded" || status === "available") return "available";
  if (status === "failed") return "failed";
  return "unavailable";
}

export function isStructurallyAvailable(
  ev: SyncodeParserEvidence | null | undefined
): boolean {
  if (!ev) return false;
  return normalizeEvidenceStatus(ev.status) === "available";
}
