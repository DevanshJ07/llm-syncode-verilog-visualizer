"use client";

import { cn } from "@/lib/utils";
import { useUiAppearance } from "@/components/ui/AppearanceContext";
import {
  formatGrammarValidProv,
  formatProvDisplay,
} from "@/lib/provenanceDisplay";
import type { UiAppearance } from "@/lib/researchAppearance";
import { provenanceLabel, type Prov, type ProvenanceKind } from "@/types/provenance";

const darkKind: Record<ProvenanceKind, string> = {
  recorded: "text-[#e6edf3]",
  derived: "text-accent-blue",
  recomputed: "text-accent-purple",
  unavailable: "text-[#484f58]",
};

const researchKind: Record<ProvenanceKind, string> = {
  recorded: "text-[#e5edf7]",
  derived: "text-[#a8b3c7]",
  recomputed: "text-purple-300",
  unavailable: "text-[#94a3b8]",
};

interface ProvenanceValueProps {
  label: string;
  value: Prov<unknown> | null | undefined;
  grammarValid?: boolean;
  className?: string;
  showMethod?: boolean;
  appearance?: UiAppearance;
  emphasis?: "primary" | "normal";
}

export function ProvenanceValue({
  label,
  value,
  grammarValid = false,
  className,
  showMethod = false,
  appearance: appearanceProp,
  emphasis = "normal",
}: ProvenanceValueProps) {
  const appearance = useUiAppearance(appearanceProp);
  const display = grammarValid
    ? formatGrammarValidProv(value as Prov<boolean> | null | undefined)
    : formatProvDisplay(value);

  const kindMap = appearance === "research" ? researchKind : darkKind;
  const research = appearance === "research";

  let valueTone = kindMap[display.kind];
  if (research && grammarValid && !display.unavailable) {
    const raw = String(display.text).toLowerCase();
    if (raw.includes("valid") && !raw.includes("invalid")) {
      valueTone = "text-emerald-300 font-semibold";
    } else if (raw.includes("invalid")) {
      valueTone = "text-red-300 font-semibold";
    }
  }

  return (
    <div
      className={cn(
        "min-w-0",
        research &&
          emphasis === "primary" &&
          "rounded-md border border-[#334155] bg-[#172033] px-3 py-2",
        className
      )}
    >
      <p
        className={cn(
          "text-[10px] uppercase tracking-wider",
          research ? "font-sans text-[#94a3b8]" : "text-[#484f58]"
        )}
      >
        {label}
      </p>
      <p
        className={cn(
          "mt-0.5 break-words font-mono text-sm",
          research && emphasis === "primary" && "text-base",
          valueTone
        )}
        title={
          value?.provenance?.method
            ? `${provenanceLabel(display.kind)}: ${value.provenance.method}`
            : provenanceLabel(display.kind)
        }
      >
        {display.text}
      </p>
      {showMethod && value?.provenance?.method && !display.unavailable && (
        <p
          className={cn(
            "mt-0.5 text-[10px]",
            research ? "text-[#94a3b8]" : "text-[#484f58]"
          )}
        >
          {value.provenance.method}
        </p>
      )}
    </div>
  );
}
