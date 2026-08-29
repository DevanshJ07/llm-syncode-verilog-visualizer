/**
 * Job-status proxy for GET /api/generate/jobs/[jobId].
 *
 * Small JSON only — cache: no-store; do not buffer experiment traces.
 */

import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 60;

const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";
const STATUS_TIMEOUT_MS = 60 * 1000;

const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function GET(
  _req: NextRequest,
  context: { params: { jobId: string } }
) {
  const jobId = context.params?.jobId ?? "";
  if (!jobId || !UUID_RE.test(jobId)) {
    return NextResponse.json(
      { detail: "malformed job id" },
      { status: 400 }
    );
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), STATUS_TIMEOUT_MS);

  try {
    const res = await fetch(
      `${BACKEND_URL}/generate/jobs/${encodeURIComponent(jobId)}`,
      {
        method: "GET",
        signal: controller.signal,
        cache: "no-store",
      }
    );

    const text = await res.text();
    return new NextResponse(text, {
      status: res.status,
      headers: {
        "Content-Type":
          res.headers.get("Content-Type") ?? "application/json",
        "Cache-Control": "no-store",
      },
    });
  } catch (err) {
    const isAbort = err instanceof Error && err.name === "AbortError";
    return NextResponse.json(
      {
        detail: {
          error: isAbort ? "gateway_timeout" : "proxy_error",
          message: isAbort
            ? "Generate job status proxy timed out"
            : `Job status proxy could not reach backend at ${BACKEND_URL}: ${String(err)}`,
        },
      },
      { status: isAbort ? 504 : 502 }
    );
  } finally {
    clearTimeout(timer);
  }
}
