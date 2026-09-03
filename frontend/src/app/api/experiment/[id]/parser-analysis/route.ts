/**
 * Large-body proxy for GET /experiment/{id}/parser-analysis (final_source).
 *
 * On-demand lossless CST analysis can be multi-MB. Dedicated handler:
 *   - wins over the ~30s rewrite proxy;
 *   - streams the upstream body when available;
 *   - 10-minute deadline; Cache-Control: no-store.
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
  req: NextRequest,
  context: { params: { id: string } }
) {
  const id = context.params?.id ?? "";
  if (!id || !UUID_RE.test(id)) {
    return NextResponse.json(
      { detail: "malformed experiment id" },
      { status: 400 }
    );
  }

  const timing = req.nextUrl.searchParams.get("timing") ?? "final_source";
  const qs = new URLSearchParams({ timing });

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), DETAIL_PROXY_TIMEOUT_MS);
  try {
    const res = await fetch(
      `${BACKEND_URL}/experiment/${encodeURIComponent(id)}/parser-analysis?${qs.toString()}`,
      {
        method: "GET",
        signal: controller.signal,
        cache: "no-store",
      }
    );
    const contentType =
      res.headers.get("Content-Type") ?? "application/json";
    if (res.body) {
      return new NextResponse(res.body, {
        status: res.status,
        headers: {
          "Content-Type": contentType,
          "Cache-Control": "no-store",
        },
      });
    }
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
            ? "Final parser-analysis proxy timed out after 10 minutes"
            : `Detail proxy could not reach backend at ${BACKEND_URL}: ${String(err)}`,
        },
      },
      { status: isAbort ? 504 : 502 }
    );
  } finally {
    clearTimeout(timer);
  }
}
