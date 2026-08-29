"use client";

/**
 * Live experiment detail — Output Viewer + selected-step evidence workspace.
 * URL: /experiment/[id]
 *
 * Single authoritative selected-step index drives player, timeline, output-at-step,
 * token evidence, and SynCode accept sequences. Does not change stored results.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";

import { CodeViewer } from "@/components/output/CodeViewer";
import { OutputAtStep } from "@/components/output/OutputAtStep";
import { DecodingTimeline } from "@/components/visualization/DecodingTimeline";
import { ParserTreeExportPanel } from "@/components/visualization/ParserTreeExportPanel";
import { StepPlayer } from "@/components/visualization/StepPlayer";
import { StepViewer } from "@/components/visualization/StepViewer";
import { SyncodeParserEvidencePanel } from "@/components/visualization/SyncodeParserEvidencePanel";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { getExperiment } from "@/lib/api";
import { reconstructLiveOutputAtStep } from "@/lib/liveOutputAtStep";
import { formatDate, formatPct } from "@/lib/utils";
import type { ExperimentResult } from "@/types/decoding";
import { isStructurallyAvailable } from "@/types/syncodeParserEvidence";

function experimentUsedSyncode(experiment: ExperimentResult): boolean {
  if (experiment.constraint_active_during_generation === true) return true;
  if (experiment.mode === "syncode") return true;
  if ((experiment.syncode_active_steps ?? 0) > 0) return true;
  return false;
}

export default function ExperimentPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();

  const [experiment, setExperiment] = useState<ExperimentResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  /** Authoritative 0-based selected decoding step. */
  const [currentStep, setCurrentStep] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [playIntervalMs, setPlayIntervalMs] = useState(1000);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    setError(null);
    setExperiment(null);
    setCurrentStep(0);
    setIsPlaying(false);
    getExperiment(id)
      .then(setExperiment)
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [id]);

  const steps = useMemo(
    () => experiment?.steps ?? [],
    [experiment?.steps]
  );
  const stepCount = steps.length;

  // Clamp selection when experiment loads / steps change
  useEffect(() => {
    if (stepCount === 0) {
      setCurrentStep(0);
      return;
    }
    setCurrentStep((prev) => Math.max(0, Math.min(stepCount - 1, prev)));
  }, [stepCount, experiment?.experiment_id]);

  // Auto-pause at final step
  useEffect(() => {
    if (stepCount > 0 && currentStep >= stepCount - 1 && isPlaying) {
      setIsPlaying(false);
    }
  }, [currentStep, stepCount, isPlaying]);

  const selectStep = useCallback(
    (idx: number) => {
      if (stepCount <= 0) return;
      const clamped = Math.max(0, Math.min(stepCount - 1, idx));
      setIsPlaying(false);
      setCurrentStep(clamped);
    },
    [stepCount]
  );

  /** Playback advances without pausing (StepPlayer interval). */
  const onPlaybackStepChange = useCallback(
    (idx: number) => {
      if (stepCount <= 0) return;
      setCurrentStep(Math.max(0, Math.min(stepCount - 1, idx)));
    },
    [stepCount]
  );

  const stats = useMemo(() => {
    if (!experiment || experiment.steps.length === 0) return null;
    const entropies = experiment.steps
      .map((s) => s.entropy_before)
      .filter((e): e is number => e !== null);
    const avgEntropy =
      entropies.length > 0
        ? entropies.reduce((a, b) => a + b, 0) / entropies.length
        : null;
    const maxEntropy = entropies.length > 0 ? Math.max(...entropies) : null;
    const minEntropy = entropies.length > 0 ? Math.min(...entropies) : null;
    const avgTopProb =
      experiment.steps.length > 0
        ? experiment.steps.reduce(
            (sum, s) => sum + (s.top_tokens[0]?.probability ?? 0),
            0
          ) / experiment.steps.length
        : null;
    return { avgEntropy, maxEntropy, minEntropy, avgTopProb };
  }, [experiment]);

  const selectedStep = stepCount > 0 ? steps[currentStep] ?? null : null;
  const outputAtStep = useMemo(
    () => reconstructLiveOutputAtStep(steps, currentStep),
    [steps, currentStep]
  );

  const syncodeActive = experiment ? experimentUsedSyncode(experiment) : false;

  if (loading) {
    return (
      <div className="flex justify-center py-32">
        <Spinner size="lg" label="Loading experiment…" />
      </div>
    );
  }

  if (error || !experiment) {
    return (
      <div className="flex flex-col items-center gap-4 py-32 text-center">
        <p className="text-accent-red">{error ?? "Experiment not found."}</p>
        <Button variant="secondary" onClick={() => router.push("/")}>
          ← Back to Generate
        </Button>
      </div>
    );
  }

  return (
    <div className="flex min-w-0 flex-col gap-5 overflow-x-hidden">
      {/* Metadata bar */}
      <div className="flex flex-wrap items-center gap-3">
        <Link href="/" className="text-sm text-[#8b949e] hover:text-accent-blue">
          ← Generate
        </Link>
        <span className="text-[#484f58]">/</span>
        <span className="font-mono text-xs text-[#484f58]">
          {experiment.experiment_id.slice(0, 8)}…
        </span>
        <Badge variant="neutral">{experiment.mode}</Badge>
        <Badge variant="info">{experiment.model_name.split("/").pop()}</Badge>
        <span className="ml-auto text-xs text-[#484f58]">
          {formatDate(experiment.created_at)}
        </span>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => router.push(`/compare?a=${experiment.experiment_id}`)}
        >
          Open in Compare
        </Button>
      </div>

      {/* Prompt */}
      <div className="rounded-md border border-surface-border bg-surface-raised px-4 py-3">
        <p className="text-[10px] uppercase tracking-wider text-[#484f58]">Prompt</p>
        <p className="mt-1 font-mono text-sm text-[#8b949e] line-clamp-2">
          {experiment.prompt}
        </p>
      </div>

      {/* Stats strip */}
      {stats && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[
            { label: "Steps", value: experiment.total_steps },
            {
              label: "Avg entropy",
              value: stats.avgEntropy !== null ? stats.avgEntropy.toFixed(3) : "—",
              title: "Mean Shannon entropy H = -Σ p·log(p) across all steps",
            },
            {
              label: "Max entropy",
              value: stats.maxEntropy !== null ? stats.maxEntropy.toFixed(3) : "—",
              title: "Most uncertain decoding step",
            },
            {
              label: "Avg top-1 prob",
              value: stats.avgTopProb !== null ? formatPct(stats.avgTopProb, 1) : "—",
              title: "Mean probability of the most likely token (greedy confidence)",
            },
          ].map(({ label, value, title }) => (
            <div
              key={label}
              title={title}
              className="rounded-md border border-surface-border bg-surface-raised px-3 py-2"
            >
              <p className="text-[10px] uppercase tracking-wider text-[#484f58]">{label}</p>
              <p className="mt-0.5 font-mono text-lg font-semibold text-[#e6edf3]">
                {value}
              </p>
            </div>
          ))}
        </div>
      )}

      {/* Persistent step player */}
      <section className="flex flex-col gap-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
          Decoding Timeline — {experiment.total_steps} token
          {experiment.total_steps !== 1 ? "s" : ""}
        </h2>
        {stepCount > 0 ? (
          <StepPlayer
            totalSteps={stepCount}
            currentStep={currentStep}
            isPlaying={isPlaying}
            onStepChange={onPlaybackStepChange}
            onPlayPause={() => setIsPlaying((p) => !p)}
            playIntervalMs={playIntervalMs}
            onIntervalChange={setPlayIntervalMs}
            showJumpInput
            enableKeyboardNav
          />
        ) : (
          <p className="text-sm text-[#484f58]">No decoding steps recorded.</p>
        )}
        <p className="text-[10px] text-[#484f58]">
          Displayed steps are 1-based. Output at step N includes generated tokens
          through the selected token. SynCode accept-sequence evidence is recorded
          before the selected token when available.
        </p>
      </section>

      {/* Selected-step workspace */}
      {selectedStep && (
        <div className="grid min-w-0 gap-4 lg:grid-cols-2">
          <section className="flex min-h-0 min-w-0 flex-col rounded-md border border-surface-border bg-surface-raised p-3">
            <OutputAtStep
              prefixBefore={outputAtStep.prefixBefore}
              selectedToken={outputAtStep.selectedToken}
              selectedUnavailable={outputAtStep.selectedUnavailable}
              className="min-h-0"
            />
          </section>

          <section className="flex max-h-[min(70vh,36rem)] min-w-0 flex-col overflow-y-auto overflow-x-hidden rounded-md border border-surface-border bg-surface-raised p-3">
            <h3 className="mb-2 shrink-0 text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
              Token decision / evidence — step {currentStep + 1}
            </h3>
            <StepViewer
              step={selectedStep}
              mode={experiment.mode}
              hideSyncodeEvidence
            />
          </section>
        </div>
      )}

      {/* SynCode Accept Sequences (selected step) */}
      <section className="min-w-0">
        {!syncodeActive ? (
          <div className="flex flex-col gap-2 rounded-md border border-[#30363d] bg-[#0d1117] px-3 py-2">
            <h3 className="text-[10px] font-semibold uppercase tracking-wider text-[#8b949e]">
              SynCode Accept Sequences — Unavailable
            </h3>
            <p className="text-[11px] text-[#484f58]">
              SynCode was not active for this generation (open / unconstrained mode).
              Accept sequences are not recorded and are not recomputed here.
            </p>
          </div>
        ) : (
          <SyncodeParserEvidencePanel
            evidence={selectedStep?.syncode_parser_evidence}
            provenanceKind={
              isStructurallyAvailable(selectedStep?.syncode_parser_evidence)
                ? "recorded"
                : undefined
            }
            context="live"
            heading="SynCode Accept Sequences"
            legacyAcceptSequences={
              isStructurallyAvailable(selectedStep?.syncode_parser_evidence)
                ? undefined
                : selectedStep?.accept_sequences ?? []
            }
          />
        )}
      </section>

      {/* Final output + full timeline */}
      <div className="grid min-w-0 gap-5 lg:grid-cols-[1fr_1fr]">
        <section className="flex min-w-0 flex-col gap-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
            Generated Output
          </h2>
          <p className="text-[10px] text-[#484f58]">
            Authoritative final output for this experiment (unchanged by step
            selection).
          </p>
          <CodeViewer
            code={experiment.generated_code || "// (no output)"}
            className="min-h-48 max-h-[50vh]"
          />
        </section>

        <section className="flex min-w-0 flex-col gap-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
            All decoding steps
          </h2>
          <DecodingTimeline
            steps={experiment.steps}
            activeStepIndex={stepCount > 0 ? currentStep : null}
            onActiveStepChange={selectStep}
            className="flex max-h-[50vh] flex-col gap-1.5 overflow-y-auto overflow-x-hidden pr-1"
          />
        </section>
      </div>

      {/* Final parser analysis — Lark-derived, not SynCode accept sequences */}
      <section className="flex min-w-0 flex-col gap-2">
        <p className="rounded-md border border-surface-border bg-surface-raised px-3 py-2 text-[11px] leading-relaxed text-[#8b949e]">
          Final parser analysis below uses Lark / parser-derived expected next
          terminals for the complete generated output.{" "}
          <span className="text-[#e6edf3]">
            Parser-derived expected terminals are not SynCode accept sequences.
          </span>{" "}
          Per-step SynCode accept sequences (when recorded) are shown in the
          selected-step section above.
        </p>
        <ParserTreeExportPanel experiment={experiment} />
      </section>
    </div>
  );
}
