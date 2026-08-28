"use client";

/**
 * Parser tree / analysis panel for live experiments.
 *
 * Prefers Phase 3B structured ParserAnalysisViewer when available.
 * Falls back to legacy parse_tree_* evidence for older stored runs.
 */

import { useCallback, useState } from "react";

import { Button } from "@/components/ui/Button";
import { ParserAnalysisViewer } from "@/components/visualization/ParserAnalysisViewer";
import {
  downloadParserTreeReport,
  formatParserTreeReport,
  parserTreeFilename,
} from "@/lib/parserTreeReport";
import type { ExperimentResult } from "@/types/decoding";
import { isParserAnalysisUnavailable } from "@/types/parserAnalysis";

interface Props {
  experiment: ExperimentResult;
}

function StatusDot({ ok }: { ok: boolean }) {
  return (
    <span
      className={`inline-block h-2 w-2 rounded-full ${ok ? "bg-[#3fb950]" : "bg-[#f85149]"}`}
    />
  );
}

/** Small key-value row in a diagnostic table. */
function DiagRow({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="whitespace-nowrap pr-2 text-[#8b949e]">{label}:</dt>
      <dd className="break-all text-[#c9d1d9]">{value || "—"}</dd>
    </>
  );
}

function LegacyParserEvidence({ experiment }: { experiment: ExperimentResult }) {
  const treeAvailable = experiment.parse_tree_available === true;
  const treeAttempted = experiment.parse_tree_available !== undefined;
  const pfc = experiment.parser_failure_context;
  const pfcAvailable = pfc?.available === true;

  return (
    <section className="flex flex-col gap-2 rounded-md border border-dashed border-surface-border bg-surface px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
          Legacy parser evidence
        </h3>
        <span className="font-mono text-[10px] text-[#484f58]">
          parse_tree_* compatibility fallback
        </span>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {[
          {
            label: "Tree available",
            value: treeAvailable ? "yes" : "no",
            ok: treeAvailable,
          },
          {
            label: "Final parse valid",
            value: experiment.final_parse_valid ? "yes" : "no",
            ok: experiment.final_parse_valid ?? false,
          },
          { label: "Grammar", value: experiment.grammar_name ?? "verilog", ok: true },
          { label: "Parser", value: experiment.parser_name ?? "lalr", ok: true },
        ].map(({ label, value, ok }) => (
          <div
            key={label}
            className="rounded border border-[#30363d] bg-[#0d1117] px-2 py-1.5"
          >
            <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
              {label}
            </p>
            <p className="mt-0.5 flex items-center gap-1.5 font-mono text-xs font-semibold text-[#e6edf3]">
              <StatusDot ok={ok} />
              {value}
            </p>
          </div>
        ))}
      </div>

      {treeAvailable ? (
        <div className="max-h-64 overflow-auto rounded border border-[#30363d] bg-[#0d1117] p-2.5">
          <pre className="whitespace-pre font-mono text-[11px] leading-relaxed text-[#3fb950]">
            {experiment.parse_tree_text || "(empty tree)"}
          </pre>
        </div>
      ) : treeAttempted && experiment.parse_tree_error_type ? (
        <div className="flex flex-col gap-2">
          <div className="rounded border border-[#f85149]/30 bg-[#f85149]/5 p-2.5">
            <p className="mb-1.5 text-[11px] font-semibold text-[#f85149]">
              PARSER ERROR — TREE UNAVAILABLE
            </p>
            <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5 font-mono text-[11px]">
              <DiagRow label="Error type" value={experiment.parse_tree_error_type ?? ""} />
              <DiagRow label="Message" value={experiment.parse_tree_error_message ?? ""} />
              <DiagRow
                label="Line"
                value={
                  experiment.parse_tree_error_line != null
                    ? String(experiment.parse_tree_error_line)
                    : "N/A"
                }
              />
              <DiagRow
                label="Column"
                value={
                  experiment.parse_tree_error_column != null
                    ? String(experiment.parse_tree_error_column)
                    : "N/A"
                }
              />
              <DiagRow
                label="Unexpected token"
                value={experiment.parse_tree_unexpected_token ?? "N/A"}
              />
              <DiagRow
                label="Expected terminals"
                value={(experiment.parse_tree_expected_terminals ?? []).join(", ") || "N/A"}
              />
              <DiagRow
                label="Previous token"
                value={experiment.parse_tree_previous_token ?? "N/A"}
              />
            </dl>
          </div>

          {pfcAvailable && pfc && (
            <div className="rounded border border-[#d29922]/30 bg-[#d29922]/5 p-2.5">
              <p className="mb-1.5 text-[11px] font-semibold text-[#d29922]">
                PARSER FAILURE CONTEXT
              </p>
              {pfc.prefix_before_error && (
                <div className="mb-2">
                  <p className="mb-0.5 text-[10px] uppercase tracking-wider text-[#484f58]">
                    Generated output near parser failure
                  </p>
                  <div className="overflow-x-auto rounded border border-[#30363d] bg-[#0d1117] p-2">
                    <pre className="font-mono text-[11px] leading-relaxed text-[#c9d1d9]">
                      {pfc.prefix_before_error}
                      {pfc.caret_line ? `\n        ${pfc.caret_line}` : ""}
                    </pre>
                  </div>
                </div>
              )}
              {pfc.likely_parser_state_summary && (
                <pre className="mb-2 whitespace-pre-wrap font-mono text-[11px] text-[#c9d1d9]">
                  {pfc.likely_parser_state_summary}
                </pre>
              )}
              {pfc.likely_interpretation && (
                <p className="text-[11px] text-[#c9d1d9]">{pfc.likely_interpretation}</p>
              )}
            </div>
          )}
        </div>
      ) : (
        <p className="text-[11px] text-[#484f58]">
          Legacy parser tree unavailable — re-run generation to populate parse
          tree data.
        </p>
      )}
    </section>
  );
}

