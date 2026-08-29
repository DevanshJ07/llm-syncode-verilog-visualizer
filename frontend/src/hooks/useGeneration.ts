"use client";

/**
 * useGeneration — async job create + status polling for live generation.
 *
 * POST /generate/jobs returns immediately with job_id. The hook polls
 * GET /generate/jobs/{id} until completed/failed, then the page navigates
 * to /experiment/{experiment_id}. Job id is kept in sessionStorage so a
 * refresh can resume polling.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { getGenerateJobStatus, postGenerateJob } from "@/lib/api";
import type {
  GenerateJobStatusResponse,
  GenerateRequest,
} from "@/types/decoding";

const STORAGE_KEY = "synviz.activeGenerateJob";
const MAX_POLL_NETWORK_FAILURES = 5;

type UiPhase =
  | "idle"
  | "submitting"
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "status_unavailable";

interface StoredJob {
  jobId: string;
}

interface UseGenerationReturn {
  phase: UiPhase;
  jobId: string | null;
  statusMessage: string | null;
  error: string | null;
  experimentId: string | null;
  /** True while a job is active (button must stay disabled). */
  isBusy: boolean;
  generate: (request: GenerateRequest) => Promise<void>;
  checkStatusAgain: () => void;
  reset: () => void;
}

function readStoredJob(): StoredJob | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = sessionStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as StoredJob;
    if (!parsed?.jobId || typeof parsed.jobId !== "string") return null;
    return parsed;
  } catch {
    return null;
  }
}

function writeStoredJob(jobId: string): void {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ jobId }));
  } catch {
    // ignore quota / private mode
  }
}

function clearStoredJob(): void {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore
  }
}

function phaseFromStatus(status: string): UiPhase {
  if (status === "queued") return "queued";
  if (status === "running") return "running";
  if (status === "completed") return "completed";
  if (status === "failed") return "failed";
  return "running";
}

function displayMessage(job: GenerateJobStatusResponse): string {
  if (job.message && job.message.trim()) return job.message;
  if (job.status === "queued") return "Queued";
  if (job.status === "running") return "Generating…";
  if (job.status === "completed") return "Completed";
  if (job.status === "failed") return "Failed";
  return job.status;
}

