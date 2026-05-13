import { NextRequest } from "next/server";

const DEFAULT_API_BASE = "http://127.0.0.1:8765";

export const dynamic = "force-dynamic";

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  return proxyWechat(request, context, "GET");
}

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  return proxyWechat(request, context, "POST");
}

async function proxyWechat(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
  method: "GET" | "POST",
) {
  const params = await context.params;
  const base = process.env.WECHAT_AGENT_API_BASE ?? DEFAULT_API_BASE;
  const upstream = new URL(`/api/${params.path.join("/")}`, base);
  upstream.search = request.nextUrl.search;

  try {
    const response = await fetch(upstream, {
      body: method === "POST" ? await request.text() : undefined,
      cache: "no-store",
      headers: {
        Accept: request.headers.get("accept") ?? "*/*",
        ...(method === "POST" ? { "Content-Type": request.headers.get("content-type") ?? "application/json" } : {}),
      },
      method,
    });
    if (method === "POST" && response.status === 501) {
      return Response.json(
        {
          ok: false,
          error: "Local Dashboard API does not support POST yet. Restart `wechat-agent serve` so it loads the latest /api/summary code.",
        },
        { status: 502 },
      );
    }
    const body = await response.arrayBuffer();
    return new Response(body, {
      status: response.status,
      headers: {
        "Content-Type": response.headers.get("content-type") ?? "application/json; charset=utf-8",
        "Cache-Control": response.headers.get("cache-control") ?? "no-store",
      },
    });
  } catch (error) {
    return Response.json(
      {
        ok: false,
        error: "Dashboard API is not reachable. Start `wechat-agent serve` on 127.0.0.1:8765.",
        detail: error instanceof Error ? error.message : String(error),
      },
      { status: 502 },
    );
  }
}
