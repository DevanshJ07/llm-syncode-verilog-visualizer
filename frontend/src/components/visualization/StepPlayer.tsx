"use client";

/**
 * StepPlayer — step navigation slider + autoplay controls.
 * appearance="research" uses layered dark research-console toolbar styles.
 */

import { useEffect, useRef } from "react";
import { useUiAppearance } from "@/components/ui/AppearanceContext";
import { cn } from "@/lib/utils";
import type { UiAppearance } from "@/lib/researchAppearance";

interface Props {
  totalSteps: number;
  currentStep: number; // 0-indexed
  isPlaying: boolean;
  onStepChange: (idx: number) => void;
  onPlayPause: () => void;
  playIntervalMs?: number;
  onIntervalChange?: (ms: number) => void;
  appearance?: UiAppearance;
}

const SPEED_OPTIONS = [
  { label: "0.5×", ms: 2000 },
  { label: "1×", ms: 1000 },
  { label: "2×", ms: 500 },
  { label: "4×", ms: 250 },
];

function Btn({
  onClick,
  disabled,
  title,
  children,
  research,
}: {
  onClick: () => void;
  disabled?: boolean;
  title?: string;
  children: React.ReactNode;
  research: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={cn(
        "flex h-7 w-7 items-center justify-center rounded text-sm transition-colors",
        "focus-visible:outline-none focus-visible:ring-2",
        research
          ? cn(
              "border border-[#334155] bg-[#172033] text-[#a8b3c7]",
              "hover:border-blue-400/50 hover:text-[#e5edf7]",
              "focus-visible:ring-blue-400",
              "disabled:border-[#334155] disabled:bg-[#111827] disabled:text-[#94a3b8] disabled:opacity-100"
            )
          : cn(
              "border border-surface-border bg-surface-raised text-[#8b949e]",
              "hover:border-[#484f58] hover:text-[#e6edf3]",
              "disabled:pointer-events-none disabled:opacity-30"
            )
      )}
    >
      {children}
    </button>
  );
}

export function StepPlayer({
  totalSteps,
  currentStep,
  isPlaying,
  onStepChange,
  onPlayPause,
  playIntervalMs = 1000,
  onIntervalChange,
  appearance: appearanceProp,
}: Props) {
  const appearance = useUiAppearance(appearanceProp);
  const research = appearance === "research";
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (intervalRef.current) clearInterval(intervalRef.current);
    if (!isPlaying) return;

    intervalRef.current = setInterval(() => {
      onStepChange(Math.min(currentStep + 1, totalSteps - 1));
    }, playIntervalMs);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [isPlaying, currentStep, totalSteps, playIntervalMs, onStepChange]);

  const atStart = currentStep === 0;
  const atEnd = currentStep === totalSteps - 1;

  return (
    <div
      className={cn(
        "flex flex-col gap-2 rounded-md border px-4 py-3",
        research
          ? "border-[#334155] bg-[#172033]"
          : "border-surface-border bg-surface-raised"
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <Btn
          research={research}
          onClick={() => onStepChange(0)}
          disabled={atStart}
          title="First step"
        >
          ⏮
        </Btn>
        <Btn
          research={research}
          onClick={() => onStepChange(currentStep - 1)}
          disabled={atStart}
          title="Previous step"
        >
          ◀
        </Btn>
        <button
          type="button"
          onClick={onPlayPause}
          title={isPlaying ? "Pause" : "Play"}
          className={cn(
            "flex h-8 w-8 items-center justify-center rounded-full text-sm font-bold transition-colors",
            "focus-visible:outline-none focus-visible:ring-2",
            research
              ? isPlaying
                ? "bg-blue-600 text-[#e5edf7] hover:bg-blue-500 focus-visible:ring-blue-400"
                : "border border-[#334155] bg-[#172033] text-[#a8b3c7] hover:border-blue-400/50 hover:text-blue-300 focus-visible:ring-blue-400"
              : isPlaying
                ? "bg-accent-blue text-surface hover:bg-blue-400"
                : "border border-surface-border bg-surface-raised text-[#8b949e] hover:border-accent-blue hover:text-accent-blue"
          )}
        >
          {isPlaying ? "⏸" : "▶"}
        </button>
        <Btn
          research={research}
          onClick={() => onStepChange(currentStep + 1)}
          disabled={atEnd}
          title="Next step"
        >
          ▶
        </Btn>
        <Btn
          research={research}
          onClick={() => onStepChange(totalSteps - 1)}
          disabled={atEnd}
          title="Last step"
        >
          ⏭
        </Btn>

        <span
          className={cn(
            "ml-2 font-mono text-xs",
            research ? "text-[#a8b3c7]" : "text-[#8b949e]"
          )}
        >
          step{" "}
          <span
            className={cn(
              "font-semibold",
              research ? "text-[#e5edf7]" : "text-[#e6edf3]"
            )}
          >
            {currentStep + 1}
          </span>
          {" / "}
          <span className={research ? "text-[#94a3b8]" : "text-[#484f58]"}>
            {totalSteps}
          </span>
        </span>

        {onIntervalChange && (
          <div className="ml-auto flex items-center gap-1">
            <span
              className={cn(
                "text-[10px]",
                research ? "text-[#94a3b8]" : "text-[#484f58]"
              )}
            >
              speed
            </span>
            {SPEED_OPTIONS.map((opt) => (
              <button
                key={opt.ms}
                type="button"
                onClick={() => onIntervalChange(opt.ms)}
                className={cn(
                  "rounded px-1.5 py-0.5 font-mono text-[10px] transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2",
                  research
                    ? playIntervalMs === opt.ms
                      ? "border border-blue-400/50 bg-blue-500/15 text-blue-300 focus-visible:ring-blue-400"
                      : "text-[#94a3b8] hover:text-[#e5edf7] focus-visible:ring-blue-400"
                    : playIntervalMs === opt.ms
                      ? "bg-accent-blue/20 text-accent-blue"
                      : "text-[#484f58] hover:text-[#8b949e]"
                )}
              >
                {opt.label}
              </button>
            ))}
          </div>
        )}
      </div>

      <input
        type="range"
        min={0}
        max={totalSteps - 1}
        value={currentStep}
        onChange={(e) => onStepChange(Number(e.target.value))}
        className={cn(
          "w-full",
          research ? "accent-blue-400" : "accent-accent-blue"
        )}
        aria-label="Trace step scrubber"
      />
    </div>
  );
}
