"use client";

/**
 * ImportedExperimentWorkspace — Phase 5A.1 compact research layout.
 *
 * Owns top-level workspace tab, selected prompt, and Token Trace step/playback
 * so evidence-subtab and workspace switches do not reset navigation state.
 * Prompt changes reset the active step safely to the first step.
 */

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";

import { MetaText } from "@/components/import/importedMeta";
import {
  ImportedTraceToolbar,
  ImportedTraceViewer,
  type TraceEvidenceTab,
} from "@/components/import/ImportedTraceViewer";
import { CodeViewer } from "@/components/output/CodeViewer";
import { Badge } from "@/components/ui/Badge";
import { ProvenanceValue } from "@/components/ui/ProvenanceValue";
import { ParserAnalysisViewer } from "@/components/visualization/ParserAnalysisViewer";
import { metaDictGet } from "@/lib/provenanceDisplay";
import { cn, formatDate } from "@/lib/utils";
import type { NormalizedExperiment, NormalizedPromptResult } from "@/types/normalized";
import { isUnavailable } from "@/types/provenance";

export type ImportedWorkspaceId =
  | "overview"
  | "output_parser"
  | "token_trace"
  | "metadata";

const WORKSPACES: { id: ImportedWorkspaceId; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "output_parser", label: "Output & Parser" },
  { id: "token_trace", label: "Token Trace" },
  { id: "metadata", label: "Metadata" },
];

function formatVerdictShort(prompt: NormalizedPromptResult): string {
  if (!isUnavailable(prompt.grammar_verdict) && prompt.grammar_verdict.value != null) {
    return String(prompt.grammar_verdict.value);
  }
  if (!isUnavailable(prompt.grammar_valid) && prompt.grammar_valid.value != null) {
    return prompt.grammar_valid.value ? "valid" : "invalid";
  }
  return "Unavailable";
}

function tokenCountLabel(prompt: NormalizedPromptResult): string {
  if (isUnavailable(prompt.generated_token_count)) return "Unavailable";
  const v = prompt.generated_token_count.value;
  if (v === null || v === undefined) return "Unavailable";
  return String(v);
}

interface Props {
  experiment: NormalizedExperiment;
}

