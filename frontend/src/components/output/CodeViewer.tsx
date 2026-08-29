"use client";

/**
 * CodeViewer — displays Verilog with clickable line numbers.
 * appearance="research" uses a deep navy-charcoal code surface.
 */

import { useState } from "react";
import { useUiAppearance } from "@/components/ui/AppearanceContext";
import { cn } from "@/lib/utils";
import type { UiAppearance } from "@/lib/researchAppearance";

interface CodeViewerProps {
  code: string;
  /** Zero-indexed line number that is currently highlighted */
  activeLine?: number;
  onLineClick?: (lineIndex: number) => void;
  className?: string;
  appearance?: UiAppearance;
}

export function CodeViewer({
  code,
  activeLine,
  onLineClick,
  className,
  appearance: appearanceProp,
}: CodeViewerProps) {
  const appearance = useUiAppearance(appearanceProp);
  const [hovered, setHovered] = useState<number | null>(null);
  const lines = code.split("\n");
  const research = appearance === "research";

  return (
    <div
      className={cn(
        "code-block overflow-auto rounded-md border",
        research
          ? "border-[#334155] bg-[#0b1220]"
          : "border-surface-border bg-surface",
        className
      )}
    >
      <table className="w-full border-collapse text-sm">
        <tbody>
          {lines.map((line, i) => (
            <tr
              key={i}
              onClick={() => onLineClick?.(i)}
              onMouseEnter={() => setHovered(i)}
              onMouseLeave={() => setHovered(null)}
              className={cn(
                "group cursor-pointer transition-colors",
                activeLine === i &&
                  (research ? "bg-blue-500/15" : "bg-accent-blue/10"),
                hovered === i &&
                  activeLine !== i &&
                  (research ? "bg-[#172033]" : "bg-surface-raised")
              )}
            >
              <td
                className={cn(
                  "w-12 select-none border-r px-3 py-0.5 text-right",
                  research
                    ? "border-[#243044] text-[#94a3b8] group-hover:text-[#a8b3c7]"
                    : "border-surface-border text-[#484f58] group-hover:text-[#8b949e]"
                )}
              >
                {i + 1}
              </td>
              <td
                className={cn(
                  "whitespace-pre px-4 py-0.5",
                  research ? "text-[#e5edf7]" : "text-[#e6edf3]"
                )}
              >
                {line || " "}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
