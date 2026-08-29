"use client";

/**
 * ImportedOutputAtStep — Derived output reconstructed from recorded
 * selected-token strings up to (and including) the active step.
 *
 * Distinct from the authoritative generated .sv (Recorded).
 */

import { Badge } from "@/components/ui/Badge";
import { escapeTokenForDisplay } from "@/lib/importedTrace";
import { cn } from "@/lib/utils";
import type { Prov } from "@/types/provenance";
import { isUnavailable } from "@/types/provenance";

interface Props {
  prefixBefore: Prov<string>;
  selectedToken: string | null;
  selectedUnavailable: boolean;
  className?: string;
}

export function ImportedOutputAtStep({
  prefixBefore,
  selectedToken,
  selectedUnavailable,
  className,
}: Props) {
  const prefixOk = !isUnavailable(prefixBefore) && prefixBefore.value !== null;
  const prefixText = prefixOk ? String(prefixBefore.value) : null;

  return (
    <div className={cn("flex h-full min-h-0 flex-col gap-2", className)}>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
          Output at this step
        </h3>
        <Badge variant="info">Derived</Badge>
      </div>
      <p className="text-[10px] leading-relaxed text-[#484f58]">
        Concatenation of recorded <code className="font-mono">selected_token</code>{" "}
        strings only. Exact whitespace is preserved. This is not the authoritative
        final .sv output.
      </p>

      {prefixText === null ? (
        <div className="flex-1 rounded border border-surface-border bg-surface px-3 py-4 text-sm text-[#484f58]">
          Unavailable — cannot reconstruct prefix (a selected token string is missing).
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-auto rounded border border-surface-border bg-surface">
          <pre className="whitespace-pre-wrap break-all p-3 font-mono text-[11px] leading-relaxed text-[#c9d1d9]">
            <span className="text-[#8b949e]">
              {prefixText.length === 0 ? "(empty prefix)" : prefixText}
            </span>
            {selectedUnavailable || selectedToken === null ? (
              <span className="text-[#484f58]">
                {prefixText.length > 0 ? "\n" : ""}
                [selected token Unavailable]
              </span>
            ) : (
              <span className="bg-accent-blue/20 text-[#58a6ff]">
                {selectedToken}
              </span>
            )}
          </pre>
        </div>
      )}

      <div className="rounded border border-surface-border/60 bg-surface-raised px-2 py-1.5">
        <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
          Active selected token (escaped)
        </p>
        <p className="mt-0.5 break-all font-mono text-xs text-[#e6edf3]">
          {selectedUnavailable || selectedToken === null
            ? "Unavailable"
            : escapeTokenForDisplay(selectedToken)}
        </p>
      </div>
    </div>
  );
}
