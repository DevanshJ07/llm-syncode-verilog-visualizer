import { cn } from "@/lib/utils";
import {
  formatGrammarValidProv,
  formatProvDisplay,
} from "@/lib/provenanceDisplay";
import { provenanceLabel, type Prov, type ProvenanceKind } from "@/types/provenance";

const kindClass: Record<ProvenanceKind, string> = {
  recorded: "text-[#e6edf3]",
  derived: "text-accent-blue",
  recomputed: "text-accent-purple",
  unavailable: "text-[#484f58]",
};

interface ProvenanceValueProps {
  label: string;
  value: Prov<unknown> | null | undefined;
  /** Treat boolean grammar_valid with Valid/Invalid wording. */
  grammarValid?: boolean;
  className?: string;
  /** Optional note under the value (e.g. method). */
  showMethod?: boolean;
}

export function ProvenanceValue({
  label,
  value,
  grammarValid = false,
  className,
  showMethod = false,
}: ProvenanceValueProps) {
  const display = grammarValid
    ? formatGrammarValidProv(value as Prov<boolean> | null | undefined)
    : formatProvDisplay(value);

  return (
    <div className={cn("min-w-0", className)}>
      <p className="text-[10px] uppercase tracking-wider text-[#484f58]">{label}</p>
      <p
        className={cn(
          "mt-0.5 break-words font-mono text-sm",
          kindClass[display.kind]
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
        <p className="mt-0.5 text-[10px] text-[#484f58]">{value.provenance.method}</p>
      )}
    </div>
  );
}
