"use client";

/**
 * Imported experiment detail — Phase 5A.1 research workspace shell.
 * URL: /imported-experiment/[id]
 *
 * Fetches once; workspace tabs and prompt selection do not refetch.
 */

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";

import { ImportedExperimentWorkspace } from "@/components/import/ImportedExperimentWorkspace";
import { Button } from "@/components/ui/Button";
import { Spinner } from "@/components/ui/Spinner";
import { getImportedExperiment } from "@/lib/api";
import type { NormalizedExperiment } from "@/types/normalized";

export default function ImportedExperimentPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();

  const [experiment, setExperiment] = useState<NormalizedExperiment | null>(
    null
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!id) return;
    setLoading(true);
    setError(null);
    getImportedExperiment(id)
      .then((exp) => setExperiment(exp))
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, [id]);

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
        <Button
          variant="secondary"
          onClick={() => router.push("/?source=imported")}
        >
          ← Back to Import
        </Button>
      </div>
    );
  }

  return <ImportedExperimentWorkspace experiment={experiment} />;
}
