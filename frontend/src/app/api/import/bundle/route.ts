/**
 * Long-running proxy for POST /import/bundle.
 *
 * Next.js rewrite proxies default to a 30s timeout
 * (see next/dist/server/lib/router-utils/proxy-request.js). SynCode
 * parser-evidence recomputation for 512-step × multi-prompt bundles
 * routinely exceeds that (~minutes). The rewrite then returns HTTP 500
 * "Internal Server Error" while FastAPI continues and eventually logs
 * 201 Created — matching the observed false frontend failure.
 *
 * This App Router handler wins over the /api rewrite and forwards the
 * multipart body with a 10-minute timeout (BACKEND_URL configurable).
 */

import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
/** Allow long import + optional SynCode recomputation. */
export const maxDuration = 600;

const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";
/** 10 minutes — matches import client timeout; SynCode recompute is slow. */
const IMPORT_PROXY_TIMEOUT_MS = 10 * 60 * 1000;

export async function POST(req: NextRequest) {
  console.log("[proxy /api/import/bundle] request received →", BACKEND_URL);

  let body: ArrayBuffer;
  let contentType: string | null;
  try {
    contentType = req.headers.get("content-type");
    body = await req.arrayBuffer();
  } catch {
    return NextResponse.json(
      {
        detail: {
          error: "bad_request",
          message: "Could not read import upload body",
        },
      },
      { status: 400 }
    );
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), IMPORT_PROXY_TIMEOUT_MS);

  try {
    const headers = new Headers();
    if (contentType) {
      // Preserve multipart boundary — do not invent Content-Type.
      headers.set("Content-Type", contentType);
    }

    const res = await fetch(`${BACKEND_URL}/import/bundle`, {
      method: "POST",
      headers,
      body,
      signal: controller.signal,
    });

    console.log("[proxy /api/import/bundle] backend status:", res.status);

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
    console.error("[proxy /api/import/bundle]", err);
    return NextResponse.json(
      {
        detail: {
          error: isAbort ? "gateway_timeout" : "proxy_error",
          message: isAbort
            ? "Import proxy timed out after 10 minutes — the backend may still be finishing; refresh the imported list before retrying"
            : `Import proxy could not reach backend at ${BACKEND_URL}: ${String(err)}`,
        },
      },
      { status: isAbort ? 504 : 502 }
    );
  } finally {
    clearTimeout(timer);
  }
}
