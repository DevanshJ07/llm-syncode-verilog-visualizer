"use client";

/**
 * Parser at Step — Checkpoint 2 tab/panel.
 *
 * Before/After toggle (default Before). Fetches on currentStep+timing change,
 * cancels obsolete AbortControllers, ignores stale responses, and never shows
 * a previous step's tree under a new step number. Timing is preserved across
 * step changes.
 */

import { useEffect, useId, useRef, useState } from "react";

import { useUiAppearance } from "@/components/ui/AppearanceContext";
import { Badge } from "@/components/ui/Badge";
import { Spinner } from "@/components/ui/Spinner";
import { LosslessParserAnalysisViewer } from "@/components/visualization/LosslessParserAnalysisViewer";
import {
  getImportedStepParserAnalysis,
  getLiveStepParserAnalysis,
} from "@/lib/api";
import { cn } from "@/lib/utils";
import type { UiAppearance } from "@/lib/researchAppearance";
import type { LosslessParserAnalysisResponse } from "@/types/losslessParserAnalysis";

export type ParserAtStepMode = "live" | "imported";

interface Props {
  experimentId: string;
  /** Zero-based decoding step index. */
  currentStep: number;
  mode: ParserAtStepMode;
  /** Required when mode=imported (problem_id). */
  promptId?: string;
  className?: string;
  appearance?: UiAppearance;
  /** When false, omit the outer section chrome (embedded in evidence tab). */
  showChrome?: boolean;
}

type TimingToggle = "before" | "after";

