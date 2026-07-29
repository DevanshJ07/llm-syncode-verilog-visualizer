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

/** Human-readable constraint-applied label: active steps / absolute budget. */
function constraintAppliedLabel(experiment: ExperimentResult): string {
  const active = experiment.syncode_active_steps ?? 0;
  const budget = experiment.absolute_max_tokens ?? experiment.total_steps;
  const status = experiment.constraint_status ?? "off";

  if (status === "off") return "off";
  if (status === "unavailable") return "unavailable";
  return `${active} / ${budget}`;
}

/** Human-readable reason why SynCode stopped constraining. */
function stoppedReasonLabel(reason: string | undefined, mode: string): string {
  if (mode !== "syncode") return "N/A (raw mode)";
  if (!reason) return "max_tokens_incomplete";

  if (reason === "parse_complete") return "parse_complete";
  if (reason === "eos_parse_complete") return "eos_parse_complete";
  if (reason === "max_tokens_incomplete" || reason === "max_tokens") {
    return "max_tokens_incomplete";
  }
  if (reason.startsWith("syncode_parser_error_at_step_")) {
    return "parser_error";
  }
  if (reason.startsWith("eos_at_step_")) {
    return "eos";
  }
  if (reason.startsWith("max_new_tokens_reached_")) {
    return "max_tokens_incomplete";
  }
  if (reason.startsWith("whitespace_stall_at_step_")) {
    return "whitespace_stall";
  }
  return reason;
}

