import { putShare } from "../kv";
import type { Matrix } from "../types";

const ALLOWED_ORIGIN = "https://nexuspt753.github.io";

function corsHeaders(extra: Record<string, string> = {}): HeadersInit {
  return {
    "access-control-allow-origin": ALLOWED_ORIGIN,
    "access-control-allow-methods": "POST, OPTIONS",
    "access-control-allow-headers": "content-type",
    ...extra,
  };
}

function randomToken(len = 10): string {
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  const bytes = new Uint8Array(len);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => chars[b % chars.length]).join("");
}

export async function handleShare(env: { kv: any }, request: Request): Promise<Response> {
  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders() });
  }
  if (request.method !== "POST") {
    return new Response("method not allowed", { status: 405, headers: corsHeaders() });
  }
  let matrix: Matrix;
  try {
    matrix = await request.json();
  } catch {
    return new Response("invalid json", { status: 400, headers: corsHeaders() });
  }
  const token = randomToken();
  await putShare(env.kv, token, matrix);
  const url = `https://t.me/github_sub_scanner_bot?start=share_${token}`;
  return Response.json({ token, url }, { headers: corsHeaders({ "content-type": "application/json" }) });
}
