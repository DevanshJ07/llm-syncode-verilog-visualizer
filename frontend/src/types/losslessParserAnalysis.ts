/**
 * TypeScript mirrors of backend/app/models/lossless_parser_analysis.py (Checkpoint 2).
 * Keep in sync with the committed Pydantic schema.
 */

import type { ParserAnalysis } from "@/types/parserAnalysis";

export const LOSSLESS_ANALYSIS_SCHEMA_VERSION = "lossless_cst_v1";

export type AnalysisKind = "lossless_cst";

export type AnalysisTiming =
  | "before_selected_token"
  | "after_selected_token"
  | "final_source";

/** Query-string values accepted by the parser-analysis routes. */
export type ParserAnalysisTimingQuery = "before" | "after" | "final_source";

export type AnalysisCompleteness =
  | "complete"
  | "incomplete_prefix"
  | "invalid_prefix"
  | "empty";

export type SourceProvenance =
  | "final_generated_source"
  | "derived_from_recorded_selected_tokens";

export type CstNodeKind = "rule" | "terminal";

export type LosslessSegmentKind =
  | "terminal"
  | "whitespace"
  | "line_comment"
  | "block_comment"
  | "unparsed";

export interface LosslessCstNode {
  id: string;
  kind: CstNodeKind;
  name: string;
  /** Original Lark terminal type when kind=terminal (may differ from display name). */
  lark_terminal_type?: string | null;
  lexeme?: string | null;
  start_offset?: number | null;
  end_offset?: number | null;
  start_line?: number | null;
  start_column?: number | null;
  end_line?: number | null;
  end_column?: number | null;
  children: LosslessCstNode[];
  /** True when this subtree is explicitly partial / not a complete program CST. */
  is_partial: boolean;
}

export interface LosslessSourceSegment {
  id: string;
  kind: LosslessSegmentKind;
  terminal_name?: string | null;
  lark_terminal_type?: string | null;
  exact_text: string;
  start_offset: number;
  end_offset: number;
  start_line: number;
  start_column: number;
  end_line: number;
  end_column: number;
  cst_node_id?: string | null;
}

export interface LlmTokenSpan {
  step_index: number;
  recorded_step?: number | null;
  token_id?: number | null;
  exact_text: string;
  start_offset: number;
  end_offset: number;
  selected_at_current_step: boolean;
}

export interface LosslessParserAnalysisResponse {
  analysis_kind: AnalysisKind;
  analysis_schema_version: string;
  timing: AnalysisTiming;
  completeness: AnalysisCompleteness;

  source_text: string;
  source_sha256: string;
  source_provenance: SourceProvenance;
  source_character_count: number;
  source_utf8_byte_count: number;
  /** Offsets are Python str / Unicode code-point indices. */
  offset_unit: string;

  grammar_name: string;
  grammar_sha256: string;
  parser_engine: string;
  parser_version: string;
  parser_mode: string;
  keep_all_tokens: boolean;

  cst_root: LosslessCstNode | null;
  source_segments: LosslessSourceSegment[];
  llm_token_spans: LlmTokenSpan[];

  /** Existing structural / partial diagnostics (never replaces this lossless view). */
  structural_parser_analysis?: ParserAnalysis | null;

  consumed_prefix: string;
  invalid_suffix: string;
  consumed_char_offset: number;

  warnings: string[];

  /** Request echo for UI cache invalidation / stale response checks. */
  experiment_id: string;
  prompt_id?: string | null;
  step_index?: number | null;
}

/** Human-readable completeness titles. */
export function losslessCompletenessLabel(
  completeness: AnalysisCompleteness
): string {
  switch (completeness) {
    case "complete":
      return "Complete";
    case "incomplete_prefix":
      return "Incomplete prefix";
    case "invalid_prefix":
      return "Invalid prefix";
    case "empty":
      return "Empty";
    default:
      return String(completeness);
  }
}

/** Human-readable source provenance titles. */
export function losslessProvenanceLabel(
  provenance: SourceProvenance
): string {
  switch (provenance) {
    case "final_generated_source":
      return "Final generated source";
    case "derived_from_recorded_selected_tokens":
      return "Derived from recorded selected tokens";
    default:
      return String(provenance);
  }
}

/** Human-readable timing titles (response DTO values). */
export function losslessTimingLabel(timing: AnalysisTiming): string {
  switch (timing) {
    case "before_selected_token":
      return "Before selected token";
    case "after_selected_token":
      return "After selected token";
    case "final_source":
      return "Final source";
    default:
      return String(timing);
  }
}

/** Map UI Before/After toggle → API query param. */
export function timingQueryFromToggle(
  toggle: "before" | "after"
): ParserAnalysisTimingQuery {
  return toggle;
}

/** Whether response timing is after-selected (for highlighting selected token spans). */
export function isAfterSelectedTiming(timing: AnalysisTiming): boolean {
  return timing === "after_selected_token";
}
