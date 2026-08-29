"use client";

/**
 * StepPlayer — step navigation + autoplay controls.
 * appearance="research" uses layered dark research-console toolbar styles.
 *
 * currentStep is 0-indexed; displayed as Step (currentStep+1) of totalSteps.
 */

import { useEffect, useRef, useState } from "react";
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
  /** When true, show jump-to-step (1-based display numbers). Default false. */
  showJumpInput?: boolean;
  /**
   * When true, Left/Right arrow keys move one step if focus is not in an
   * editable field. Default false (imported toolbar keeps its own controls).
   */
  enableKeyboardNav?: boolean;
  className?: string;
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
      aria-label={title}
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
              "focus-visible:ring-accent-blue",
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
  showJumpInput = false,
  enableKeyboardNav = false,
  className,
}: Props) {
  const appearance = useUiAppearance(appearanceProp);
  const research = appearance === "research";
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [jumpValue, setJumpValue] = useState(String(currentStep + 1));

  useEffect(() => {
    setJumpValue(String(currentStep + 1));
  }, [currentStep]);

  /** Manual navigation pauses playback, then moves. */
  const navigateManual = (idx: number) => {
    if (totalSteps <= 0) return;
    const clamped = Math.max(0, Math.min(totalSteps - 1, idx));
    if (isPlaying) onPlayPause();
    onStepChange(clamped);
  };

  useEffect(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
    // Do not advance past the last step. Parent should set isPlaying=false at end
    // (avoids toggle races with onPlayPause under React Strict Mode).
    if (!isPlaying || totalSteps <= 0 || currentStep >= totalSteps - 1) {
      return;
    }

    intervalRef.current = setInterval(() => {
      onStepChange(currentStep + 1);
    }, playIntervalMs);

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [isPlaying, currentStep, totalSteps, playIntervalMs, onStepChange]);

  useEffect(() => {
    if (!enableKeyboardNav || totalSteps <= 0) return;

    const onKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      if (!target) return;
      const tag = target.tagName;
      if (
        tag === "INPUT" ||
        tag === "TEXTAREA" ||
        tag === "SELECT" ||
        target.isContentEditable
      ) {
        return;
      }
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        navigateManual(currentStep - 1);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        navigateManual(currentStep + 1);
      }
    };

    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
    // navigateManual closes over currentStep / isPlaying
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enableKeyboardNav, totalSteps, currentStep, isPlaying, onPlayPause, onStepChange]);

  const atStart = totalSteps <= 0 || currentStep <= 0;
  const atEnd = totalSteps <= 0 || currentStep >= totalSteps - 1;

  function handleJump() {
    const n = Number(jumpValue);
    if (!Number.isFinite(n)) return;
    const display = Math.floor(n);
    if (display < 1 || display > totalSteps) return;
    navigateManual(display - 1);
  }

  return (
    <div
      className={cn(
        "flex flex-col gap-2 rounded-md border px-4 py-3",
        research
          ? "border-[#334155] bg-[#172033]"
          : "border-surface-border bg-surface-raised",
        className
      )}
      role="group"
      aria-label="Decoding step player"
    >
      <div className="flex flex-wrap items-center gap-2">
        <Btn
          research={research}
          onClick={() => navigateManual(0)}
          disabled={atStart}
          title="First step"
        >
          ⏮
        </Btn>
        <Btn
          research={research}
          onClick={() => navigateManual(currentStep - 1)}
          disabled={atStart}
          title="Previous step"
        >
          ◀
        </Btn>
        <button
          type="button"
          onClick={onPlayPause}
          title={isPlaying ? "Pause" : "Play"}
          aria-label={isPlaying ? "Pause" : "Play"}
          className={cn(
            "flex h-8 w-8 items-center justify-center rounded-full text-sm font-bold transition-colors",
            "focus-visible:outline-none focus-visible:ring-2",
            research
              ? isPlaying
                ? "bg-blue-600 text-[#e5edf7] hover:bg-blue-500 focus-visible:ring-blue-400"
                : "border border-[#334155] bg-[#172033] text-[#a8b3c7] hover:border-blue-400/50 hover:text-blue-300 focus-visible:ring-blue-400"
              : isPlaying
                ? "bg-accent-blue text-surface hover:bg-blue-400 focus-visible:ring-accent-blue"
                : "border border-surface-border bg-surface-raised text-[#8b949e] hover:border-accent-blue hover:text-accent-blue focus-visible:ring-accent-blue"
          )}
        >
          {isPlaying ? "⏸" : "▶"}
        </button>
        <Btn
          research={research}
          onClick={() => navigateManual(currentStep + 1)}
          disabled={atEnd}
          title="Next step"
        >
          ▶
        </Btn>
        <Btn
          research={research}
          onClick={() => navigateManual(totalSteps - 1)}
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
          aria-live="polite"
        >
          Step{" "}
          <span
            className={cn(
              "font-semibold",
              research ? "text-[#e5edf7]" : "text-[#e6edf3]"
            )}
          >
            {totalSteps === 0 ? 0 : currentStep + 1}
          </span>
          {" of "}
          <span className={research ? "text-[#94a3b8]" : "text-[#484f58]"}>
            {totalSteps}
          </span>
        </span>

        {showJumpInput && totalSteps > 0 && (
          <label
            className={cn(
              "ml-2 flex items-center gap-1 text-xs",
              research ? "text-[#a8b3c7]" : "text-[#8b949e]"
            )}
          >
            Jump
            <input
              type="number"
              min={1}
              max={totalSteps}
              value={jumpValue}
              onChange={(e) => setJumpValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleJump();
              }}
              className={cn(
                "w-16 rounded border px-1.5 py-0.5 font-mono text-xs",
                "focus-visible:outline-none focus-visible:ring-2",
                research
                  ? "border-[#334155] bg-[#0b1220] text-[#e5edf7] focus-visible:ring-blue-400"
                  : "border-surface-border bg-[#0d1117] text-[#e6edf3] focus-visible:ring-accent-blue"
              )}
              aria-label="Jump to step number"
            />
            <button
              type="button"
              onClick={handleJump}
              className={cn(
                "rounded border px-1.5 py-0.5 text-[10px] transition-colors",
                "focus-visible:outline-none focus-visible:ring-2",
                research
                  ? "border-[#334155] bg-[#172033] text-[#a8b3c7] hover:text-[#e5edf7] focus-visible:ring-blue-400"
                  : "border-surface-border bg-surface-raised text-[#8b949e] hover:text-[#e6edf3] focus-visible:ring-accent-blue"
              )}
            >
              Go
            </button>
          </label>
        )}

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
                aria-label={`Playback speed ${opt.label}`}
                aria-pressed={playIntervalMs === opt.ms}
                className={cn(
                  "rounded px-1.5 py-0.5 font-mono text-[10px] transition-colors",
                  "focus-visible:outline-none focus-visible:ring-2",
                  research
                    ? playIntervalMs === opt.ms
                      ? "border border-blue-400/50 bg-blue-500/15 text-blue-300 focus-visible:ring-blue-400"
                      : "text-[#94a3b8] hover:text-[#e5edf7] focus-visible:ring-blue-400"
                    : playIntervalMs === opt.ms
                      ? "bg-accent-blue/20 text-accent-blue focus-visible:ring-accent-blue"
                      : "text-[#484f58] hover:text-[#8b949e] focus-visible:ring-accent-blue"
                )}
              >
                {opt.label}
              </button>
            ))}
          </div>
        )}
      </div>

      {totalSteps > 0 && (
        <input
          type="range"
          min={0}
          max={Math.max(0, totalSteps - 1)}
          value={currentStep}
          onChange={(e) => navigateManual(Number(e.target.value))}
          className={cn(
            "w-full",
            research ? "accent-blue-400" : "accent-accent-blue"
          )}
          aria-label="Trace step scrubber"
        />
      )}
    </div>
  );
}