export function SyncodeEvidencePanel({ experiment }: Props) {
  const isSyncodeMode = experiment.mode === "syncode";
  const total = experiment.total_steps;
  const active = experiment.syncode_active_steps ?? 0;
  const fallback = experiment.syncode_fallback_steps ?? 0;
  const parseErr = experiment.syncode_parse_error_steps ?? 0;
  const available = experiment.syncode_available ?? false;
  const rawFallbackUsed = fallback > 0;
  const maskStoreLoaded = experiment.syncode_mask_store_loaded ?? available;
  const larkLoaded = experiment.lark_grammar_loaded ?? true;
  const constraintActiveDuringGen =
    experiment.constraint_active_during_generation ?? (active > 0);
  const rawUnconstrainedUsed =
    experiment.raw_unconstrained_generation_used ?? rawFallbackUsed;
  const unconstrainedReason = experiment.unconstrained_reason ?? "";
  const finalValid = experiment.final_parse_valid ?? false;
  const constraintStatus = experiment.constraint_status ?? "off";
  const stoppedReason = experiment.syncode_stopped_reason ?? "";
  const rawFallbackPrevented = experiment.raw_fallback_prevented ?? false;

  const isSyncodeFastFail = stoppedReason.startsWith("syncode_parser_error");
  const isParseComplete =
    stoppedReason === "parse_complete" || stoppedReason === "eos_parse_complete";
  const treeAvailable = experiment.parse_tree_available === true;
  const eosAllowed = experiment.eos_allowed_at_completion ?? false;

  const fullyConstrained =
    isSyncodeMode &&
    maskStoreLoaded &&
    !rawFallbackUsed &&
    active === total &&
    total > 0 &&
    (constraintStatus === "full" || isParseComplete) &&
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

        {isSyncodeMode && isParseComplete && finalValid && (
          <span className="rounded border border-[#3fb950]/40 bg-[#3fb950]/10 px-1.5 py-0.5 text-[10px] font-medium text-[#3fb950]">
            ✓ {stoppedReason === "eos_parse_complete" ? "eos_parse_complete" : "parse_complete"}
            {" — grammar-valid module"}
          </span>
        )}

        {isSyncodeMode && isSyncodeFastFail && (
          <span className="rounded border border-[#f85149]/40 bg-[#f85149]/10 px-1.5 py-0.5 text-[10px] font-medium text-[#f85149]">
            ✗ SynCode parser error — generation stopped (no raw fallback)
          </span>
        )}

        {isSyncodeMode && !fullyConstrained && !isSyncodeFastFail && !isParseComplete && !finalValid && (
          <span className="rounded border border-[#f85149]/40 bg-[#f85149]/10 px-1.5 py-0.5 text-[10px] font-medium text-[#f85149]">
            ✗ SynCode output not grammar-valid under absolute token budget
          </span>
        )}

        {isSyncodeMode && !fullyConstrained && !isSyncodeFastFail && !isParseComplete && finalValid && (
          <span className="rounded border border-[#d29922]/40 bg-[#d29922]/10 px-1.5 py-0.5 text-[10px] font-medium text-[#d29922]">
            ⚠ output valid but constraint evidence is incomplete
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
          label="Lark grammar loaded"
          value={larkLoaded ? "yes" : "no"}
          ok={larkLoaded}
          bad={!larkLoaded}
        />
        <Chip
          label="SynCode mask store"
          value={maskStoreLoaded ? "loaded" : "unavailable"}
          ok={maskStoreLoaded}
          warn={isSyncodeMode && !maskStoreLoaded}
          bad={isSyncodeMode && !maskStoreLoaded}
        />
        <Chip
          label="Constraint active during gen"
          value={isSyncodeMode ? (constraintActiveDuringGen ? "yes" : "no") : "N/A"}
          ok={isSyncodeMode && constraintActiveDuringGen}
          bad={isSyncodeMode && !constraintActiveDuringGen}
        />
        <Chip
          label="Raw unconstrained used"
          value={isSyncodeMode ? (rawUnconstrainedUsed ? "yes" : "no") : "N/A"}
          ok={isSyncodeMode && !rawUnconstrainedUsed}
          bad={isSyncodeMode && rawUnconstrainedUsed}
        />
        {unconstrainedReason && isSyncodeMode && (
          <Chip label="Unconstrained reason" value={unconstrainedReason} bad warn />
        )}
        <Chip
          label="Constraint requested"
          value={isSyncodeMode ? "yes" : "no"}
          ok={isSyncodeMode}
        />
        <Chip
          label="Constraint applied steps"
          value={isSyncodeMode ? constraintAppliedLabel(experiment) : "N/A"}
          ok={constraintChipOk}
          warn={constraintChipWarn}
          bad={constraintChipBad}
        />
        <Chip
          label="Constraint stopped reason"
          value={stoppedReasonLabel(stoppedReason, experiment.mode)}
          ok={isParseComplete}
          warn={
            isSyncodeMode &&
            !isParseComplete &&
            !isSyncodeFastFail &&
            (stoppedReason === "max_tokens_incomplete" ||
              stoppedReason === "max_tokens" ||
              stoppedReason.startsWith("whitespace_stall") ||
              stoppedReason.startsWith("eos_at_step"))
          }
          bad={isSyncodeFastFail}
        />
        <Chip
          label="Raw fallback used"
          value={isSyncodeMode ? (rawFallbackUsed ? "yes" : "no") : "N/A"}
          ok={isSyncodeMode && !rawFallbackUsed}
          bad={isSyncodeMode && rawFallbackUsed}
        />
        <Chip
          label="Final parse valid"
          value={finalValid ? "yes" : "no"}
          ok={finalValid}
          bad={!finalValid && isSyncodeMode}
          warn={!finalValid && !isSyncodeMode}
        />
        <Chip
          label="Parser tree available"
          value={treeAvailable ? "yes" : "no"}
          ok={treeAvailable}
          bad={!treeAvailable && isSyncodeMode}
        />
        <Chip
          label="EOS allowed at completion"
          value={isSyncodeMode ? (eosAllowed ? "yes" : "no") : "N/A"}
          ok={isSyncodeMode && eosAllowed}
          warn={isSyncodeMode && !eosAllowed && finalValid}
          bad={isSyncodeMode && !eosAllowed && !finalValid}
        />
        {parseErr > 0 && (
          <Chip label="Step parse errors" value={`${parseErr} / ${total}`} warn bad />
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
          {isSyncodeFastFail && (
            <p className="mt-1">
              <strong>Generation stopped:</strong> SynCode parser failed and raw
              fallback is disabled (<code>ALLOW_RAW_FALLBACK=false</code>).
              Only the constrained prefix (
              {active} step{active !== 1 ? "s" : ""}) was returned.
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

      {isSyncodeMode && !maskStoreLoaded && (
        <p className="mt-2 text-[11px] text-[#f85149]">
          SynCode mask store unavailable — generation was not run unconstrained.
          {experiment.syncode_init_error && (
            <span className="mt-1 block font-mono opacity-90">
              {experiment.syncode_init_error}
            </span>
          )}
          {!experiment.syncode_init_error && experiment.syncode_error && (
            <span className="mt-1 block font-mono opacity-90">
              {experiment.syncode_error}
            </span>
          )}
        </p>
      )}
    </div>
  );
}