export function useGeneration(): UseGenerationReturn {
  const [phase, setPhase] = useState<UiPhase>("idle");
  const [jobId, setJobId] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [experimentId, setExperimentId] = useState<string | null>(null);

  const inFlightRef = useRef(false);
  const pollAbortRef = useRef<AbortController | null>(null);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const networkFailCountRef = useRef(0);
  const pollCountRef = useRef(0);
  const mountedRef = useRef(true);

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    if (pollAbortRef.current) {
      pollAbortRef.current.abort();
      pollAbortRef.current = null;
    }
  }, []);

  const handleTerminal = useCallback(
    (job: GenerateJobStatusResponse) => {
      stopPolling();
      clearStoredJob();
      inFlightRef.current = false;
      if (job.status === "completed") {
        if (!job.experiment_id) {
          setPhase("failed");
          setError(
            "Generation completed but no experiment_id was returned. Check the backend terminal."
          );
          setStatusMessage("Failed");
          return;
        }
        setPhase("completed");
        setExperimentId(job.experiment_id);
        setStatusMessage(displayMessage(job));
        setError(null);
        return;
      }
      setPhase("failed");
      setExperimentId(null);
      setStatusMessage("Failed");
      setError(
        `Generation failed: ${job.error || job.message || "unknown error"}`
      );
    },
    [stopPolling]
  );

  const schedulePoll = useCallback(
    (id: string, delayMs: number) => {
      stopPolling();
      const controller = new AbortController();
      pollAbortRef.current = controller;

      pollTimerRef.current = setTimeout(async () => {
        if (!mountedRef.current) return;
        try {
          const job = await getGenerateJobStatus(id, {
            signal: controller.signal,
          });
          if (!mountedRef.current || controller.signal.aborted) return;
          networkFailCountRef.current = 0;
          pollCountRef.current += 1;
          setJobId(job.job_id);
          setStatusMessage(displayMessage(job));

          if (job.status === "completed" || job.status === "failed") {
            handleTerminal(job);
            return;
          }

          setPhase(phaseFromStatus(job.status));
          const nextDelay = pollCountRef.current < 3 ? 1000 : 2000;
          schedulePoll(id, nextDelay);
        } catch (err) {
          if (!mountedRef.current) return;
          if (err instanceof Error && err.name === "AbortError") return;

          const message = err instanceof Error ? err.message : String(err);
          const isNotFound =
            message.includes("job_not_found") ||
            message.includes("API 404") ||
            message.includes("was not found");

          if (isNotFound) {
            stopPolling();
            clearStoredJob();
            inFlightRef.current = false;
            setPhase("failed");
            setError(
              `Generation job ${id} was not found. In-memory job records are lost if the backend restarted.`
            );
            setStatusMessage("Failed");
            return;
          }

          networkFailCountRef.current += 1;
          setPhase("status_unavailable");
          setStatusMessage("Status temporarily unavailable");
          setError(
            `Generation job ${id} may still be running, but its status is temporarily unavailable. Status checking will retry.`
          );

          if (networkFailCountRef.current >= MAX_POLL_NETWORK_FAILURES) {
            // Stop auto-retry; keep job id for manual "Check status again".
            stopPolling();
            return;
          }

          const backoff = Math.min(
            8000,
            1000 * 2 ** (networkFailCountRef.current - 1)
          );
          schedulePoll(id, backoff);
        }
      }, delayMs);
    },
    [handleTerminal, stopPolling]
  );

  const startPolling = useCallback(
    (id: string) => {
      pollCountRef.current = 0;
      networkFailCountRef.current = 0;
      writeStoredJob(id);
      setJobId(id);
      setPhase("queued");
      setStatusMessage("Queued");
      setError(null);
      schedulePoll(id, 1000);
    },
    [schedulePoll]
  );

  // Resume from sessionStorage after refresh (once on mount).
  useEffect(() => {
    mountedRef.current = true;
    const stored = readStoredJob();
    if (stored?.jobId) {
      inFlightRef.current = true;
      startPolling(stored.jobId);
    }
    return () => {
      mountedRef.current = false;
      stopPolling();
    };
    // Intentionally mount-only: resume once; polling owns its own timers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const generate = useCallback(
    async (request: GenerateRequest) => {
      if (inFlightRef.current) return;
      inFlightRef.current = true;
      stopPolling();
      setPhase("submitting");
      setStatusMessage("Submitting job…");
      setError(null);
      setExperimentId(null);
      setJobId(null);

      try {
        const created = await postGenerateJob(request);
        if (!mountedRef.current) return;
        startPolling(created.job_id);
      } catch (err) {
        inFlightRef.current = false;
        clearStoredJob();
        const message = err instanceof Error ? err.message : String(err);
        console.error("[useGeneration] job create failed:", message);
        setPhase("failed");
        setStatusMessage("Failed");
        setError(message);
      }
    },
    [startPolling, stopPolling]
  );

  const checkStatusAgain = useCallback(() => {
    if (!jobId) return;
    networkFailCountRef.current = 0;
    inFlightRef.current = true;
    setPhase("running");
    setError(null);
    setStatusMessage("Checking status…");
    schedulePoll(jobId, 0);
  }, [jobId, schedulePoll]);

  const reset = useCallback(() => {
    if (inFlightRef.current && phase !== "status_unavailable" && phase !== "failed") {
      return;
    }
    stopPolling();
    clearStoredJob();
    inFlightRef.current = false;
    setPhase("idle");
    setJobId(null);
    setStatusMessage(null);
    setError(null);
    setExperimentId(null);
  }, [phase, stopPolling]);

  const isBusy =
    phase === "submitting" ||
    phase === "queued" ||
    phase === "running" ||
    phase === "status_unavailable";

  return {
    phase,
    jobId,
    statusMessage,
    error,
    experimentId,
    isBusy,
    generate,
    checkStatusAgain,
    reset,
  };
}
