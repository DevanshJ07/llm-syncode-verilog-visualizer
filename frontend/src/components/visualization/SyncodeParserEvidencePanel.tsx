"use client";

/**
 * SyncodeParserEvidencePanel — shared Phase 4B display of structured
 * SynCode ParseResult evidence for live and imported steps.
 *
 * SynCode accept sequences are terminal paths used for DFA mask construction.
 * They are not Lark expected terminals and not tokenizer vocabulary tokens.
 */

import { useState } from "react";

import { useUiAppearance } from "@/components/ui/AppearanceContext";
import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/utils";
import type { UiAppearance } from "@/lib/researchAppearance";
import {
  compareSyncodeEvidence,
  eosAllowedLabel,
  evidenceOriginLabel,
  evidenceStatusLabel,
  evidenceTimingLabel,
  formatAcceptSequenceTerminals,
  formatRemainderDisplay,
  remainderStateExplanation,
  resolveDisplayProvenance,
  shouldShowEosMaskSection,
} from "@/lib/syncodeParserEvidenceDisplay";
import {
  isUnavailable,
  provenanceLabel,
  type Prov,
  type ProvenanceKind,
} from "@/types/provenance";
import type { SyncodeParserEvidence } from "@/types/syncodeParserEvidence";
import {
  isStructurallyAvailable,
  normalizeEvidenceStatus,
} from "@/types/syncodeParserEvidence";

export interface SyncodeParserEvidencePanelProps {
  evidence: SyncodeParserEvidence | null | undefined;
  /** Outer Prov.kind when imported; live capture defaults to Recorded. */
  provenanceKind?: ProvenanceKind;
  grammarSha256?: string | null;
  heading?: string;
  context?: "live" | "imported";
  /** Legacy unstructured accept_sequences — only when structured evidence is absent. */
  legacyAcceptSequences?: string[];
  className?: string;
  /** Collapse long sequence lists by default when many rows. */
  collapseSequencesAbove?: number;
  appearance?: UiAppearance;
}

function remainderStateValueClass(
  state: string | null | undefined,
  research: boolean
): string {
  if (research) {
    if (state?.toUpperCase() === "COMPLETE") {
      return "font-mono text-sm font-semibold text-emerald-300";
    }
    return "font-mono text-sm font-semibold text-[#e5edf7]";
  }
  return "font-mono text-sm font-semibold text-[#e6edf3]";
}

function MetaChip({
  label,
  value,
  warn,
  bad,
  research,
}: {
  label: string;
  value: string;
  warn?: boolean;
  bad?: boolean;
  research: boolean;
}) {
  return (
    <div
      className={cn(
        "rounded border px-2 py-1.5 min-w-0",
        research
          ? "border-[#334155] bg-[#172033]"
          : "border-[#21262d] bg-[#010409]"
      )}
    >
      <p
        className={cn(
          "text-[10px] uppercase tracking-wider",
          research ? "text-[#94a3b8]" : "text-[#484f58]"
        )}
      >
        {label}
      </p>
      <p
        className={cn(
          "mt-0.5 break-all font-mono text-xs font-semibold",
          bad
            ? research
              ? "text-red-300"
              : "text-[#f85149]"
            : warn
              ? research
                ? "text-amber-300"
                : "text-[#d29922]"
              : research
                ? "text-[#e5edf7]"
                : "text-[#c9d1d9]"
        )}
        title={value}
      >
        {value}
      </p>
    </div>
  );
}

