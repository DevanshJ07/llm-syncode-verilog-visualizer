"use client";

/**
 * Lossless CST / source-segment viewer (Checkpoint 2).
 *
 * Renders on-demand LosslessParserAnalysisResponse honestly:
 * provenance, timing, completeness, collapsible CST, segment stream,
 * and optional LLM-token span overlay (after mode only for selected token).
 *
 * Grammar terminals and LLM tokenizer tokens are different layers.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { useUiAppearance } from "@/components/ui/AppearanceContext";
import { Badge } from "@/components/ui/Badge";
import { cn } from "@/lib/utils";
import type { UiAppearance } from "@/lib/researchAppearance";
import {
  isAfterSelectedTiming,
  losslessCompletenessLabel,
  losslessProvenanceLabel,
  losslessTimingLabel,
  type LosslessCstNode,
  type LosslessParserAnalysisResponse,
  type LosslessSegmentKind,
  type LosslessSourceSegment,
} from "@/types/losslessParserAnalysis";

const INITIAL_EXPAND_DEPTH = 2;

/** Friendly short names for common punctuation lexemes (matches backend). */
const PUNCT_DISPLAY: Record<string, string> = {
  ",": "COMMA",
  ";": "SEMICOLON",
  "(": "LPAR",
  ")": "RPAR",
  "[": "LSQB",
  "]": "RSQB",
  "{": "LBRACE",
  "}": "RBRACE",
  ":": "COLON",
  ".": "DOT",
  "#": "HASH",
  "@": "AT",
  "=": "EQUAL",
  "'": "TICK",
};

export type LosslessParserAnalysisViewerContext = "live" | "imported";

interface Props {
  analysis: LosslessParserAnalysisResponse | null | undefined;
  context?: LosslessParserAnalysisViewerContext;
  className?: string;
  title?: string;
  appearance?: UiAppearance;
  /** Optional step label shown in header (1-based display). */
  displayStep?: number | null;
}

function escapeWhitespaceForDisplay(text: string): string {
  return text
    .replace(/\r\n/g, "\\r\\n")
    .replace(/\n/g, "\\n")
    .replace(/\r/g, "\\r")
    .replace(/\t/g, "\\t")
    .replace(/ /g, "·");
}

function segmentDisplayText(seg: LosslessSourceSegment): string {
  if (seg.kind === "whitespace") {
    return escapeWhitespaceForDisplay(seg.exact_text);
  }
  if (seg.kind === "terminal") {
    const punctName =
      (seg.terminal_name && PUNCT_DISPLAY[seg.exact_text]
        ? seg.terminal_name
        : null) ?? PUNCT_DISPLAY[seg.exact_text];
    if (punctName) {
      return `${punctName} ${JSON.stringify(seg.exact_text)}`;
    }
    return seg.exact_text;
  }
  if (seg.kind === "line_comment" || seg.kind === "block_comment") {
    return seg.exact_text;
  }
  // unparsed
  return seg.exact_text;
}

function segmentKindLabel(kind: LosslessSegmentKind): string {
  switch (kind) {
    case "terminal":
      return "terminal";
    case "whitespace":
      return "whitespace";
    case "line_comment":
      return "line comment";
    case "block_comment":
      return "block comment";
    case "unparsed":
      return "unparsed";
    default:
      return String(kind);
  }
}

function segmentKindClass(kind: LosslessSegmentKind, research: boolean): string {
  if (research) {
    switch (kind) {
      case "terminal":
        return "border-emerald-400/40 bg-emerald-500/10 text-emerald-200";
      case "whitespace":
        return "border-[#475569] bg-[#172033] text-[#94a3b8]";
      case "line_comment":
      case "block_comment":
        return "border-amber-400/40 bg-amber-500/10 text-amber-200";
      case "unparsed":
        return "border-red-400/40 bg-red-500/10 text-red-200";
      default:
        return "border-[#334155] bg-[#0b1220] text-[#a8b3c7]";
    }
  }
  switch (kind) {
    case "terminal":
      return "border-[#3fb950]/40 bg-[#3fb950]/10 text-[#3fb950]";
    case "whitespace":
      return "border-surface-border bg-surface text-[#8b949e]";
    case "line_comment":
    case "block_comment":
      return "border-[#d29922]/40 bg-[#d29922]/10 text-[#d29922]";
    case "unparsed":
      return "border-[#f85149]/40 bg-[#f85149]/10 text-[#f85149]";
    default:
      return "border-surface-border bg-surface text-[#c9d1d9]";
  }
}

