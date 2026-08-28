/**
 * API client layer.
 *
 * All calls go through the Next.js rewrite proxy (/api → FastAPI).
 * Components and hooks should import from here, never fetch() directly.
 */

import type {
  ExperimentResult,
  GenerateRequest,
  GenerateResponse,
  StepResponse,
} from "@/types/decoding";
import type {
  ImportedExperimentSummary,
  NormalizedExperiment,
} from "@/types/normalized";

const BASE = "/api";
const DEBUG_API = process.env.NODE_ENV === "development";

/** Generation can run many minutes on CPU — do not abort early. */
const GENERATE_CLIENT_TIMEOUT_MS = 10 * 60 * 1000;

/** Import ZIP can be large but should not need model load. */
const IMPORT_CLIENT_TIMEOUT_MS = 5 * 60 * 1000;

/** Parse FastAPI error bodies into a human-readable string. */
export function formatApiError(status: number, body: string): string {
  try {
    const parsed = JSON.parse(body) as { detail?: unknown };
    const detail = parsed.detail;
    if (typeof detail === "string") {
      return `API ${status}: ${detail}`;
    }
    if (Array.isArray(detail)) {
      const parts = detail
        .map((item) => {
          if (typeof item === "string") return item;
          if (item && typeof item === "object") {
            const o = item as Record<string, unknown>;
            if (typeof o.msg === "string") return o.msg;
            if (typeof o.message === "string") return o.message;
          }
          return null;
        })
        .filter(Boolean);
      if (parts.length) {
        return `API ${status}: ${parts.join("; ")}`;
      }
    }
    if (detail && typeof detail === "object") {
      const d = detail as Record<string, unknown>;
      const message =
        typeof d.message === "string"
          ? d.message
          : typeof d.error === "string"
            ? d.error
            : "Request failed";
      const reasons = Array.isArray(d.reasons)
        ? (d.reasons as string[]).join("; ")
        : "";
      const genId =
        typeof d.generation_id === "string" ? ` [gen=${d.generation_id}]` : "";
      return `API ${status}: ${message}${reasons ? ` — ${reasons}` : ""}${genId}`;
    }
  } catch {
    // body is not JSON — use raw text
  }
  return `API ${status}: ${body.slice(0, 500)}`;
}

async function request<T>(
  path: string,
  init?: RequestInit,
  options?: { timeoutMs?: number; json?: boolean }
): Promise<T> {
  if (DEBUG_API) {
    console.debug("[API request]", init?.method ?? "GET", path);
  }

  const timeoutMs = options?.timeoutMs;
  const useJson = options?.json !== false;
  const controller = timeoutMs ? new AbortController() : undefined;
  const timer =
    timeoutMs && controller
      ? setTimeout(() => controller.abort(), timeoutMs)
      : undefined;

  const headers = new Headers(init?.headers);
  if (useJson && !headers.has("Content-Type") && init?.body) {
    headers.set("Content-Type", "application/json");
  }

  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      headers,
      signal: controller?.signal ?? init?.signal,
    });
  } catch (err) {
    if (timer) clearTimeout(timer);
    if (err instanceof Error && err.name === "AbortError") {
      throw new Error(
        "Request timed out — generation may still be running on the backend. " +
          "Wait and refresh, or reduce max_new_tokens."
      );
    }
    throw err;
  } finally {
    if (timer) clearTimeout(timer);
  }

  const bodyText = await res.text();

  if (DEBUG_API) {
    console.debug(
      "[API response]",
      path,
      "status=",
      res.status,
      "bytes=",
      bodyText.length
    );
  }

  if (!res.ok) {
    const message = formatApiError(res.status, bodyText);
    console.error("[API error]", path, message);
    throw new Error(message);
  }

  try {
    const data = JSON.parse(bodyText) as T;
    if (DEBUG_API && path === "/generate") {
      const preview = bodyText.slice(0, 1500);
      console.debug(
        "[API /generate payload preview]",
        preview + (bodyText.length > 1500 ? "…" : "")
      );
    }
    return data;
  } catch (parseErr) {
    console.error("[API JSON parse error]", path, parseErr);
    throw new Error(
      `API ${res.status}: response is not valid JSON (${String(parseErr)})`
    );
  }
}

