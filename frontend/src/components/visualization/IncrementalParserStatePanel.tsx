"use client";

/**
 * IncrementalParserStatePanel — per-step Lark parser state for research.
 *
 * Shows how the Verilog grammar interprets the generated prefix at the
 * selected decoding step.  Does not affect generation or masking.
 */

import type { DecodingStep } from "@/types/decoding";

interface Props {
  step: DecodingStep;
}

function statusColor(status: string): string {
  if (status === "complete_parse") return "text-[#3fb950]";
  if (status === "valid_prefix") return "text-[#58a6ff]";
  if (status === "invalid_prefix") return "text-[#f85149]";
  return "text-[#8b949e]";
}

export function IncrementalParserStatePanel({ step }: Props) {
  const ips = step.incremental_parser_state;
  const prefix = ips?.prefix_output ?? step.context + step.selected_token;

  if (!ips?.available) {
    return (
      <div className="rounded-md border border-[#30363d] bg-[#0d1117] px-3 py-2">
        <h3 className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-[#8b949e]">
          Incremental Parser State
        </h3>
        <p className="text-[11px] text-[#484f58]">
          Incremental parser snapshot unavailable — re-run generation to populate.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2 rounded-md border border-[#30363d] bg-[#0d1117] px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-[10px] font-semibold uppercase tracking-wider text-[#8b949e]">
          Incremental Parser State
        </h3>
        <span className="font-mono text-[10px] text-[#484f58]">
          step {step.step} · grammar=verilog · lalr
        </span>
      </div>

      <p className="text-[11px] text-[#8b949e]">
        STEP {step.step} — selected token{" "}
        <code className="font-mono text-[#c9d1d9]">{JSON.stringify(step.selected_token)}</code>
      </p>

      <div className="max-h-24 overflow-auto rounded border border-[#21262d] bg-[#010409] p-2">
        <pre className="whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-[#c9d1d9]">
          {prefix || "(empty)"}
        </pre>
      </div>

      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {[
          {
            label: "Prefix status",
            value: ips.prefix_parse_status || "N/A",
            className: statusColor(ips.prefix_parse_status),
          },
          {
            label: "Accepts end ($END)",
            value: ips.parser_accepts_end ? "yes" : "no",
            className: ips.parser_accepts_end ? "text-[#3fb950]" : "text-[#c9d1d9]",
          },
          {
            label: "SynCode active",
            value: step.syncode_active || step.constraint_applied ? "yes" : "no",
            className: "text-[#c9d1d9]",
          },
        ].map(({ label, value, className }) => (
          <div
            key={label}
            className="rounded border border-[#21262d] bg-[#010409] px-2 py-1.5"
          >
            <p className="text-[10px] uppercase tracking-wider text-[#484f58]">{label}</p>
            <p className={`mt-0.5 font-mono text-xs font-semibold ${className}`}>{value}</p>
          </div>
        ))}
      </div>

      {ips.likely_grammar_context && (
        <div>
          <p className="mb-0.5 text-[10px] uppercase tracking-wider text-[#484f58]">
            Parser context
          </p>
          <p className="text-[11px] text-[#c9d1d9]">{ips.likely_grammar_context}</p>
        </div>
      )}

      {(ips.expected_next_terminals?.length ?? 0) > 0 && (
        <div>
          <p className="mb-1 text-[10px] uppercase tracking-wider text-[#484f58]">
            Expected next terminals
          </p>
          <div className="flex flex-wrap gap-1">
            {ips.expected_next_terminals.map((t) => (
              <span
                key={t}
                className="rounded border border-[#30363d] bg-[#161b22] px-1.5 py-0.5 font-mono text-[11px] text-[#58a6ff]"
              >
                {t}
              </span>
            ))}
          </div>
        </div>
      )}

      {(ips.accepted_next_terminals?.length ?? 0) > 0 && (
        <div>
          <p className="mb-1 text-[10px] uppercase tracking-wider text-[#484f58]">
            Accepted next terminals
          </p>
          <div className="flex flex-wrap gap-1">
            {ips.accepted_next_terminals.map((t) => (
              <span
                key={t}
                className="rounded border border-[#3fb950]/30 bg-[#3fb950]/10 px-1.5 py-0.5 font-mono text-[11px] text-[#3fb950]"
              >
                {t}
              </span>
            ))}
          </div>
        </div>
      )}

      {ips.selected_token_interpretation && (
        <div className="rounded border border-[#d29922]/25 bg-[#d29922]/5 px-2.5 py-2">
          <p className="mb-0.5 text-[10px] uppercase tracking-wider text-[#d29922]">
            Selected token interpretation
          </p>
          <p className="font-mono text-[11px] text-[#c9d1d9]">
            {ips.selected_token_interpretation}
          </p>
        </div>
      )}

      {ips.likely_grammar_path && (
        <div>
          <p className="mb-0.5 text-[10px] uppercase tracking-wider text-[#484f58]">
            Likely grammar path
          </p>
          <pre className="whitespace-pre-wrap rounded border border-[#21262d] bg-[#010409] p-2 font-mono text-[11px] leading-relaxed text-[#58a6ff]">
            {ips.likely_grammar_path}
          </pre>
        </div>
      )}

      {ips.likely_parser_interpretation && (
        <div className="rounded border border-[#58a6ff]/20 bg-[#58a6ff]/5 px-2.5 py-2">
          <p className="mb-0.5 text-[10px] uppercase tracking-wider text-[#58a6ff]">
            Research conclusion
          </p>
          <p className="text-[11px] leading-relaxed text-[#c9d1d9]">
            {ips.likely_parser_interpretation}
          </p>
        </div>
      )}

      {ips.prefix_parse_status === "complete_parse" && ips.parse_tree_text ? (
        <div>
          <p className="mb-1 text-[10px] uppercase tracking-wider text-[#3fb950]">
            Full parser tree (prefix complete)
          </p>
          <div className="max-h-48 overflow-auto rounded border border-[#3fb950]/20 bg-[#010409] p-2">
            <pre className="whitespace-pre font-mono text-[11px] leading-relaxed text-[#3fb950]">
              {ips.parse_tree_text}
            </pre>
          </div>
        </div>
      ) : ips.partial_parse_view ? (
        <div>
          <p className="mb-1 text-[10px] uppercase tracking-wider text-[#484f58]">
            Partial parse / parser stack
          </p>
          <div className="max-h-48 overflow-auto rounded border border-[#21262d] bg-[#010409] p-2">
            <pre className="whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-[#8b949e]">
              {ips.partial_parse_view}
            </pre>
          </div>
        </div>
      ) : null}

      {ips.prefix_parse_status === "invalid_prefix" && ips.parser_error_message && (
        <div className="rounded border border-[#f85149]/25 bg-[#f85149]/5 px-2.5 py-2 text-[11px] text-[#f85149]">
          <p className="font-semibold">{ips.parser_error_type || "Parser error"}</p>
          <p className="mt-1 font-mono opacity-90">{ips.parser_error_message}</p>
        </div>
      )}
    </div>
  );
}
