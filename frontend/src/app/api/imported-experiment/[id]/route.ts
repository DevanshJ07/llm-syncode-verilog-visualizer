/**
 * Long-running / large-body proxy for GET /imported-experiment/{id}.
 *
 * Full normalized experiments with SynCode evidence can be tens of MB.
 * Serving them through the default rewrite is usually fine for duration,
 * but this dedicated handler keeps behaviour consistent with the import
 * proxy and uses BACKEND_URL rather than hardcoding a machine address.
 */

import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 600;

const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";
const DETAIL_PROXY_TIMEOUT_MS = 10 * 60 * 1000;

export async function GET(
  _req: NextRequest,
  context: { params: { id: string } }
) {
  const id = context.params?.id ?? "";
  // Mirror backend is_safe_experiment_id (UUID).
  if (
    !id ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id)
  ) {
    return NextResponse.json(
      { detail: "malformed experiment id" },
      { status: 400 }
    );
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), DETAIL_PROXY_TIMEOUT_MS);

  try {
    const res = await fetch(
      `${BACKEND_URL}/imported-experiment/${encodeURIComponent(id)}`,
      { method: "GET", signal: controller.signal }
    );
    const text = await res.text();
    return new NextResponse(text, {
      status: res.status,
      headers: {
        "Content-Type":
          res.headers.get("Content-Type") ?? "application/json",
      },
    });
  } catch (err) {
    const isAbort = err instanceof Error && err.name === "AbortError";
    return NextResponse.json(
      {
        detail: {
          error: isAbort ? "gateway_timeout" : "proxy_error",
          message: isAbort
            ? "Imported experiment detail proxy timed out after 10 minutes"
            : `Detail proxy could not reach backend at ${BACKEND_URL}: ${String(err)}`,
        },
      },
      { status: isAbort ? 504 : 502 }
    );
  } finally {
    clearTimeout(timer);
  }
}
