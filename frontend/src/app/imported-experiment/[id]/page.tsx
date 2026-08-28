"use client";

/**
 * Imported experiment detail — Phase 2B.1 metadata + Phase 2B.2 trace viewer.
 * URL: /imported-experiment/[id]
 */

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";

import { ImportedTraceViewer } from "@/components/import/ImportedTraceViewer";
import { CodeViewer } from "@/components/output/CodeViewer";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ProvenanceValue } from "@/components/ui/ProvenanceValue";
import { Spinner } from "@/components/ui/Spinner";
import { ParserAnalysisViewer } from "@/components/visualization/ParserAnalysisViewer";
import { getImportedExperiment } from "@/lib/api";
import { metaDictGet } from "@/lib/provenanceDisplay";
import { formatDate } from "@/lib/utils";
import type { NormalizedExperiment } from "@/types/normalized";
import { isUnavailable } from "@/types/provenance";

function looksLikeWindowsPath(s: string): boolean {
  return /^[A-Za-z]:[\\/]/.test(s) || s.includes("\\Users\\") || s.includes("\\\\");
}

/** Never treat recorded host paths as openable filesystem targets. */
function MetaText({ value }: { value: unknown }) {
  if (value === null || value === undefined) {
    return <span className="text-[#484f58]">Unavailable</span>;
  }
  const text =
    typeof value === "string"
      ? value
      : typeof value === "number" || typeof value === "boolean"
        ? String(value)
        : JSON.stringify(value);
  if (typeof value === "string" && looksLikeWindowsPath(value)) {
    return (
      <span
        className="break-all font-mono text-xs text-[#8b949e]"
        title="Recorded historical host path — not opened"
      >
        {text}
        <span className="ml-2 text-[10px] text-[#484f58]">(path not opened)</span>
      </span>
    );
  }
  return <span className="break-all font-mono text-xs text-[#e6edf3]">{text}</span>;
}

