"use client";

/**
 * OutputAtStep — Derived output reconstructed from recorded selected tokens.
 * Shared by live and imported experiment UIs.
 */

import { Badge } from "@/components/ui/Badge";
import { useUiAppearance } from "@/components/ui/AppearanceContext";
import { escapeLiveTokenForDisplay } from "@/lib/liveOutputAtStep";
import { cn } from "@/lib/utils";
import type { UiAppearance } from "@/lib/researchAppearance";

export interface OutputAtStepProps {
  prefixBefore: string | null;
  selectedToken: string | null;
  selectedUnavailable: boolean;
  className?: string;
  appearance?: UiAppearance;
  /** Override title (default: "Output at selected step"). */
  title?: string;
  /** Override provenance blurb under the title. */
  description?: React.ReactNode;
}

export function OutputAtStep({
  prefixBefore,
  selectedToken,
  selectedUnavailable,
  className,
  appearance: appearanceProp,
  title = "Output at selected step",
  description,
}: OutputAtStepProps) {
  const appearance = useUiAppearance(appearanceProp);
  const research = appearance === "research";

  return (
    <div className={cn("flex h-full min-h-0 flex-col gap-2", className)}>
      <div className="flex flex-wrap items-center gap-2">
        <h3
          className={cn(
            "text-xs font-semibold uppercase tracking-wider",
            research ? "font-sans text-[#a8b3c7]" : "text-[#8b949e]"
          )}
        >
          {title}
        </h3>
        <Badge variant="derived">Derived</Badge>
      </div>
      <p
        className={cn(
          "text-[10px] leading-relaxed",
          research ? "font-sans text-[#94a3b8]" : "text-[#484f58]"
        )}
      >
        {description ?? (
          <>
            Concatenation of recorded{" "}
            <code className="font-mono">selected_token</code> strings only. Exact
            whitespace is preserved. This is not the authoritative final{" "}
            {research ? ".sv output" : "generated output"}.
          </>
        )}
      </p>

      {prefixBefore === null ? (
        <div
          className={cn(
            "flex-1 rounded border px-3 py-4 text-sm",
            research
              ? "border-[#334155] bg-[#0b1220] text-[#94a3b8]"
              : "border-surface-border bg-[#0d1117] text-[#8b949e]"
          )}
        >
          Unavailable — cannot reconstruct prefix (a selected token string is
          missing).
        </div>
      ) : (
        <div
          className={cn(
            "min-h-0 max-h-64 flex-1 overflow-auto rounded border sm:max-h-80",
            research
              ? "border-[#334155] bg-[#0b1220]"
              : "border-surface-border bg-[#0d1117]"
          )}
        >
          <pre
            className={cn(
              "whitespace-pre-wrap break-all p-3 font-mono text-[11px] leading-relaxed",
              research ? "text-[#e5edf7]" : "text-[#e6edf3]"
            )}
          >
            <span>
              {prefixBefore.length === 0 ? "(empty prefix)" : prefixBefore}
            </span>
            {selectedUnavailable || selectedToken === null ? (
              <span className={research ? "text-[#94a3b8]" : "text-[#8b949e]"}>
                {prefixBefore.length > 0 ? "\n" : ""}
                [selected token Unavailable]
              </span>
            ) : (
              <span
                className={
                  research
                    ? "rounded bg-blue-500/20 text-blue-200 outline outline-1 outline-blue-400/50"
                    : "rounded bg-accent-blue/20 text-accent-blue outline outline-1 outline-accent-blue/40"
                }
                title="Selected token at this step"
              >
                {selectedToken}
              </span>
            )}
          </pre>
        </div>
      )}

      <div
        className={cn(
          "rounded border px-2 py-1.5",
          research
            ? "border-[#334155] bg-[#172033]"
            : "border-surface-border bg-surface-raised"
        )}
      >
        <p
          className={cn(
            "text-[10px] uppercase tracking-wider",
            research ? "font-sans text-[#94a3b8]" : "text-[#484f58]"
          )}
        >
          Active selected token (escaped) · Derived
        </p>
        <p
          className={cn(
            "mt-0.5 break-all font-mono text-xs",
            research ? "text-[#e5edf7]" : "text-[#e6edf3]"
          )}
        >
          {selectedUnavailable || selectedToken === null
            ? "Unavailable"
            : escapeLiveTokenForDisplay(selectedToken)}
        </p>
      </div>
    </div>
  );
}
