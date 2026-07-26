/**
 * Parser tree report formatting and export helpers.
 *
 * Uses the parse_tree_* fields returned in ExperimentResult / GenerateResponse.
 * Tree is built server-side from the same grammar as final validation.
 */

import type { ExperimentResult } from "@/types/decoding";

const DIVIDER = "=".repeat(60);

/** Format the full parser-tree TXT report for a generation run. */
export function formatParserTreeReport(experiment: ExperimentResult): string {
  const lines: string[] = [
    DIVIDER,
    "VERILOG PARSER TREE REPORT",
    DIVIDER,
    "",
    `Run ID:            ${experiment.experiment_id}`,
    `Model:             ${experiment.model_name}`,
    `Grammar:           ${experiment.grammar_name ?? "verilog"}`,
    `Parser:            ${experiment.parser_name ?? "lalr"}`,
    `Final parse valid: ${experiment.final_parse_valid ? "yes" : "no"}`,
    `Fallback occurred: ${experiment.fallback_occurred ? "yes" : "no"}`,
    `Constraint applied:${experiment.constraint_applied ? "yes" : "no"}`,
    `Mode:              ${experiment.mode}`,
    "",
    DIVIDER,
    "FINAL GENERATED OUTPUT",
    DIVIDER,
    "",
    experiment.generated_code || "(no generated output)",
    "",
  ];

  const treeAvailable = experiment.parse_tree_available === true;
  // parse_tree_available is explicitly false (not undefined) when the backend
  // ran the parser and it failed. When undefined the field never arrived.
  const treeAttempted = experiment.parse_tree_available !== undefined;

  if (treeAvailable) {
    lines.push(
      DIVIDER,
      "PARSER TREE",
      DIVIDER,
      "",
      experiment.parse_tree_text || "(empty tree)",
      "",
    );
  } else if (treeAttempted && experiment.parse_tree_error_type) {
    // Backend ran the parser and it threw an error.
    lines.push(
      DIVIDER,
      "PARSER ERROR",
      DIVIDER,
      "",
      `Error type:         ${experiment.parse_tree_error_type}`,
      `Message:            ${experiment.parse_tree_error_message || ""}`,
      `Line:               ${experiment.parse_tree_error_line || "N/A"}`,
      `Column:             ${experiment.parse_tree_error_column || "N/A"}`,
      `Unexpected token:   ${experiment.parse_tree_unexpected_token || "N/A"}`,
      `Expected terminals: ${(experiment.parse_tree_expected_terminals ?? []).join(", ") || "N/A"}`,
      `Previous token:     ${experiment.parse_tree_previous_token || "N/A"}`,
      "",
    );
  } else {
    // Fields were not returned by the backend (old run, or data not mapped).
    lines.push(
      DIVIDER,
      "PARSER TREE UNAVAILABLE",
      DIVIDER,
      "",
      "Reason: parser tree was not returned by the backend.",
      "Re-run generation to populate parse_tree fields.",
      "",
    );
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