export function SyncodeParserEvidencePanel({
  evidence,
  provenanceKind,
  grammarSha256,
  heading = "SynCode incremental parser",
  context = "live",
  legacyAcceptSequences,
  className,
  collapseSequencesAbove = 24,
  appearance: appearanceProp,
}: SyncodeParserEvidencePanelProps) {
  const appearance = useUiAppearance(appearanceProp);
  const research = appearance === "research";
  const [sequencesExpanded, setSequencesExpanded] = useState(false);

  const hasStructured = evidence != null;
  const status = evidence
    ? normalizeEvidenceStatus(evidence.status)
    : "unavailable";
  const statusLabel = evidence
    ? evidenceStatusLabel(evidence.status)
    : "Unavailable";
  const prov = resolveDisplayProvenance(provenanceKind, evidence ?? undefined);
  const legacy = legacyAcceptSequences ?? [];
  const showLegacy =
    (!hasStructured || status === "unavailable") && legacy.length > 0;

  const panelShell = (gap: "2" | "3" = "2") =>
    cn(
      "flex flex-col rounded-md border px-3 py-2",
      gap === "3" ? "gap-3" : "gap-2",
      research ? "border-[#334155] bg-[#111827] text-[#e5edf7]" : "border-[#30363d] bg-[#0d1117]",
      className
    );

  const sectionHeading = research
    ? "font-sans text-[10px] font-semibold uppercase tracking-wider text-[#a8b3c7]"
    : "text-[10px] font-semibold uppercase tracking-wider text-[#8b949e]";

  const bodySecondary = research ? "text-[11px] text-[#a8b3c7]" : "text-[11px] text-[#8b949e]";
  const bodyMuted = research ? "text-[11px] text-[#94a3b8]" : "text-[11px] text-[#484f58]";
  const metaMuted = research ? "font-mono text-[10px] text-[#94a3b8]" : "font-mono text-[10px] text-[#484f58]";

  if (!hasStructured && legacy.length === 0) {
    return (
      <div className={panelShell()}>
        <h3 className={sectionHeading}>{heading}</h3>
        <p className={bodyMuted}>
          {context === "imported"
            ? "SynCode parser evidence unavailable. It was not recorded in this bundle and was not recomputed during import."
            : "SynCode parser evidence unavailable for this step."}
        </p>
      </div>
    );
  }

  if (!hasStructured && showLegacy) {
    return (
      <div className={panelShell()}>
        <h3 className={sectionHeading}>{heading}</h3>
        <LegacyUnstructuredAcceptSequences sequences={legacy} research={research} />
      </div>
    );
  }

  const ev = evidence!;
  const rem = formatRemainderDisplay(ev.remainder);
  const sequences = ev.accept_sequences ?? [];
  const collapse =
    sequences.length > collapseSequencesAbove && !sequencesExpanded;
  const visibleSequences = collapse
    ? sequences.slice(0, collapseSequencesAbove)
    : sequences;

  return (
    <div className={panelShell("3")}>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className={sectionHeading}>{heading}</h3>
        <Badge
          appearance={appearance}
          variant={
            status === "available"
              ? "valid"
              : status === "failed"
                ? "masked"
                : "neutral"
          }
        >
          {statusLabel}
        </Badge>
        <span className={metaMuted}>
          {provenanceLabel(prov)} · {evidenceOriginLabel(ev.origin)}
        </span>
      </div>

      <p className={cn(bodySecondary, "leading-relaxed")}>
        SynCode sequences describe terminal paths used for DFA mask construction.
        They are not Lark expected terminals and not tokenizer vocabulary tokens.
      </p>

      {ev.origin === "import_recomputed_parser_only" && (
        <p
          className={cn(
            "inline-flex rounded border px-2 py-0.5 text-[11px]",
            research
              ? "border-purple-400/40 bg-purple-500/15 text-purple-300"
              : "border-[#a371f7]/30 bg-[#a371f7]/10 text-[#d2a8ff]"
          )}
        >
          Recomputed with the current canonical grammar and SynCode parser. This
          is not the original runtime token mask.
        </p>
      )}

      <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3 lg:grid-cols-4">
        <MetaChip label="Provenance" value={provenanceLabel(prov)} research={research} />
        <MetaChip label="Origin" value={evidenceOriginLabel(ev.origin)} research={research} />
        <MetaChip
          label="Timing"
          value={evidenceTimingLabel(ev.evidence_timing)}
          research={research}
        />
        <MetaChip
          label="SynCode version"
          value={ev.syncode_version || "Unavailable"}
          research={research}
        />
        {grammarSha256 ? (
          <MetaChip label="Grammar SHA-256" value={grammarSha256} research={research} />
        ) : null}
      </div>

      {status === "failed" && (
        <div
          className={cn(
            "rounded border px-2.5 py-2 text-[11px]",
            research
              ? "border-red-400/40 bg-red-500/15 text-red-300"
              : "border-[#f85149]/30 bg-[#f85149]/10 text-[#f85149]"
          )}
        >
          <p className="font-semibold">Failed</p>
          <p className="mt-1 font-mono opacity-90">
            {ev.error || "SynCode parser evidence capture failed."}
          </p>
        </div>
      )}

      {status === "unavailable" && (
        <p className={bodyMuted}>
          {context === "imported"
            ? ev.error ||
              "SynCode parser evidence unavailable. It was not recorded in this bundle and was not recomputed during import."
            : ev.error || "SynCode parser evidence unavailable for this step."}
        </p>
      )}

      {status === "unavailable" && showLegacy && (
        <LegacyUnstructuredAcceptSequences sequences={legacy} research={research} />
      )}

      {isStructurallyAvailable(ev) && (
        <>
          {/* Accept sequences */}
          <div className="flex flex-col gap-1.5">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <p className={sectionHeading}>Accept sequences</p>
              <p className={metaMuted}>
                stored {ev.accept_sequence_count_stored} / total{" "}
                {ev.accept_sequence_count_total}
                {ev.accept_sequences_truncated ? " · truncated" : ""}
              </p>
            </div>
            <p className={bodySecondary}>
              Each row is one terminal sequence that SynCode&apos;s incremental
              parser considered acceptable from this prefix.
            </p>
            {ev.accept_sequences_truncated && (
              <p
                className={cn(
                  "text-[11px]",
                  research ? "text-amber-300" : "text-[#d29922]"
                )}
              >
                Accept-sequence list was truncated by the backend storage limit.
              </p>
            )}
            {sequences.length === 0 ? (
              <p
                className={cn(
                  "rounded border px-2.5 py-2 text-[11px]",
                  research
                    ? "border-[#334155] bg-[#0b1220] text-[#a8b3c7]"
                    : "border-[#21262d] bg-[#010409] text-[#8b949e]"
                )}
              >
                0 sequences — recorded/recomputed empty sequence set (structured
                evidence is available; SynCode returned no accept sequences for
                this prefix). This is not Unavailable.
              </p>
            ) : (
              <div
                className={cn(
                  "max-h-56 overflow-y-auto rounded border",
                  research
                    ? "border-[#334155] bg-[#0b1220]"
                    : "border-[#21262d] bg-[#010409]"
                )}
              >
                <ol
                  className={cn(
                    "divide-y text-[11px]",
                    research ? "divide-[#334155]" : "divide-[#21262d]"
                  )}
                >
                  {visibleSequences.map((seq, i) => (
                    <li
                      key={i}
                      className={cn(
                        "flex gap-2 border-[#334155] px-2.5 py-1.5",
                        research
                          ? i % 2 === 0
                            ? "bg-[#0b1220]"
                            : "bg-[#111827]"
                          : undefined
                      )}
                    >
                      <span
                        className={cn(
                          "shrink-0 font-mono",
                          research ? "text-[#94a3b8]" : "text-[#484f58]"
                        )}
                      >
                        {i + 1}.
                      </span>
                      <span
                        className={cn(
                          "break-all font-mono",
                          research ? "text-blue-300" : "text-[#58a6ff]"
                        )}
                      >
                        {formatAcceptSequenceTerminals(seq.terminals ?? [])}
                      </span>
                    </li>
                  ))}
                </ol>
              </div>
            )}
            {sequences.length > collapseSequencesAbove && (
              <button
                type="button"
                className={cn(
                  "self-start text-[11px] hover:underline",
                  research ? "text-blue-300" : "text-accent-blue"
                )}
                aria-expanded={sequencesExpanded}
                onClick={() => setSequencesExpanded((v) => !v)}
              >
                {sequencesExpanded
                  ? "Collapse sequences"
                  : `Show all ${sequences.length} sequences`}
              </button>
            )}
          </div>

          {/* Remainder state */}
          <div className="flex flex-col gap-1.5">
            <p className={sectionHeading}>Remainder state</p>
            <p className={remainderStateValueClass(ev.remainder_state, research)}>
              {ev.remainder_state ?? "Unavailable"}
            </p>
            {ev.remainder_state && (
              <p className={bodySecondary}>
                {remainderStateExplanation(ev.remainder_state)}
              </p>
            )}
            <p className={metaMuted}>
              COMPLETE does not mean the Verilog program is complete — only that
              no unfinished lexical remainder is being carried.
            </p>
          </div>

          {/* Remainder value */}
          <div className="flex flex-col gap-1.5">
            <p className={sectionHeading}>Remainder value</p>
            <div className="grid gap-1.5 sm:grid-cols-2">
              <MetaChip label="Remainder type" value={rem.kindLabel} research={research} />
              <MetaChip
                label="Escaped text"
                value={rem.textDisplay}
                warn={rem.emptyDistinct}
                research={research}
              />
              {rem.hexDisplay != null && rem.hexDisplay !== "" && (
                <MetaChip label="Byte hex" value={rem.hexDisplay} research={research} />
              )}
              {ev.remainder?.original_type ? (
                <MetaChip
                  label="Original type"
                  value={ev.remainder.original_type}
                  research={research}
                />
              ) : null}
            </div>
            {ev.remainder?.truncated && (
              <p
                className={cn(
                  "text-[11px]",
                  research ? "text-amber-300" : "text-[#d29922]"
                )}
              >
                Remainder bytes were truncated for storage
                {ev.remainder.original_byte_length != null
                  ? ` (original ${ev.remainder.original_byte_length} bytes, stored ${ev.remainder.stored_byte_length ?? "n/a"})`
                  : ""}
                .
              </p>
            )}
          </div>

          {/* Grammar-end */}
          <div className="flex flex-col gap-1">
            <p className={sectionHeading}>Grammar-end evidence</p>
            {ev.grammar_end_marker_present ? (
              <>
                <p
                  className={cn(
                    "text-sm font-semibold",
                    research ? "text-emerald-300" : "text-[#3fb950]"
                  )}
                >
                  Grammar-end marker present
                </p>
                <p className={bodySecondary}>
                  This does not by itself prove that an EOS tokenizer token was
                  allowed by the final mask.
                </p>
              </>
            ) : (
              <p className={bodySecondary}>
                Grammar-end marker not present in accept sequences.
              </p>
            )}
          </div>

          {/* EOS mask observation — live only when genuinely recorded */}
          {shouldShowEosMaskSection(ev) && ev.mask_eos_observation && (
            <div
              className={cn(
                "flex flex-col gap-1.5 rounded border px-2.5 py-2",
                research
                  ? "border-[#334155] bg-[#0b1220]"
                  : "border-[#d29922]/25 bg-[#d29922]/5"
              )}
            >
              <p
                className={cn(
                  "text-[10px] font-semibold uppercase tracking-wider",
                  research ? "font-sans text-[#a8b3c7]" : "text-[#d29922]"
                )}
              >
                EOS mask observation
              </p>
              <p className={bodySecondary}>
                Token-level EOS allowance from the SynCode accept mask. Separate
                from grammar-end markers ($END / EOF).
              </p>
              <div className="grid gap-1.5 sm:grid-cols-2">
                <MetaChip
                  label="SynCode tokenizer EOS token ID"
                  value={
                    ev.mask_eos_observation.syncode_tokenizer_eos_token_id !=
                    null
                      ? String(
                          ev.mask_eos_observation.syncode_tokenizer_eos_token_id
                        )
                      : "Unavailable"
                  }
                  research={research}
                />
                <MetaChip
                  label="Application EOS token IDs"
                  value={
                    (ev.mask_eos_observation.application_eos_token_ids ?? [])
                      .length
                      ? (
                          ev.mask_eos_observation.application_eos_token_ids ?? []
                        ).join(", ")
                      : "Unavailable"
                  }
                  research={research}
                />
                <MetaChip
                  label="SynCode EOS allowed by accept mask"
                  value={eosAllowedLabel(
                    ev.mask_eos_observation.syncode_eos_allowed_by_accept_mask
                  )}
                  warn={
                    ev.mask_eos_observation.syncode_eos_allowed_by_accept_mask ===
                    false
                  }
                  research={research}
                />
              </div>
              {Object.entries(
                ev.mask_eos_observation.application_eos_allowed_by_accept_mask ??
                  {}
              ).map(([key, val]) => (
                <MetaChip
                  key={key}
                  label={`Application EOS ${key} allowed`}
                  value={eosAllowedLabel(val)}
                  warn={val === false}
                  research={research}
                />
              ))}
            </div>
          )}

          {/* Prefix alignment */}
          <div className="flex flex-col gap-1.5">
            <p className={sectionHeading}>Prefix alignment</p>
            <p className={bodySecondary}>
              Evidence belongs to the prefix before the active selected token.
            </p>
            <div className="grid gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
              <MetaChip
                label="Generated-token count before selection"
                value={
                  ev.generated_token_count_before_selection != null
                    ? String(ev.generated_token_count_before_selection)
                    : "Unavailable"
                }
                research={research}
              />
              <MetaChip
                label="Prefix character count"
                value={
                  ev.generated_prefix_char_count != null
                    ? String(ev.generated_prefix_char_count)
                    : "Unavailable"
                }
                research={research}
              />
              <MetaChip
                label="Prefix SHA-256"
                value={ev.generated_prefix_sha256 || "Unavailable"}
                research={research}
              />
              {ev.origin === "live_mask_runtime" &&
                ev.mask_call_index != null && (
                  <MetaChip
                    label="Mask-call index"
                    value={String(ev.mask_call_index)}
                    research={research}
                  />
                )}
              <MetaChip
                label="Evidence timing"
                value={evidenceTimingLabel(ev.evidence_timing)}
                research={research}
              />
            </div>
          </div>
        </>
      )}

      {(ev.warnings?.length ?? 0) > 0 && (
        <div
          className={cn(
            "rounded border px-2.5 py-2 text-[11px]",
            research
              ? "border-amber-400/40 bg-amber-500/15 text-[#e5edf7]"
              : "border-amber-500/30 bg-amber-900/10 text-[#e6edf3]"
          )}
        >
          <p
            className={cn(
              "font-semibold",
              research ? "text-amber-300" : "text-amber-200/80"
            )}
          >
            Warnings
          </p>
          <ul className="mt-1 list-disc pl-4">
            {ev.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function LegacyUnstructuredAcceptSequences({
  sequences,
  research,
}: {
  sequences: string[];
  research: boolean;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <p
        className={cn(
          "text-[10px] font-semibold uppercase tracking-wider",
          research ? "text-amber-300" : "text-[#d29922]"
        )}
      >
        Legacy unstructured evidence
      </p>
      <p
        className={cn(
          "text-[11px]",
          research ? "text-[#a8b3c7]" : "text-[#8b949e]"
        )}
      >
        Older live experiments stored stringified accept-sequence reprs only.
        Terminal ordering/structure beyond these strings is not claimed.
      </p>
      <div
        className={cn(
          "max-h-40 overflow-y-auto rounded border",
          research
            ? "border-[#334155] bg-[#0b1220]"
            : "border-[#d29922]/25 bg-[#010409]"
        )}
      >
        <ul
          className={cn(
            "divide-y font-mono text-[11px]",
            research
              ? "divide-[#334155] text-amber-300"
              : "divide-[#21262d] text-[#d29922]"
          )}
        >
          {sequences.map((s, i) => (
            <li key={i} className="break-all px-2.5 py-1.5">
              {s}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/** Imported coexistence: primary + optional recorded sibling + Derived compare. */
export function ImportedSyncodeParserEvidenceSection({
  primary,
  recordedSibling,
  className,
  appearance: appearanceProp,
}: {
  primary: Prov<SyncodeParserEvidence> | null | undefined;
  recordedSibling?: Prov<SyncodeParserEvidence> | null;
  className?: string;
  appearance?: UiAppearance;
}) {
  const appearance = useUiAppearance(appearanceProp);
  const research = appearance === "research";

  const primaryUnavailable = !primary || isUnavailable(primary);
  const siblingPresent =
    recordedSibling != null &&
    !isUnavailable(recordedSibling) &&
    recordedSibling.value != null;

  const primaryEv = primaryUnavailable ? null : primary!.value;
  const recordedEv = siblingPresent ? recordedSibling!.value : null;

  const showPrimary = primaryEv != null;
  const showRecorded = recordedEv != null;

  const bothStructurallyAvailable =
    isStructurallyAvailable(primaryEv) && isStructurallyAvailable(recordedEv);
  const compare = bothStructurallyAvailable
    ? compareSyncodeEvidence(recordedEv, primaryEv)
    : null;

  // Unavailable / failed primary must never hide an available recorded sibling.
  if (!showPrimary && !showRecorded) {
    return (
      <SyncodeParserEvidencePanel
        evidence={null}
        context="imported"
        className={className}
        appearance={appearance}
      />
    );
  }

  if (showRecorded && showPrimary) {
    const primaryIsRecomputed =
      primary!.provenance.kind === "recomputed" ||
      primaryEv!.origin === "import_recomputed_parser_only";

    return (
      <div className={cn("flex flex-col gap-3", className)}>
        <SyncodeParserEvidencePanel
          evidence={recordedEv}
          provenanceKind={recordedSibling!.provenance.kind}
          grammarSha256={recordedSibling!.provenance.grammar_sha256}
          heading="Original recorded evidence"
          context="imported"
          appearance={appearance}
        />
        <SyncodeParserEvidencePanel
          evidence={primaryEv}
          provenanceKind={primary!.provenance.kind}
          grammarSha256={primary!.provenance.grammar_sha256}
          heading={
            primaryIsRecomputed
              ? "Current SynViz recomputation"
              : "SynCode incremental parser (primary)"
          }
          context="imported"
          appearance={appearance}
        />
        {compare && (
          <div
            className={cn(
              "rounded-md border px-3 py-2",
              research
                ? "border-[#334155] bg-[#0b1220]"
                : "border-[#58a6ff]/25 bg-[#58a6ff]/5"
            )}
          >
            <p
              className={cn(
                "text-[10px] font-semibold uppercase tracking-wider",
                research ? "font-sans text-[#a8b3c7]" : "text-[#58a6ff]"
              )}
            >
              Derived comparison
            </p>
            <p
              className={cn(
                "mt-1 text-[11px]",
                research ? "text-[#a8b3c7]" : "text-[#8b949e]"
              )}
            >
              Comparison only — neither source is declared correct. Equality uses
              ordered structured accept sequences, remainder state, and
              grammar-end marker.
            </p>
            <ul
              className={cn(
                "mt-2 grid gap-1 font-mono text-[11px] sm:grid-cols-3",
                research ? "text-[#e5edf7]" : "text-[#c9d1d9]"
              )}
            >
              <li>
                Accept sequences:{" "}
                {compare.acceptSequencesEqual === null
                  ? "Unavailable"
                  : compare.acceptSequencesEqual
                    ? "equal"
                    : "differ"}{" "}
                — Derived
              </li>
              <li>
                Remainder state:{" "}
                {compare.remainderStateEqual === null
                  ? "Unavailable"
                  : compare.remainderStateEqual
                    ? "equal"
                    : "differ"}{" "}
                — Derived
              </li>
              <li>
                Grammar-end marker:{" "}
                {compare.grammarEndEqual === null
                  ? "Unavailable"
                  : compare.grammarEndEqual
                    ? "equal"
                    : "differ"}{" "}
                — Derived
              </li>
            </ul>
          </div>
        )}
      </div>
    );
  }

  if (showRecorded) {
    // Primary absent/unavailable — still surface recorded sibling honestly.
    return (
      <SyncodeParserEvidencePanel
        evidence={recordedEv}
        provenanceKind={recordedSibling!.provenance.kind}
        grammarSha256={recordedSibling!.provenance.grammar_sha256}
        heading="Original recorded evidence"
        context="imported"
        className={className}
        appearance={appearance}
      />
    );
  }

  return (
    <SyncodeParserEvidencePanel
      evidence={primaryEv}
      provenanceKind={primary!.provenance.kind}
      grammarSha256={primary!.provenance.grammar_sha256}
      heading="SynCode incremental parser"
      context="imported"
      className={className}
      appearance={appearance}
    />
  );
}
