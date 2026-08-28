/**
 * Parser tree / analysis report formatting and export helpers.
 *
 * Prefers Phase 3A structured ``parser_analysis`` when available.
 * Falls back to legacy ``parse_tree_*`` fields for older stored experiments.
 * Never labels a partial or recovered forest as a complete parse tree.
 */

import type { ExperimentResult } from "@/types/decoding";
import {
  isParserAnalysisUnavailable,
  parserAnalysisStatusTitle,
  parserRepresentationCaption,
  type ParserAnalysis,
} from "@/types/parserAnalysis";
import { provenanceLabel } from "@/types/provenance";

const DIVIDER = "=".repeat(60);

function formatStructuredAnalysisSection(analysis: ParserAnalysis): string[] {
  const caption = parserRepresentationCaption(analysis);
  const lines: string[] = [
    DIVIDER,
    "STRUCTURED PARSER ANALYSIS",
    DIVIDER,
    "",
    `Status:              ${parserAnalysisStatusTitle(analysis.status)}`,
    `Representation kind: ${analysis.representation_kind}`,
    `Label:               ${caption.primary}`,
  ];
  if (caption.notCompleteTree) {
    lines.push("Note:                 This is not a complete parse tree.");
  }
  lines.push(
    `Provenance:          ${provenanceLabel(analysis.provenance?.kind ?? "unavailable")}`,
    `Method:              ${analysis.provenance?.method ?? ""}`,
    `Grammar:             ${analysis.grammar_name || "verilog"}`,
    `Grammar SHA-256:     ${analysis.grammar_sha256 || "N/A"}`,
    `Parser:              ${analysis.parser_name || "lalr"}`,
    `Parser version:      ${analysis.parser_version || "N/A"}`,
    `$END accepted:       ${analysis.accepts_end ? "yes" : "no"}`,
    `Node count:          ${analysis.node_count}`,
    `Max depth seen:      ${analysis.max_depth_seen}`,
    `Truncated:           ${analysis.truncated ? "yes" : "no"}`,
    `Consumed offset:     ${analysis.consumed_char_offset}`,
    `Error offset:        ${analysis.error_offset ?? "N/A"}`,
    `Error line/column:   ${analysis.error_line ?? "N/A"} / ${analysis.error_column ?? "N/A"}`,
    `Error type:          ${analysis.error_type || "N/A"}`,
    `Error message:       ${analysis.error_message || ""}`,
    `Unexpected:          ${analysis.unexpected_token_or_char || "N/A"}`,
    `Previous token:      ${analysis.previous_token || "N/A"}`,
    `Expected terminals:  ${
      analysis.expected_next_terminals.length
        ? analysis.expected_next_terminals.join(", ")
        : "None recorded"
    }`,
    "",
    "Expected terminals above are Lark / parser-derived — not SynCode accept sequences.",
    "",
  );

  if (analysis.warnings.length) {
    lines.push("Warnings:");
    for (const w of analysis.warnings) {
      lines.push(`  - ${w}`);
    }
    lines.push("");
  }

  lines.push(
    DIVIDER,
    "PARSED / RECOVERED PREFIX",
    DIVIDER,
    "",
    analysis.parsed_prefix.length ? analysis.parsed_prefix : "(empty)",
    "",
  );

  if (analysis.status === "invalid_input" || analysis.invalid_suffix.length > 0) {
    lines.push(
      DIVIDER,
      "INVALID SUFFIX",
      DIVIDER,
      "",
      analysis.invalid_suffix.length ? analysis.invalid_suffix : "(empty)",
      "",
    );
  } else if (analysis.status === "incomplete_prefix") {
    lines.push(
      "Invalid suffix: empty (incomplete prefix — end-of-input before completion).",
      "",
    );
  }

  if (analysis.pretty_text) {
    const sectionTitle =
      analysis.status === "complete_valid"
        ? "COMPLETE LARK PARSE TREE (pretty)"
        : analysis.status === "incomplete_prefix"
          ? "PARTIAL PARSER STACK (pretty) — NOT A COMPLETE PARSE TREE"
          : analysis.status === "invalid_input"
            ? "RECOVERED PREFIX FOREST (pretty) — NOT A COMPLETE PARSE TREE"
            : "PARSER REPRESENTATION (pretty)";
    lines.push(DIVIDER, sectionTitle, DIVIDER, "", analysis.pretty_text, "");
  }

  return lines;
}