export function ParserAtStepPanel({
  experimentId,
  currentStep,
  mode,
  promptId,
  className,
  appearance: appearanceProp,
  showChrome = true,
}: Props) {
  const appearance = useUiAppearance(appearanceProp);
  const research = appearance === "research";
  const reactId = useId();

  const [timing, setTiming] = useState<TimingToggle>("before");
  const [analysis, setAnalysis] =
    useState<LosslessParserAnalysisResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  /** Step for which `analysis` / loading / error currently apply. */
  const [boundStep, setBoundStep] = useState<number | null>(null);
  const [boundTiming, setBoundTiming] = useState<TimingToggle | null>(null);

  const requestSeq = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!experimentId || currentStep < 0) return;
    if (mode === "imported" && !promptId) {
      setAnalysis(null);
      setError("Missing prompt id for imported parser analysis.");
      setLoading(false);
      setBoundStep(currentStep);
      setBoundTiming(timing);
      return;
    }

    // Invalidate previous step's tree immediately so it never appears under the
    // new step number while the next request is in flight.
    setAnalysis(null);
    setError(null);
    setLoading(true);
    setBoundStep(currentStep);
    setBoundTiming(timing);

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const seq = ++requestSeq.current;
    const stepAtRequest = currentStep;
    const timingAtRequest = timing;

    const fetchFn =
      mode === "live"
        ? () =>
            getLiveStepParserAnalysis(
              experimentId,
              stepAtRequest,
              timingAtRequest,
              controller.signal
            )
        : () =>
            getImportedStepParserAnalysis(
              experimentId,
              promptId!,
              stepAtRequest,
              timingAtRequest,
              controller.signal
            );

    fetchFn()
      .then((data) => {
        if (seq !== requestSeq.current) return;
        if (controller.signal.aborted) return;
        // Echo checks — ignore stale responses that don't match the request.
        if (
          data.step_index != null &&
          data.step_index !== stepAtRequest
        ) {
          return;
        }
        const expectedTiming =
          timingAtRequest === "before"
            ? "before_selected_token"
            : "after_selected_token";
        if (data.timing !== expectedTiming) {
          return;
        }
        setAnalysis(data);
        setError(null);
        setBoundStep(stepAtRequest);
        setBoundTiming(timingAtRequest);
      })
      .catch((err: unknown) => {
        if (seq !== requestSeq.current) return;
        if (controller.signal.aborted) return;
        const message =
          err instanceof Error ? err.message : String(err ?? "Request failed");
        // Aborts from superseded requests should not surface as errors.
        if (
          err instanceof Error &&
          (err.name === "AbortError" || /aborted/i.test(err.message))
        ) {
          return;
        }
        setAnalysis(null);
        setError(message);
        setBoundStep(stepAtRequest);
        setBoundTiming(timingAtRequest);
      })
      .finally(() => {
        if (seq !== requestSeq.current) return;
        setLoading(false);
      });

    return () => {
      controller.abort();
    };
  }, [experimentId, currentStep, timing, mode, promptId]);

  const headingClass = research
    ? "font-sans text-xs font-semibold uppercase tracking-wider text-[#a8b3c7]"
    : "text-xs font-semibold uppercase tracking-wider text-[#8b949e]";

  const mutedClass = research ? "text-[#94a3b8]" : "text-[#8b949e]";

  const toggleBtn = (value: TimingToggle, label: string) => {
    const selected = timing === value;
    return (
      <button
        key={value}
        type="button"
        role="radio"
        aria-checked={selected}
        onClick={() => setTiming(value)}
        className={cn(
          "rounded border px-2.5 py-1 text-[11px] font-medium transition-colors focus-visible:outline-none focus-visible:ring-2",
          research
            ? selected
              ? "border-blue-400/50 bg-blue-500/15 text-blue-200 focus-visible:ring-blue-400"
              : "border-[#334155] text-[#a8b3c7] hover:bg-[#172033] focus-visible:ring-blue-400"
            : selected
              ? "border-[#58a6ff]/50 bg-[#58a6ff]/15 text-[#58a6ff] focus-visible:ring-[#58a6ff]"
              : "border-surface-border text-[#8b949e] hover:bg-surface focus-visible:ring-[#58a6ff]"
        )}
      >
        {label}
      </button>
    );
  };

  const stepMatches =
    boundStep === currentStep && boundTiming === timing;
  const showAnalysis = stepMatches && analysis != null && !loading;
  const showLoading = loading || !stepMatches;
  const showError = stepMatches && error != null && !loading;

  const body = (
    <div className={cn("flex min-w-0 flex-col gap-3", className)}>
      <div className="flex flex-wrap items-center gap-2">
        {showChrome && (
          <h3 className={headingClass}>Parser at Step</h3>
        )}
        <Badge variant="neutral" appearance={appearance}>
          Step {currentStep + 1}
        </Badge>
        <div
          role="radiogroup"
          aria-label="Parser analysis timing"
          aria-describedby={`${reactId}-timing-hint`}
          className="ml-auto flex items-center gap-1"
        >
          {toggleBtn("before", "Before")}
          {toggleBtn("after", "After")}
        </div>
      </div>
      <p id={`${reactId}-timing-hint`} className={cn("text-[10px]", mutedClass)}>
        Before = source through tokens before the selected token (matches SynCode
        mask timing). After includes the selected token. Timing is preserved when
        changing steps.
      </p>

      {showLoading && (
        <div className="flex justify-center py-8">
          <Spinner
            size="md"
            label={`Analyzing step ${currentStep + 1} (${timing})…`}
          />
        </div>
      )}

      {showError && (
        <div
          className={cn(
            "rounded border px-3 py-2 text-sm",
            research
              ? "border-red-400/40 bg-red-500/10 text-red-200"
              : "border-[#f85149]/40 bg-[#f85149]/10 text-[#f85149]"
          )}
        >
          {error}
        </div>
      )}

      {showAnalysis && (
        <LosslessParserAnalysisViewer
          analysis={analysis}
          context={mode}
          appearance={appearance}
          displayStep={currentStep + 1}
          title="Lossless parser at step"
          className={
            research
              ? "rounded-none border-0 bg-transparent p-0 shadow-none"
              : "rounded-none border-0 bg-transparent p-0 shadow-none"
          }
        />
      )}

      {!showLoading && !showError && !showAnalysis && (
        <p className={cn("text-sm", mutedClass)}>
          No lossless analysis for this step.
        </p>
      )}
    </div>
  );

  if (!showChrome) {
    return body;
  }

  return (
    <section
      className={cn(
        "min-w-0 overflow-x-hidden rounded-md border p-3",
        research
          ? "border-[#334155] bg-[#111827]"
          : "border-surface-border bg-surface-raised"
      )}
    >
      {body}
    </section>
  );
}
