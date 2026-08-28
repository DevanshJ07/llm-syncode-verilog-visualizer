"use client";

import { cn } from "@/lib/utils";

export type ExperimentSourceMode = "live" | "imported";

interface ExperimentSourceSelectorProps {
  value: ExperimentSourceMode;
  onChange: (mode: ExperimentSourceMode) => void;
  disabled?: boolean;
}

const OPTIONS: {
  id: ExperimentSourceMode;
  title: string;
  description: string;
}[] = [
  {
    id: "live",
    title: "Live Local Generation",
    description: "Qwen → SynCode → live decoding trace",
  },
  {
    id: "imported",
    title: "Imported Experiment",
    description: "ZIP bundle → normalized stored experiment",
  },
];

export function ExperimentSourceSelector({
  value,
  onChange,
  disabled = false,
}: ExperimentSourceSelectorProps) {
  return (
    <fieldset className="flex flex-col gap-2" disabled={disabled}>
      <legend className="text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
        Experiment source
      </legend>
      <div
        className="grid gap-2 sm:grid-cols-2"
        role="radiogroup"
        aria-label="Experiment source"
      >
        {OPTIONS.map((opt) => {
          const selected = value === opt.id;
          return (
            <label
              key={opt.id}
              className={cn(
                "cursor-pointer rounded-md border px-3 py-3 transition-colors",
                selected
                  ? "border-accent-blue/60 bg-accent-blue/10"
                  : "border-surface-border bg-surface-raised hover:border-[#484f58]",
                disabled && "cursor-not-allowed opacity-50"
              )}
            >
              <input
                type="radio"
                name="experiment-source"
                value={opt.id}
                checked={selected}
                onChange={() => onChange(opt.id)}
                className="sr-only"
                disabled={disabled}
              />
              <span className="block text-sm font-medium text-[#e6edf3]">
                {opt.title}
              </span>
              <span className="mt-0.5 block text-xs text-[#8b949e]">
                {opt.description}
              </span>
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
