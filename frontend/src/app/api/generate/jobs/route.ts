/**
 * Immediate job-create proxy for POST /api/generate/jobs.
 *
 * Returns the backend acknowledgement without waiting for model generation.
 * Must not buffer experiment traces (response is tiny).
 */

import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 60;

const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";
const CREATE_TIMEOUT_MS = 60 * 1000;

export async function POST(req: NextRequest) {
  let body: string;
  try {
    body = await req.text();
  } catch {
    return NextResponse.json(
      {
        detail: {
          error: "bad_request",
          message: "Could not read job create body",
        },
      },
      { status: 400 }
    );
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), CREATE_TIMEOUT_MS);

  try {
    const res = await fetch(`${BACKEND_URL}/generate/jobs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      signal: controller.signal,
      cache: "no-store",
    });

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
            ? "Generate job create proxy timed out"
            : `Job create proxy could not reach backend at ${BACKEND_URL}: ${String(err)}`,
        },
      },
      { status: isAbort ? 504 : 502 }
    );
  } finally {
    clearTimeout(timer);
  }
}
