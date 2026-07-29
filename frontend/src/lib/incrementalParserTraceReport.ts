/**
 * Incremental parser trace export — one block per decoding step.
 */

import { formatTokenForReport } from "@/lib/stepAnalysisReport";
import type { DecodingStep, ExperimentResult } from "@/types/decoding";

const DIVIDER = "=".repeat(60);

function formatIncrementalParserBlock(step: DecodingStep): string {
  const ips = step.incremental_parser_state;
  const prefix = ips?.prefix_output ?? step.context + step.selected_token;

  const lines: string[] = [
    DIVIDER,
    `STEP ${step.step} — selected token ${formatTokenForReport(step.selected_token)}`,
    DIVIDER,
    "",
    "Current output:",
    prefix,
    "",
    "SynCode masking active:",
    step.syncode_active || step.constraint_applied ? "yes" : "no",
    "",
  ];

  if (!ips?.available) {
    lines.push(
      "Incremental parser state: unavailable",
      "(re-run generation to populate incremental parser snapshots)",
      "",
    );
    return lines.join("\n");
  }

  lines.push(
    DIVIDER,
    "INCREMENTAL PARSER STATE",
    DIVIDER,
    "",
    `Prefix status:                 ${ips.prefix_parse_status || "N/A"}`,
    `Accepts end ($END):            ${ips.parser_accepts_end ? "yes" : "no"}`,
    `Expected next terminals:       ${(ips.expected_next_terminals ?? []).join(", ") || "N/A"}`,
    `Accepted next terminals:       ${(ips.accepted_next_terminals ?? []).join(", ") || "N/A"}`,
    `Likely grammar context:        ${ips.likely_grammar_context || "N/A"}`,
    `Selected token interpretation: ${ips.selected_token_interpretation || "N/A"}`,
    "",
    "Likely grammar path:",
    ips.likely_grammar_path || "(not inferred)",
    "",
    "Research conclusion:",
    ips.likely_parser_interpretation || "(none)",
    "",
  );

  if (ips.prefix_parse_status === "complete_parse" && ips.parse_tree_text) {
    lines.push(
      DIVIDER,
      "FULL PARSER TREE (prefix complete)",
      DIVIDER,
      "",
      ips.parse_tree_text,
      "",
    );
  } else if (ips.partial_parse_view) {
    lines.push(
      "Partial parse / parser stack:",
      ips.partial_parse_view,
      "",
    );
  }

  if (ips.prefix_parse_status === "invalid_prefix" && ips.parser_error_message) {
    lines.push(
      `Parser error (${ips.parser_error_type}):`,
      ips.parser_error_message,
      "",
    );
  }

  return lines.join("\n");
}

/** Format the full incremental parser trace for all steps. */
export function formatIncrementalParserTrace(experiment: ExperimentResult): string {
  const header = [
    DIVIDER,
    "VERILOG INCREMENTAL PARSER TRACE",
    DIVIDER,
    "",
    `Run ID:     ${experiment.experiment_id}`,
    `Mode:       ${experiment.mode}`,
    `Model:      ${experiment.model_name}`,
    `Grammar:    ${experiment.grammar_name ?? "verilog"}`,
    `Parser:     ${experiment.parser_name ?? "lalr"}`,
    `Steps:      ${experiment.total_steps}`,
    "",
    "Each step shows the Lark parser state after context + selected_token.",
    "Incomplete prefixes show partial stack view — not a fake full tree.",
    "",
  ].join("\n");

  const blocks = experiment.steps.map((s) => formatIncrementalParserBlock(s));
  return [header, ...blocks].join("\n");
}

export function incrementalParserTraceFilename(experimentId: string): string {
  return `syncode_verilog_incremental_parser_trace_${experimentId}.txt`;
}

export function downloadIncrementalParserTrace(experiment: ExperimentResult): void {
  const content = formatIncrementalParserTrace(experiment);
  const filename = incrementalParserTraceFilename(experiment.experiment_id);
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export { formatIncrementalParserBlock };