function formatLegacyParseTreeSection(experiment: ExperimentResult): string[] {
  const lines: string[] = [
    DIVIDER,
    "LEGACY PARSER EVIDENCE",
    DIVIDER,
    "",
    "Structured parser_analysis was unavailable; showing legacy parse_tree_* fields.",
    "",
  ];

  const treeAvailable = experiment.parse_tree_available === true;
  const treeAttempted = experiment.parse_tree_available !== undefined;

  if (treeAvailable) {
    lines.push(
      DIVIDER,
      "LEGACY PARSER TREE TEXT",
      DIVIDER,
      "",
      experiment.parse_tree_text || "(empty tree)",
      "",
    );
  } else if (treeAttempted && experiment.parse_tree_error_type) {
    lines.push(
      DIVIDER,
      "LEGACY PARSER ERROR",
      DIVIDER,
      "",
      `Error type:         ${experiment.parse_tree_error_type}`,
      `Message:            ${experiment.parse_tree_error_message || ""}`,
      `Line:               ${experiment.parse_tree_error_line ?? "N/A"}`,
      `Column:             ${experiment.parse_tree_error_column ?? "N/A"}`,
      `Unexpected token:   ${experiment.parse_tree_unexpected_token || "N/A"}`,
      `Expected terminals: ${(experiment.parse_tree_expected_terminals ?? []).join(", ") || "N/A"}`,
      `Previous token:     ${experiment.parse_tree_previous_token || "N/A"}`,
      "",
    );

    const pfc = experiment.parser_failure_context;
    if (pfc?.available) {
      lines.push(DIVIDER, "PARSER FAILURE CONTEXT", DIVIDER, "");

      if (pfc.prefix_before_error) {
        lines.push("Output near failure:", pfc.prefix_before_error);
        if (pfc.caret_line) {
          lines.push(`        ${pfc.caret_line}`);
        }
        lines.push("");
      }

      if (pfc.likely_parser_state_summary) {
        lines.push("Parser state at failure:", pfc.likely_parser_state_summary, "");
      }

      if (pfc.likely_interpretation) {
        lines.push("Likely parser interpretation:", pfc.likely_interpretation, "");
      }

      lines.push(
        "Reason no complete parser tree was generated:",
        "The final output is not a complete valid derivation under the Verilog",
        "grammar. Lark can only produce tree.pretty() after a successful parse.",
        "",
      );
    }
  } else {
    lines.push(
      DIVIDER,
      "PARSER TREE UNAVAILABLE",
      DIVIDER,
      "",
      "Reason: parser tree was not returned by the backend.",
      "Re-run generation to populate parse_tree / parser_analysis fields.",
      "",
    );
  }

  return lines;
}

/** Format the full parser-tree TXT report for a generation run. */
export function formatParserTreeReport(experiment: ExperimentResult): string {
  const lines: string[] = [
    DIVIDER,
    "VERILOG PARSER ANALYSIS REPORT",
    DIVIDER,
    "",
    `Run ID:             ${experiment.experiment_id}`,
    `Model:              ${experiment.model_name}`,
    `Grammar:            ${experiment.grammar_name ?? "verilog"}`,
    `Parser:             ${experiment.parser_name ?? "lalr"}`,
    `Final parse valid:  ${experiment.final_parse_valid ? "yes" : "no"}`,
    `Constraint applied: ${experiment.constraint_applied ? "yes" : "no"}`,
    `Raw fallback used:  ${(experiment.syncode_fallback_steps ?? 0) > 0 ? "yes" : "no"}`,
    `Mode:               ${experiment.mode}`,
    "",
    DIVIDER,
    "FINAL GENERATED OUTPUT",
    DIVIDER,
    "",
    experiment.generated_code || "(no generated output)",
    "",
  ];

  const analysis = experiment.parser_analysis;
  if (!isParserAnalysisUnavailable(analysis)) {
    lines.push(...formatStructuredAnalysisSection(analysis!));
  } else {
    lines.push(...formatLegacyParseTreeSection(experiment));
  }

  return lines.join("\n");
}

/** Trigger a browser download of the parser tree TXT report. */
export function downloadParserTreeReport(experiment: ExperimentResult): void {
  const content = formatParserTreeReport(experiment);
  const filename = `syncode_verilog_parser_tree_${experiment.experiment_id}.txt`;
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function parserTreeFilename(experimentId: string): string {
  return `syncode_verilog_parser_tree_${experimentId}.txt`;
}