function completenessBadgeVariant(
  completeness: LosslessParserAnalysisResponse["completeness"]
): "valid" | "warning" | "masked" | "neutral" {
  switch (completeness) {
    case "complete":
      return "valid";
    case "incomplete_prefix":
      return "warning";
    case "invalid_prefix":
      return "masked";
    case "empty":
    default:
      return "neutral";
  }
}

function collectExpandableIds(
  node: LosslessCstNode,
  depth: number,
  maxDepth: number
): string[] {
  const ids: string[] = [];
  if (node.children.length === 0) return ids;
  if (depth < maxDepth) {
    ids.push(node.id);
    for (const child of node.children) {
      ids.push(...collectExpandableIds(child, depth + 1, maxDepth));
    }
  }
  return ids;
}

function CstNodeRow({
  node,
  depth,
  expanded,
  onToggle,
  research,
}: {
  node: LosslessCstNode;
  depth: number;
  expanded: Set<string>;
  onToggle: (id: string) => void;
  research: boolean;
}) {
  const hasChildren = node.children.length > 0;
  const isOpen = hasChildren && expanded.has(node.id);
  const kindColor =
    node.kind === "terminal"
      ? research
        ? "border-l-emerald-300 text-emerald-300"
        : "border-l-[#3fb950] text-[#3fb950]"
      : research
        ? "border-l-[#334155] text-[#e5edf7]"
        : "border-l-accent-blue text-[#58a6ff]";

  const lexemeDisplay =
    node.lexeme != null
      ? node.kind === "terminal" && /^\s+$/.test(node.lexeme)
        ? escapeWhitespaceForDisplay(node.lexeme)
        : node.lexeme.length <= 2 && PUNCT_DISPLAY[node.lexeme]
          ? `${node.name} ${JSON.stringify(node.lexeme)}`
          : JSON.stringify(node.lexeme)
      : null;

  const posParts: string[] = [];
  if (node.start_line != null) {
    posParts.push(`L${node.start_line}`);
    if (node.start_column != null) posParts.push(`C${node.start_column}`);
  }
  if (node.start_offset != null) posParts.push(`@${node.start_offset}`);

  return (
    <li className="list-none">
      <div
        className={cn(
          "flex items-start gap-1 border-l-2 py-0.5 pl-2 font-mono text-[11px] leading-snug",
          kindColor
        )}
        style={{ marginLeft: depth * 12 }}
      >
        {hasChildren ? (
          <button
            type="button"
            aria-expanded={isOpen}
            aria-label={isOpen ? `Collapse ${node.name}` : `Expand ${node.name}`}
            onClick={() => onToggle(node.id)}
            className={cn(
              "mt-0.5 shrink-0 rounded px-1 text-[10px]",
              research
                ? "text-[#94a3b8] hover:bg-[#172033]"
                : "text-[#8b949e] hover:bg-surface"
            )}
          >
            {isOpen ? "▾" : "▸"}
          </button>
        ) : (
          <span className="mt-0.5 inline-block w-4 shrink-0" />
        )}
        <div className="min-w-0 break-all">
          <span className="font-semibold">{node.name}</span>
          <span
            className={cn(
              "ml-1 text-[10px]",
              research ? "text-[#94a3b8]" : "text-[#484f58]"
            )}
          >
            [{node.kind}
            {node.is_partial ? ", partial" : ""}]
          </span>
          {lexemeDisplay != null && (
            <span
              className={cn(
                "ml-1",
                research ? "text-[#a8b3c7]" : "text-[#c9d1d9]"
              )}
            >
              {lexemeDisplay}
            </span>
          )}
          {posParts.length > 0 && (
            <span
              className={cn(
                "ml-2 text-[10px]",
                research ? "text-[#94a3b8]" : "text-[#484f58]"
              )}
            >
              {posParts.join(" ")}
            </span>
          )}
          {hasChildren && (
            <span
              className={cn(
                "ml-2 text-[10px]",
                research ? "text-[#94a3b8]" : "text-[#484f58]"
              )}
            >
              ({node.children.length} child
              {node.children.length === 1 ? "" : "ren"})
            </span>
          )}
        </div>
      </div>
      {isOpen && (
        <ul className="m-0 p-0">
          {node.children.map((child) => (
            <CstNodeRow
              key={child.id}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              onToggle={onToggle}
              research={research}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

function LosslessCstTree({
  root,
  research,
}: {
  root: LosslessCstNode;
  research: boolean;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set(collectExpandableIds(root, 0, INITIAL_EXPAND_DEPTH))
  );

  useEffect(() => {
    setExpanded(new Set(collectExpandableIds(root, 0, INITIAL_EXPAND_DEPTH)));
  }, [root]);

  const onToggle = useCallback((id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  return (
    <div
      className={cn(
        "max-h-80 overflow-auto overflow-x-auto rounded border p-2",
        research
          ? "border-[#334155] bg-[#0b1220]"
          : "border-surface-border bg-surface"
      )}
    >
      <ul className="m-0 min-w-0 p-0">
        <CstNodeRow
          node={root}
          depth={0}
          expanded={expanded}
          onToggle={onToggle}
          research={research}
        />
      </ul>
    </div>
  );
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
    <div className="min-w-0">
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

export function LosslessParserAnalysisViewer({
  analysis,
  context = "live",
  className,
  title = "Lossless parser analysis",
  appearance: appearanceProp,
  displayStep = null,
}: Props) {
  const appearance = useUiAppearance(appearanceProp);
  const research = appearance === "research";

  const sectionClass = research
    ? "flex min-w-0 flex-col gap-3 overflow-x-hidden rounded-md border border-[#334155] bg-[#111827] px-3 py-3 text-[#e5edf7]"
    : "flex min-w-0 flex-col gap-3 overflow-x-hidden rounded-md border border-surface-border bg-surface-raised px-3 py-3";

  const headingClass = research
    ? "font-sans text-xs font-semibold uppercase tracking-wider text-[#a8b3c7]"
    : "text-xs font-semibold uppercase tracking-wider text-[#8b949e]";

  const mutedTextClass = research ? "text-[#94a3b8]" : "text-[#8b949e]";
  const mutedSmallClass = research ? "text-[#94a3b8]" : "text-[#484f58]";
  const subheadingClass = research
    ? "font-sans text-[10px] font-semibold uppercase tracking-wider text-[#a8b3c7]"
    : "text-[10px] font-semibold uppercase tracking-wider text-[#484f58]";

  const [cstOpen, setCstOpen] = useState(false);
  const [segmentsOpen, setSegmentsOpen] = useState(true);

  useEffect(() => {
    // Reset collapse state when a new analysis arrives.
    setCstOpen(false);
    setSegmentsOpen(true);
  }, [analysis?.source_sha256, analysis?.timing, analysis?.step_index]);

  const highlightSelected = useMemo(
    () => (analysis ? isAfterSelectedTiming(analysis.timing) : false),
    [analysis]
  );

  const selectedSpan = useMemo(() => {
    if (!analysis || !highlightSelected) return null;
    return (
      analysis.llm_token_spans.find((s) => s.selected_at_current_step) ?? null
    );
  }, [analysis, highlightSelected]);

  if (!analysis) {
    return (
      <section className={cn(sectionClass, className)}>
        <div className="flex flex-wrap items-center gap-2">
          <h2 className={headingClass}>{title}</h2>
          <Badge variant="neutral" appearance={appearance}>
            Unavailable
          </Badge>
        </div>
        <p className={cn("text-sm", mutedTextClass)}>
          Lossless parser analysis is not available
          {context === "imported" ? " for this imported prompt." : "."}
        </p>
      </section>
    );
  }

  const a = analysis;

  return (
    <section className={cn(sectionClass, className)}>
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className={headingClass}>{title}</h2>
          <Badge
            variant={completenessBadgeVariant(a.completeness)}
            appearance={appearance}
          >
            {losslessCompletenessLabel(a.completeness)}
          </Badge>
          <Badge variant="neutral" appearance={appearance}>
            {losslessTimingLabel(a.timing)}
          </Badge>
          <Badge variant="info" appearance={appearance}>
            {losslessProvenanceLabel(a.source_provenance)}
          </Badge>
          {displayStep != null && (
            <Badge variant="neutral" appearance={appearance}>
              Step {displayStep}
            </Badge>
          )}
        </div>
        <p className={cn("text-[11px] leading-relaxed", mutedTextClass)}>
          Grammar terminals and LLM tokenizer tokens are different layers.
        </p>
      </div>

      <div className="grid min-w-0 gap-2 sm:grid-cols-2 lg:grid-cols-3">
        <MetaRow
          label="Schema"
          value={a.analysis_schema_version}
          research={research}
        />
        <MetaRow
          label="Grammar"
          value={`${a.grammar_name}${a.grammar_sha256 ? ` · ${a.grammar_sha256.slice(0, 12)}…` : ""}`}
          research={research}
        />
        <MetaRow
          label="Parser"
          value={`${a.parser_engine}${a.parser_version ? ` ${a.parser_version}` : ""} · ${a.parser_mode}`}
          research={research}
        />
        <MetaRow
          label="Source chars / UTF-8 bytes"
          value={`${a.source_character_count} / ${a.source_utf8_byte_count}`}
          research={research}
        />
        <MetaRow
          label="Offset unit"
          value={a.offset_unit}
          research={research}
        />
        <MetaRow
          label="keep_all_tokens"
          value={a.keep_all_tokens ? "true" : "false"}
          research={research}
        />
        <MetaRow
          label="source_sha256"
          value={a.source_sha256 || "(empty)"}
          research={research}
        />
        <MetaRow
          label="Consumed offset"
          value={String(a.consumed_char_offset)}
          research={research}
        />
      </div>

      {/* LLM token span note + optional highlight */}
      <div
        className={cn(
          "rounded border px-2 py-2",
          research
            ? "border-[#334155] bg-[#0b1220]"
            : "border-surface-border bg-surface"
        )}
      >
        <p className={subheadingClass}>LLM tokenizer spans</p>
        <p className={cn("mt-1 text-[11px] leading-relaxed", mutedTextClass)}>
          Overlay note: LLM token spans are tokenizer-layer ranges mapped onto
          the same source characters. They are not grammar terminals.
          {highlightSelected
            ? " Highlighting the token selected at this step (after mode)."
            : " Selected-token highlight applies only in after mode."}
        </p>
        {selectedSpan && (
          <p
            className={cn(
              "mt-2 break-all rounded border px-2 py-1 font-mono text-[11px]",
              research
                ? "border-blue-400/50 bg-blue-500/15 text-blue-200"
                : "border-[#58a6ff]/40 bg-[#58a6ff]/10 text-[#58a6ff]"
            )}
          >
            selected @ [{selectedSpan.start_offset}, {selectedSpan.end_offset}){" "}
            {JSON.stringify(selectedSpan.exact_text)}
            {selectedSpan.token_id != null
              ? ` · id=${selectedSpan.token_id}`
              : ""}
          </p>
        )}
        {!highlightSelected && a.llm_token_spans.length > 0 && (
          <p className={cn("mt-1 font-mono text-[10px]", mutedSmallClass)}>
            {a.llm_token_spans.length} span
            {a.llm_token_spans.length === 1 ? "" : "s"} recorded (not highlighted
            in before / final mode)
          </p>
        )}
      </div>

      {/* Source text preview */}
      <div>
        <p className={subheadingClass}>Source snapshot</p>
        <pre
          className={cn(
            "mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-all rounded border p-2 font-mono text-[11px] leading-relaxed",
            research
              ? "border-[#334155] bg-[#0b1220] text-[#e5edf7]"
              : "border-surface-border bg-surface text-[#c9d1d9]"
          )}
        >
          {a.source_text.length === 0 ? "(empty)" : a.source_text}
        </pre>
      </div>

      {(a.consumed_prefix.length > 0 || a.invalid_suffix.length > 0) && (
        <div className="grid min-w-0 gap-2 sm:grid-cols-2">
          <div
            className={cn(
              "rounded border p-2",
              research
                ? "border-green-400/40 bg-green-500/15"
                : "border-[#3fb950]/30 bg-[#3fb950]/5"
            )}
          >
            <p className={subheadingClass}>Consumed prefix</p>
            <pre
              className={cn(
                "mt-1 max-h-28 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px]",
                research ? "text-[#e5edf7]" : "text-[#c9d1d9]"
              )}
            >
              {a.consumed_prefix.length === 0 ? "(empty)" : a.consumed_prefix}
            </pre>
          </div>
          <div
            className={cn(
              "rounded border p-2",
              research
                ? "border-red-400/40 bg-red-500/15"
                : "border-[#f85149]/30 bg-[#f85149]/5"
            )}
          >
            <p className={subheadingClass}>Invalid suffix</p>
            <pre
              className={cn(
                "mt-1 max-h-28 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px]",
                research ? "text-[#e5edf7]" : "text-[#c9d1d9]"
              )}
            >
              {a.invalid_suffix.length === 0 ? "(empty)" : a.invalid_suffix}
            </pre>
          </div>
        </div>
      )}

      {/* Collapsible CST */}
      <div>
        <button
          type="button"
          aria-expanded={cstOpen}
          onClick={() => setCstOpen((o) => !o)}
          className={cn(
            "flex w-full items-center justify-between rounded border px-2 py-1.5 text-left",
            research
              ? "border-[#334155] bg-[#172033] hover:bg-[#1a2538]"
              : "border-surface-border bg-surface hover:bg-surface-raised"
          )}
        >
          <span className={subheadingClass}>
            Lossless CST {a.cst_root ? "" : "(none)"}
          </span>
          <span className={cn("text-[10px]", mutedSmallClass)}>
            {cstOpen ? "Collapse" : "Expand"} · deep nodes start collapsed
          </span>
        </button>
        {cstOpen && (
          <div className="mt-2">
            {a.cst_root ? (
              <LosslessCstTree root={a.cst_root} research={research} />
            ) : (
              <p className={cn("text-sm", mutedTextClass)}>No CST root.</p>
            )}
          </div>
        )}
      </div>

      {/* Source segment stream */}
      <div>
        <button
          type="button"
          aria-expanded={segmentsOpen}
          onClick={() => setSegmentsOpen((o) => !o)}
          className={cn(
            "flex w-full items-center justify-between rounded border px-2 py-1.5 text-left",
            research
              ? "border-[#334155] bg-[#172033] hover:bg-[#1a2538]"
              : "border-surface-border bg-surface hover:bg-surface-raised"
          )}
        >
          <span className={subheadingClass}>
            Source segments ({a.source_segments.length})
          </span>
          <span className={cn("text-[10px]", mutedSmallClass)}>
            {segmentsOpen ? "Collapse" : "Expand"}
          </span>
        </button>
        {segmentsOpen && (
          <div
            className={cn(
              "mt-2 max-h-72 overflow-y-auto overflow-x-hidden rounded border p-2",
              research
                ? "border-[#334155] bg-[#0b1220]"
                : "border-surface-border bg-surface"
            )}
          >
            {a.source_segments.length === 0 ? (
              <p className={cn("text-sm", mutedTextClass)}>No segments.</p>
            ) : (
              <ul className="flex min-w-0 flex-col gap-1">
                {a.source_segments.map((seg) => {
                  const overlapsSelected =
                    selectedSpan != null &&
                    seg.start_offset < selectedSpan.end_offset &&
                    seg.end_offset > selectedSpan.start_offset;
                  return (
                    <li
                      key={seg.id}
                      className={cn(
                        "min-w-0 rounded border px-2 py-1 font-mono text-[11px]",
                        segmentKindClass(seg.kind, research),
                        overlapsSelected &&
                          (research
                            ? "ring-2 ring-blue-400/60"
                            : "ring-2 ring-[#58a6ff]/50")
                      )}
                    >
                      <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
                        <span className="shrink-0 text-[10px] uppercase opacity-80">
                          {segmentKindLabel(seg.kind)}
                        </span>
                        {seg.terminal_name && (
                          <span className="shrink-0 text-[10px] opacity-80">
                            {seg.terminal_name}
                          </span>
                        )}
                        <span className="shrink-0 text-[10px] opacity-70">
                          [{seg.start_offset},{seg.end_offset}) L{seg.start_line}
                          :{seg.start_column}
                        </span>
                      </div>
                      <p className="mt-0.5 break-all whitespace-pre-wrap">
                        {segmentDisplayText(seg)}
                      </p>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        )}
      </div>

      {a.warnings.length > 0 && (
        <div
          className={cn(
            "rounded border px-2 py-2 text-xs",
            research
              ? "border-amber-500/50 bg-amber-500/10 text-amber-200"
              : "border-[#d29922]/40 bg-[#d29922]/10 text-[#d29922]"
          )}
        >
          <p className="font-semibold">Warnings</p>
          <ul className="mt-1 list-disc pl-4">
            {a.warnings.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
