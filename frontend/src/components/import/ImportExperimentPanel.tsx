"use client";

import { useId, useRef, useState } from "react";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/Button";
import { postImportBundle } from "@/lib/api";

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

interface ImportExperimentPanelProps {
  onImported?: (experimentId: string) => void;
}

export function ImportExperimentPanel({ onImported }: ImportExperimentPanelProps) {
  const router = useRouter();
  const fileInputId = useId();
  const recomputeGrammarId = useId();
  const recomputeParserId = useId();
  const inFlightRef = useRef(false);

  const [file, setFile] = useState<File | null>(null);
  const [recomputeGrammar, setRecomputeGrammar] = useState(false);
  const [recomputeParserEvidence, setRecomputeParserEvidence] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successId, setSuccessId] = useState<string | null>(null);

  function handleFileChange(list: FileList | null) {
    setError(null);
    setSuccessId(null);
    const next = list?.[0] ?? null;
    if (!next) {
      setFile(null);
      return;
    }
    if (!next.name.toLowerCase().endsWith(".zip")) {
      setFile(null);
      setError("Please select a .zip experiment bundle. TAR archives are not supported.");
      return;
    }
    setFile(next);
  }

  async function handleImport() {
    if (!file || loading || inFlightRef.current) return;
    inFlightRef.current = true;
    setLoading(true);
    setError(null);
    setSuccessId(null);
    try {
      const result = await postImportBundle(file, {
        recomputeWithCurrentGrammar: recomputeGrammar,
        recomputeSyncodeParserEvidence: recomputeParserEvidence,
      });
      setSuccessId(result.experiment_id);
      onImported?.(result.experiment_id);
      router.push(`/imported-experiment/${result.experiment_id}`);
    } catch (err) {
      const message =
        err instanceof Error ? err.message : "Import failed unexpectedly.";
      // Never render as HTML — plain text only.
      setError(message);
    } finally {
      inFlightRef.current = false;
      setLoading(false);
    }
  }

  const waitingHint = recomputeParserEvidence
    ? "Importing and recomputing parser evidence. This may take several minutes."
    : recomputeGrammar
      ? "Importing and recomputing grammar verdict…"
      : "Uploading and normalizing…";

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h2 className="text-sm font-semibold text-[#e6edf3]">Import ZIP bundle</h2>
        <p className="mt-1 text-xs leading-relaxed text-[#8b949e]">
          Upload a SynViz experiment result ZIP (
          <span className="font-mono">results/&lt;experiment&gt;/</span>
          ). Only <strong className="font-medium text-[#e6edf3]">.zip</strong> is
          accepted — TAR / tar.gz are not supported in this phase.
        </p>
      </div>

      <div className="flex flex-col gap-2">
        <label htmlFor={fileInputId} className="text-xs font-medium text-[#8b949e]">
          Experiment ZIP file
        </label>
        <input
          id={fileInputId}
          type="file"
          accept=".zip,application/zip,application/x-zip-compressed"
          disabled={loading}
          onChange={(e) => handleFileChange(e.target.files)}
          className="block w-full text-sm text-[#8b949e] file:mr-3 file:rounded-md file:border-0 file:bg-surface-raised file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-[#e6edf3] hover:file:bg-[#21262d]"
        />
        {file && (
          <p className="font-mono text-xs text-[#8b949e]">
            Selected: <span className="text-[#e6edf3]">{file.name}</span>
            {" · "}
            {formatBytes(file.size)}
          </p>
        )}
      </div>

      <label
        htmlFor={recomputeGrammarId}
        className="flex cursor-pointer items-start gap-2 text-sm text-[#e6edf3]"
      >
        <input
          id={recomputeGrammarId}
          type="checkbox"
          checked={recomputeGrammar}
          disabled={loading}
          onChange={(e) => setRecomputeGrammar(e.target.checked)}
          className="mt-1"
        />
        <span>
          Recompute final grammar verdict with current canonical grammar
          <span className="mt-0.5 block text-xs text-[#8b949e]">
            Optional. Default off — preserves recorded grammar evidence only.
          </span>
        </span>
      </label>

      <label
        htmlFor={recomputeParserId}
        className="flex cursor-pointer items-start gap-2 text-sm text-[#e6edf3]"
      >
        <input
          id={recomputeParserId}
          type="checkbox"
          checked={recomputeParserEvidence}
          disabled={loading}
          onChange={(e) => setRecomputeParserEvidence(e.target.checked)}
          className="mt-1"
        />
        <span>
          Recompute SynCode parser evidence for each trace step
          <span className="mt-0.5 block text-xs text-[#8b949e]">
            Uses the current canonical grammar and SynCode incremental parser. It
            does not replay the original model tokenizer mask. Default off —
            independent of grammar-verdict recomputation.
          </span>
        </span>
      </label>

      <div className="flex items-center gap-3">
        <Button
          type="button"
          loading={loading}
          disabled={!file || loading}
          onClick={handleImport}
        >
          Import
        </Button>
        {loading && (
          <span className="text-xs text-[#8b949e]" role="status" aria-live="polite">
            {waitingHint}
          </span>
        )}
      </div>

      {error && (
        <div
          role="alert"
          className="rounded-md border border-accent-red/40 bg-red-900/10 px-3 py-2 text-sm text-accent-red"
        >
          {error}
        </div>
      )}

      {successId && !error && (
        <p className="text-xs text-token-valid">
          Imported successfully. Opening experiment{" "}
          <span className="font-mono">{successId.slice(0, 8)}…</span>
        </p>
      )}
    </div>
  );
}
