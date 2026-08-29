"use client";

/**
 * Shared Phase 3B parser-analysis viewer for live and imported experiments.
 *
 * Renders structured ParserAnalysis honestly:
 *   complete_valid      → Complete Lark parse tree
 *   incomplete_prefix   → Partial parser stack (not a complete tree)
 *   invalid_input       → Recovered prefix forest (not a complete tree)
 *   unavailable         → explicit unavailable / import guidance
 *
 * Does not display Lark expected terminals as SynCode accept sequences.
 */

import { useUiAppearance } from "@/components/ui/AppearanceContext";
import { Badge } from "@/components/ui/Badge";
import { ParserNodeTree } from "@/components/visualization/ParserNodeTree";
import { escapeTokenForDisplay } from "@/lib/importedTrace";
import type { UiAppearance } from "@/lib/researchAppearance";
import { cn } from "@/lib/utils";
import {
  isParserAnalysisUnavailable,
  parserAnalysisStatusTitle,
  parserRepresentationCaption,
  type ParserAnalysis,
} from "@/types/parserAnalysis";
import { provenanceLabel } from "@/types/provenance";

export type ParserAnalysisViewerContext = "live" | "imported";

interface Props {
  analysis: ParserAnalysis | null | undefined;
  /** Live vs imported wording for unavailable / provenance notes. */
  context?: ParserAnalysisViewerContext;
  className?: string;
  /** Optional heading override. */
  title?: string;
  /**
   * When true, source-boundary and pretty-text diagnostics start collapsed,
   * and the node tree sits in a bounded scroll region (Phase 5A.1 workspace).
   */
  compactDiagnostics?: boolean;
  appearance?: UiAppearance;
}

function MetaRow({
  label,
  value,
  research,
}: {
  label: string;
  value: string;
  research: boolean;
}) {
  return (
    <div>
      <p
        className={cn(
          "text-[10px] uppercase tracking-wider",
          research ? "font-sans text-[#94a3b8]" : "text-[#484f58]"
        )}
      >
        {label}
      </p>
      <p
        className={cn(
          "mt-0.5 break-all font-mono text-xs",
          research ? "text-[#e5edf7]" : "text-[#e6edf3]"
        )}
      >
        {value}
      </p>
    </div>
  );
}

function SourceBlock({
  label,
  text,
  tone,
  research,
}: {
  label: string;
  text: string;
  tone?: "prefix" | "suffix" | "neutral";
  research: boolean;
}) {
  const border = research
    ? tone === "prefix"
      ? "border-green-400/40 bg-green-500/15 text-[#e5edf7]"
      : tone === "suffix"
        ? "border-red-400/40 bg-red-500/15 text-[#e5edf7]"
        : "border-[#334155] bg-[#0b1220]"
    : tone === "prefix"
      ? "border-[#3fb950]/30 bg-[#3fb950]/5"
      : tone === "suffix"
        ? "border-[#f85149]/30 bg-[#f85149]/5"
        : "border-surface-border bg-surface";
  return (
    <div className={cn("rounded border p-2", border)}>
      <p
        className={cn(
          "mb-1 text-[10px] uppercase tracking-wider",
          research ? "font-sans text-[#94a3b8]" : "text-[#484f58]"
        )}
      >
        {label}
      </p>
      <pre
        className={cn(
          "max-h-48 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px] leading-relaxed",
          research ? "text-[#e5edf7]" : "text-[#c9d1d9]"
        )}
      >
        {text.length === 0 ? "(empty)" : text}
      </pre>
    </div>
  );
}

function TerminalsList({
  terminals,
  research,
}: {
  terminals: string[];
  research: boolean;
}) {
  if (terminals.length === 0) {
    return (
      <p
        className={cn(
          "font-mono text-xs",
          research ? "text-[#94a3b8]" : "text-[#484f58]"
        )}
      >
        None recorded
      </p>
    );
  }
  return (
    <ul className="flex flex-wrap gap-1">
      {terminals.map((t) => (
        <li
          key={t}
          className={cn(
            "rounded border px-1.5 py-0.5 font-mono text-[10px]",
            research
              ? "border-[#334155] bg-[#0b1220] text-blue-300"
              : "border-surface-border bg-surface text-[#c9d1d9]"
          )}
        >
          {t}
        </li>
      ))}
    </ul>
  );
}

