import { NextRequest } from "next/server";

const DEFAULT_API_BASE = "http://127.0.0.1:8765";

export const dynamic = "force-dynamic";

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const params = await context.params;
  const base = process.env.WECHAT_AGENT_API_BASE ?? DEFAULT_API_BASE;
  const upstream = new URL(`/api/${params.path.join("/")}`, base);
  upstream.search = request.nextUrl.search;

  try {
    const response = await fetch(upstream, {
      cache: "no-store",
      headers: {
        Accept: "application/json",
      },
    });
    const body = await response.text();
    return new Response(body, {
      status: response.status,
      headers: {
        "Content-Type": response.headers.get("content-type") ?? "application/json; charset=utf-8",
        "Cache-Control": "no-store",
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