export default function ImportedExperimentPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();

  const [experiment, setExperiment] = useState<NormalizedExperiment | null>(
    null
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [promptIndex, setPromptIndex] = useState(0);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    setError(null);
    getImportedExperiment(id)
      .then((exp) => {
        setExperiment(exp);
        setPromptIndex(0);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [id]);

  const prompt = useMemo(() => {
    if (!experiment || experiment.prompt_results.length === 0) return null;
    const idx = Math.min(promptIndex, experiment.prompt_results.length - 1);
    return experiment.prompt_results[idx];
  }, [experiment, promptIndex]);

  if (loading) {
    return (
      <div className="flex justify-center py-32">
        <Spinner size="lg" label="Loading imported experiment…" />
      </div>
    );
  }

  if (error || !experiment) {
    return (
      <div className="flex flex-col items-center gap-4 py-32 text-center">
        <p className="max-w-lg text-accent-red" role="alert">
          {error ?? "Imported experiment not found."}
        </p>
        <Button variant="secondary" onClick={() => router.push("/?source=imported")}>
          ← Back to Import
        </Button>
      </div>
    );
  }

  const llm = experiment.llm_metadata;
  const decoding = experiment.decoding_metadata;
  const runtime = experiment.runtime_metadata;
  const grammarMeta = experiment.grammar_metadata;
  const modelName = metaDictGet(llm, "model") ?? metaDictGet(llm, "model_name");
  const device =
    metaDictGet(llm, "device") ?? metaDictGet(llm, "input_device");
  const maxNew = metaDictGet(decoding, "max_new_tokens");
  const versions = metaDictGet(runtime, "versions");
  const grammarMatch = metaDictGet(grammarMeta, "grammar_match_status");

  return (
    <div className="flex flex-col gap-5">
      {/* Metadata bar */}
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

      <div>
        <h1 className="text-xl font-bold text-[#e6edf3]">
          {experiment.experiment_name || "Imported experiment"}
        </h1>
        <p className="mt-1 text-xs text-[#8b949e]">
          Schema {experiment.schema_version} · source_type={experiment.source_type}
        </p>
      </div>

      {/* Experiment-level metadata */}
      <Card>
        <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
          Model &amp; runtime
        </h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          <div>
            <p className="text-[10px] uppercase tracking-wider text-[#484f58]">Model</p>
            <p className="mt-0.5">
              {isUnavailable(llm) ? (
                <span className="text-[#484f58]">Unavailable</span>
              ) : (
                <MetaText value={modelName} />
              )}
            </p>
          </div>
          <div>
            <p className="text-[10px] uppercase tracking-wider text-[#484f58]">Device</p>
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
          <div className="sm:col-span-2">
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
            <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
              Recompute grammar verdict
            </p>
            <p className="mt-0.5 font-mono text-sm text-[#e6edf3]">
              {isUnavailable(runtime)
                ? "Unavailable"
                : metaDictGet(runtime, "recompute_with_current_grammar") === true
                  ? "Requested"
                  : metaDictGet(runtime, "recompute_with_current_grammar") === false
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
                : metaDictGet(runtime, "recompute_syncode_parser_evidence") === true
                  ? "Requested"
                  : metaDictGet(runtime, "recompute_syncode_parser_evidence") ===
                      false
                    ? "Not requested"
                    : "Unavailable"}
            </p>
          </div>
        </div>
      </Card>

      {experiment.import_warnings.length > 0 && (
        <div className="rounded-md border border-amber-500/30 bg-amber-900/10 px-4 py-3">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-amber-200/80">
            Experiment warnings ({experiment.import_warnings.length})
          </h2>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-[#e6edf3]">
            {experiment.import_warnings.map((w, i) => (
              <li key={`${i}-${w.slice(0, 24)}`}>{w}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Prompt selector */}
      {experiment.prompt_results.length > 1 && (
        <div className="flex flex-col gap-1.5">
          <label
            htmlFor="prompt-select"
            className="text-xs font-semibold uppercase tracking-wider text-[#8b949e]"
          >
            Prompt result
          </label>
          <select
            id="prompt-select"
            value={promptIndex}
            onChange={(e) => setPromptIndex(Number(e.target.value))}
            className="max-w-md rounded-md border border-surface-border bg-surface-raised px-3 py-2 text-sm text-[#e6edf3]"
          >
            {experiment.prompt_results.map((pr, i) => (
              <option key={pr.problem_id} value={i}>
                {pr.problem_id}
              </option>
            ))}
          </select>
        </div>
      )}

      {!prompt ? (
        <p className="text-sm text-[#8b949e]">No prompt results in this experiment.</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="text-sm font-semibold text-[#e6edf3]">{prompt.problem_id}</h2>
            <Badge variant="neutral">{prompt.steps.length} steps</Badge>
          </div>

          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            <ProvenanceValue
              label="Grammar valid"
              value={prompt.grammar_valid}
              grammarValid
            />
            <ProvenanceValue label="Grammar verdict" value={prompt.grammar_verdict} />
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

          {prompt.warnings.length > 0 && (
            <div className="rounded-md border border-amber-500/30 bg-amber-900/10 px-4 py-3">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-amber-200/80">
                Prompt warnings
              </h3>
              <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-[#e6edf3]">
                {prompt.warnings.map((w, i) => (
                  <li key={`${i}-${w.slice(0, 24)}`}>{w}</li>
                ))}
              </ul>
            </div>
          )}

          <section className="flex flex-col gap-1.5">
            <div className="flex items-center gap-2">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
                Authoritative generated Verilog
              </h3>
              {!isUnavailable(prompt.generated_output) && (
                <Badge variant="valid">Recorded</Badge>
              )}
            </div>
            {isUnavailable(prompt.generated_output) ||
            prompt.generated_output.value === null ? (
              <div className="rounded-md border border-surface-border px-4 py-6 text-sm text-[#484f58]">
                Unavailable
              </div>
            ) : (
              <CodeViewer
                code={prompt.generated_output.value}
                className="min-h-40 max-h-[50vh]"
              />
            )}
          </section>

          <section className="flex flex-col gap-2">
            <ParserAnalysisViewer
              key={prompt.problem_id}
              analysis={
                isUnavailable(prompt.parser_analysis)
                  ? null
                  : prompt.parser_analysis?.value ?? null
              }
              context="imported"
              title="Structured parser analysis"
            />
          </section>

          <section className="flex flex-col gap-2">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
              Token-step masking visualization
            </h3>
            <ImportedTraceViewer prompt={prompt} />
          </section>
        </>
      )}
    </div>
  );
}
