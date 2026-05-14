import type { ApiResult } from "./wechat-api";

const DEFAULT_API_BASE = "http://127.0.0.1:8765";

export async function getServerWechat<T>(
  path: string,
  params?: Record<string, string | number | boolean | undefined>,
) {
  const base = process.env.WECHAT_AGENT_API_BASE ?? DEFAULT_API_BASE;
  const url = new URL(`/api/${path}`, base);
  Object.entries(params ?? {}).forEach(([key, value]) => {
    if (value !== undefined && value !== "") {
      url.searchParams.set(key, String(value));
    }
  });

  const response = await fetch(url, {
    cache: "no-store",
    headers: {
      Accept: "application/json",
    },
  });
  return parseWechatResponse<T>(response);
}

async function parseWechatResponse<T>(response: Response) {
  const text = await response.text();
  let payload: ApiResult<T> | null = null;
  if (text) {
    try {
      payload = JSON.parse(text) as ApiResult<T>;
    } catch {
      payload = null;
    }
  }
  if (!payload) {
    throw new Error(formatNonJsonError(response, text));
  }
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error ?? `Request failed: ${response.status}`);
  }
  return payload;
}

function formatNonJsonError(response: Response, text: string) {
  const detail = text
    .replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  const suffix = detail ? `: ${detail.slice(0, 240)}` : "";
  return `Request failed: ${response.status} ${response.statusText}${suffix}`;
}
