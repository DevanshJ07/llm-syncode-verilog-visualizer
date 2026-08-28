/**
 * TypeScript mirrors of the Pydantic schemas in backend/app/models/schemas.py.
 * Keep these in sync whenever the backend schemas change.
 */

import type { ParserAnalysis } from "@/types/parserAnalysis";
import type { SyncodeParserEvidence } from "@/types/syncodeParserEvidence";

// ---------------------------------------------------------------------------
// Parser failure context
// ---------------------------------------------------------------------------

/**
 * Rich diagnostics around a Lark parse failure.
 * Populated when parse_tree_available === false and the backend successfully
 * extracted failure context from the exception.
 */
export interface ParserFailureContext {
  available: boolean;
  /** Numbered source lines ending at (and including) the error line. */
  prefix_before_error: string;
  /** The exact source line where the error occurred. */
  error_line_text: string;
  /** Spaces followed by "^" aligned to the error column. */
  caret_line: string;
  expected_terminals: string[];
  /** Concise LALR state description including research note when relevant. */
  likely_parser_state_summary: string;
  /** Heuristic natural-language explanation of the failure. */
  likely_interpretation: string;
}

// ---------------------------------------------------------------------------
// Core decoding data
// ---------------------------------------------------------------------------

/**
 * One candidate token at a decoding step.
 * Matches the JSON logging format from PROJECT_SPEC:
 *   { "token": "main", "probability": 0.42, "token_id": 1234 }
 */
export interface TopToken {
  token: string;       // decoded string (may contain spaces, newlines, special chars)
  probability: number; // softmax probability after temperature scaling [0, 1]
  token_id: number;    // vocabulary index
}

/** Extended candidate model for Syncode before/after distributions. */
export interface TokenCandidate {
  token_id: number;
  token_str: string;
  probability: number;
  is_masked: boolean;
  is_selected: boolean;
}

/** A token rejected by Syncode grammar masking, carrying its raw probability. */
export interface MaskedTokenEntry {
  token: string;
  token_id: number;
  raw_prob: number;
}

/** Top masked tokens by pre-mask probability (server-side, max 50 per step). */
export interface TopMaskedTokenEntry {
  token: string;
  token_id: number;
  pre_mask_prob: number;
  status: string;
}

export interface DecodingStep {
  step: number;
  /** Decoded text generated before this step (context fed into the model). */
  context: string;

  // --- Real generation fields ---
  /** Top-k candidates ranked by probability (after temperature scaling). */
  top_tokens: TopToken[];
  /** The token chosen by greedy decoding (argmax). */
  selected_token: string;
  /** Vocabulary index of the selected token. */
  selected_token_id: number;
  /** Shannon entropy H = -Σ p·log(p) over the full vocabulary distribution. */
  entropy_before: number | null;

  // --- Syncode fields ---
  /** Raw top-k BEFORE Syncode masking (with is_masked annotation). */
  top_tokens_before_syncode: TokenCandidate[];
  /** Rejected tokens with their raw probabilities (Syncode mode only). */
  masked_tokens: MaskedTokenEntry[];
  /** Top 50 masked tokens sorted by pre-mask probability (full vocab scan). */
  top_masked_tokens?: TopMaskedTokenEntry[];
  /** Top-k from the constrained (post-mask) distribution. */
  valid_tokens_after_syncode: TokenCandidate[];
  entropy_after: number | null;
  num_masked: number;

  // --- Syncode masking statistics per step ---
  vocab_size: number;
  valid_token_count: number;
  masked_token_count: number;
  masked_percentage: number;
  probability_mass_removed: number;

  // --- Grammar forensics ---
  /**
   * Legacy stringified SynCode AcceptSequence reprs (not Lark terminals,
   * not tokenizer vocabulary tokens). Prefer syncode_parser_evidence.
   */
  accept_sequences: string[];
  /**
   * Authoritative structured SynCode ParseResult evidence (Phase 4A.1).
   * Optional on older saved experiments that predate structured capture.
   */
  syncode_parser_evidence?: SyncodeParserEvidence;
  /** True when grammar masking was applied and changed at least one logit. */
  constraint_applied: boolean;

  // --- Parser recovery metadata ---
  /** True when the Syncode grammar parser threw at this step. */
  parser_error: boolean;
  /** Description of the parser exception (empty string when no error). */
  parser_error_message: string;
  /** True when raw logits were used because Syncode masking failed/was unavailable. */
  fallback_used: boolean;

  // --- Pipeline integrity diagnostics ---
  syncode_active: boolean;
  logits_diverge: boolean;
  raw_argmax_token_id: number;
  raw_argmax_token: string;
  constrained_argmax_token_id: number;
  constrained_argmax_token: string;
  selection_source: string;
  grammar_masked_count: number;
  whitespace_tokens_masked: boolean;
  selected_rank_raw: number;
  selected_rank_constrained: number;

  /** Post-generation Lark incremental parser snapshot for this step prefix. */
  incremental_parser_state?: IncrementalParserState;
}

/** Per-step incremental parser state from the tested Verilog grammar. */
export interface IncrementalParserState {
  available: boolean;
  prefix_output: string;
  /** valid_prefix | invalid_prefix | complete_parse | unavailable */
  prefix_parse_status: string;
  parser_accepts_end: boolean;
  expected_next_terminals: string[];
  accepted_next_terminals: string[];
  likely_grammar_context: string;
  likely_grammar_path: string;
  selected_token_interpretation: string;
  likely_parser_interpretation: string;
  partial_parse_view: string;
  parse_tree_text: string;
  parser_error_type: string;
  parser_error_message: string;
}

