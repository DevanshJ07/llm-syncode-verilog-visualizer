/**
 * TypeScript mirrors of backend/app/models/provenance.py.
 * Provenance wrappers — never coerce unavailable to false/0/[].
 */

export type ProvenanceKind =
  | "recorded"
  | "derived"
  | "recomputed"
  | "unavailable";

export interface ProvenanceInfo {
  kind: ProvenanceKind;
  source_file?: string | null;
  source_field?: string | null;
  method?: string | null;
  grammar_sha256?: string | null;
  warnings?: string[];
}

/** Provenanced value matching Prov[T] JSON from FastAPI/Pydantic. */
export interface Prov<T> {
  value: T | null;
  provenance: ProvenanceInfo;
}

export function isUnavailable<T>(p: Prov<T> | null | undefined): boolean {
  return !p || p.provenance.kind === "unavailable" || p.value === null;
}

export function provenanceLabel(kind: ProvenanceKind): string {
  switch (kind) {
    case "recorded":
      return "Recorded";
    case "derived":
      return "Derived";
    case "recomputed":
      return "Recomputed";
    case "unavailable":
      return "Unavailable";
    default:
      return "Unavailable";
  }
}
