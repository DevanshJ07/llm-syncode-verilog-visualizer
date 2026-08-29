"use client";

/**
 * Shared display helpers for imported-experiment metadata.
 * Host filesystem paths are never treated as openable targets.
 * Phase 5A.2: dark research-console typography.
 */

export function looksLikeWindowsPath(s: string): boolean {
  return /^[A-Za-z]:[\\/]/.test(s) || s.includes("\\Users\\") || s.includes("\\\\");
}

export function MetaText({ value }: { value: unknown }) {
  if (value === null || value === undefined) {
    return <span className="text-[#94a3b8]">Unavailable</span>;
  }
  const text =
    typeof value === "string"
      ? value
      : typeof value === "number" || typeof value === "boolean"
        ? String(value)
        : JSON.stringify(value);
  if (typeof value === "string" && looksLikeWindowsPath(value)) {
    return (
      <span
        className="break-all font-mono text-xs text-[#a8b3c7]"
        title="Recorded historical host path — not opened"
      >
        {text}
        <span className="ml-2 font-sans text-[10px] text-[#94a3b8]">
          (path not opened)
        </span>
      </span>
    );
  }
  return (
    <span className="break-all font-mono text-xs text-[#e5edf7]">{text}</span>
  );
}
