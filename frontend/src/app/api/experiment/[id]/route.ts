/**
 * Large-body proxy for GET /experiment/{id} (live saved experiments).
 *
 * Full decoding traces are multi-megabyte JSON. Serving them through the
 * default Next rewrite is usually fine for short localhost GETs, but this
 * dedicated handler:
 *   - wins over the ~30s rewrite proxy;
 *   - uses BACKEND_URL consistently;
 *   - streams the upstream body when available (no full text buffer);
 *   - applies a 10-minute deadline consistent with other detail proxies.
 */
import { NextRequest, NextResponse } from "next/server";
export const runtime = "nodejs";
export const dynamic = "force-dynamic";
export const maxDuration = 600;
const BACKEND_URL = process.env.BACKEND_URL ?? "http://127.0.0.1:8000";
const DETAIL_PROXY_TIMEOUT_MS = 10 * 60 * 1000;
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export async function GET(
  _req: NextRequest,
  context: { params: { id: string } }
) {
  const id = context.params?.id ?? "";
  if (!id || !UUID_RE.test(id)) {
    return NextResponse.json(
      { detail: "malformed experiment id" },
      { status: 400 }
    );
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), DETAIL_PROXY_TIMEOUT_MS);
  try {
    const res = await fetch(
      `${BACKEND_URL}/experiment/${encodeURIComponent(id)}`,
      {
        method: "GET",
        signal: controller.signal,
        cache: "no-store",
      }
    );
    const contentType =
      res.headers.get("Content-Type") ?? "application/json";
    // Prefer streaming the upstream body — avoids buffering multi-MB traces.
    if (res.body) {
      return new NextResponse(res.body, {
        status: res.status,
        headers: {
          "Content-Type": contentType,
          "Cache-Control": "no-store",
        },
      });
    }
    // Fallback if the runtime provides no body stream.
    const text = await res.text();
    return new NextResponse(text, {
      status: res.status,
      headers: {
        "Content-Type": contentType,
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
            ? "Experiment detail proxy timed out after 10 minutes"
            : `Detail proxy could not reach backend at ${BACKEND_URL}: ${String(err)}`,
        },
      },
      { status: isAbort ? 504 : 502 }
    );
  } finally {
    clearTimeout(timer);
  }
}
