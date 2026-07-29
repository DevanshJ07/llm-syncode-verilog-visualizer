"use client";

/**
 * ParserTreeExportPanel — shows the Lark parse tree (or error diagnostics)
 * for the final generated output of the current run.
 *
 * Three states:
 *   1. parse_tree_available === true  → green tree preview
 *   2. parse_tree_available === false → PARSER ERROR block + PARSER FAILURE CONTEXT
 *   3. parse_tree_available === undefined → neutral "re-run" notice
 */

import { useCallback, useState } from "react";

import { Button } from "@/components/ui/Button";
import {
  downloadParserTreeReport,
  formatParserTreeReport,
  parserTreeFilename,
} from "@/lib/parserTreeReport";
import type { ExperimentResult } from "@/types/decoding";

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
      <dt className="text-[#8b949e] pr-2 whitespace-nowrap">{label}:</dt>
      <dd className="break-all text-[#c9d1d9]">{value || "—"}</dd>
    </>
  );
}

export function ParserTreeExportPanel({ experiment }: Props) {
  const [copyStatus, setCopyStatus] = useState<"idle" | "ok" | "err">("idle");

  const treeAvailable = experiment.parse_tree_available === true;
  const treeAttempted = experiment.parse_tree_available !== undefined;
  const parseValid = experiment.final_parse_valid ?? false;
  const pfc = experiment.parser_failure_context;
  const pfcAvailable = pfc?.available === true;

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
    <section className="flex flex-col gap-2 rounded-md border border-surface-border bg-surface-raised px-3 py-2">
      {/* Title */}
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
          Parser Tree Export
        </h2>
        <span className="font-mono text-[10px] text-[#484f58]">
          grammar=verilog · parser=lalr
        </span>
      </div>

      {/* Status grid */}
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {[
          {
            label: "Tree available",
            value: treeAvailable ? "yes" : "no",
            ok: treeAvailable,
          },
          {
            label: "Final parse valid",
            value: parseValid ? "yes" : "no",
            ok: parseValid,
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

      {/* Buttons */}
      <div className="flex flex-wrap gap-2">
        <Button variant="secondary" size="sm" onClick={handleCopy}>
          {copyStatus === "ok"
            ? "Copied!"
            : copyStatus === "err"
              ? "Copy failed"
              : "Copy parser tree"}
        </Button>
        <Button variant="secondary" size="sm" onClick={handleDownload}>
          Download parser tree (.txt)
        </Button>
      </div>

      {/* Content area */}
      {treeAvailable ? (
        /* ── Case 1: valid parse → show tree ── */
        <div className="max-h-64 overflow-auto rounded border border-[#30363d] bg-[#0d1117] p-2.5">
          <pre className="whitespace-pre font-mono text-[11px] leading-relaxed text-[#3fb950]">
            {experiment.parse_tree_text || "(empty tree)"}
          </pre>
        </div>
      ) : treeAttempted && experiment.parse_tree_error_type ? (
        /* ── Case 2: parse failed → PARSER ERROR + PARSER FAILURE CONTEXT ── */
        <div className="flex flex-col gap-2">
          {/* ── PARSER ERROR block ── */}
          <div className="rounded border border-[#f85149]/30 bg-[#f85149]/5 p-2.5">
            <p className="mb-1.5 text-[11px] font-semibold text-[#f85149]">
              PARSER ERROR — TREE UNAVAILABLE
            </p>
            <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5 font-mono text-[11px]">
              <DiagRow label="Error type" value={experiment.parse_tree_error_type ?? ""} />
              <DiagRow label="Message" value={experiment.parse_tree_error_message ?? ""} />
              <DiagRow
                label="Line"
                value={experiment.parse_tree_error_line ? String(experiment.parse_tree_error_line) : "N/A"}
              />
              <DiagRow
                label="Column"
                value={experiment.parse_tree_error_column ? String(experiment.parse_tree_error_column) : "N/A"}
              />
              <DiagRow label="Unexpected token" value={experiment.parse_tree_unexpected_token ?? "N/A"} />
              <DiagRow
                label="Expected terminals"
                value={(experiment.parse_tree_expected_terminals ?? []).join(", ") || "N/A"}
              />
              <DiagRow label="Previous token" value={experiment.parse_tree_previous_token ?? "N/A"} />
            </dl>
          </div>

          {/* ── PARSER FAILURE CONTEXT block ── */}
          {pfcAvailable && pfc && (
            <div className="rounded border border-[#d29922]/30 bg-[#d29922]/5 p-2.5">
              <p className="mb-1.5 text-[11px] font-semibold text-[#d29922]">
                PARSER FAILURE CONTEXT
              </p>

              {/* Source excerpt */}
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

              {/* Parser state summary */}
              {pfc.likely_parser_state_summary && (
                <div className="mb-2">
                  <p className="mb-0.5 text-[10px] uppercase tracking-wider text-[#484f58]">
                    Parser state at failure
                  </p>
                  <pre className="whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-[#c9d1d9]">
                    {pfc.likely_parser_state_summary}
                  </pre>
                </div>
              )}

              {/* Likely interpretation */}
              {pfc.likely_interpretation && (
                <div className="mb-2">
                  <p className="mb-0.5 text-[10px] uppercase tracking-wider text-[#484f58]">
                    Likely parser interpretation
                  </p>
                  <p className="text-[11px] text-[#c9d1d9]">{pfc.likely_interpretation}</p>
                </div>
              )}

              {/* Why no tree */}
              <div className="mt-1 rounded border border-[#30363d] bg-[#0d1117]/60 px-2.5 py-1.5">
                <p className="text-[10px] text-[#8b949e]">
                  <span className="font-semibold text-[#484f58]">
                    Why no parser tree:{" "}
                  </span>
                  The final output is not a complete valid derivation under the
                  Verilog grammar. Lark can only produce{" "}
                  <code className="font-mono">tree.pretty()</code> after a
                  successful parse. Re-run with a prompt that produces
                  valid old-style Verilog to see the tree.
                </p>
              </div>
            </div>
          )}
        </div>
      ) : (
        /* ── Case 3: fields never arrived ── */
        <p className="text-[11px] text-[#484f58]">
          Parser tree unavailable — re-run generation to populate parse tree data.
        </p>
      )}

      <p className="font-mono text-[10px] text-[#484f58]">
        Export filename: {parserTreeFilename(experiment.experiment_id)}
      </p>
    </section>
  );
}
