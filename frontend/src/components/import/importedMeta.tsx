"use client";

/**
 * Shared display helpers for imported-experiment metadata.
 * Host filesystem paths are never treated as openable targets.
 */

export function looksLikeWindowsPath(s: string): boolean {
  return /^[A-Za-z]:[\\/]/.test(s) || s.includes("\\Users\\") || s.includes("\\\\");
}

export function MetaText({ value }: { value: unknown }) {
  if (value === null || value === undefined) {
    return <span className="text-[#484f58]">Unavailable</span>;
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
        className="break-all font-mono text-xs text-[#8b949e]"
        title="Recorded historical host path — not opened"
      >
        {text}
        <span className="ml-2 text-[10px] text-[#484f58]">(path not opened)</span>
      </span>
    );
  }
  return <span className="break-all font-mono text-xs text-[#e6edf3]">{text}</span>;
}
