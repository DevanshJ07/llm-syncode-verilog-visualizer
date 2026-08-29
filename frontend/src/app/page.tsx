"use client";

/**
 * Home page — Generate & Visualize / Import
 *
 * Live generation: POST /generate/jobs → poll status → navigate to detail.
 */

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";

import {
  ExperimentSourceSelector,
  type ExperimentSourceMode,
} from "@/components/import/ExperimentSourceSelector";
import { ImportExperimentPanel } from "@/components/import/ImportExperimentPanel";
import { ImportedExperimentList } from "@/components/import/ImportedExperimentList";
import { PromptForm } from "@/components/prompt/PromptForm";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Spinner } from "@/components/ui/Spinner";
import { useGeneration } from "@/hooks/useGeneration";
import { DEFAULT_MAX_NEW_TOKENS } from "@/lib/generationDefaults";
import type { GenerateRequest } from "@/types/decoding";

export default function HomePage() {
  return (
    <Suspense
      fallback={
        <div className="flex justify-center py-32">
          <Spinner size="lg" label="Loading…" />
        </div>
      }
    >
      <HomePageInner />
    </Suspense>
  );
}

function HomePageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const initialSource: ExperimentSourceMode =
    searchParams.get("source") === "imported" ? "imported" : "live";

  const [source, setSource] = useState<ExperimentSourceMode>(initialSource);
  const [importRefreshKey, setImportRefreshKey] = useState(0);
  const submitGuardRef = useRef(false);
  const navigatedForRef = useRef<string | null>(null);

  useEffect(() => {
    if (searchParams.get("source") === "imported") {
      setSource("imported");
    }
  }, [searchParams]);

  const {
    phase,
    jobId,
    statusMessage,
    error,
    experimentId,
    isBusy,
    generate,
    checkStatusAgain,
  } = useGeneration();

  useEffect(() => {
    if (!isBusy) submitGuardRef.current = false;
  }, [isBusy]);

  useEffect(() => {
    if (phase !== "completed" || !experimentId) return;
    if (navigatedForRef.current === experimentId) return;
    navigatedForRef.current = experimentId;
    router.push(`/experiment/${experimentId}`);
  }, [phase, experimentId, router]);

  const handleGenerate = useCallback(
    async (req: GenerateRequest) => {
      if (isBusy || submitGuardRef.current) return;
      submitGuardRef.current = true;
      await generate(req);
    },
    [generate, isBusy]
  );

  const sourceSelector = (
    <ExperimentSourceSelector
      value={source}
      onChange={(mode) => {
        setSource(mode);
      }}
      disabled={isBusy}
    />
  );

  if (source === "imported") {
    return (
      <div className="mx-auto flex max-w-3xl flex-col gap-6">
        <div>
          <h1 className="text-2xl font-bold text-[#e6edf3]">Import &amp; Browse</h1>
          <p className="mt-1 text-sm leading-relaxed text-[#8b949e]">
            Import a SynViz experiment result ZIP into the normalized store, then
            open stored experiments. Full token-step masking visualization for
            imports arrives in Phase 2B.2.
          </p>
        </div>
        {sourceSelector}
        <Card>
          <ImportExperimentPanel
            onImported={() => setImportRefreshKey((k) => k + 1)}
          />
        </Card>
        <ImportedExperimentList refreshKey={importRefreshKey} />
      </div>
    );
  }

  if (isBusy) {
    return (
      <div className="flex flex-col items-center gap-6 py-32">
        <Spinner
          size="lg"
          label={statusMessage || "Working on generation job…"}
        />
        <div className="max-w-md space-y-2 text-center text-xs text-[#484f58]">
          <p>
            Qwen2.5-Coder-1.5B-Instruct runs on CPU. First run may download
            weights (~3 GB). Long runs (up to {DEFAULT_MAX_NEW_TOKENS} tokens)
            can take several minutes.
          </p>
          {jobId && (
            <p className="font-mono text-[11px] text-[#8b949e]">
              job {jobId}
            </p>
          )}
          {phase === "status_unavailable" && (
            <>
              <p className="text-accent-red">{error}</p>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => checkStatusAgain()}
              >
                Check status again
              </Button>
            </>
          )}
        </div>
      </div>
    );
  }

  const showFailed = phase === "failed" && error;

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-6">
      <div>
        <h1 className="text-2xl font-bold text-[#e6edf3]">Generate &amp; Visualize</h1>
        <p className="mt-1 text-sm leading-relaxed text-[#8b949e]">
          {showFailed
            ? "The generation job ended with an error. Details are shown below — no placeholder output is shown."
            : "Qwen2.5-Coder generates Verilog token-by-token with full decoding traces. Toggle Syncode Verilog-grammar masking to compare raw vs constrained distributions. Generation runs as a background job; when it finishes you open the saved experiment detail page."}
        </p>
      </div>
      {sourceSelector}
      <Card>
        <PromptForm onSubmit={handleGenerate} isLoading={isBusy} error={error} />
      </Card>
      {!showFailed && (
        <div className="grid grid-cols-2 gap-3 text-xs text-[#484f58] sm:grid-cols-4">
          {[
            ["Model", "Qwen2.5-Coder"],
            ["Runtime", "CPU · fp32"],
            ["Decoding", "Job + poll"],
            ["Syncode", "Verilog grammar"],
          ].map(([k, v]) => (
            <div
              key={k}
              className="rounded-md border border-surface-border bg-surface-raised p-2"
            >
              <p className="text-[#484f58]">{k}</p>
              <p className="font-medium text-[#8b949e]">{v}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
