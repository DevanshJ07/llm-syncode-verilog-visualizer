/**
 * TypeScript mirrors of backend/app/models/parser_analysis.py (Phase 3A).
 * Keep in sync with the committed Pydantic schema.
 */

import type { ProvenanceInfo } from "@/types/provenance";

export type ParserAnalysisStatus =
  | "complete_valid"
  | "incomplete_prefix"
  | "invalid_input"
  | "unavailable";

export type ParserRepresentationKind =
  | "complete_parse_tree"
  | "partial_parse_forest"
  | "recovered_prefix_forest"
  | "none";

export type ParserNodeKind =
  | "rule"
  | "token"
  | "synthetic_root"
  | "recovery_marker"
  | "stack_value";

export interface ParserSourcePosition {
  line?: number | null;
  column?: number | null;
  start_pos?: number | null;
  end_pos?: number | null;
  end_line?: number | null;
  end_column?: number | null;
}

export interface ParserNode {
  id: string;
  kind: ParserNodeKind;
  label: string;
  token_value?: string | null;
  children: ParserNode[];
  position?: ParserSourcePosition | null;
}

export interface ParserAnalysis {
  status: ParserAnalysisStatus;
  representation_kind: ParserRepresentationKind;
  label: string;
  is_complete: boolean;
  is_partial: boolean;
  is_recovered: boolean;

  grammar_name: string;
  grammar_sha256: string;
  parser_name: string;
  parser_version: string;

  root: ParserNode | null;
  pretty_text: string;

  expected_next_terminals: string[];
  accepts_end: boolean;

  parsed_prefix: string;
  invalid_suffix: string;
  consumed_char_offset: number;

  error_offset?: number | null;
  error_line?: number | null;
  error_column?: number | null;
  error_type: string;
  error_message: string;
  unexpected_token_or_char: string;
  previous_token: string;

  warnings: string[];
  provenance: ProvenanceInfo;

  node_count: number;
  max_depth_seen: number;
  truncated: boolean;
  source_length: number;
  comment_handling: string;
}

/** Human-readable status titles for the UI header. */
export function parserAnalysisStatusTitle(status: ParserAnalysisStatus): string {
  switch (status) {
    case "complete_valid":
      return "Complete valid program";
    case "incomplete_prefix":
      return "Incomplete valid prefix";
    case "invalid_input":
      return "Invalid program — recovered prefix";
    case "unavailable":
    default:
      return "Parser analysis unavailable";
  }
}

/** Truthful representation captions (never call partial/recovered a “parse tree” alone). */
export function parserRepresentationCaption(
  analysis: ParserAnalysis
): { primary: string; notCompleteTree: boolean } {
  if (analysis.status === "complete_valid") {
    return {
      primary: analysis.label || "Complete Lark parse tree",
      notCompleteTree: false,
    };
  }
  if (analysis.status === "incomplete_prefix") {
    return {
      primary: analysis.label || "Partial parser stack — incomplete prefix",
      notCompleteTree: true,
    };
  }
  if (analysis.status === "invalid_input") {
    return {
      primary: analysis.label || "Recovered parser stack — valid prefix before error",
      notCompleteTree: true,
    };
  }
  return { primary: analysis.label || "Parser analysis unavailable", notCompleteTree: false };
}

export function isParserAnalysisUnavailable(
  analysis: ParserAnalysis | null | undefined
): boolean {
  if (!analysis) return true;
  return (
    analysis.status === "unavailable" || analysis.representation_kind === "none"
  );
}

/** Default unavailable payload matching backend defaults. */
export function unavailableParserAnalysis(): ParserAnalysis {
  return {
    status: "unavailable",
    representation_kind: "none",
    label: "Parser analysis unavailable",
    is_complete: false,
    is_partial: false,
    is_recovered: false,
    grammar_name: "verilog",
    grammar_sha256: "",
    parser_name: "lalr",
    parser_version: "",
    root: null,
    pretty_text: "",
    expected_next_terminals: [],
    accepts_end: false,
    parsed_prefix: "",
    invalid_suffix: "",
    consumed_char_offset: 0,
    error_offset: null,
    error_line: null,
    error_column: null,
    error_type: "",
    error_message: "",
    unexpected_token_or_char: "",
    previous_token: "",
    warnings: [],
    provenance: {
      kind: "unavailable",
      method: "parser analysis not run",
    },
    node_count: 0,
    max_depth_seen: 0,
    truncated: false,
    source_length: 0,
    comment_handling:
      "canonical grammar %ignore LINE_COMMENT, BLOCK_COMMENT, and WS; " +
      "source is not comment-stripped for position fidelity",
  };
}
