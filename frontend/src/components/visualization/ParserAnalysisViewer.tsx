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

import { Badge } from "@/components/ui/Badge";
import { ParserNodeTree } from "@/components/visualization/ParserNodeTree";
import { escapeTokenForDisplay } from "@/lib/importedTrace";
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
}

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wider text-[#484f58]">{label}</p>
      <p className="mt-0.5 break-all font-mono text-xs text-[#e6edf3]">{value}</p>
    </div>
  );
}

function SourceBlock({
  label,
  text,
  tone,
}: {
  label: string;
  text: string;
  tone?: "prefix" | "suffix" | "neutral";
}) {
  const border =
    tone === "prefix"
      ? "border-[#3fb950]/30 bg-[#3fb950]/5"
      : tone === "suffix"
        ? "border-[#f85149]/30 bg-[#f85149]/5"
        : "border-surface-border bg-surface";
  return (
    <div className={cn("rounded border p-2", border)}>
      <p className="mb-1 text-[10px] uppercase tracking-wider text-[#484f58]">
        {label}
      </p>
      <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px] leading-relaxed text-[#c9d1d9]">
        {text.length === 0 ? "(empty)" : text}
      </pre>
    </div>
  );
}

function TerminalsList({ terminals }: { terminals: string[] }) {
  if (terminals.length === 0) {
    return (
      <p className="font-mono text-xs text-[#484f58]">None recorded</p>
    );
  }
  return (
    <ul className="flex flex-wrap gap-1">
      {terminals.map((t) => (
        <li
          key={t}
          className="rounded border border-surface-border bg-surface px-1.5 py-0.5 font-mono text-[10px] text-[#c9d1d9]"
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
}: Props) {
  if (isParserAnalysisUnavailable(analysis)) {
    return (
      <section
        className={cn(
          "flex flex-col gap-2 rounded-md border border-surface-border bg-surface-raised px-3 py-3",
          className
        )}
      >
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
            {title}
          </h2>
          <Badge variant="neutral">Unavailable</Badge>
        </div>
        <p className="text-sm text-[#8b949e]">
          {parserAnalysisStatusTitle("unavailable")}
        </p>
        {context === "imported" ? (
          <p className="text-xs leading-relaxed text-[#8b949e]">
            Parser analysis was not recomputed during import. Enable{" "}
            <code className="font-mono text-[11px]">recompute_with_current_grammar</code>{" "}
            when importing to obtain a structured analysis against the current
            canonical grammar. This does not imply the imported run originally
            recorded a parse tree.
          </p>
        ) : (
          <p className="text-xs leading-relaxed text-[#8b949e]">
            Structured parser analysis is unavailable for this experiment. Older
            stored runs may still expose legacy{" "}
            <code className="font-mono text-[11px]">parse_tree_*</code> fields
            below.
          </p>
        )}
        {analysis?.provenance?.method && (
          <p className="font-mono text-[10px] text-[#484f58]">
            method: {analysis.provenance.method}
          </p>
        )}
      </section>
    );
  }

  const a = analysis!;
  const caption = parserRepresentationCaption(a);
  const provKind = a.provenance?.kind ?? "unavailable";

  return (
    <section
      className={cn(
        "flex flex-col gap-3 rounded-md border border-surface-border bg-surface-raised px-3 py-3",
        className
      )}
    >
      {/* Status header */}
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
            {title}
          </h2>
          <Badge
            variant={
              a.status === "complete_valid"
                ? "valid"
                : a.status === "invalid_input"
                  ? "masked"
                  : "info"
            }
          >
            {parserAnalysisStatusTitle(a.status)}
          </Badge>
          <Badge variant="neutral">{provenanceLabel(provKind)}</Badge>
          {a.truncated && <Badge variant="info">Truncated</Badge>}
        </div>

        <div>
          <p className="font-mono text-sm font-semibold text-[#e6edf3]">
            {caption.primary}
          </p>
          {caption.notCompleteTree && (
            <p className="mt-0.5 text-xs font-medium text-[#d29922]">
              This is not a complete parse tree.
            </p>
          )}
        </div>

        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <MetaRow label="Representation kind" value={a.representation_kind} />
          <MetaRow
            label="Provenance"
            value={`${provenanceLabel(provKind)}${
              a.provenance?.method ? ` — ${a.provenance.method}` : ""
            }`}
          />
          <MetaRow
            label="Grammar"
            value={`${a.grammar_name || "verilog"}${
              a.grammar_sha256
                ? ` · ${a.grammar_sha256.slice(0, 12)}…`
                : ""
            }`}
          />
          <MetaRow
            label="Parser"
            value={`${a.parser_name || "lalr"}${
              a.parser_version ? ` · ${a.parser_version}` : ""
            }`}
          />
        </div>

        {a.grammar_sha256 && (
          <p className="break-all font-mono text-[10px] text-[#484f58]">
            grammar_sha256={a.grammar_sha256}
          </p>
        )}

        {a.warnings.length > 0 && (
          <div className="rounded border border-amber-500/30 bg-amber-900/10 px-2.5 py-2">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-amber-200/80">
              Warnings ({a.warnings.length})
            </p>
            <ul className="mt-1 list-disc space-y-0.5 pl-4 text-[11px] text-[#e6edf3]">
              {a.warnings.map((w, i) => (
                <li key={`${i}-${w.slice(0, 32)}`}>{w}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* Node viewer */}
      <div className="flex flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-[10px] font-semibold uppercase tracking-wider text-[#484f58]">
            Structured nodes
          </h3>
          <span className="font-mono text-[10px] text-[#484f58]">
            nodes={a.node_count} · max_depth_seen={a.max_depth_seen}
            {a.truncated ? " · truncated" : ""}
          </span>
        </div>
        {a.root ? (
          <ParserNodeTree root={a.root} />
        ) : (
          <p className="text-xs text-[#8b949e]">
            Root node absent.
            {a.pretty_text
              ? " Pretty text / diagnostics may still be available below."
              : " No structured forest was returned."}
          </p>
        )}
        {a.pretty_text && (
          <details className="rounded border border-surface-border bg-surface">
            <summary className="cursor-pointer px-2 py-1.5 text-[10px] uppercase tracking-wider text-[#484f58]">
              Pretty-text representation
            </summary>
            <pre className="max-h-48 overflow-auto whitespace-pre border-t border-surface-border p-2 font-mono text-[11px] text-[#c9d1d9]">
              {a.pretty_text}
            </pre>
          </details>
        )}
      </div>

      {/* Source boundary */}
      <div className="flex flex-col gap-2">
        <h3 className="text-[10px] font-semibold uppercase tracking-wider text-[#484f58]">
          Source boundary
        </h3>

        {a.status === "complete_valid" && (
          <SourceBlock
            label="Analyzed source (complete)"
            text={a.parsed_prefix || "(empty)"}
            tone="neutral"
          />
        )}

        {a.status === "incomplete_prefix" && (
          <>
            <SourceBlock
              label="Consumed prefix (entire analyzed source)"
              text={a.parsed_prefix}
              tone="prefix"
            />
            <p className="text-xs text-[#8b949e]">
              Invalid suffix is empty — failure is end-of-input before completion,
              not a rejected mid-stream token.
            </p>
            <div>
              <p className="mb-1 text-[10px] uppercase tracking-wider text-[#484f58]">
                Expected next terminals (Lark / parser-derived)
              </p>
              <TerminalsList terminals={a.expected_next_terminals} />
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
              />
              <SourceBlock
                label="Invalid suffix"
                text={a.invalid_suffix}
                tone="suffix"
              />
            </div>
            <p className="font-mono text-[10px] text-[#484f58]">
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
              />
              <MetaRow
                label="Previous token"
                value={
                  a.previous_token.length > 0
                    ? escapeTokenForDisplay(a.previous_token)
                    : "Unavailable"
                }
              />
              <MetaRow
                label="Error"
                value={
                  a.error_type
                    ? `${a.error_type}${a.error_message ? `: ${a.error_message}` : ""}`
                    : "Unavailable"
                }
              />
            </div>
            <div>
              <p className="mb-1 text-[10px] uppercase tracking-wider text-[#484f58]">
                Expected terminals (Lark / parser-derived)
              </p>
              <TerminalsList terminals={a.expected_next_terminals} />
            </div>
          </>
        )}
      </div>

      {/* EOF / expected */}
      <div className="grid gap-2 sm:grid-cols-2">
        <div>
          <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
            $END accepted
          </p>
          <p className="mt-0.5 font-mono text-xs text-[#e6edf3]">
            {a.accepts_end ? "True" : "False"}
          </p>
        </div>
        {a.status === "complete_valid" && (
          <div>
            <p className="mb-1 text-[10px] uppercase tracking-wider text-[#484f58]">
              Expected next terminals (Lark / parser-derived)
            </p>
            <TerminalsList terminals={a.expected_next_terminals} />
          </div>
        )}
      </div>

      <p className="text-[10px] text-[#484f58]">
        Lark expected terminals are not SynCode accept sequences.{" "}
        {a.comment_handling}
      </p>
    </section>
  );
}
