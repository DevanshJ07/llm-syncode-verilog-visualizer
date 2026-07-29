"use client";

/**
 * StepAnalysisExportPanel — preview and export decoding-step evidence
 * for the current generation run (copy step / download full TXT report).
 */

import { useCallback, useMemo, useState } from "react";

import { Button } from "@/components/ui/Button";
import {
  downloadIncrementalParserTrace,
  incrementalParserTraceFilename,
} from "@/lib/incrementalParserTraceReport";
import {
  downloadTextFile,
  formatFullRunReport,
  formatStepAnalysisBlock,
  fullRunReportFilename,
} from "@/lib/stepAnalysisReport";
import type { DecodingStep, ExperimentResult } from "@/types/decoding";

interface Props {
  experiment: ExperimentResult;
  step: DecodingStep | undefined;
  stepIndex: number;
}

export function StepAnalysisExportPanel({
  experiment,
  step,
  stepIndex,
}: Props) {
  const [copyStatus, setCopyStatus] = useState<"idle" | "ok" | "err">("idle");

  const previewText = useMemo(() => {
    if (!step) return "No step selected.";
    return formatStepAnalysisBlock(step, experiment.mode);
  }, [step, experiment.mode]);

  const handleCopyStep = useCallback(async () => {
    if (!step) return;
    try {
      await navigator.clipboard.writeText(
        formatStepAnalysisBlock(step, experiment.mode)
      );
      setCopyStatus("ok");
      window.setTimeout(() => setCopyStatus("idle"), 2000);
    } catch {
      setCopyStatus("err");
      window.setTimeout(() => setCopyStatus("idle"), 2500);
    }
  }, [step, experiment.mode]);

  const handleDownloadFull = useCallback(() => {
    const content = formatFullRunReport(experiment);
    downloadTextFile(content, fullRunReportFilename(experiment.experiment_id));
  }, [experiment]);

  const handleDownloadIncremental = useCallback(() => {
    downloadIncrementalParserTrace(experiment);
  }, [experiment]);

  return (
    <section className="flex flex-col gap-2 rounded-md border border-surface-border bg-surface-raised px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
          Step Analysis Export
        </h2>
        {step && (
          <span className="font-mono text-[10px] text-[#484f58]">
            preview — step {step.step}
          </span>
        )}
      </div>

      <p className="text-[11px] text-[#484f58]">
        Copy or download decoding evidence for SynCode research. Full report
        includes all {experiment.total_steps} steps using pre-mask top-50 masked
        tokens per step.
      </p>

      <div className="flex flex-wrap gap-2">
        <Button
          variant="secondary"
          size="sm"
          onClick={handleCopyStep}
          disabled={!step}
        >
          {copyStatus === "ok"
            ? "Copied!"
            : copyStatus === "err"
              ? "Copy failed"
              : "Copy current step analysis"}
        </Button>
        <Button variant="secondary" size="sm" onClick={handleDownloadFull}>
          Download full run report (.txt)
        </Button>
        <Button variant="secondary" size="sm" onClick={handleDownloadIncremental}>
          Download incremental parser trace (.txt)
        </Button>
      </div>

      <div className="max-h-48 overflow-auto rounded border border-[#30363d] bg-[#0d1117] p-2.5">
        <pre className="whitespace-pre-wrap break-words font-mono text-[11px] leading-relaxed text-[#c9d1d9]">
          {previewText}
        </pre>
      </div>

      <p className="font-mono text-[10px] text-[#484f58]">
        Step report: {fullRunReportFilename(experiment.experiment_id)}
        {" · "}parser trace:{" "}
        {incrementalParserTraceFilename(experiment.experiment_id)}
        {" · "}player step index {stepIndex + 1}
      </p>
    </section>
  );
}
