/**
 * API client layer.
 *
 * Dedicated App Router handlers (not the generic rewrite):
 *   - POST /api/generate/jobs
 *   - GET  /api/generate/jobs/[jobId]
 *   - POST /api/generate (sync compat)
 *   - GET  /api/experiment/[id]
 *   - POST /api/import/bundle
 *   - GET  /api/imported-experiment/[id]
 *
 * Components and hooks should import from here, never fetch() directly.
 */
import type {
  ExperimentResult,
  GenerateCreatedResponse,
  GenerateJobCreatedResponse,
  GenerateJobStatusResponse,
  GenerateRequest,
  StepResponse,
} from "@/types/decoding";
import type {
  ImportedExperimentCreatedResponse,
  ImportedExperimentSummary,
  NormalizedExperiment,
} from "@/types/normalized";
const BASE = "/api";
const DEBUG_API = process.env.NODE_ENV === "development";
/** Sync POST /generate compat path — long CPU runs. */
const GENERATE_CLIENT_TIMEOUT_MS = 10 * 60 * 1000;
/** Job create must return promptly. */
const GENERATE_JOB_CREATE_TIMEOUT_MS = 60 * 1000;
/** Single status poll. */
const GENERATE_JOB_STATUS_TIMEOUT_MS = 60 * 1000;
/**
 * Import + optional SynCode parser-evidence recomputation can take several
 * minutes (measured ~3–4+ min for 4×512-step bundles). Separate from ordinary
 * GET timeouts. Matches the dedicated /api/import/bundle route proxy.
 */