export function ParserAnalysisViewer({
  analysis,
  context = "live",
  className,
  title = "Structured parser analysis",
  compactDiagnostics = false,
  appearance: appearanceProp,
}: Props) {
  const appearance = useUiAppearance(appearanceProp);
  const research = appearance === "research";

  const sectionClass = research
    ? "flex flex-col gap-2 rounded-md border border-[#334155] bg-[#111827] px-3 py-3 text-[#e5edf7]"
    : "flex flex-col gap-2 rounded-md border border-surface-border bg-surface-raised px-3 py-3";

  const sectionClassGap3 = research
    ? "flex flex-col gap-3 rounded-md border border-[#334155] bg-[#111827] px-3 py-3 text-[#e5edf7]"
    : "flex flex-col gap-3 rounded-md border border-surface-border bg-surface-raised px-3 py-3";

  const headingClass = research
    ? "font-sans text-xs font-semibold uppercase tracking-wider text-[#a8b3c7]"
    : "text-xs font-semibold uppercase tracking-wider text-[#8b949e]";

  const mutedTextClass = research ? "text-[#94a3b8]" : "text-[#8b949e]";

  const mutedSmallClass = research ? "text-[#94a3b8]" : "text-[#484f58]";

  if (isParserAnalysisUnavailable(analysis)) {
    return (
      <section className={cn(sectionClass, className)}>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className={headingClass}>{title}</h2>
          <Badge variant="neutral" appearance={appearance}>
            Unavailable
          </Badge>
        </div>
        <p className={cn("text-sm", mutedTextClass)}>
          {parserAnalysisStatusTitle("unavailable")}
        </p>
        {context === "imported" ? (
          <p className={cn("text-xs leading-relaxed", mutedTextClass)}>
            Parser analysis was not recomputed during import. Enable{" "}
            <code className="font-mono text-[11px]">recompute_with_current_grammar</code>{" "}
            when importing to obtain a structured analysis against the current
            canonical grammar. This does not imply the imported run originally
            recorded a parse tree.
          </p>
        ) : (
          <p className={cn("text-xs leading-relaxed", mutedTextClass)}>
            Structured parser analysis is unavailable for this experiment. Older
            stored runs may still expose legacy{" "}
            <code className="font-mono text-[11px]">parse_tree_*</code> fields
            below.
          </p>
        )}
        {analysis?.provenance?.method && (
          <p className={cn("font-mono text-[10px]", mutedSmallClass)}>
            method: {analysis.provenance.method}
          </p>
        )}
      </section>
    );
  }

  const a = analysis!;
  const caption = parserRepresentationCaption(a);
  const provKind = a.provenance?.kind ?? "unavailable";

  const statusBadgeVariant =
    a.status === "complete_valid"
      ? "valid"
      : a.status === "invalid_input"
        ? "masked"
        : "warning";

  const subheadingClass = research
    ? "font-sans text-[10px] font-semibold uppercase tracking-wider text-[#a8b3c7]"
    : "text-[10px] font-semibold uppercase tracking-wider text-[#484f58]";

  return (
    <section className={cn(sectionClassGap3, className)}>
      {/* Status header */}
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className={headingClass}>{title}</h2>
          <Badge variant={statusBadgeVariant} appearance={appearance}>
            {parserAnalysisStatusTitle(a.status)}
          </Badge>
          <Badge variant="neutral" appearance={appearance}>
            {provenanceLabel(provKind)}
          </Badge>
          {a.truncated && (
            <Badge variant="info" appearance={appearance}>
              Truncated
            </Badge>
          )}
        </div>

        <div>
          <p
            className={cn(
              "font-mono text-sm font-semibold",
              research ? "text-[#e5edf7]" : "text-[#e6edf3]"
            )}
          >
            {caption.primary}
          </p>
          {caption.notCompleteTree && (
            <p
              className={cn(
                "mt-0.5 text-xs font-medium",
                research ? "text-amber-300" : "text-[#d29922]"
              )}
            >
              This is not a complete parse tree.
            </p>
          )}
        </div>

        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <MetaRow
            label="Representation kind"
            value={a.representation_kind}
            research={research}
          />
          <MetaRow
            label="Provenance"
            value={`${provenanceLabel(provKind)}${
              a.provenance?.method ? ` — ${a.provenance.method}` : ""
            }`}
            research={research}
          />
          <MetaRow
            label="Grammar"
            value={`${a.grammar_name || "verilog"}${
              a.grammar_sha256
                ? ` · ${a.grammar_sha256.slice(0, 12)}…`
                : ""
            }`}
            research={research}
          />
          <MetaRow
            label="Parser"
            value={`${a.parser_name || "lalr"}${
              a.parser_version ? ` · ${a.parser_version}` : ""
            }`}
            research={research}
          />
        </div>

        {a.grammar_sha256 && (
          <p className={cn("break-all font-mono text-[10px]", mutedSmallClass)}>
            grammar_sha256={a.grammar_sha256}
          </p>
        )}

        {a.warnings.length > 0 && (
          <div
            className={cn(
              "rounded border px-2.5 py-2",
              research
                ? "border-amber-400/40 bg-amber-500/15"
                : "border-amber-500/30 bg-amber-900/10"
            )}
          >
            <p
              className={cn(
                "text-[10px] font-semibold uppercase tracking-wider",
                research ? "font-sans text-amber-300" : "text-amber-200/80"
              )}
            >
              Warnings ({a.warnings.length})
            </p>
            <ul
              className={cn(
                "mt-1 list-disc space-y-0.5 pl-4 text-[11px]",
                research ? "text-[#e5edf7]" : "text-[#e6edf3]"
              )}
            >
              {a.warnings.map((w, i) => (
                <li key={`${i}-${w.slice(0, 32)}`}>{w}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* Node viewer */}
      <div className="flex min-h-0 flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className={subheadingClass}>Structured nodes</h3>
          <span className={cn("font-mono text-[10px]", mutedSmallClass)}>
            nodes={a.node_count} · max_depth_seen={a.max_depth_seen}
            {a.truncated ? " · truncated" : ""}
          </span>
        </div>
        {a.root ? (
          <div
            className={cn(
              compactDiagnostics &&
                (research
                  ? "max-h-[min(40vh,28rem)] overflow-auto rounded border border-[#334155] bg-[#0b1220] p-2"
                  : "max-h-[min(40vh,28rem)] overflow-auto rounded border border-surface-border bg-surface p-2")
            )}
          >
            <ParserNodeTree root={a.root} appearance={appearance} />
          </div>
        ) : (
          <p className={cn("text-xs", mutedTextClass)}>
            Root node absent.
            {a.pretty_text
              ? " Pretty text / diagnostics may still be available below."
              : " No structured forest was returned."}
          </p>
        )}
        {a.pretty_text &&
          (compactDiagnostics ? (
            <details
              className={cn(
                "rounded border",
                research
                  ? "border-[#334155] bg-[#0b1220]"
                  : "border-surface-border bg-surface"
              )}
            >
              <summary
                className={cn(
                  "cursor-pointer px-2 py-1.5 text-[10px] uppercase tracking-wider",
                  research ? "font-sans text-[#a8b3c7]" : "text-[#484f58]"
                )}
              >
                Pretty-text representation
              </summary>
              <pre
                className={cn(
                  "max-h-48 overflow-auto whitespace-pre border-t p-2 font-mono text-[11px]",
                  research
                    ? "border-[#334155] bg-[#0b1220] text-[#e5edf7]"
                    : "border-surface-border text-[#c9d1d9]"
                )}
              >
                {a.pretty_text}
              </pre>
            </details>
          ) : (
            <details
              className={cn(
                "rounded border",
                research
                  ? "border-[#334155] bg-[#0b1220]"
                  : "border-surface-border bg-surface"
              )}
            >
              <summary
                className={cn(
                  "cursor-pointer px-2 py-1.5 text-[10px] uppercase tracking-wider",
                  research ? "font-sans text-[#a8b3c7]" : "text-[#484f58]"
                )}
              >
                Pretty-text representation
              </summary>
              <pre
                className={cn(
                  "max-h-48 overflow-auto whitespace-pre border-t p-2 font-mono text-[11px]",
                  research
                    ? "border-[#334155] bg-[#0b1220] text-[#e5edf7]"
                    : "border-surface-border text-[#c9d1d9]"
                )}
              >
                {a.pretty_text}
              </pre>
            </details>
          ))}
      </div>

      {/* Source boundary */}
      <SourceBoundarySection
        analysis={a}
        compactDiagnostics={compactDiagnostics}
        research={research}
      />
    </section>
  );
}

function SourceBoundarySection({
  analysis: a,
  compactDiagnostics,
  research,
}: {
  analysis: NonNullable<ParserAnalysis>;
  compactDiagnostics: boolean;
  research: boolean;
}) {
  const subheadingClass = research
    ? "font-sans text-[10px] font-semibold uppercase tracking-wider text-[#a8b3c7]"
    : "text-[10px] font-semibold uppercase tracking-wider text-[#484f58]";

  const labelClass = research
    ? "font-sans text-[10px] uppercase tracking-wider text-[#94a3b8]"
    : "text-[10px] uppercase tracking-wider text-[#484f58]";

  const mutedTextClass = research ? "text-[#94a3b8]" : "text-[#8b949e]";

  const mutedSmallClass = research ? "text-[#94a3b8]" : "text-[#484f58]";

  const metaValueClass = research
    ? "mt-0.5 font-mono text-xs text-[#e5edf7]"
    : "mt-0.5 font-mono text-xs text-[#e6edf3]";

  const body = (
    <div
      className={cn(
        "flex flex-col gap-2",
        compactDiagnostics &&
          (research ? "border-t border-[#334155] p-2" : "border-t border-surface-border p-2")
      )}
    >
      {a.status === "complete_valid" && (
        <SourceBlock
          label="Analyzed source (complete)"
          text={a.parsed_prefix || "(empty)"}
          tone="neutral"
          research={research}
        />
      )}

      {a.status === "incomplete_prefix" && (
        <>
          <SourceBlock
            label="Consumed prefix (entire analyzed source)"
            text={a.parsed_prefix}
            tone="prefix"
            research={research}
          />
          <p className={cn("text-xs", mutedTextClass)}>
            Invalid suffix is empty — failure is end-of-input before completion,
            not a rejected mid-stream token.
          </p>
          <div>
            <p className={cn("mb-1", labelClass)}>
              Expected next terminals (Lark / parser-derived)
            </p>
            <TerminalsList terminals={a.expected_next_terminals} research={research} />
          </div>
        </>
      )}

      {a.status === "invalid_input" && (
        <>
          <div className="grid gap-2 lg:grid-cols-2">
            <SourceBlock
              label="Parsed / recovered prefix"
              text={a.parsed_prefix}
              tone="prefix"
              research={research}
            />
            <SourceBlock
              label="Invalid suffix"
              text={a.invalid_suffix}
              tone="suffix"
              research={research}
            />
          </div>
          <p className={cn("font-mono text-[10px]", mutedSmallClass)}>
            boundary: consumed_char_offset={a.consumed_char_offset}
            {a.error_offset != null ? ` · error_offset=${a.error_offset}` : ""}
            {a.error_line != null ? ` · line=${a.error_line}` : ""}
            {a.error_column != null ? ` · column=${a.error_column}` : ""}
          </p>
          <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
            <MetaRow
              label="Unexpected token / character"
              value={
                a.unexpected_token_or_char.length > 0
                  ? escapeTokenForDisplay(a.unexpected_token_or_char)
                  : "Unavailable"
              }
              research={research}
            />
            <MetaRow
              label="Previous token"
              value={
                a.previous_token.length > 0
                  ? escapeTokenForDisplay(a.previous_token)
                  : "Unavailable"
              }
              research={research}
            />
            <MetaRow
              label="Error"
              value={
                a.error_type
                  ? `${a.error_type}${a.error_message ? `: ${a.error_message}` : ""}`
                  : "Unavailable"
              }
              research={research}
            />
          </div>
          <div>
            <p className={cn("mb-1", labelClass)}>
              Expected terminals (Lark / parser-derived)
            </p>
            <TerminalsList terminals={a.expected_next_terminals} research={research} />
          </div>
        </>
      )}

      <div className="grid gap-2 sm:grid-cols-2">
        <div>
          <p className={labelClass}>$END accepted</p>
          <p className={metaValueClass}>{a.accepts_end ? "True" : "False"}</p>
        </div>
        {a.status === "complete_valid" && (
          <div>
            <p className={cn("mb-1", labelClass)}>
              Expected next terminals (Lark / parser-derived)
            </p>
            <TerminalsList terminals={a.expected_next_terminals} research={research} />
          </div>
        )}
      </div>

      <p className={cn("text-[10px]", mutedSmallClass)}>
        Lark expected terminals are not SynCode accept sequences.{" "}
        {a.comment_handling}
      </p>
    </div>
  );

  if (compactDiagnostics) {
    return (
      <details
        className={cn(
          "rounded border",
          research ? "border-[#334155] bg-[#0b1220]" : "border-surface-border bg-surface"
        )}
      >
        <summary className={cn("cursor-pointer px-2 py-1.5", subheadingClass)}>
          Source boundary &amp; diagnostics
        </summary>
        {body}
      </details>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <h3 className={subheadingClass}>Source boundary</h3>
      {body}
    </div>
  );
}