export interface ExperimentResult {
  experiment_id: string;
  prompt: string;
  /** "raw" | "syncode" */
  mode: string;
  generated_code: string;
  steps: DecodingStep[];
  total_steps: number;
  model_name: string;
  created_at: string;

  // --- Grammar / Syncode configuration metadata ---
  grammar_name: string;
  parser_name: string;
  syncode_mode_name: string;
  syncode_available: boolean;
  syncode_active_steps: number;
  syncode_fallback_steps: number;
  syncode_parse_error_steps: number;

  // --- Final output grammar validation (optional for legacy saved experiments) ---
  final_parse_valid?: boolean;
  final_parse_error?: string;
  unsupported_constructs_detected?: string[];

  // --- Honest constraint evidence ---
  constraint_requested?: boolean;
  constraint_status?: string;
  constraint_applied?: boolean;
  fallback_occurred?: boolean;
  syncode_error?: string;
  lark_grammar_loaded?: boolean;
  syncode_mask_store_loaded?: boolean;
  constraint_active_during_generation?: boolean;
  raw_unconstrained_generation_used?: boolean;
  unconstrained_reason?: string;
  syncode_init_error?: string;

  // --- Fail-fast / raw-fallback fields ---
  /** Why generation stopped in syncode mode, e.g. "parse_complete", "eos_parse_complete" */
  syncode_stopped_reason?: string;
  /** True when fail-fast prevented raw continuation after a parser error */
  raw_fallback_prevented?: boolean;
  /** True when model EOS was unmasked because Lark $END was accepted. */
  eos_allowed_at_completion?: boolean;
  /** Caller-requested / display limit (typically 120). */
  normal_max_tokens?: number;
  /** Hard cap including SynCode completion budget (typically 200). */
  absolute_max_tokens?: number;

  // --- Parse tree (built from final output using same grammar as validation) ---
  parse_tree_available?: boolean;
  parse_tree_text?: string;
  parse_tree_error_type?: string;
  parse_tree_error_message?: string;
  parse_tree_error_line?: number;
  parse_tree_error_column?: number;
  parse_tree_unexpected_token?: string;
  parse_tree_expected_terminals?: string[];
  parse_tree_previous_token?: string;
  /** Rich failure diagnostics — populated when parse_tree_available is false. */
  parser_failure_context?: ParserFailureContext;
  /**
   * Phase 3A/3B structured complete / partial / recovered parser analysis.
   * Prefer this over legacy parse_tree_* when present and not unavailable.
   */
  parser_analysis?: ParserAnalysis;
}

// ---------------------------------------------------------------------------
// API request / response shapes
// ---------------------------------------------------------------------------

export interface GenerateRequest {
  prompt: string;
  use_syncode: boolean;
  top_k: number;
  max_new_tokens: number;
  temperature: number;
}

/**
 * POST /generate response — full decoding trace returned inline.
 * The experiment is also persisted; use experiment_id with GET /experiment/{id}
 * if you need to retrieve it later.
 */
export interface GenerateResponse {
  // identity
  experiment_id: string;
  status: string;
  message: string;
  // generated output
  generated_text: string;
  model_name: string;
  mode: string;
  prompt: string;
  total_steps: number;
  // grammar / syncode metadata
  grammar_name: string;
  parser_name: string;
  syncode_mode_name: string;
  syncode_available: boolean;
  syncode_active_steps: number;
  syncode_fallback_steps: number;
  syncode_parse_error_steps: number;
  final_parse_valid?: boolean;
  final_parse_error?: string;
  unsupported_constructs_detected?: string[];
  constraint_requested?: boolean;
  constraint_status?: string;
  constraint_applied?: boolean;
  fallback_occurred?: boolean;
  syncode_error?: string;
  lark_grammar_loaded?: boolean;
  syncode_mask_store_loaded?: boolean;
  constraint_active_during_generation?: boolean;
  raw_unconstrained_generation_used?: boolean;
  unconstrained_reason?: string;
  syncode_init_error?: string;
  // fail-fast / raw-fallback
  syncode_stopped_reason?: string;
  raw_fallback_prevented?: boolean;
  eos_allowed_at_completion?: boolean;
  normal_max_tokens?: number;
  absolute_max_tokens?: number;
  // parse tree
  parse_tree_available?: boolean;
  parse_tree_text?: string;
  parse_tree_error_type?: string;
  parse_tree_error_message?: string;
  parse_tree_error_line?: number;
  parse_tree_error_column?: number;
  parse_tree_unexpected_token?: string;
  parse_tree_expected_terminals?: string[];
  parse_tree_previous_token?: string;
  parser_failure_context?: ParserFailureContext;
  /** Phase 3A structured parser analysis (complete / partial / recovered). */
  parser_analysis?: ParserAnalysis;
  // full decoding trace — one entry per generated token
  steps: DecodingStep[];
}

export interface StepResponse {
  step: DecodingStep;
  total_steps: number;
}

/** Convenience type for the compare view. */
export interface CompareState {
  raw: ExperimentResult | null;
  syncode: ExperimentResult | null;
}