export function ImportedExperimentWorkspace({ experiment }: Props) {
  const [promptIndex, setPromptIndex] = useState(0);
  const [workspace, setWorkspace] = useState<ImportedWorkspaceId>("overview");
  const [activeIndex, setActiveIndex] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [playIntervalMs, setPlayIntervalMs] = useState(1000);
  const [evidenceTab, setEvidenceTab] = useState<TraceEvidenceTab>("decision");

  const prompt = useMemo(() => {
    if (experiment.prompt_results.length === 0) return null;
    const idx = Math.min(promptIndex, experiment.prompt_results.length - 1);
    return experiment.prompt_results[idx];
  }, [experiment, promptIndex]);

  // Reset trace step when the selected problem changes (not on workspace tab).
  useEffect(() => {
    setActiveIndex(0);
    setIsPlaying(false);
    setEvidenceTab("decision");
  }, [prompt?.problem_id]);

  // Clamp if step count shrinks.
  useEffect(() => {
    if (!prompt || prompt.steps.length === 0) {
      setActiveIndex(0);
      return;
    }
    setActiveIndex((i) => Math.min(i, prompt.steps.length - 1));
  }, [prompt?.steps.length, prompt]);

  const llm = experiment.llm_metadata;
  const decoding = experiment.decoding_metadata;
  const runtime = experiment.runtime_metadata;
  const grammarMeta = experiment.grammar_metadata;
  const modelName = metaDictGet(llm, "model") ?? metaDictGet(llm, "model_name");
  const device = metaDictGet(llm, "device") ?? metaDictGet(llm, "input_device");
  const maxNew = metaDictGet(decoding, "max_new_tokens");
  const versions = metaDictGet(runtime, "versions");
  const grammarMatch = metaDictGet(grammarMeta, "grammar_match_status");

  const warningCount =
    experiment.import_warnings.length + (prompt?.warnings.length ?? 0);

  function selectPrompt(index: number) {
    setPromptIndex(index);
  }

  return (
    <div className="flex flex-col gap-3">
      {/* Breadcrumb (scrolls away) */}
      <div className="flex flex-wrap items-center gap-3">
        <Link
          href="/?source=imported"
          className="text-sm text-[#8b949e] hover:text-accent-blue"
        >
          ← Import
        </Link>
        <span className="text-[#484f58]">/</span>
        <span className="font-mono text-xs text-[#484f58]">
          {experiment.experiment_id}
        </span>
        <Badge variant="info">Imported</Badge>
        {experiment.created_at && (
          <span className="ml-auto text-xs text-[#484f58]">
            {formatDate(experiment.created_at)}
          </span>
        )}
      </div>

      {/* Sticky context + workspace tabs (+ trace toolbar when active) */}
      <div className="sticky top-0 z-20 -mx-4 border-b border-surface-border bg-surface/95 px-4 py-2 backdrop-blur-sm sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8">
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <h1 className="text-sm font-semibold text-[#e6edf3] sm:text-base">
              {experiment.experiment_name || "Imported experiment"}
            </h1>
            <Badge variant="neutral">{experiment.source_type}</Badge>
            <span className="font-mono text-[10px] text-[#484f58]">
              schema {experiment.schema_version}
            </span>

            {experiment.prompt_results.length > 0 && (
              <label className="ml-auto flex items-center gap-1.5 text-xs text-[#8b949e]">
                Prompt
                <select
                  value={promptIndex}
                  onChange={(e) => selectPrompt(Number(e.target.value))}
                  className="max-w-[14rem] rounded border border-surface-border bg-surface-raised px-2 py-1 text-xs text-[#e6edf3]"
                  aria-label="Select prompt result"
                >
                  {experiment.prompt_results.map((pr, i) => (
                    <option key={pr.problem_id} value={i}>
                      {pr.problem_id}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>

          {prompt && (
            <div className="flex flex-wrap items-center gap-2 text-xs">
              <span className="text-[#484f58]">Verdict</span>
              <Badge variant="neutral">{formatVerdictShort(prompt)}</Badge>
              <span className="text-[#484f58]">·</span>
              <span className="text-[#484f58]">Generated tokens</span>
              <span className="font-mono text-[#e6edf3]">
                {tokenCountLabel(prompt)}
              </span>
              <span className="text-[#484f58]">·</span>
              <span className="font-mono text-[#8b949e]">
                {prompt.steps.length} steps
              </span>
            </div>
          )}

          <div
            role="tablist"
            aria-label="Imported experiment workspaces"
            className="flex flex-wrap gap-1"
          >
            {WORKSPACES.map((w) => {
              const selected = workspace === w.id;
              return (
                <button
                  key={w.id}
                  type="button"
                  role="tab"
                  id={`workspace-tab-${w.id}`}
                  aria-selected={selected}
                  aria-controls={`workspace-panel-${w.id}`}
                  tabIndex={selected ? 0 : -1}
                  onClick={() => setWorkspace(w.id)}
                  className={cn(
                    "rounded px-2.5 py-1.5 text-xs font-medium transition-colors",
                    selected
                      ? "bg-accent-blue/15 text-accent-blue"
                      : "text-[#8b949e] hover:bg-surface-raised hover:text-[#e6edf3]"
                  )}
                >
                  {w.label}
                </button>
              );
            })}
          </div>

          {workspace === "token_trace" && prompt && prompt.steps.length > 0 && (
            <div className="border-t border-surface-border/80 pt-2">
              <ImportedTraceToolbar
                steps={prompt.steps}
                activeIndex={activeIndex}
                setActiveIndex={setActiveIndex}
                isPlaying={isPlaying}
                setIsPlaying={setIsPlaying}
                playIntervalMs={playIntervalMs}
                setPlayIntervalMs={setPlayIntervalMs}
              />
            </div>
          )}
        </div>
      </div>

      {/* Workspace panels */}
      {!prompt ? (
        <p className="text-sm text-[#8b949e]">
          No prompt results in this experiment.
        </p>
      ) : (
        <>
          {workspace === "overview" && (
            <div
              role="tabpanel"
              id="workspace-panel-overview"
              aria-labelledby="workspace-tab-overview"
              className="flex flex-col gap-4"
            >
              <section className="rounded-md border border-surface-border bg-surface-raised p-3">
                <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
                  Prompt verdict summary
                </h2>
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  <ProvenanceValue
                    label="Grammar valid"
                    value={prompt.grammar_valid}
                    grammarValid
                  />
                  <ProvenanceValue
                    label="Grammar verdict"
                    value={prompt.grammar_verdict}
                  />
                  <ProvenanceValue
                    label="Recomputed grammar verdict"
                    value={prompt.recomputed_grammar_verdict}
                  />
                  <ProvenanceValue
                    label="Termination"
                    value={prompt.termination_reason}
                  />
                  <ProvenanceValue
                    label="Generated tokens"
                    value={prompt.generated_token_count}
                  />
                  <ProvenanceValue label="Token limit" value={prompt.token_limit} />
                  <ProvenanceValue
                    label="Reconstruction match"
                    value={prompt.reconstruction_matches_authoritative}
                  />
                  <ProvenanceValue label="Parse error" value={prompt.parse_error} />
                  <ProvenanceValue
                    label="Recomputed parse error"
                    value={prompt.recomputed_parse_error}
                  />
                </div>
              </section>

              <section className="rounded-md border border-surface-border bg-surface-raised p-3">
                <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
                  Model &amp; runtime summary
                </h2>
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
                      Model
                    </p>
                    <p className="mt-0.5">
                      {isUnavailable(llm) ? (
                        <span className="text-[#484f58]">Unavailable</span>
                      ) : (
                        <MetaText value={modelName} />
                      )}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
                      Device
                    </p>
                    <p className="mt-0.5">
                      {isUnavailable(llm) || device === undefined ? (
                        <span className="text-[#484f58]">Unavailable</span>
                      ) : (
                        <MetaText value={device} />
                      )}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
                      Decoding limit
                    </p>
                    <p className="mt-0.5">
                      {isUnavailable(decoding) || maxNew === undefined ? (
                        <span className="text-[#484f58]">Unavailable</span>
                      ) : (
                        <MetaText value={maxNew} />
                      )}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
                      Grammar match
                    </p>
                    <p className="mt-0.5 font-mono text-sm text-[#e6edf3]">
                      {isUnavailable(grammarMeta)
                        ? "Unavailable"
                        : typeof grammarMatch === "string"
                          ? grammarMatch
                          : "unknown"}
                    </p>
                  </div>
                </div>
              </section>

              <details className="rounded-md border border-amber-500/30 bg-amber-900/10 px-3 py-2">
                <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wider text-amber-200/80">
                  Warnings ({warningCount})
                </summary>
                <div className="mt-2 flex flex-col gap-3">
                  {experiment.import_warnings.length > 0 && (
                    <div>
                      <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
                        Experiment warnings
                      </p>
                      <ul className="mt-1 list-disc space-y-1 pl-5 text-xs text-[#e6edf3]">
                        {experiment.import_warnings.map((w, i) => (
                          <li key={`e-${i}-${w.slice(0, 24)}`}>{w}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {prompt.warnings.length > 0 && (
                    <div>
                      <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
                        Prompt warnings
                      </p>
                      <ul className="mt-1 list-disc space-y-1 pl-5 text-xs text-[#e6edf3]">
                        {prompt.warnings.map((w, i) => (
                          <li key={`p-${i}-${w.slice(0, 24)}`}>{w}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {warningCount === 0 && (
                    <p className="text-xs text-[#8b949e]">No warnings recorded.</p>
                  )}
                </div>
              </details>
            </div>
          )}

          {workspace === "output_parser" && (
            <div
              role="tabpanel"
              id="workspace-panel-output_parser"
              aria-labelledby="workspace-tab-output_parser"
              className="grid min-h-0 gap-3 lg:grid-cols-2 lg:items-stretch"
            >
              <section className="flex min-h-[min(50vh,28rem)] flex-col gap-1.5 rounded-md border border-surface-border bg-surface-raised p-3 lg:min-h-[min(70vh,40rem)]">
                <div className="flex shrink-0 items-center gap-2">
                  <h2 className="text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
                    Authoritative generated Verilog
                  </h2>
                  {!isUnavailable(prompt.generated_output) && (
                    <Badge variant="valid">Recorded</Badge>
                  )}
                </div>
                {isUnavailable(prompt.generated_output) ||
                prompt.generated_output.value === null ? (
                  <div className="flex-1 rounded border border-surface-border px-4 py-6 text-sm text-[#484f58]">
                    Unavailable
                  </div>
                ) : (
                  <div className="min-h-0 flex-1 overflow-hidden">
                    <CodeViewer
                      code={prompt.generated_output.value}
                      className="h-full max-h-full min-h-0"
                    />
                  </div>
                )}
              </section>

              <section className="flex min-h-[min(50vh,28rem)] flex-col overflow-hidden rounded-md border border-surface-border lg:min-h-[min(70vh,40rem)]">
                <div className="min-h-0 flex-1 overflow-y-auto">
                  <ParserAnalysisViewer
                    key={prompt.problem_id}
                    analysis={
                      isUnavailable(prompt.parser_analysis)
                        ? null
                        : prompt.parser_analysis?.value ?? null
                    }
                    context="imported"
                    title="Structured parser analysis"
                    compactDiagnostics
                    className="h-full rounded-none border-0"
                  />
                </div>
              </section>
            </div>
          )}

          {workspace === "token_trace" && (
            <div
              role="tabpanel"
              id="workspace-panel-token_trace"
              aria-labelledby="workspace-tab-token_trace"
            >
              <ImportedTraceViewer
                prompt={prompt}
                activeIndex={activeIndex}
                onActiveIndexChange={setActiveIndex}
                isPlaying={isPlaying}
                onPlayingChange={setIsPlaying}
                playIntervalMs={playIntervalMs}
                onPlayIntervalChange={setPlayIntervalMs}
                evidenceTab={evidenceTab}
                onEvidenceTabChange={setEvidenceTab}
                showToolbar={false}
              />
            </div>
          )}

          {workspace === "metadata" && (
            <div
              role="tabpanel"
              id="workspace-panel-metadata"
              aria-labelledby="workspace-tab-metadata"
              className="flex flex-col gap-4"
            >
              <section className="rounded-md border border-surface-border bg-surface-raised p-3">
                <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
                  Experiment schema &amp; runtime
                </h2>
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
                      Experiment id
                    </p>
                    <p className="mt-0.5 break-all font-mono text-xs text-[#e6edf3]">
                      {experiment.experiment_id}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
                      Schema version
                    </p>
                    <p className="mt-0.5 font-mono text-xs text-[#e6edf3]">
                      {experiment.schema_version}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
                      Source type
                    </p>
                    <p className="mt-0.5 font-mono text-xs text-[#e6edf3]">
                      {experiment.source_type}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
                      Model
                    </p>
                    <p className="mt-0.5">
                      {isUnavailable(llm) ? (
                        <span className="text-[#484f58]">Unavailable</span>
                      ) : (
                        <MetaText value={modelName} />
                      )}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
                      Device
                    </p>
                    <p className="mt-0.5">
                      {isUnavailable(llm) || device === undefined ? (
                        <span className="text-[#484f58]">Unavailable</span>
                      ) : (
                        <MetaText value={device} />
                      )}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
                      Decoding limit
                    </p>
                    <p className="mt-0.5">
                      {isUnavailable(decoding) || maxNew === undefined ? (
                        <span className="text-[#484f58]">Unavailable</span>
                      ) : (
                        <MetaText value={maxNew} />
                      )}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
                      Grammar match
                    </p>
                    <p className="mt-0.5 font-mono text-sm text-[#e6edf3]">
                      {isUnavailable(grammarMeta)
                        ? "Unavailable"
                        : typeof grammarMatch === "string"
                          ? grammarMatch
                          : "unknown"}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
                      Recompute grammar verdict
                    </p>
                    <p className="mt-0.5 font-mono text-sm text-[#e6edf3]">
                      {isUnavailable(runtime)
                        ? "Unavailable"
                        : metaDictGet(runtime, "recompute_with_current_grammar") ===
                            true
                          ? "Requested"
                          : metaDictGet(
                                runtime,
                                "recompute_with_current_grammar"
                              ) === false
                            ? "Not requested"
                            : "Unavailable"}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
                      Recompute SynCode parser evidence
                    </p>
                    <p className="mt-0.5 font-mono text-sm text-[#e6edf3]">
                      {isUnavailable(runtime)
                        ? "Unavailable"
                        : metaDictGet(
                              runtime,
                              "recompute_syncode_parser_evidence"
                            ) === true
                          ? "Requested"
                          : metaDictGet(
                                runtime,
                                "recompute_syncode_parser_evidence"
                              ) === false
                            ? "Not requested"
                            : "Unavailable"}
                    </p>
                  </div>
                </div>
              </section>

              <section className="rounded-md border border-surface-border bg-surface-raised p-3">
                <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
                  Hashes &amp; evidence availability
                </h2>
                <div className="grid gap-3 sm:grid-cols-2">
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
                      Grammar SHA-256 (parser analysis)
                    </p>
                    <p className="mt-0.5 break-all font-mono text-xs text-[#e6edf3]">
                      {isUnavailable(prompt.parser_analysis) ||
                      !prompt.parser_analysis?.value?.grammar_sha256
                        ? "Unavailable"
                        : prompt.parser_analysis.value.grammar_sha256}
                    </p>
                  </div>
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
                      Prompt source files
                    </p>
                    {prompt.source_files.length === 0 ? (
                      <p className="mt-0.5 text-xs text-[#484f58]">None recorded</p>
                    ) : (
                      <ul className="mt-1 space-y-1 text-xs">
                        {prompt.source_files.map((f, i) => (
                          <li key={`${f.path}-${i}`}>
                            <MetaText value={f.path} />
                            {(f.category || f.role) && (
                              <span className="ml-2 text-[10px] text-[#484f58]">
                                {[f.category, f.role].filter(Boolean).join(" · ")}
                              </span>
                            )}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>
                <p className="mt-3 text-[10px] text-[#484f58]">
                  Per-step evidence availability is listed on the Token Trace →
                  Availability tab. Values remain Unavailable when not recorded
                  (never coerced to false, zero, or empty).
                </p>
              </section>

              <details className="rounded-md border border-surface-border bg-surface-raised p-3">
                <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
                  Advanced metadata &amp; host paths
                </summary>
                <div className="mt-3 flex flex-col gap-3">
                  <div>
                    <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
                      Library versions
                    </p>
                    <p className="mt-0.5">
                      {isUnavailable(runtime) || versions === undefined ? (
                        <span className="text-[#484f58]">Unavailable</span>
                      ) : (
                        <MetaText value={versions} />
                      )}
                    </p>
                  </div>
                  <div>
                    <p className="mb-1 text-[10px] uppercase tracking-wider text-[#484f58]">
                      Tokenizer metadata (full)
                    </p>
                    <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded border border-surface-border bg-surface p-2 font-mono text-[10px] text-[#c9d1d9]">
                      {isUnavailable(experiment.tokenizer_metadata)
                        ? "Unavailable"
                        : JSON.stringify(
                            experiment.tokenizer_metadata.value,
                            null,
                            2
                          )}
                    </pre>
                  </div>
                  <div>
                    <p className="mb-1 text-[10px] uppercase tracking-wider text-[#484f58]">
                      LLM metadata (full)
                    </p>
                    <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded border border-surface-border bg-surface p-2 font-mono text-[10px] text-[#c9d1d9]">
                      {isUnavailable(llm)
                        ? "Unavailable"
                        : JSON.stringify(llm.value, null, 2)}
                    </pre>
                  </div>
                  <div>
                    <p className="mb-1 text-[10px] uppercase tracking-wider text-[#484f58]">
                      Runtime metadata (full)
                    </p>
                    <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded border border-surface-border bg-surface p-2 font-mono text-[10px] text-[#c9d1d9]">
                      {isUnavailable(runtime)
                        ? "Unavailable"
                        : JSON.stringify(runtime.value, null, 2)}
                    </pre>
                  </div>
                  <div>
                    <p className="mb-1 text-[10px] uppercase tracking-wider text-[#484f58]">
                      Grammar metadata (full)
                    </p>
                    <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded border border-surface-border bg-surface p-2 font-mono text-[10px] text-[#c9d1d9]">
                      {isUnavailable(grammarMeta)
                        ? "Unavailable"
                        : JSON.stringify(grammarMeta.value, null, 2)}
                    </pre>
                  </div>
                  <div>
                    <p className="mb-1 text-[10px] uppercase tracking-wider text-[#484f58]">
                      Decoding metadata (full)
                    </p>
                    <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all rounded border border-surface-border bg-surface p-2 font-mono text-[10px] text-[#c9d1d9]">
                      {isUnavailable(decoding)
                        ? "Unavailable"
                        : JSON.stringify(decoding.value, null, 2)}
                    </pre>
                  </div>
                  <p className="text-[10px] text-[#484f58]">
                    Host filesystem paths in metadata are historical records
                    only — they are not opened by this UI.
                  </p>
                </div>
              </details>

              {experiment.import_warnings.length > 0 && (
                <div className="rounded-md border border-amber-500/30 bg-amber-900/10 px-3 py-2">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-amber-200/80">
                    Import warnings ({experiment.import_warnings.length})
                  </h3>
                  <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-[#e6edf3]">
                    {experiment.import_warnings.map((w, i) => (
                      <li key={`${i}-${w.slice(0, 24)}`}>{w}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