export function ParserTreeExportPanel({ experiment }: Props) {
  const [copyStatus, setCopyStatus] = useState<"idle" | "ok" | "err">("idle");
  const hasStructured = !isParserAnalysisUnavailable(experiment.parser_analysis);
  const hasLegacy =
    experiment.parse_tree_available !== undefined ||
    Boolean(experiment.parse_tree_text) ||
    Boolean(experiment.parse_tree_error_type);

  const handleCopy = useCallback(async () => {
    const content = formatParserTreeReport(experiment);
    try {
      await navigator.clipboard.writeText(content);
      setCopyStatus("ok");
      window.setTimeout(() => setCopyStatus("idle"), 2000);
    } catch {
      setCopyStatus("err");
      window.setTimeout(() => setCopyStatus("idle"), 2500);
    }
  }, [experiment]);

  const handleDownload = useCallback(() => {
    downloadParserTreeReport(experiment);
  }, [experiment]);

  return (
    <div className="flex flex-col gap-3">
      <ParserAnalysisViewer
        analysis={experiment.parser_analysis}
        context="live"
        title="Structured parser analysis"
      />

      {/* Always keep export controls + legacy fallback when useful */}
      <section className="flex flex-col gap-2 rounded-md border border-surface-border bg-surface-raised px-3 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
            Parser analysis export
          </h2>
          <span className="font-mono text-[10px] text-[#484f58]">
            grammar=verilog · parser=lalr
          </span>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" size="sm" onClick={handleCopy}>
            {copyStatus === "ok"
              ? "Copied!"
              : copyStatus === "err"
                ? "Copy failed"
                : "Copy parser analysis"}
          </Button>
          <Button variant="secondary" size="sm" onClick={handleDownload}>
            Download parser analysis (.txt)
          </Button>
        </div>
        <p className="font-mono text-[10px] text-[#484f58]">
          Export filename: {parserTreeFilename(experiment.experiment_id)}
        </p>
      </section>

      {!hasStructured && hasLegacy && (
        <LegacyParserEvidence experiment={experiment} />
      )}
    </div>
  );
}
