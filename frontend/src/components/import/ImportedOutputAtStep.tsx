"use client";

/**
 * ImportedOutputAtStep — Derived output reconstructed from recorded
 * selected-token strings. Distinct from authoritative Recorded .sv.
 * Phase 5A.2 dark research-console styling.
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
        <h3 className="font-sans text-xs font-semibold uppercase tracking-wider text-[#a8b3c7]">
          Output at this step
        </h3>
        <Badge variant="derived">Derived</Badge>
      </div>
      <p className="font-sans text-[10px] leading-relaxed text-[#94a3b8]">
        Concatenation of recorded <code className="font-mono">selected_token</code>{" "}
        strings only. Exact whitespace is preserved. This is not the authoritative
        final .sv output.
      </p>

      {prefixText === null ? (
        <div className="flex-1 rounded border border-[#334155] bg-[#0b1220] px-3 py-4 text-sm text-[#94a3b8]">
          Unavailable — cannot reconstruct prefix (a selected token string is
          missing).
        </div>
      ) : (
        <div className="min-h-0 flex-1 overflow-auto rounded border border-[#334155] bg-[#0b1220]">
          <pre className="whitespace-pre-wrap break-all p-3 font-mono text-[11px] leading-relaxed text-[#e5edf7]">
            <span>
              {prefixText.length === 0 ? "(empty prefix)" : prefixText}
            </span>
            {selectedUnavailable || selectedToken === null ? (
              <span className="text-[#94a3b8]">
                {prefixText.length > 0 ? "\n" : ""}
                [selected token Unavailable]
              </span>
            ) : (
              <span
                className="rounded bg-blue-500/20 text-blue-200 outline outline-1 outline-blue-400/50"
                title="Active selected token"
              >
                {selectedToken}
              </span>
            )}
          </pre>
        </div>
      )}

      <div className="rounded border border-[#334155] bg-[#172033] px-2 py-1.5">
        <p className="font-sans text-[10px] uppercase tracking-wider text-[#94a3b8]">
          Active selected token (escaped) · Derived
        </p>
        <p className="mt-0.5 break-all font-mono text-xs text-[#e5edf7]">
          {selectedUnavailable || selectedToken === null
            ? "Unavailable"
            : escapeTokenForDisplay(selectedToken)}
        </p>
      </div>
    </div>
  );
}
