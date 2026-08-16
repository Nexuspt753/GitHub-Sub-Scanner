import { handleTelegram } from "./handlers/telegram";
import { handlePush } from "./handlers/push";
import { handleShare } from "./handlers/share";
import { getResults } from "./kv";

export interface Env {
  BOT_KV: any;
  TELEGRAM_BOT_TOKEN: string;
  TELEGRAM_SECRET_TOKEN: string;
  PUSH_SECRET: string;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const kv = env.BOT_KV;
    const ctx = {
      kv,
      token: env.TELEGRAM_BOT_TOKEN,
      secretToken: env.TELEGRAM_SECRET_TOKEN,
      pushSecret: env.PUSH_SECRET,
    };

    if (url.pathname === "/telegram" && request.method === "POST") {
      return handleTelegram(ctx, request);
    }
    if (url.pathname === "/push" && request.method === "POST") {
      return handlePush(ctx, request);
    }
    if (url.pathname === "/share") {
      return handleShare(ctx, request);
    }
    if (url.pathname === "/health" && request.method === "GET") {
      const cache = await getResults(kv);
      return Response.json({ ok: true, cached: !!cache, nodes: cache?.nodes.length ?? 0 });
    }
    return new Response("not found", { status: 404 });
  },
};
