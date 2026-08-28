/**
 * Display helpers for provenanced fields.
 * Unavailable must never render as false / 0 / empty-success.
 */

import {
  isUnavailable,
  provenanceLabel,
  type Prov,
  type ProvenanceKind,
} from "@/types/provenance";

function formatScalar(value: unknown): string {
  if (typeof value === "boolean") {
    return value ? "True" : "False";
  }
  if (typeof value === "number") {
    return String(value);
  }
  if (typeof value === "string") {
    return value === "" ? "(empty)" : value;
  }
  if (value === null || value === undefined) {
    return "Unavailable";
  }
  if (Array.isArray(value)) {
    return value.length === 0 ? "[]" : JSON.stringify(value);
  }
  if (typeof value === "object") {
    return JSON.stringify(value);
  }
  return String(value);
}

/** Grammar-oriented boolean / verdict wording. */
export function formatGrammarValid(value: boolean): string {
  return value ? "Valid" : "Invalid";
}

export function formatProvDisplay(
  p: Prov<unknown> | null | undefined,
  options?: {
    /** Custom formatter for the recorded/derived/recomputed value. */
    formatValue?: (value: unknown) => string;
  }
): { text: string; kind: ProvenanceKind; unavailable: boolean } {
  if (!p || isUnavailable(p)) {
    return {
      text: "Unavailable",
      kind: "unavailable",
      unavailable: true,
    };
  }
  const formatValue = options?.formatValue ?? formatScalar;
  const label = provenanceLabel(p.provenance.kind);
  return {
    text: `${formatValue(p.value)} — ${label}`,
    kind: p.provenance.kind,
    unavailable: false,
  };
}

export function formatGrammarValidProv(
  p: Prov<boolean> | null | undefined
): { text: string; kind: ProvenanceKind; unavailable: boolean } {
  return formatProvDisplay(p, {
    formatValue: (v) => formatGrammarValid(Boolean(v)),
  });
}

export function metaDictGet(
  meta: Prov<Record<string, unknown>> | null | undefined,
  key: string
): unknown {
  if (!meta || isUnavailable(meta) || !meta.value) return undefined;
  return meta.value[key];
}
