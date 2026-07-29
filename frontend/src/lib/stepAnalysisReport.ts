/**
 * Step analysis report formatting for SynCode decoding evidence export.
 */

import { formatIncrementalParserBlock } from "@/lib/incrementalParserTraceReport";
import { formatPct } from "@/lib/utils";
import type { DecodingStep, ExperimentResult } from "@/types/decoding";

/** JSON-style quoted token string for readable reports (whitespace visible). */
export function formatTokenForReport(token: string): string {
  return JSON.stringify(token);
}

function formatNum(value: number | null | undefined, decimals = 4): string {
  if (value === null || value === undefined) return "N/A";
  return value.toFixed(decimals);
}

function formatPctOrNa(value: number | null | undefined, decimals = 4): string {
  if (value === null || value === undefined) return "N/A";
  return formatPct(value, decimals);
}

/** Pre-mask probability of the selected token when available (raw distribution). */
export function getSelectedPreMaskProb(step: DecodingStep): number | null {
  const fromBefore = step.top_tokens_before_syncode.find(
    (tc) => tc.is_selected || tc.token_id === step.selected_token_id
  );
  if (fromBefore) return fromBefore.probability;

  const fromTopK = step.top_tokens.find(
    (t) => t.token_id === step.selected_token_id
  );
  if (fromTopK) return fromTopK.probability;

  return null;
}

function currentOutputAtStep(step: DecodingStep): string {
  return step.context + step.selected_token;
}

function syncodeStatusLabel(step: DecodingStep, mode?: string): string {
  if (mode !== "syncode" && !step.syncode_active && step.num_masked === 0) {
    return "inactive (raw mode)";
  }
  if (step.fallback_used) return "fallback (raw logits used)";
  if (step.syncode_active || step.constraint_applied) return "active";
  if (mode === "syncode") return "inactive";
  return "inactive";
}

function formatTopMaskedTokensSection(
  step: DecodingStep,
  mode?: string
): string {
  const lines: string[] = [];
  lines.push("Top masked tokens sorted by pre-mask probability:");

  if (mode !== "syncode" && !step.syncode_active && step.num_masked === 0) {
    lines.push("Masked-token list unavailable for this step.");
    return lines.join("\n");
  }

  const rows = [...(step.top_masked_tokens ?? [])].sort(
    (a, b) => b.pre_mask_prob - a.pre_mask_prob
  );

  if (rows.length === 0) {
    const totalMasked = step.masked_token_count ?? step.num_masked ?? 0;
    if (totalMasked === 0) {
      lines.push("(no tokens masked at this step)");
    } else if (!step.top_masked_tokens) {
      lines.push("Masked-token list unavailable for this step.");
    } else {
      lines.push("Masked-token list unavailable for this step.");
    }
    return lines.join("\n");
  }

  rows.forEach((row, idx) => {
    const status = row.status || "masked by SynCode";
    lines.push(
      `${idx + 1}. token: ${formatTokenForReport(row.token)} | id: ${row.token_id} | prob: ${formatPct(row.pre_mask_prob, 4)} | ${status}`
    );
  });

  return lines.join("\n");
}

/** Format a single decoding step as a plain-text analysis block. */
export function formatStepAnalysisBlock(
  step: DecodingStep,
  mode?: string
): string {
  const divider = "=".repeat(60);
  const preMaskProb = getSelectedPreMaskProb(step);
  const entropyBefore = step.entropy_before;
  const entropyAfter = step.entropy_after;
  const deltaEntropy =
    entropyBefore !== null &&
    entropyBefore !== undefined &&
    entropyAfter !== null &&
    entropyAfter !== undefined
      ? entropyAfter - entropyBefore
      : null;

  const maskedCount = step.masked_token_count ?? step.num_masked ?? 0;
  const validCount = step.valid_token_count ?? 0;
  const vocabSize = step.vocab_size ?? 0;

  const lines: string[] = [
    divider,
    `STEP ${step.step}`,
    divider,
    "",
    "Current output:",
    currentOutputAtStep(step),
    "",
    "Selected token:",
    formatTokenForReport(step.selected_token),
    `Token id: ${step.selected_token_id}`,
    `Pre-mask probability: ${formatPctOrNa(preMaskProb, 4)}`,
    "",
    "SynCode status:",
    syncodeStatusLabel(step, mode),
    `Vocab: ${vocabSize.toLocaleString()}`,
    `Valid tokens: ${validCount.toLocaleString()}`,
    `Masked tokens: ${maskedCount.toLocaleString()}`,
    `Masked percentage: ${step.masked_percentage != null ? `${step.masked_percentage.toFixed(2)}%` : "N/A"}`,
    `Mass removed: ${formatNum(step.probability_mass_removed, 6)}`,
    `Entropy before: ${formatNum(entropyBefore, 4)}`,
    `Entropy after: ${formatNum(entropyAfter, 4)}`,
    `Delta entropy: ${formatNum(deltaEntropy, 4)}`,
    "",
    formatTopMaskedTokensSection(step, mode),
    "",
  ];

  if (step.incremental_parser_state?.available) {
    lines.push(formatIncrementalParserBlock(step));
  }

  return lines.join("\n");
}

/** Format header metadata for a full run report. */
export function formatRunReportHeader(experiment: ExperimentResult): string {
  const treeAvail = experiment.parse_tree_available ? "yes" : "no";
  const lines = [
    "SynViz Verilog — SynCode Step Analysis Report",
    "=".repeat(60),
    `Run id:              ${experiment.experiment_id}`,
    `Mode:                ${experiment.mode}`,
    `Model:               ${experiment.model_name}`,
    `Prompt:              ${experiment.prompt}`,
    `Total steps:         ${experiment.total_steps}`,
    `Generated at:        ${experiment.created_at}`,
    `Parser tree available: ${treeAvail}`,
    `Parser tree export:  available separately as syncode_verilog_parser_tree_${experiment.experiment_id}.txt`,
    "",
  ];
  return lines.join("\n");
}

/** Format all steps in order for full-run TXT export. */
export function formatFullRunReport(experiment: ExperimentResult): string {
  const parts = [formatRunReportHeader(experiment)];
  for (const step of experiment.steps) {
    parts.push(formatStepAnalysisBlock(step, experiment.mode));
  }
  return parts.join("\n");
}

/** Trigger a browser download of plain-text content. */
export function downloadTextFile(
  content: string,
  filename: string
): void {
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function fullRunReportFilename(experimentId: string): string {
  return `syncode_verilog_step_analysis_${experimentId}.txt`;
}
