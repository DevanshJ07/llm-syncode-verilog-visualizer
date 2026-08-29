/**
 * TypeScript mirrors of backend/app/models/normalized.py (schema 2A.2).
 * Keep in sync with the committed Pydantic models.
 */

import type { Prov } from "@/types/provenance";
import type { ParserAnalysis } from "@/types/parserAnalysis";
import type { SyncodeParserEvidence } from "@/types/syncodeParserEvidence";

export type ExperimentSourceType = "live_local" | "imported";

export interface SourceFileRef {
  path: string;
  category?: string;
  role?: string;
}

export interface TokenRef {
  token_id?: number | null;
  token?: string | null;
}

export interface NormalizedTraceStep {
  step_index: number;
  prefix_before_selected: Prov<string>;
  raw_preferred: Prov<TokenRef>;
  selected: Prov<TokenRef>;
  constrained_preferred: Prov<TokenRef>;
  masking_changed_selection: Prov<boolean>;
  raw_rank: Prov<number>;
  selected_rank: Prov<number>;
  raw_probability: Prov<number>;
  selected_probability: Prov<number>;
  entropy_before: Prov<number>;
  entropy_after: Prov<number>;
  valid_token_count: Prov<number>;
  masked_token_count: Prov<number>;
  newly_masked_token_count: Prov<number>;
  blocked_token_info: Prov<Record<string, unknown>>;
  recorded_top_raw_tokens: Prov<unknown[]>;
  recorded_vocab_logits: Prov<unknown>;
  parser_info: Prov<Record<string, unknown>>;
  expected_terminals: Prov<string[]>;
  syncode_accept_sequences: Prov<unknown[]>;
  remainder_state: Prov<string>;
  eos_eligible: Prov<boolean>;
  /**
   * Phase 4A.2 primary SynCode parser evidence (recomputed when requested,
   * otherwise bundle-recorded if present).
   */
  syncode_parser_evidence?: Prov<SyncodeParserEvidence>;
  /**
   * Preserved bundle-recorded sibling when recomputation also ran.
   * Never silently overwritten by recomputed evidence.
   */
  syncode_parser_evidence_recorded?: Prov<SyncodeParserEvidence>;
  step_warnings: string[];
}

export interface NormalizedPromptResult {
  problem_id: string;
  prompt_text: Prov<string>;
  reference_program: Prov<string>;
  generated_output: Prov<string>;
  reconstructed_from_tokens: Prov<string>;
  reconstruction_matches_authoritative: Prov<boolean>;
  termination_reason: Prov<string>;
  generated_token_count: Prov<number>;
  token_limit: Prov<number>;
  grammar_valid: Prov<boolean>;
  grammar_verdict: Prov<string>;
  parse_error: Prov<string>;
  findings: Prov<unknown>;
  mask_counts: Prov<Record<string, unknown>>;
  recomputed_grammar_verdict: Prov<string>;
  recomputed_parse_error: Prov<string>;
  /**
   * Phase 3A structured parser analysis. Unavailable unless
   * recompute_with_current_grammar was true at import. Optional for
   * pre-3A persisted JSON that still loads via defaults on the backend.
   */
  parser_analysis?: Prov<ParserAnalysis>;
  steps: NormalizedTraceStep[];
  source_files: SourceFileRef[];
  warnings: string[];
}

export interface NormalizedExperiment {
  schema_version: string;
  experiment_id: string;
  source_type: ExperimentSourceType;
  experiment_name: string;
  created_at: string;
  llm_metadata: Prov<Record<string, unknown>>;
  grammar_metadata: Prov<Record<string, unknown>>;
  tokenizer_metadata: Prov<Record<string, unknown>>;
  decoding_metadata: Prov<Record<string, unknown>>;
  runtime_metadata: Prov<Record<string, unknown>>;
  prompt_results: NormalizedPromptResult[];
  import_warnings: string[];
}

/** Lightweight list row from GET /imported-experiments (no steps). */
export interface ImportedExperimentSummary {
  experiment_id: string;
  experiment_name: string;
  source_type: ExperimentSourceType;
  created_at: string;
  schema_version: string;
  prompt_count: number;
  prompt_ids: string[];
  import_warning_count: number;
  has_generated_outputs: boolean;
  model_name: string | null;
}

/**
 * Lightweight POST /import/bundle 201 body — no per-step traces.
 * Full detail remains on GET /imported-experiment/{id}.
 */
export interface ImportedExperimentCreatedResponse {
  experiment_id: string;
  experiment_name: string;
  created_at: string;
  prompt_count: number;
  import_warnings: string[];
  recompute_with_current_grammar: boolean;
  recompute_syncode_parser_evidence: boolean;
}
