/**
 * Phase 5A.2 — imported research-workspace appearance tokens.
 *
 * Shared components accept appearance?: "default" | "research".
 * Default preserves the live dark visualizer.
 * Research is a layered dark research-console palette (not light/white).
 */

export type UiAppearance = "default" | "research";

/**
 * Layered dark research-console palette.
 * Colour is never the sole cue — pair with explicit text labels.
 */
export const research = {
  canvas: "bg-[#0b1120]",
  panel: "bg-[#111827]",
  raised: "bg-[#172033]",
  nested: "bg-[#0b1220]",
  code: "bg-[#0b1220]",
  border: "border-[#334155]",
  borderSoft: "border-[#243044]",
  text: "text-[#e5edf7]",
  textSecondary: "text-[#a8b3c7]",
  textMuted: "text-[#94a3b8]",
  textUnavailable: "text-[#94a3b8]",
  mono: "font-mono text-[#e5edf7]",
  focus:
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-offset-2 focus-visible:ring-offset-[#0b1120]",
  /* Semantic accents — small badges/tints only */
  blue: {
    text: "text-blue-300",
    bg: "bg-blue-500/15",
    border: "border-blue-400/50",
    soft: "bg-blue-500/15 border-blue-400/40 text-blue-200",
  },
  green: {
    text: "text-emerald-300",
    bg: "bg-emerald-500/15",
    border: "border-emerald-400/50",
    soft: "bg-emerald-500/15 border-emerald-400/40 text-emerald-200",
  },
  amber: {
    text: "text-amber-300",
    bg: "bg-amber-500/15",
    border: "border-amber-400/50",
    soft: "bg-amber-500/15 border-amber-400/40 text-amber-200",
  },
  red: {
    text: "text-red-300",
    bg: "bg-red-500/15",
    border: "border-red-400/50",
    soft: "bg-red-500/15 border-red-400/40 text-red-200",
  },
  purple: {
    text: "text-purple-300",
    bg: "bg-purple-500/15",
    border: "border-purple-400/50",
    soft: "bg-purple-500/15 border-purple-400/40 text-purple-200",
  },
  slate: {
    text: "text-[#a8b3c7]",
    bg: "bg-[#172033]",
    border: "border-[#334155]",
    soft: "bg-[#172033] border-[#334155] text-[#a8b3c7]",
  },
} as const;

export function isResearch(appearance: UiAppearance | undefined): boolean {
  return appearance === "research";
}
