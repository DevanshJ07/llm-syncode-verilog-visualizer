"use client";

/**
 * useGeneration hook
 *
 * POST /generate returns the full decoding trace inline.
 * Empty or invalid traces are rejected — the hook surfaces HTTP 500 errors
 * from the backend and never enters "done" with zero steps.
 */

import { useState, useCallback } from "react";

import { postGenerate } from "@/lib/api";
import type { ExperimentResult, GenerateRequest } from "@/types/decoding";

type GenerationStatus = "idle" | "generating" | "done" | "error";

interface UseGenerationReturn {
  status: GenerationStatus;
  experiment: ExperimentResult | null;
  error: string | null;
  generate: (request: GenerateRequest) => Promise<string | null>;
  reset: () => void;
}

export function useGeneration(): UseGenerationReturn {
  const [status, setStatus] = useState<GenerationStatus>("idle");
  const [experiment, setExperiment] = useState<ExperimentResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const generate = useCallback(async (request: GenerateRequest) => {
    setStatus("generating");
    setError(null);
    setExperiment(null);

    try {
      const response = await postGenerate(request);

      const result: ExperimentResult = {
        experiment_id: response.experiment_id,
        prompt: response.prompt,
        mode: response.mode,
        generated_code: response.generated_text,
        steps: response.steps,
        total_steps: response.total_steps,
        model_name: response.model_name,
        created_at: new Date().toISOString(),
        // Grammar / SynCode metadata from the backend
        grammar_name: response.grammar_name ?? "verilog",
        parser_name: response.parser_name ?? "lalr",
        syncode_mode_name: response.syncode_mode_name ?? "grammar_mask",
        syncode_available: response.syncode_available ?? false,
        syncode_active_steps: response.syncode_active_steps ?? 0,
        syncode_fallback_steps: response.syncode_fallback_steps ?? 0,
        syncode_parse_error_steps: response.syncode_parse_error_steps ?? 0,
        final_parse_valid: response.final_parse_valid ?? false,
        final_parse_error: response.final_parse_error ?? "",
        unsupported_constructs_detected: response.unsupported_constructs_detected ?? [],
        constraint_requested: response.constraint_requested ?? false,
        constraint_status: response.constraint_status ?? "off",
        constraint_applied: response.constraint_applied ?? false,
        fallback_occurred: response.fallback_occurred ?? false,
        syncode_error: response.syncode_error ?? "",
        // Fail-fast / raw-fallback reason
        syncode_stopped_reason: response.syncode_stopped_reason ?? "",
        raw_fallback_prevented: response.raw_fallback_prevented ?? false,
        eos_allowed_at_completion: response.eos_allowed_at_completion ?? false,
        normal_max_tokens: response.normal_max_tokens ?? 120,
        absolute_max_tokens: response.absolute_max_tokens ?? 200,
        // Parse tree — built server-side from the same grammar as final validation.
        // parser_failure_context is a nested object; pass through as-is.
        parse_tree_available: response.parse_tree_available ?? false,
        parse_tree_text: response.parse_tree_text ?? "",
        parse_tree_error_type: response.parse_tree_error_type ?? "",
        parse_tree_error_message: response.parse_tree_error_message ?? "",
        parse_tree_error_line: response.parse_tree_error_line ?? 0,
        parse_tree_error_column: response.parse_tree_error_column ?? 0,
        parse_tree_unexpected_token: response.parse_tree_unexpected_token ?? "",
        parse_tree_expected_terminals: response.parse_tree_expected_terminals ?? [],
        parse_tree_previous_token: response.parse_tree_previous_token ?? "",
        parser_failure_context: response.parser_failure_context,
      };

      // Final client-side guard — never show visualization with a truly empty trace,
      // UNLESS syncode fail-fast fired (0 steps is valid when the parser fails on
      // the very first generated token).
      const isSyncodeFastFail =
        result.syncode_stopped_reason?.startsWith("syncode_parser_error") ?? false;
      if ((result.steps.length === 0 || result.total_steps === 0) && !isSyncodeFastFail) {
        throw new Error(
          "Backend returned HTTP 200 but decoding trace is empty. " +
            "This should not happen — check backend logs."
        );
      }

      setExperiment(result);
      setStatus("done");
      return response.experiment_id;
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      console.error("[useGeneration] failed:", message);
      setError(message);
      setExperiment(null);
      setStatus("error");
      return null;
    }
  }, []);

  const reset = useCallback(() => {
    setStatus("idle");
    setExperiment(null);
    setError(null);
  }, []);

  return { status, experiment, error, generate, reset };
}
