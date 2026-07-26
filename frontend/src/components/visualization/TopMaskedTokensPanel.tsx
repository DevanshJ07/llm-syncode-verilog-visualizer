"use client";

/**
 * TopMaskedTokensPanel — research view of highest-probability tokens
 * rejected by SynCode at the current decoding step.
 *
 * Sorted by pre-mask (raw LLM) probability, not post-mask (zero) probability.
 */

import { useMemo } from "react";
import { formatPct } from "@/lib/utils";
import type { DecodingStep } from "@/types/decoding";

interface Props {
  step: DecodingStep | undefined;
  mode?: string;
}

function formatTokenVisible(token: string): string {
  return JSON.stringify(token);
}

export function TopMaskedTokensPanel({ step, mode }: Props) {
  const rows = useMemo(() => {
    if (!step?.top_masked_tokens?.length) return [];
    return [...step.top_masked_tokens].sort(
      (a, b) => b.pre_mask_prob - a.pre_mask_prob
    );
  }, [step]);

  const stats = useMemo(() => {
    if (!step || rows.length === 0) return null;
    const cumulative = rows.reduce((s, r) => s + r.pre_mask_prob, 0);
    return {
      totalMasked: step.masked_token_count || step.num_masked || 0,
      displayed: rows.length,
      highest: rows[0]?.pre_mask_prob ?? 0,
      cumulative,
    };
  }, [step, rows]);

  const totalMasked = step?.masked_token_count ?? step?.num_masked ?? 0;

  let emptyMessage: string | null = null;
  if (!step) {
    emptyMessage = "Masked-token list unavailable for this step.";
  } else if (mode !== "syncode") {
    emptyMessage = "Masked-token list unavailable for this step (raw mode).";
  } else if (rows.length === 0) {
    if (totalMasked === 0) {
      emptyMessage = "No tokens were masked at this step.";
    } else if (!step.top_masked_tokens) {
      emptyMessage =
        "Masked-token list unavailable for this step (re-run generation to populate top_masked_tokens).";
    } else {
      emptyMessage = "Masked-token list unavailable for this step.";
    }
  }

  return (
    <section className="flex flex-col gap-2 rounded-md border border-surface-border bg-surface-raised px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
          Top Masked Tokens — sorted by pre-mask probability
        </h2>
        {step && (
          <span className="font-mono text-[10px] text-[#484f58]">
            step {step.step}
          </span>
        )}
      </div>

      {emptyMessage && (
        <p className="text-sm text-[#484f58]">{emptyMessage}</p>
      )}

      {stats && rows.length > 0 && (
        <>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            {[
              {
                label: "Total masked",
                value: stats.totalMasked.toLocaleString(),
              },
              {
                label: "Displayed",
                value: `top ${stats.displayed} of ${stats.totalMasked.toLocaleString()} masked tokens`,
              },
              {
                label: "Highest pre-mask p",
                value: formatPct(stats.highest, 4),
              },
              {
                label: "Cumulative p (shown)",
                value: formatPct(stats.cumulative, 4),
              },
            ].map(({ label, value }) => (
              <div
                key={label}
                className="rounded border border-[#30363d] bg-[#0d1117] px-2 py-1.5"
              >
                <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
                  {label}
                </p>
                <p className="mt-0.5 font-mono text-xs font-semibold text-[#e6edf3]">
                  {value}
                </p>
              </div>
            ))}
          </div>

          <div className="max-h-64 overflow-auto rounded border border-[#30363d]">
            <table className="w-full min-w-[520px] border-collapse font-mono text-[12px]">
              <thead className="sticky top-0 z-10 bg-[#161b22]">
                <tr className="border-b border-[#30363d] text-left text-[10px] uppercase tracking-wider text-[#8b949e]">
                  <th className="px-2.5 py-1.5 font-semibold">#</th>
                  <th className="px-2.5 py-1.5 font-semibold">token</th>
                  <th className="px-2.5 py-1.5 font-semibold">id</th>
                  <th className="px-2.5 py-1.5 text-right font-semibold">
                    pre-mask prob
                  </th>
                  <th className="px-2.5 py-1.5 font-semibold">status</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, rank) => (
                  <tr
                    key={`${row.token_id}-${rank}`}
                    className="border-b border-[#21262d]/60 text-[#c9d1d9] hover:bg-red-900/10"
                  >
                    <td className="px-2.5 py-1 text-[#8b949e]">{rank + 1}</td>
                    <td className="px-2.5 py-1 text-[#f85149]">
                      {formatTokenVisible(row.token)}
                    </td>
                    <td className="px-2.5 py-1 text-[#8b949e]">{row.token_id}</td>
                    <td className="px-2.5 py-1 text-right tabular-nums font-medium">
                      {formatPct(row.pre_mask_prob, 4)}
                    </td>
                    <td className="px-2.5 py-1 text-[11px] text-[#8b949e]">
                      {row.status || "masked by SynCode"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}
