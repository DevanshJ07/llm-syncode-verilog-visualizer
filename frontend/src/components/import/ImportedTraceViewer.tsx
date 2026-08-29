"use client";

/**
 * ImportedTraceViewer — Phase 5A.1 Token Trace workspace panel.
 *
 * Active step / playback state is owned by the parent workspace so:
 * - switching evidence tabs does not reset the step
 * - switching top-level workspaces preserves the step for the same prompt
 * - prompt changes reset the step (parent responsibility)
 *
 * Renders only the active step's detail (no per-step DOM cards).
 */

import { useEffect, useMemo, useState } from "react";

import { ImportedOutputAtStep } from "@/components/import/ImportedOutputAtStep";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { ProvenanceValue } from "@/components/ui/ProvenanceValue";
import { StepPlayer } from "@/components/visualization/StepPlayer";
import { ImportedSyncodeParserEvidenceSection } from "@/components/visualization/SyncodeParserEvidencePanel";
import { cn } from "@/lib/utils";
import {
  derivePrefixFromSelected,
  escapeTokenForDisplay,
  evidenceChannelsForStep,
  explainIntervention,
  findNextIntervention,
  getBlockedFlag,
  getGeneratedPrefixTokenCount,
  isRecordedIntervention,
  parseTopRawTokens,
  stepMarkerKind,
  summarizeTrace,
  tokenRefLabel,
  vocabLogitCount,
} from "@/lib/importedTrace";
import { formatProvDisplay } from "@/lib/provenanceDisplay";
import type { NormalizedPromptResult, NormalizedTraceStep } from "@/types/normalized";
import { isUnavailable, provenanceLabel, type ProvenanceKind } from "@/types/provenance";

export type TraceEvidenceTab =
  | "decision"
  | "syncode"
  | "tokenizer"
  | "prefix_lark"
  | "top_raw"
  | "availability";

const EVIDENCE_TABS: { id: TraceEvidenceTab; label: string }[] = [
  { id: "decision", label: "Decision" },
  { id: "syncode", label: "SynCode Parser" },
  { id: "tokenizer", label: "Tokenizer Mask" },
  { id: "prefix_lark", label: "Prefix & Lark" },
  { id: "top_raw", label: "Top Raw Tokens" },
  { id: "availability", label: "Availability" },
];

export interface ImportedTraceViewerProps {
  prompt: NormalizedPromptResult;
  /** Controlled active step (0-based array index). */
  activeIndex: number;
  onActiveIndexChange: (index: number) => void;
  isPlaying: boolean;
  onPlayingChange: (playing: boolean) => void;
  playIntervalMs: number;
  onPlayIntervalChange: (ms: number) => void;
  evidenceTab: TraceEvidenceTab;
  onEvidenceTabChange: (tab: TraceEvidenceTab) => void;
  /**
   * When false, the parent owns the sticky toolbar (Phase 5A.1 workspace shell).
   * Default true for standalone use.
   */
  showToolbar?: boolean;
}

const MARKER_COLOR: Record<string, string> = {
  ordinary: "#30363d",
  intervention: "#f85149",
  parse_failed: "#d29922",
  both: "#a371f7",
  active: "#58a6ff",
};

function FlagCell({
  label,
  value,
  kind,
}: {
  label: string;
  value: boolean | null;
  kind: ProvenanceKind;
}) {
  const text =
    value === null
      ? "Unavailable"
      : `${value ? "True" : "False"} — ${provenanceLabel(kind)}`;
  return (
    <div>
      <p className="text-[10px] uppercase tracking-wider text-[#484f58]">{label}</p>
      <p
        className={cn(
          "mt-0.5 font-mono text-sm",
          value === null ? "text-[#484f58]" : "text-[#e6edf3]"
        )}
      >
        {text}
      </p>
    </div>
  );
}

