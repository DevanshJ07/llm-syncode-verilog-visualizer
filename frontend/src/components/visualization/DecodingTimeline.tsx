"use client";

/**
 * DecodingTimeline — scrollable list of all decoding steps for an experiment.
 *
 * Supports controlled selected-step index (player / parent ownership) while
 * keeping per-row expansion local so playback does not expand hundreds of rows.
 */

import { useEffect, useRef, useState } from "react";

import { TokenStep } from "@/components/visualization/TokenStep";
import { Spinner } from "@/components/ui/Spinner";
import type { DecodingStep } from "@/types/decoding";

interface Props {
  steps: DecodingStep[];
  loading?: boolean;
  /** Controlled 0-based selected step. When omitted, selection is local. */
  activeStepIndex?: number | null;
  onActiveStepChange?: (stepIndex: number) => void;
  /** @deprecated Prefer onActiveStepChange — still called for compatibility. */
  onStepSelect?: (stepIndex: number) => void;
  className?: string;
}

export function DecodingTimeline({
  steps,
  loading,
  activeStepIndex: controlledIndex,
  onActiveStepChange,
  onStepSelect,
  className,
}: Props) {
  const [uncontrolledActive, setUncontrolledActive] = useState<number | null>(
    null
  );
  const scrollRef = useRef<HTMLDivElement>(null);
  const rowRefs = useRef<Map<number, HTMLDivElement>>(new Map());

  const isControlled = controlledIndex !== undefined;
  const activeStep = isControlled ? controlledIndex ?? null : uncontrolledActive;

  useEffect(() => {
    if (activeStep === null || activeStep < 0) return;
    const row = rowRefs.current.get(activeStep);
    const container = scrollRef.current;
    if (!row || !container) return;

    const rowTop = row.offsetTop;
    const rowBottom = rowTop + row.offsetHeight;
    const viewTop = container.scrollTop;
    const viewBottom = viewTop + container.clientHeight;

    if (rowTop < viewTop) {
      container.scrollTop = rowTop;
    } else if (rowBottom > viewBottom) {
      container.scrollTop = rowBottom - container.clientHeight;
    }
  }, [activeStep]);

  if (loading) {
    return (
      <div className="flex justify-center py-16">
        <Spinner label="Loading decoding steps…" />
      </div>
    );
  }

  if (steps.length === 0) {
    return (
      <div className="rounded-md border border-surface-border bg-surface p-8 text-center text-sm text-[#484f58]">
        No decoding steps to display.
        <br />
        Run a generation to see step-by-step token data.
      </div>
    );
  }

  const handleSelect = (idx: number) => {
    if (!isControlled) setUncontrolledActive(idx);
    onActiveStepChange?.(idx);
    onStepSelect?.(idx);
  };

  const hasMaskingData = steps.some((s) => s.masked_percentage > 0);

  return (
    <div
      ref={scrollRef}
      className={
        className ??
        "flex max-h-[40vh] flex-col gap-1.5 overflow-y-auto overflow-x-hidden pr-1"
      }
    >
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-xs text-[#484f58]">
          {steps.length} decoding step{steps.length !== 1 ? "s" : ""} — click to
          select / expand
        </p>
        {hasMaskingData && (
          <div className="flex items-center gap-3 text-[10px] text-[#484f58]">
            <span className="flex items-center gap-1">
              <span className="inline-block h-3 w-1 rounded-sm bg-[#3fb950]" />
              &lt;50% masked
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block h-3 w-1 rounded-sm bg-[#d29922]" />
              50–85%
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block h-3 w-1 rounded-sm bg-[#f85149]" />
              &gt;85%
            </span>
          </div>
        )}
      </div>
      {steps.map((step, i) => (
        <div
          key={step.step}
          ref={(el) => {
            if (el) rowRefs.current.set(i, el);
            else rowRefs.current.delete(i);
          }}
          data-step-index={i}
        >
          <TokenStep
            step={step}
            isActive={activeStep === i}
            onClick={() => handleSelect(i)}
          />
        </div>
      ))}
    </div>
  );
}
