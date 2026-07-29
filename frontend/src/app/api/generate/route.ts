/**
 * Long-running proxy for POST /generate.
 *
 * Next.js rewrite proxies default to a short timeout (~30–120 s).  CPU
 * generation with 120+ tokens routinely exceeds that, which surfaces as
 * "API 500: Internal Server Error" in the browser while the backend is still
 * running.  This route handler forwards to FastAPI with a 10-minute timeout.
 */

import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
/** Allow long CPU generation (Vercel / compatible hosts). */
export const maxDuration = 600;

const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";
/** 10 minutes — matches expected worst-case CPU + Syncode DFA run. */
const GENERATE_TIMEOUT_MS = 10 * 60 * 1000;

export async function POST(req: NextRequest) {
  console.log("[proxy /api/generate] request received →", BACKEND_URL);

  let body: string;
  try {
    body = await req.text();
  } catch {
    return NextResponse.json(
      { detail: { error: "bad_request", message: "Could not read request body" } },
      { status: 400 }
    );
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), GENERATE_TIMEOUT_MS);

  try {
    const res = await fetch(`${BACKEND_URL}/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      signal: controller.signal,
    });

    console.log("[proxy /api/generate] backend status:", res.status);

    const text = await res.text();
    return new NextResponse(text, {
      status: res.status,
      headers: { "Content-Type": "application/json" },
    });
  } catch (err) {
    const isAbort = err instanceof Error && err.name === "AbortError";
    return NextResponse.json(
      {
        detail: {
          error: isAbort ? "gateway_timeout" : "proxy_error",
          message: isAbort
            ? "Generation proxy timed out after 10 minutes — try fewer max_new_tokens"
            : `Proxy could not reach backend at ${BACKEND_URL}: ${String(err)}`,
        },
      },
      { status: isAbort ? 504 : 502 }
    );
  } finally {
    clearTimeout(timer);
  }
}
