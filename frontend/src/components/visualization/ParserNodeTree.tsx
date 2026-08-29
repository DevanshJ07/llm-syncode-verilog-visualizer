"use client";

/**
 * Expandable ParserNode hierarchy for Phase 3B.
 * appearance="research" uses layered dark tree colours with kind labels (not colour alone).
 */

import { useCallback, useEffect, useState } from "react";

import { useUiAppearance } from "@/components/ui/AppearanceContext";
import { escapeTokenForDisplay } from "@/lib/importedTrace";
import { cn } from "@/lib/utils";
import type { UiAppearance } from "@/lib/researchAppearance";
import type { ParserNode, ParserNodeKind } from "@/types/parserAnalysis";

const INITIAL_EXPAND_DEPTH = 2;

function kindClass(kind: ParserNodeKind, research: boolean): string {
  if (research) {
    switch (kind) {
      case "rule":
        return "border-l-[#334155] text-[#e5edf7]";
      case "token":
        return "border-l-emerald-300 text-emerald-300";
      case "synthetic_root":
        return "border-l-amber-300 text-amber-300";
      case "recovery_marker":
        return "border-l-red-300 text-red-300";
      case "stack_value":
        return "border-l-[#475569] text-[#a8b3c7]";
      default:
        return "border-l-[#334155] text-[#a8b3c7]";
    }
  }
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

function collectExpandableIds(
  node: ParserNode,
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

function ParserNodeRow({
  node,
  depth,
  expanded,
  onToggle,
  research,
}: {
  node: ParserNode;
  depth: number;
  expanded: Set<string>;
  onToggle: (id: string) => void;
  research: boolean;
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
          kindClass(node.kind, research)
        )}
        style={{ marginLeft: depth * 12 }}
      >
        {hasChildren ? (
          <button
            type="button"
            aria-expanded={isOpen}
            aria-label={
              isOpen ? `Collapse ${node.label}` : `Expand ${node.label}`
            }
            onClick={() => onToggle(node.id)}
            className={cn(
              "mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded border text-[10px]",
              "focus-visible:outline-none focus-visible:ring-2",
              research
                ? "border-[#334155] bg-[#0b1220] text-[#a8b3c7] hover:border-blue-400/50 hover:text-blue-300 focus-visible:ring-blue-400"
                : "border-surface-border bg-surface text-[#8b949e] hover:border-accent-blue hover:text-accent-blue"
            )}
          >
            {isOpen ? "−" : "+"}
          </button>
        ) : (
          <span className="mt-0.5 inline-block h-4 w-4 shrink-0" aria-hidden />
        )}
        <div className="min-w-0 flex-1 break-all">
          <span
            className={cn(
              "text-[10px] uppercase tracking-wider",
              research ? "text-[#94a3b8]" : "text-[#484f58]"
            )}
          >
            {node.kind}
          </span>{" "}
          <span
            className={cn(
              "font-semibold",
              research ? "text-[#e5edf7]" : "text-[#e6edf3]"
            )}
          >
            {node.label}
          </span>
          {tokenDisplay != null && (
            <span className={cn("ml-1", research ? "text-[#a8b3c7]" : "text-[#c9d1d9]")}>
              {tokenDisplay}
            </span>
          )}
          {pos && (
            <span
              className={cn(
                "ml-2 text-[10px]",
                research ? "text-[#94a3b8]" : "text-[#484f58]"
              )}
            >
              {pos}
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
            <ParserNodeRow
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

interface Props {
  root: ParserNode;
  className?: string;
  appearance?: UiAppearance;
}

export function ParserNodeTree({ root, className, appearance: appearanceProp }: Props) {
  const appearance = useUiAppearance(appearanceProp);
  const research = appearance === "research";
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
        "max-h-96 overflow-auto rounded border p-2",
        research
          ? "border-[#334155] bg-[#0b1220]"
          : "border-surface-border bg-surface",
        className
      )}
    >
      <ul className="m-0 p-0">
        <ParserNodeRow
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
