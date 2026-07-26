"use client";

/**
 * ParserTreeExportPanel — shows the Lark parse tree (or error diagnostics)
 * for the final generated output of the current run.
 *
 * The tree is built server-side using the same grammar/parser as final
 * validation, so parse_tree_available === final_parse_valid in most cases.
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

export function ParserTreeExportPanel({ experiment }: Props) {
  const [copyStatus, setCopyStatus] = useState<"idle" | "ok" | "err">("idle");

  // parse_tree_available is explicitly false (not undefined) when the backend
  // ran the parser and it failed; undefined means the field was never sent
  // (old run or mapping gap — show "unavailable" rather than "error").
  const treeAvailable = experiment.parse_tree_available === true;
  const treeAttempted = experiment.parse_tree_available !== undefined;
  const parseValid = experiment.final_parse_valid ?? false;

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

      {/* Preview — three cases:
           1. parse_tree_available === true  → show green tree
           2. parse_tree_available === false + error_type → show red error block
           3. parse_tree_available === undefined (field never arrived) → neutral notice */}
      {treeAvailable ? (
        <div className="max-h-64 overflow-auto rounded border border-[#30363d] bg-[#0d1117] p-2.5">
          <pre className="whitespace-pre font-mono text-[11px] leading-relaxed text-[#3fb950]">
            {experiment.parse_tree_text || "(empty tree)"}
          </pre>
        </div>
      ) : treeAttempted && experiment.parse_tree_error_type ? (
        <div className="rounded border border-[#f85149]/30 bg-[#f85149]/5 p-2.5">
          <p className="mb-1 text-[11px] font-semibold text-[#f85149]">
            Parser error — tree unavailable
          </p>
          <dl className="grid grid-cols-[max-content_1fr] gap-x-3 gap-y-0.5 font-mono text-[11px]">
            {[
              ["Error type", experiment.parse_tree_error_type],
              ["Message", experiment.parse_tree_error_message || "—"],
              [
                "Line",
                experiment.parse_tree_error_line
                  ? String(experiment.parse_tree_error_line)
                  : "N/A",
              ],
              [
                "Column",
                experiment.parse_tree_error_column
                  ? String(experiment.parse_tree_error_column)
                  : "N/A",
              ],
              [
                "Unexpected token",
                experiment.parse_tree_unexpected_token || "N/A",
              ],
              [
                "Expected terminals",
                (experiment.parse_tree_expected_terminals ?? []).join(", ") ||
                  "N/A",
              ],
              [
                "Previous token",
                experiment.parse_tree_previous_token || "N/A",
              ],
            ].map(([k, v]) => (
              <>
                <dt key={`k-${k}`} className="text-[#8b949e]">{k}:</dt>
                <dd key={`v-${k}`} className="break-all text-[#c9d1d9]">{v}</dd>
              </>
            ))}
          </dl>
        </div>
      ) : (
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
