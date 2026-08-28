"use client";

/**
 * Expandable ParserNode hierarchy for Phase 3B.
 *
 * Collapsed nodes do not recursively render descendants.
 * Initial expansion is limited to a small depth for performance.
 */

import { useCallback, useEffect, useState } from "react";

import { escapeTokenForDisplay } from "@/lib/importedTrace";
import { cn } from "@/lib/utils";
import type { ParserNode, ParserNodeKind } from "@/types/parserAnalysis";

const INITIAL_EXPAND_DEPTH = 2;

function kindClass(kind: ParserNodeKind): string {
  switch (kind) {
    case "rule":
      return "border-l-accent-blue text-[#58a6ff]";
    case "token":
      return "border-l-[#3fb950] text-[#3fb950]";
    case "synthetic_root":
      return "border-l-[#d29922] text-[#d29922]";
    case "recovery_marker":
      return "border-l-[#f85149] text-[#f85149]";
    case "stack_value":
      return "border-l-[#8b949e] text-[#8b949e]";
    default:
      return "border-l-[#484f58] text-[#c9d1d9]";
  }
}

function formatPosition(node: ParserNode): string | null {
  const p = node.position;
  if (!p) return null;
  const parts: string[] = [];
  // Preserve recorded zeros — only omit when the field is null/undefined.
  if (p.line != null) {
    parts.push(`L${p.line}`);
    if (p.column != null) parts.push(`C${p.column}`);
  } else if (p.column != null) {
    parts.push(`C${p.column}`);
  }
  if (p.start_pos != null) {
    parts.push(`@${p.start_pos}`);
  }
  return parts.length ? parts.join(" ") : null;
}

function collectExpandableIds(node: ParserNode, depth: number, maxDepth: number): string[] {
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

function ParserNodeRow({
  node,
  depth,
  expanded,
  onToggle,
}: {
  node: ParserNode;
  depth: number;
  expanded: Set<string>;
  onToggle: (id: string) => void;
}) {
  const hasChildren = node.children.length > 0;
  const isOpen = hasChildren && expanded.has(node.id);
  const pos = formatPosition(node);
  const tokenDisplay =
    node.kind === "token" || node.token_value != null
      ? node.token_value === null || node.token_value === undefined
        ? null
        : escapeTokenForDisplay(node.token_value)
      : null;

  return (
    <li className="list-none">
      <div
        className={cn(
          "flex items-start gap-1 border-l-2 py-0.5 pl-2 font-mono text-[11px] leading-snug",
          kindClass(node.kind)
        )}
        style={{ marginLeft: depth * 12 }}
      >
        {hasChildren ? (
          <button
            type="button"
            aria-expanded={isOpen}
            aria-label={isOpen ? `Collapse ${node.label}` : `Expand ${node.label}`}
            onClick={() => onToggle(node.id)}
            className="mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded border border-surface-border bg-surface text-[10px] text-[#8b949e] hover:border-accent-blue hover:text-accent-blue"
          >
            {isOpen ? "−" : "+"}
          </button>
        ) : (
          <span className="mt-0.5 inline-block h-4 w-4 shrink-0" aria-hidden />
        )}
        <div className="min-w-0 flex-1 break-all">
          <span className="text-[10px] uppercase tracking-wider text-[#484f58]">
            {node.kind}
          </span>{" "}
          <span className="font-semibold text-[#e6edf3]">{node.label}</span>
          {tokenDisplay != null && (
            <span className="ml-1 text-[#c9d1d9]">{tokenDisplay}</span>
          )}
          {pos && (
            <span className="ml-2 text-[10px] text-[#484f58]">{pos}</span>
          )}
          {hasChildren && (
            <span className="ml-2 text-[10px] text-[#484f58]">
              ({node.children.length} child{node.children.length === 1 ? "" : "ren"})
            </span>
          )}
        </div>
      </div>
      {isOpen && (
        <ul className="m-0 p-0">
          {node.children.map((child) => (
            <ParserNodeRow
              key={child.id}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              onToggle={onToggle}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

interface Props {
  root: ParserNode;
  className?: string;
}

export function ParserNodeTree({ root, className }: Props) {
  const [expanded, setExpanded] = useState<Set<string>>(
    () => new Set(collectExpandableIds(root, 0, INITIAL_EXPAND_DEPTH))
  );

  // Reset expansion when the analyzed root changes (e.g. prompt switch).
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
    <div className={cn("max-h-96 overflow-auto rounded border border-surface-border bg-surface p-2", className)}>
      <ul className="m-0 p-0">
        <ParserNodeRow
          node={root}
          depth={0}
          expanded={expanded}
          onToggle={onToggle}
        />
      </ul>
    </div>
  );
}
