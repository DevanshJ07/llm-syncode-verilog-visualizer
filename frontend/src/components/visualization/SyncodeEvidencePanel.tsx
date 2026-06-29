"use client";

/**
 * SyncodeEvidencePanel — truthful SynCode constraint and final-parse evidence.
 *
 * Green "constrained decoding active" ONLY when:
 *   • syncode mode requested
 *   • mask store loaded
 *   • no fallback on any step
 *   • all steps constrained (full)
 *   • final output parses under tested Verilog grammar
 */

import type { ExperimentResult } from "@/types/decoding";

interface Props {
  experiment: ExperimentResult;
}

function Chip({
  label,
  value,
  ok,
  warn,
  bad,
}: {
  label: string;
  value: string;
  ok?: boolean;
  warn?: boolean;
  bad?: boolean;
}) {
  const valueColor = bad
    ? "text-[#f85149]"
    : ok
      ? "text-[#3fb950]"
      : warn
        ? "text-[#d29922]"
        : "text-[#e6edf3]";
  return (
    <div className="flex flex-col gap-0.5 rounded-md border border-[#30363d] bg-[#0d1117] px-2.5 py-1.5 min-w-0">
      <span className="text-[10px] uppercase tracking-wider text-[#484f58] truncate">{label}</span>
      <span className={`font-mono text-sm font-semibold truncate ${valueColor}`} title={value}>
        {value}
      </span>
    </div>
  );
}

function constraintLabel(experiment: ExperimentResult): string {
  const total = experiment.total_steps;
  const active = experiment.syncode_active_steps ?? 0;
  const status = experiment.constraint_status ?? "off";

  if (status === "off") return "off";
  if (status === "unavailable") return "unavailable";
  if (status === "none") return `none (0/${total})`;
  if (status === "partial") return `partial ${active}/${total}`;
  if (status === "full") return `full ${active}/${total}`;
  if (status === "failed") {
    if (active > 0) return `partial ${active}/${total} (output invalid)`;
    return `failed (0/${total} constrained)`;
  }
  return status;
}

export function SyncodeEvidencePanel({ experiment }: Props) {
  const isSyncodeMode = experiment.mode === "syncode";
  const total = experiment.total_steps;
  const active = experiment.syncode_active_steps ?? 0;
  const fallback = experiment.syncode_fallback_steps ?? 0;
  const parseErr = experiment.syncode_parse_error_steps ?? 0;
  const available = experiment.syncode_available ?? false;
  const finalValid = experiment.final_parse_valid ?? false;
  const fallbackOccurred = experiment.fallback_occurred ?? fallback > 0;
  const constraintStatus = experiment.constraint_status ?? "off";
  const fullyConstrained =
    isSyncodeMode &&
    available &&
    !fallbackOccurred &&
    active === total &&
    total > 0 &&
    constraintStatus === "full" &&
    finalValid;

  const constraintChipOk = constraintStatus === "full" && finalValid;
  const constraintChipWarn =
    constraintStatus === "partial" ||
    (constraintStatus === "full" && !finalValid);
  const constraintChipBad =
    constraintStatus === "failed" ||
    constraintStatus === "unavailable" ||
    constraintStatus === "none";

  return (
    <div className="rounded-md border border-[#30363d] bg-[#0d1117] px-3 py-2">
      <div className="flex flex-wrap items-center gap-2 mb-2">
        <span className="text-[10px] font-semibold uppercase tracking-wider text-[#8b949e]">
          SynCode Evidence
        </span>

        {fullyConstrained && (
          <span className="rounded border border-[#3fb950]/40 bg-[#3fb950]/10 px-1.5 py-0.5 text-[10px] font-medium text-[#3fb950]">
            ✓ constrained decoding active — output grammar-valid
          </span>
        )}

        {isSyncodeMode && !fullyConstrained && !finalValid && (
          <span className="rounded border border-[#f85149]/40 bg-[#f85149]/10 px-1.5 py-0.5 text-[10px] font-medium text-[#f85149]">
            ✗ SynCode constraint partial or failed; final output not valid under tested grammar
          </span>
        )}

        {isSyncodeMode && !fullyConstrained && finalValid && (
          <span className="rounded border border-[#d29922]/40 bg-[#d29922]/10 px-1.5 py-0.5 text-[10px] font-medium text-[#d29922]">
            ⚠ partial constraint — output valid but not all steps were constrained
          </span>
        )}

        {!isSyncodeMode && (
          <span className="rounded border border-[#484f58]/40 bg-[#21262d] px-1.5 py-0.5 text-[10px] font-medium text-[#8b949e]">
            raw mode — no grammar constraint
          </span>
        )}
      </div>

      <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-3 lg:grid-cols-5 xl:grid-cols-10">
        <Chip
          label="Constraint requested"
          value={isSyncodeMode ? "yes" : "no"}
          ok={isSyncodeMode}
        />
        <Chip label="Backend mode" value={experiment.mode} ok={isSyncodeMode} />
        <Chip label="Grammar" value={experiment.grammar_name || "verilog"} ok />
        <Chip label="Parser" value={experiment.parser_name || "lalr"} ok />
        <Chip
          label="Mask store"
          value={available ? "loaded" : "unavailable"}
          ok={available}
          warn={isSyncodeMode && !available}
          bad={isSyncodeMode && !available}
        />
        <Chip
          label="Constraint applied"
          value={constraintLabel(experiment)}
          ok={constraintChipOk}
          warn={constraintChipWarn}
          bad={constraintChipBad}
        />
        <Chip
          label="Final output valid"
          value={finalValid ? "yes" : "no"}
          ok={finalValid}
          bad={!finalValid && isSyncodeMode}
          warn={!finalValid && !isSyncodeMode}
        />
        <Chip
          label="Fallback occurred"
          value={fallbackOccurred ? `yes (${fallback}/${total})` : "no"}
          ok={!fallbackOccurred}
          warn={fallbackOccurred}
          bad={fallbackOccurred && isSyncodeMode}
        />
        {parseErr > 0 && (
          <Chip label="Step parse errors" value={`${parseErr}/${total}`} warn bad />
        )}
        {(experiment.unsupported_constructs_detected?.length ?? 0) > 0 && (
          <Chip
            label="Unsupported constructs"
            value={experiment.unsupported_constructs_detected!.join(", ")}
            bad
          />
        )}
      </div>

      {/* Parse / syncode error detail */}
      {!finalValid && (
        <div className="mt-2 rounded border border-[#f85149]/25 bg-red-900/10 px-3 py-2 text-[11px] text-[#f85149]">
          {experiment.unsupported_constructs_detected &&
            experiment.unsupported_constructs_detected.length > 0 && (
              <p>
                <strong>Unsupported:</strong>{" "}
                {experiment.unsupported_constructs_detected.join(", ")}
              </p>
            )}
          {(experiment.final_parse_error || experiment.syncode_error) && (
            <p className="mt-1 font-mono opacity-90">
              {experiment.syncode_error || experiment.final_parse_error}
            </p>
          )}
        </div>
      )}

      {isSyncodeMode && available && fallback > 0 && active < total && (
        <p className="mt-2 text-[11px] text-[#d29922]">
          SynCode masked logits on {active}/{total} steps; {fallback} step(s) used raw fallback.
          {finalValid
            ? " Final output still parses under the tested grammar."
            : " Final output does not satisfy the tested grammar."}
        </p>
      )}

      {isSyncodeMode && !available && (
        <p className="mt-2 text-[11px] text-[#f85149]">
          SynCode was not available (package missing or grammar failed to compile).
          Generation ran unconstrained. Check backend startup logs.
        </p>
      )}
    </div>
  );
}