function TokenDecisionRow({
  label,
  step,
  field,
}: {
  label: string;
  step: NormalizedTraceStep;
  field: "raw_preferred" | "constrained_preferred" | "selected";
}) {
  const info = tokenRefLabel(step[field]);
  return (
    <div className="rounded-md border border-surface-border bg-surface px-3 py-2">
      <p className="text-[10px] uppercase tracking-wider text-[#484f58]">{label}</p>
      <p
        className={cn(
          "mt-1 break-all font-mono text-sm",
          info.unavailable ? "text-[#484f58]" : "text-[#e6edf3]"
        )}
      >
        {info.tokenDisplay}
      </p>
      <p className="mt-0.5 font-mono text-[11px] text-[#8b949e]">
        id={info.idDisplay}
        {!info.unavailable && (
          <span className="ml-2 text-[#484f58]">
            · {provenanceLabel(info.kind)}
          </span>
        )}
      </p>
    </div>
  );
}

function CompactTimeline({
  steps,
  activeIndex,
  onSelect,
}: {
  steps: NormalizedTraceStep[];
  activeIndex: number;
  onSelect: (i: number) => void;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-3 text-[10px] text-[#484f58]">
        <span className="flex items-center gap-1">
          <span
            className="inline-block h-2.5 w-2.5 rounded-sm"
            style={{ background: MARKER_COLOR.ordinary }}
          />
          Ordinary
        </span>
        <span className="flex items-center gap-1">
          <span
            className="inline-block h-2.5 w-2.5 rounded-sm"
            style={{ background: MARKER_COLOR.intervention }}
          />
          Intervention
        </span>
        <span className="flex items-center gap-1">
          <span
            className="inline-block h-2.5 w-2.5 rounded-sm"
            style={{ background: MARKER_COLOR.parse_failed }}
          />
          Parse failed
        </span>
        <span className="flex items-center gap-1">
          <span
            className="inline-block h-2.5 w-2.5 rounded-sm"
            style={{ background: MARKER_COLOR.both }}
          />
          Both
        </span>
      </div>
      <div
        className="flex max-h-16 flex-wrap content-start gap-px overflow-y-auto rounded border border-surface-border bg-surface p-1"
        role="listbox"
        aria-label="Trace step timeline"
      >
        {steps.map((step, i) => {
          const kind = stepMarkerKind(step);
          const active = i === activeIndex;
          return (
            <button
              key={`${step.step_index}-${i}`}
              type="button"
              role="option"
              aria-selected={active}
              title={`Step ${step.step_index} (${kind})`}
              onClick={() => onSelect(i)}
              className={cn(
                "h-3 w-1.5 shrink-0 rounded-[1px] transition-transform",
                active &&
                  "scale-y-125 ring-1 ring-accent-blue ring-offset-1 ring-offset-surface"
              )}
              style={{
                background: active ? MARKER_COLOR.active : MARKER_COLOR[kind],
              }}
            />
          );
        })}
      </div>
    </div>
  );
}

export function ImportedTraceToolbar({
  steps,
  activeIndex,
  setActiveIndex,
  isPlaying,
  setIsPlaying,
  playIntervalMs,
  setPlayIntervalMs,
}: {
  steps: NormalizedTraceStep[];
  activeIndex: number;
  setActiveIndex: (i: number) => void;
  isPlaying: boolean;
  setIsPlaying: (playing: boolean) => void;
  playIntervalMs: number;
  setPlayIntervalMs: (ms: number) => void;
}) {
  const overview = useMemo(() => summarizeTrace(steps), [steps]);
  const [jumpValue, setJumpValue] = useState(String(steps[activeIndex]?.step_index ?? 1));
  const step = steps[activeIndex] ?? null;

  useEffect(() => {
    setJumpValue(String(steps[activeIndex]?.step_index ?? activeIndex + 1));
  }, [activeIndex, steps]);

  function goIntervention(dir: 1 | -1) {
    const next = findNextIntervention(steps, activeIndex, dir);
    if (next !== null) setActiveIndex(next);
  }

  function handleJump() {
    if (steps.length === 0) return;
    const n = Number(jumpValue);
    if (!Number.isFinite(n)) return;
    const byRecorded = steps.findIndex((s) => s.step_index === n);
    if (byRecorded >= 0) {
      setActiveIndex(byRecorded);
      return;
    }
    const ordinal = Math.max(1, Math.min(steps.length, Math.floor(n))) - 1;
    setActiveIndex(ordinal);
  }

  return (
    <div className="flex flex-col gap-2">
      <CompactTimeline
        steps={steps}
        activeIndex={activeIndex}
        onSelect={setActiveIndex}
      />
      <StepPlayer
        totalSteps={steps.length}
        currentStep={activeIndex}
        isPlaying={isPlaying}
        onStepChange={setActiveIndex}
        onPlayPause={() => setIsPlaying(!isPlaying)}
        playIntervalMs={playIntervalMs}
        onIntervalChange={setPlayIntervalMs}
      />
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-xs text-[#8b949e]">
          recorded step_index={" "}
          <span className="text-[#e6edf3]">{step?.step_index ?? "—"}</span>
          {" · "}
          ordinal {activeIndex + 1}/{steps.length}
        </span>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1 text-xs text-[#8b949e]">
            Jump to step
            <input
              type="number"
              value={jumpValue}
              onChange={(e) => setJumpValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleJump();
              }}
              className="w-20 rounded border border-surface-border bg-surface px-2 py-1 font-mono text-xs text-[#e6edf3]"
              aria-label="Jump to recorded step index"
            />
          </label>
          <Button type="button" size="sm" variant="secondary" onClick={handleJump}>
            Go
          </Button>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            disabled={findNextIntervention(steps, activeIndex, -1) === null}
            onClick={() => goIntervention(-1)}
          >
            ← Intervention
          </Button>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            disabled={findNextIntervention(steps, activeIndex, 1) === null}
            onClick={() => goIntervention(1)}
          >
            Intervention →
          </Button>
          <Badge variant="masked">
            {overview.interventionEvidenceAvailable
              ? `${overview.interventionCount} interventions`
              : "interventions unavailable"}
          </Badge>
        </div>
      </div>
    </div>
  );
}

export function ImportedTraceViewer({
  prompt,
  activeIndex,
  onActiveIndexChange,
  isPlaying,
  onPlayingChange,
  playIntervalMs,
  onPlayIntervalChange,
  evidenceTab,
  onEvidenceTabChange,
  showToolbar = true,
}: ImportedTraceViewerProps) {
  const steps = prompt.steps;

  useEffect(() => {
    if (steps.length > 0 && activeIndex >= steps.length - 1) {
      onPlayingChange(false);
    }
  }, [activeIndex, steps.length, onPlayingChange]);

  const overview = useMemo(() => summarizeTrace(steps), [steps]);
  const step = steps[activeIndex] ?? null;

  const topRaw = useMemo(
    () => (step ? parseTopRawTokens(step) : null),
    [step]
  );
  const channels = useMemo(
    () => (step ? evidenceChannelsForStep(step) : []),
    [step]
  );
  const prefixBefore = useMemo(
    () => derivePrefixFromSelected(steps, activeIndex),
    [steps, activeIndex]
  );
  const prefixAfter = useMemo(
    () => derivePrefixFromSelected(steps, activeIndex + 1),
    [steps, activeIndex]
  );

  if (steps.length === 0) {
    return (
      <div className="rounded-md border border-dashed border-surface-border px-4 py-8 text-center text-sm text-[#8b949e]">
        Empty trace — no recorded decoding steps for this prompt.
      </div>
    );
  }

  const blocked = step ? getBlockedFlag(step, "raw_argmax_blocked") : null;
  const equals = step
    ? getBlockedFlag(step, "selected_equals_constrained_argmax")
    : null;
  const finite = step
    ? getBlockedFlag(step, "constrained_argmax_finite")
    : null;
  const vocabCount = step ? vocabLogitCount(step) : null;
  const prefixTokCount = step ? getGeneratedPrefixTokenCount(step) : null;

  const rawId =
    step && !isUnavailable(step.raw_preferred)
      ? step.raw_preferred.value?.token_id ?? null
      : null;
  const constrainedId =
    step && !isUnavailable(step.constrained_preferred)
      ? step.constrained_preferred.value?.token_id ?? null
      : null;
  const selectedId =
    step && !isUnavailable(step.selected)
      ? step.selected.value?.token_id ?? null
      : null;
  const rawTok =
    step && !isUnavailable(step.raw_preferred)
      ? step.raw_preferred.value?.token ?? null
      : null;
  const constrainedTok =
    step && !isUnavailable(step.constrained_preferred)
      ? step.constrained_preferred.value?.token ?? null
      : null;
  const selectedTok =
    step && !isUnavailable(step.selected)
      ? step.selected.value?.token ?? null
      : null;
  const selectedUnavailable =
    !step || isUnavailable(step.selected) || !step.selected.value;
  const selectedToken =
    !selectedUnavailable && step
      ? step.selected.value?.token ?? null
      : null;

  return (
    <div className="flex flex-col gap-3">
      {/* Compact trace summary (not full evidence) */}
      <div className="grid gap-2 rounded-md border border-surface-border bg-surface-raised px-3 py-2 sm:grid-cols-4">
        <div>
          <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
            Total steps
          </p>
          <p className="font-mono text-sm text-[#e6edf3]">{overview.totalSteps}</p>
        </div>
        <div>
          <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
            Interventions
          </p>
          <p className="font-mono text-sm text-[#e6edf3]">
            {overview.interventionEvidenceAvailable
              ? overview.interventionCount
              : "Unavailable"}
          </p>
        </div>
        <div>
          <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
            Parse-failed steps
          </p>
          <p className="font-mono text-sm text-[#e6edf3]">
            {overview.parseFailedCount}
          </p>
        </div>
        <div>
          <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
            First intervention
          </p>
          <p className="font-mono text-sm text-[#e6edf3]">
            {overview.firstInterventionIndex === null
              ? overview.interventionEvidenceAvailable
                ? "None"
                : "Unavailable"
              : `step ${steps[overview.firstInterventionIndex].step_index}`}
          </p>
        </div>
      </div>

      {showToolbar && (
        <ImportedTraceToolbar
          steps={steps}
          activeIndex={activeIndex}
          setActiveIndex={onActiveIndexChange}
          isPlaying={isPlaying}
          setIsPlaying={onPlayingChange}
          playIntervalMs={playIntervalMs}
          setPlayIntervalMs={onPlayIntervalChange}
        />
      )}

      {!step ? (
        <p className="text-sm text-[#8b949e]">No active step.</p>
      ) : (
        <div className="grid min-h-0 gap-3 lg:grid-cols-2 lg:items-stretch">
          {/* LEFT: derived output at this step */}
          <div className="flex min-h-[min(50vh,28rem)] flex-col rounded-md border border-surface-border bg-surface-raised p-3 lg:min-h-[min(70vh,40rem)]">
            <ImportedOutputAtStep
              prefixBefore={prefixBefore}
              selectedToken={selectedToken}
              selectedUnavailable={selectedUnavailable || selectedToken === null}
              className="min-h-0 flex-1"
            />
          </div>

          {/* RIGHT: evidence tabs */}
          <div className="flex min-h-[min(50vh,28rem)] flex-col rounded-md border border-surface-border bg-surface-raised lg:min-h-[min(70vh,40rem)]">
            <div
              role="tablist"
              aria-label="Step evidence"
              className="flex shrink-0 flex-wrap gap-1 border-b border-surface-border p-2"
            >
              {EVIDENCE_TABS.map((tab) => {
                const selected = evidenceTab === tab.id;
                return (
                  <button
                    key={tab.id}
                    type="button"
                    role="tab"
                    id={`evidence-tab-${tab.id}`}
                    aria-selected={selected}
                    aria-controls={`evidence-panel-${tab.id}`}
                    tabIndex={selected ? 0 : -1}
                    onClick={() => onEvidenceTabChange(tab.id)}
                    className={cn(
                      "rounded px-2 py-1 text-[11px] font-medium transition-colors",
                      selected
                        ? "bg-accent-blue/15 text-accent-blue"
                        : "text-[#8b949e] hover:bg-surface hover:text-[#e6edf3]"
                    )}
                  >
                    {tab.label}
                  </button>
                );
              })}
            </div>

            <div
              role="tabpanel"
              id={`evidence-panel-${evidenceTab}`}
              aria-labelledby={`evidence-tab-${evidenceTab}`}
              className="min-h-0 flex-1 overflow-y-auto p-3"
            >
              {evidenceTab === "decision" && (
                <section className="flex flex-col gap-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
                      Token decision
                    </h3>
                    {isRecordedIntervention(step) && (
                      <Badge variant="masked">Intervention</Badge>
                    )}
                    {stepMarkerKind(step) === "parse_failed" ||
                    stepMarkerKind(step) === "both" ? (
                      <Badge variant="info">syncode_parse_failed</Badge>
                    ) : null}
                  </div>
                  <div className="grid gap-2 md:grid-cols-1 xl:grid-cols-3">
                    <TokenDecisionRow
                      label="Model raw argmax (pre-mask)"
                      step={step}
                      field="raw_preferred"
                    />
                    <TokenDecisionRow
                      label="Constrained argmax"
                      step={step}
                      field="constrained_preferred"
                    />
                    <TokenDecisionRow
                      label="Selected token"
                      step={step}
                      field="selected"
                    />
                  </div>
                  <div className="grid gap-2 sm:grid-cols-3">
                    <FlagCell
                      label="raw_argmax_blocked"
                      value={blocked!.value}
                      kind={blocked!.kind}
                    />
                    <FlagCell
                      label="selected_equals_constrained_argmax"
                      value={equals!.value}
                      kind={equals!.kind}
                    />
                    <FlagCell
                      label="constrained_argmax_finite"
                      value={finite!.value}
                      kind={finite!.kind}
                    />
                  </div>
                  <p className="text-xs leading-relaxed text-[#8b949e]">
                    {explainIntervention(step)}
                  </p>
                  <p className="text-[10px] text-[#484f58]">
                    Whitespace tokens are shown escaped (e.g. &quot;\n&quot;,
                    &quot;,\n&quot;, spaces as ·). Strings are not trimmed.
                  </p>
                  {step.step_warnings.length > 0 && (
                    <div className="rounded-md border border-amber-500/30 bg-amber-900/10 px-3 py-2 text-xs text-[#e6edf3]">
                      <p className="font-semibold text-amber-200/80">
                        Step warnings
                      </p>
                      <ul className="mt-1 list-disc pl-4">
                        {step.step_warnings.map((w, i) => (
                          <li key={i}>{w}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </section>
              )}

              {evidenceTab === "syncode" && (
                <ImportedSyncodeParserEvidenceSection
                  primary={step.syncode_parser_evidence}
                  recordedSibling={step.syncode_parser_evidence_recorded}
                />
              )}

              {evidenceTab === "tokenizer" && (
                <section className="flex flex-col gap-3">
                  <div>
                    <h3 className="text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
                      Tokenizer mask
                    </h3>
                    <p className="mt-1 text-[10px] text-[#484f58]">
                      Tokenizer-mask evidence describes actual vocabulary tokens
                      / logits allowed or blocked. Not SynCode accept sequences
                      and not Lark expected terminals.
                    </p>
                  </div>
                  <h4 className="text-[10px] font-semibold uppercase tracking-wider text-[#484f58]">
                    Mask statistics
                  </h4>
                  <div className="grid gap-2 sm:grid-cols-2">
                    <div>
                      <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
                        Vocabulary-logit count
                      </p>
                      <p
                        className={cn(
                          "mt-0.5 font-mono text-sm",
                          vocabCount!.value === null
                            ? "text-[#484f58]"
                            : "text-[#e6edf3]"
                        )}
                      >
                        {vocabCount!.value === null
                          ? "Unavailable"
                          : `${vocabCount!.value} — ${provenanceLabel(vocabCount!.kind)}`}
                      </p>
                    </div>
                    <ProvenanceValue
                      label="allowed_token_count"
                      value={step.valid_token_count}
                    />
                    <ProvenanceValue
                      label="newly_masked_token_count"
                      value={step.newly_masked_token_count}
                    />
                    <ProvenanceValue
                      label="masked_token_count"
                      value={step.masked_token_count}
                    />
                  </div>
                  <p className="text-[10px] text-[#484f58]">
                    Field names match the recorded schema.
                    newly_masked_token_count is not reinterpreted as a stronger
                    claim.
                  </p>
                </section>
              )}

              {evidenceTab === "prefix_lark" && (
                <section className="flex flex-col gap-4">
                  <div>
                    <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
                      Prefix information
                    </h3>
                    <div className="grid gap-3">
                      <div>
                        <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
                          Recorded prefix_tail
                        </p>
                        <p className="mt-1 max-h-24 overflow-y-auto break-all font-mono text-xs text-[#e6edf3]">
                          {formatProvDisplay(step.prefix_before_selected, {
                            formatValue: (v) =>
                              typeof v === "string"
                                ? JSON.stringify(v)
                                : String(v),
                          }).text}
                        </p>
                        <p className="mt-1 text-[10px] text-[#484f58]">
                          Not the complete prefix — may be truncated. Shown with
                          JSON escaping so whitespace stays visible.
                        </p>
                      </div>
                      <div>
                        <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
                          generated_prefix_tokens
                        </p>
                        <p className="mt-1 font-mono text-sm text-[#e6edf3]">
                          {prefixTokCount!.value === null
                            ? "Unavailable"
                            : `${prefixTokCount!.value} — ${provenanceLabel(prefixTokCount!.kind)}`}
                        </p>
                      </div>
                      <div>
                        <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
                          Derived prefix before selected
                        </p>
                        <p className="mt-1 max-h-24 overflow-y-auto break-all font-mono text-xs text-accent-blue">
                          {isUnavailable(prefixBefore) ||
                          prefixBefore.value === null
                            ? "Unavailable"
                            : `${JSON.stringify(prefixBefore.value)} — Derived`}
                        </p>
                      </div>
                      <div>
                        <p className="text-[10px] uppercase tracking-wider text-[#484f58]">
                          Derived prefix after selected
                        </p>
                        <p className="mt-1 max-h-24 overflow-y-auto break-all font-mono text-xs text-accent-blue">
                          {isUnavailable(prefixAfter) ||
                          prefixAfter.value === null
                            ? "Unavailable"
                            : `${JSON.stringify(prefixAfter.value)} — Derived`}
                        </p>
                      </div>
                    </div>
                  </div>

                  <div>
                    <h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
                      Parser flags
                    </h3>
                    {isUnavailable(step.parser_info) || !step.parser_info.value ? (
                      <p className="text-sm text-[#484f58]">
                        Parser details were not recorded for this step
                        (Unavailable).
                      </p>
                    ) : (
                      <p className="font-mono text-sm text-[#e6edf3]">
                        syncode_parse_failed:{" "}
                        {step.parser_info.value.syncode_parse_failed === true
                          ? "True — Recorded"
                          : step.parser_info.value.syncode_parse_failed === false
                            ? "False — Recorded"
                            : "Unavailable"}
                      </p>
                    )}
                  </div>

                  <div>
                    <h3 className="mb-1 text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
                      Lark incremental parser
                    </h3>
                    <p className="mb-3 text-[10px] text-[#484f58]">
                      Lark terminals describe grammar-parser expectations. They
                      are not SynCode accept sequences and not tokenizer
                      vocabulary tokens.
                    </p>
                    {isUnavailable(step.expected_terminals) ||
                    step.expected_terminals.value === null ? (
                      <p className="text-sm text-[#484f58]">
                        Expected Lark terminals unavailable for this step.
                      </p>
                    ) : step.expected_terminals.value.length === 0 ? (
                      <p className="text-sm text-[#484f58]">
                        Recorded empty Lark expected-terminal list.
                      </p>
                    ) : (
                      <div className="flex flex-wrap gap-1">
                        {step.expected_terminals.value.map((t, i) => (
                          <span
                            key={`${t}-${i}`}
                            className="rounded border border-[#30363d] bg-[#161b22] px-1.5 py-0.5 font-mono text-[11px] text-[#58a6ff]"
                          >
                            {t}
                          </span>
                        ))}
                      </div>
                    )}
                    <p className="mt-2 text-[10px] text-[#484f58]">
                      Provenance:{" "}
                      {provenanceLabel(
                        isUnavailable(step.expected_terminals)
                          ? "unavailable"
                          : step.expected_terminals.provenance.kind
                      )}
                    </p>
                  </div>
                </section>
              )}

              {evidenceTab === "top_raw" && (
                <section className="flex flex-col gap-2">
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
                    Top raw tokens
                  </h3>
                  <p className="text-[10px] text-[#484f58]">
                    Recorded top-k subset only — not the full vocabulary. Logits
                    are shown as recorded; probabilities and entropy are not
                    derived.
                  </p>
                  {topRaw!.unavailable ? (
                    <p className="text-sm text-[#484f58]">Unavailable</p>
                  ) : topRaw!.items.length === 0 ? (
                    <p className="text-sm text-[#484f58]">
                      Recorded empty top-k list — []
                    </p>
                  ) : (
                    <div className="overflow-x-auto">
                      <table className="w-full min-w-[28rem] border-collapse text-left text-xs">
                        <thead>
                          <tr className="border-b border-surface-border text-[#484f58]">
                            <th className="px-2 py-1.5 font-medium">Token</th>
                            <th className="px-2 py-1.5 font-medium">ID</th>
                            <th className="px-2 py-1.5 font-medium">Raw logit</th>
                            <th className="px-2 py-1.5 font-medium">
                              Allowed after SynCode
                            </th>
                            <th className="px-2 py-1.5 font-medium">Roles</th>
                          </tr>
                        </thead>
                        <tbody>
                          {topRaw!.items.map((row, i) => {
                            const roles: string[] = [];
                            if (
                              (row.tokenId !== null &&
                                rawId !== null &&
                                row.tokenId === rawId) ||
                              (row.token !== null &&
                                rawTok !== null &&
                                row.token === rawTok)
                            ) {
                              roles.push("raw argmax");
                            }
                            if (
                              (row.tokenId !== null &&
                                constrainedId !== null &&
                                row.tokenId === constrainedId) ||
                              (row.token !== null &&
                                constrainedTok !== null &&
                                row.token === constrainedTok)
                            ) {
                              roles.push("constrained argmax");
                            }
                            if (
                              (row.tokenId !== null &&
                                selectedId !== null &&
                                row.tokenId === selectedId) ||
                              (row.token !== null &&
                                selectedTok !== null &&
                                row.token === selectedTok)
                            ) {
                              roles.push("selected");
                            }
                            const highlight = roles.length > 0;
                            return (
                              <tr
                                key={i}
                                className={cn(
                                  "border-b border-surface-border/60",
                                  highlight && "bg-accent-blue/5"
                                )}
                              >
                                <td className="px-2 py-1.5 font-mono text-[#e6edf3]">
                                  {row.token === null
                                    ? "Unavailable"
                                    : escapeTokenForDisplay(row.token)}
                                </td>
                                <td className="px-2 py-1.5 font-mono text-[#8b949e]">
                                  {row.tokenId === null
                                    ? "Unavailable"
                                    : row.tokenId}
                                </td>
                                <td className="px-2 py-1.5 font-mono text-[#e6edf3]">
                                  {row.logit === null
                                    ? "Unavailable"
                                    : String(row.logit)}
                                </td>
                                <td className="px-2 py-1.5 font-mono text-[#8b949e]">
                                  {row.allowedAfterSyncode === null
                                    ? "Unavailable"
                                    : row.allowedAfterSyncode
                                      ? "True"
                                      : "False"}
                                </td>
                                <td className="px-2 py-1.5 text-[#8b949e]">
                                  {roles.length ? roles.join(", ") : "—"}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                      <p className="mt-1 text-[10px] text-[#484f58]">
                        Provenance: {provenanceLabel(topRaw!.kind)}
                      </p>
                    </div>
                  )}
                </section>
              )}

              {evidenceTab === "availability" && (
                <section>
                  <h3 className="mb-3 text-xs font-semibold uppercase tracking-wider text-[#8b949e]">
                    Evidence availability
                  </h3>
                  <ul className="grid gap-1.5">
                    {channels.map((row) => (
                      <li
                        key={row.channel}
                        className="flex items-baseline justify-between gap-2 rounded border border-surface-border/50 px-2 py-1 text-xs"
                      >
                        <span className="text-[#8b949e]">{row.channel}</span>
                        <span
                          className={cn(
                            "shrink-0 font-mono",
                            row.status === "unavailable"
                              ? "text-[#484f58]"
                              : row.status === "derived"
                                ? "text-accent-blue"
                                : row.status === "recomputed"
                                  ? "text-accent-purple"
                                  : "text-[#e6edf3]"
                          )}
                          title={row.note}
                        >
                          {provenanceLabel(
                            row.status === "mixed" ? "unavailable" : row.status
                          )}
                        </span>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