/** Validate a successful /generate response — throws if trace is empty. */
export function assertValidGenerateResponse(response: GenerateResponse): void {
  const issues: string[] = [];
  if (!response.steps || response.steps.length === 0) {
    issues.push("response.steps is empty");
  }
  if (response.total_steps <= 0) {
    issues.push(`response.total_steps=${response.total_steps}`);
  }
  if (!response.generated_text || !response.generated_text.trim()) {
    issues.push("response.generated_text is empty");
  }
  if (response.status === "error") {
    issues.push(
      `response.status=error: ${response.message || "no message"}`
    );
  }
  if (issues.length > 0) {
    throw new Error(
      `Invalid generate response (${issues.join("; ")}). ` +
        `experiment_id=${response.experiment_id}`
    );
  }
}

// ---------------------------------------------------------------------------
// Generation
// ---------------------------------------------------------------------------

export async function postGenerate(
  payload: GenerateRequest
): Promise<GenerateResponse> {
  if (DEBUG_API) {
    console.debug("[API postGenerate] request", {
      prompt_len: payload.prompt.length,
      use_syncode: payload.use_syncode,
      max_new_tokens: payload.max_new_tokens,
    });
  }
  const response = await request<GenerateResponse>(
    "/generate",
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
    { timeoutMs: GENERATE_CLIENT_TIMEOUT_MS }
  );
  assertValidGenerateResponse(response);
  if (DEBUG_API) {
    console.debug("[API postGenerate] validated", {
      experiment_id: response.experiment_id,
      total_steps: response.total_steps,
      generated_text_len: response.generated_text.length,
    });
  }
  return response;
}

// ---------------------------------------------------------------------------
// Experiments (live)
// ---------------------------------------------------------------------------

export async function getExperiment(id: string): Promise<ExperimentResult> {
  return request<ExperimentResult>(`/experiment/${id}`);
}

export async function getExperimentStep(
  id: string,
  step: number
): Promise<StepResponse> {
  return request<StepResponse>(`/experiment/${id}/steps/${step}`);
}

export async function listExperiments(): Promise<string[]> {
  return request<string[]>("/experiments");
}

// ---------------------------------------------------------------------------
// Imported experiments (Phase 2A.2 / 2B.1)
// ---------------------------------------------------------------------------

/**
 * POST /import/bundle — multipart ZIP upload.
 * Do not set Content-Type manually (browser must supply the boundary).
 *
 * Grammar-verdict and SynCode parser-evidence recomputation are independent
 * FormData fields (both default false).
 */
export async function postImportBundle(
  file: File,
  options:
    | boolean
    | {
        recomputeWithCurrentGrammar?: boolean;
        recomputeSyncodeParserEvidence?: boolean;
      } = false
): Promise<NormalizedExperiment> {
  const recomputeWithCurrentGrammar =
    typeof options === "boolean"
      ? options
      : Boolean(options.recomputeWithCurrentGrammar);
  const recomputeSyncodeParserEvidence =
    typeof options === "boolean"
      ? false
      : Boolean(options.recomputeSyncodeParserEvidence);

  const form = new FormData();
  form.append("file", file);
  form.append(
    "recompute_with_current_grammar",
    recomputeWithCurrentGrammar ? "true" : "false"
  );
  form.append(
    "recompute_syncode_parser_evidence",
    recomputeSyncodeParserEvidence ? "true" : "false"
  );
  return request<NormalizedExperiment>(
    "/import/bundle",
    {
      method: "POST",
      body: form,
    },
    { timeoutMs: IMPORT_CLIENT_TIMEOUT_MS, json: false }
  );
}

export async function listImportedExperiments(): Promise<
  ImportedExperimentSummary[]
> {
  return request<ImportedExperimentSummary[]>("/imported-experiments");
}

export async function getImportedExperiment(
  id: string
): Promise<NormalizedExperiment> {
  return request<NormalizedExperiment>(`/imported-experiment/${id}`);
}

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

export async function getHealth(): Promise<{ status: string }> {
  return request<{ status: string }>("/health");
}
