"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";

import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { listImportedExperiments } from "@/lib/api";
import { formatDate } from "@/lib/utils";
import type { ImportedExperimentSummary } from "@/types/normalized";

interface ImportedExperimentListProps {
  /** Bump to force a reload (e.g. after import). */
  refreshKey?: number;
}

export function ImportedExperimentList({
  refreshKey = 0,
}: ImportedExperimentListProps) {
  const [items, setItems] = useState<ImportedExperimentSummary[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    listImportedExperiments()
      .then((rows) => {
        // List payload must stay lightweight — no per-step traces.
        setItems(rows);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  if (loading && !items) {
    return (
      <div className="flex justify-center py-10">
        <Spinner size="md" label="Loading imported experiments…" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex flex-col items-start gap-3 rounded-md border border-accent-red/40 bg-red-900/10 px-4 py-3">
        <p className="text-sm text-accent-red" role="alert">
          {error}
        </p>
        <Button variant="secondary" size="sm" onClick={load}>
          Retry
        </Button>
      </div>
    );
  }

  if (!items || items.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-surface-border px-4 py-8 text-center">
        <p className="text-sm text-[#8b949e]">No imported experiments yet.</p>
        <p className="mt-1 text-xs text-[#484f58]">
          Import a result ZIP above to store a normalized experiment.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
          Imported experiments ({items.length})
        </h2>
        <Button variant="ghost" size="sm" onClick={load} disabled={loading}>
          Refresh
        </Button>
      </div>
      <ul className="divide-y divide-surface-border rounded-md border border-surface-border">
        {items.map((row) => (
          <li
            key={row.experiment_id}
            className="flex flex-wrap items-center gap-3 bg-surface-raised px-3 py-3 first:rounded-t-md last:rounded-b-md"
          >
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="truncate text-sm font-medium text-[#e6edf3]">
                  {row.experiment_name || row.experiment_id.slice(0, 8)}
                </span>
                <Badge variant="info">Imported</Badge>
                {row.import_warning_count > 0 && (
                  <Badge variant="masked">
                    {row.import_warning_count} warning
                    {row.import_warning_count === 1 ? "" : "s"}
                  </Badge>
                )}
              </div>
              <p className="mt-1 text-xs text-[#8b949e]">
                {row.model_name ? (
                  <span className="font-mono">{row.model_name}</span>
                ) : (
                  <span>Model unavailable</span>
                )}
                {" · "}
                {row.prompt_count} prompt{row.prompt_count === 1 ? "" : "s"}
                {row.created_at ? (
                  <>
                    {" · "}
                    {formatDate(row.created_at)}
                  </>
                ) : null}
              </p>
            </div>
            <Link
              href={`/imported-experiment/${row.experiment_id}`}
              className="inline-flex items-center rounded-md border border-surface-border bg-surface px-3 py-1.5 text-xs font-medium text-[#e6edf3] hover:border-accent-blue/50 hover:text-accent-blue"
            >
              Open
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