const IMPORT_CLIENT_TIMEOUT_MS = 10 * 60 * 1000;
/** Large imported detail payloads (SynCode evidence) may take longer to load. */
const IMPORT_DETAIL_CLIENT_TIMEOUT_MS = 10 * 60 * 1000;
/** Live experiment detail (full decoding trace) via dedicated proxy. */
const EXPERIMENT_DETAIL_CLIENT_TIMEOUT_MS = 10 * 60 * 1000;
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
  if (controller && init?.signal) {
    if (init.signal.aborted) {
      controller.abort();
    } else {
      init.signal.addEventListener("abort", () => controller.abort(), {
        once: true,
      });
    }
  }
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
      const isImport =
        path.startsWith("/import/") || path.startsWith("/imported-experiment/");
      const isJobCreate = path === "/generate/jobs";
      const isJobStatus = path.startsWith("/generate/jobs/");
      const isGenerate = path === "/generate" || path.startsWith("/generate?");
      if (isImport) {
        throw new Error(
          "Import request timed out after 10 minutes. The backend may still be finishing — refresh the imported list before retrying to avoid duplicates."
        );
      }
      if (isJobCreate) {
        throw new Error(
          "Timed out while creating the generation job. Check the backend terminal before submitting again."
        );
      }
      if (isJobStatus) {
        throw new Error(
          "Timed out while checking generation job status."
        );
      }
      if (isGenerate) {
        throw new Error(
          "Request timed out after 10 minutes — generation may still be running on the backend. Wait and check the backend terminal before submitting again, or reduce max_new_tokens."
        );
      }
      throw new Error(
        "Request timed out — the backend may still be finishing. Wait and refresh before retrying."
      );
    }
    if (err instanceof TypeError) {
      const isJobCreate = path === "/generate/jobs";
      const isJobStatus = path.startsWith("/generate/jobs/");
      const isGenerate = path === "/generate" || path.startsWith("/generate?");
      if (isJobCreate) {
        throw new Error(
          "The generation job could not be created because the connection was lost. Check the backend terminal before submitting again."
        );
      }
      if (isJobStatus) {
        throw new Error(
          `Network or proxy failure while calling ${path}: ${err.message || String(err)}`
        );
      }
      if (isGenerate) {
        throw new Error(
          "The connection to the generation request was lost. The backend may still be running and may save the experiment. Check the backend terminal before submitting again."
        );
      }
      throw new Error(
        `Network or proxy failure while calling ${path}: ${err.message || String(err)}`
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
/** Validate a successful lightweight /generate acknowledgement. */
export function assertValidGenerateCreatedResponse(
  response: GenerateCreatedResponse
): void {
  const issues: string[] = [];
  if (!response.experiment_id || !String(response.experiment_id).trim()) {
    issues.push("response.experiment_id is empty");
  }
  if (response.status === "error") {
    issues.push(`response.status=error: ${response.message || "no message"}`);
  }
  if (typeof response.step_count === "number" && response.step_count < 0) {
    issues.push(`response.step_count=${response.step_count}`);
  }
  if (issues.length > 0) {
    throw new Error(
      `Invalid generate response (${issues.join("; ")}). ` +
        `experiment_id=${response.experiment_id ?? "(missing)"}`
    );
  }
}
// ---------------------------------------------------------------------------
// Generation (async jobs — browser live path)
// ---------------------------------------------------------------------------

export function assertValidGenerateJobCreated(
  response: GenerateJobCreatedResponse
): void {
  if (!response.job_id || !String(response.job_id).trim()) {
    throw new Error("Invalid generate job response (job_id is empty).");
  }
}

export async function postGenerateJob(
  payload: GenerateRequest
): Promise<GenerateJobCreatedResponse> {
  if (DEBUG_API) {
    console.debug("[API postGenerateJob] request", {
      prompt_len: payload.prompt.length,
      use_syncode: payload.use_syncode,
      max_new_tokens: payload.max_new_tokens,
    });
  }
  const response = await request<GenerateJobCreatedResponse>(
    "/generate/jobs",
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
    { timeoutMs: GENERATE_JOB_CREATE_TIMEOUT_MS }
  );
  assertValidGenerateJobCreated(response);
  return response;
}

export async function getGenerateJobStatus(
  jobId: string,
  init?: RequestInit
): Promise<GenerateJobStatusResponse> {
  return request<GenerateJobStatusResponse>(
    `/generate/jobs/${encodeURIComponent(jobId)}`,
    init,
    { timeoutMs: GENERATE_JOB_STATUS_TIMEOUT_MS }
  );
}

// ---------------------------------------------------------------------------
// Generation (sync compat — tests / internal)
// ---------------------------------------------------------------------------

export async function postGenerate(
  payload: GenerateRequest
): Promise<GenerateCreatedResponse> {
  if (DEBUG_API) {
    console.debug("[API postGenerate] request", {
      prompt_len: payload.prompt.length,
      use_syncode: payload.use_syncode,
      max_new_tokens: payload.max_new_tokens,
    });
  }
  const response = await request<GenerateCreatedResponse>(
    "/generate",
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
    { timeoutMs: GENERATE_CLIENT_TIMEOUT_MS }
  );
  assertValidGenerateCreatedResponse(response);
  if (DEBUG_API) {
    console.debug("[API postGenerate] validated", {
      experiment_id: response.experiment_id,
      step_count: response.step_count,
      mode: response.mode,
      detail_path: response.detail_path,
    });
  }
  return response;
}
// ---------------------------------------------------------------------------
// Experiments (live)
// ---------------------------------------------------------------------------
export async function getExperiment(id: string): Promise<ExperimentResult> {
  return request<ExperimentResult>(`/experiment/${id}`, undefined, {
    timeoutMs: EXPERIMENT_DETAIL_CLIENT_TIMEOUT_MS,
  });
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
 *
 * Returns a lightweight created response (no per-step traces). Navigate with
 * experiment_id; load full detail via GET /imported-experiment/{id}.
 */
export async function postImportBundle(
  file: File,
  options:
    | boolean
    | {
        recomputeWithCurrentGrammar?: boolean;
        recomputeSyncodeParserEvidence?: boolean;
      } = false
): Promise<ImportedExperimentCreatedResponse> {
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
  return request<ImportedExperimentCreatedResponse>(
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
  return request<NormalizedExperiment>(`/imported-experiment/${id}`, undefined, {
    timeoutMs: IMPORT_DETAIL_CLIENT_TIMEOUT_MS,
  });
}
// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------
export async function getHealth(): Promise<{ status: string }> {
  return request<{ status: string }>("/health");
}
