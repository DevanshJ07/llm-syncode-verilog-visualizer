"use client";

import { cn } from "@/lib/utils";
import { useUiAppearance } from "@/components/ui/AppearanceContext";
import type { UiAppearance } from "@/lib/researchAppearance";

type BadgeVariant =
  | "masked"
  | "valid"
  | "selected"
  | "neutral"
  | "info"
  | "warning"
  | "recomputed"
  | "derived";

interface BadgeProps {
  variant?: BadgeVariant;
  appearance?: UiAppearance;
  className?: string;
  children: React.ReactNode;
}

const darkVariant: Record<BadgeVariant, string> = {
  masked: "bg-red-900/40 text-token-masked border-token-masked/40",
  valid: "bg-green-900/40 text-token-valid border-token-valid/40",
  selected: "bg-blue-900/40 text-token-selected border-token-selected/40",
  neutral: "bg-[#21262d] text-token-neutral border-[#30363d]",
  info: "bg-purple-900/40 text-accent-purple border-accent-purple/40",
  warning: "bg-amber-900/40 text-accent-yellow border-accent-yellow/40",
  recomputed: "bg-purple-900/40 text-accent-purple border-accent-purple/40",
  derived: "bg-[#21262d] text-[#8b949e] border-[#30363d]",
};

/** Research-console badges: dark surfaces + restrained semantic accents. */
const researchVariant: Record<BadgeVariant, string> = {
  masked: "bg-red-500/15 text-red-300 border-red-400/40",
  valid: "bg-emerald-500/15 text-emerald-300 border-emerald-400/40",
  selected: "bg-blue-500/15 text-blue-300 border-blue-400/40",
  neutral: "bg-[#172033] text-[#a8b3c7] border-[#334155]",
  info: "bg-[#172033] text-[#a8b3c7] border-[#334155]",
  warning: "bg-amber-500/15 text-amber-300 border-amber-400/40",
  recomputed: "bg-purple-500/15 text-purple-300 border-purple-400/40",
  derived: "bg-[#172033] text-[#a8b3c7] border-[#334155]",
};

export function Badge({
  variant = "neutral",
  appearance: appearanceProp,
  className,
  children,
}: BadgeProps) {
  const appearance = useUiAppearance(appearanceProp);
  const map = appearance === "research" ? researchVariant : darkVariant;
  return (
    <span
      className={cn(
        "inline-flex items-center rounded border px-1.5 py-0.5 text-xs font-medium",
        appearance === "research" ? "font-sans" : "font-mono",
        map[variant],
        className
      )}
    >
      {children}
    </span>
  );
}
