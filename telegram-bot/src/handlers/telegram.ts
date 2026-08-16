import { getSubscriber, putSubscriber, getResults, getShare } from "../kv";
import { evaluate } from "../evaluate";
import { buildTxt, makeCaption } from "../format";
import { sendDocument, sendMessage } from "../telegram";
import type { SubscriberRecord } from "../types";

interface Env {
  kv: any;
  token: string;
  secretToken: string;
}

export async function handleTelegram(env: Env, request: Request): Promise<Response> {
  const got = request.headers.get("x-telegram-bot-api-secret-token");
  if (got !== env.secretToken) {
    return new Response("forbidden", { status: 403 });
  }
  let update: any;
  try {
    update = await request.json();
  } catch {
    return new Response("ok", { status: 200 });
  }
  const msg = update.message;
  if (!msg || !msg.chat) return new Response("ok", { status: 200 });
  const chatId = msg.chat.id;
  const text = (msg.text ?? "").trim();

  try {
    if (text.startsWith("/start")) {
      await cmdStart(env, chatId, text);
    } else if (text === "/help") {
      await sendMessage(env.token, chatId, "Commands: /subscribe /unsubscribe /myfilters /top <n> /country <name> /status /help");
    } else if (text.startsWith("/top")) {
      const n = parseInt(text.split(/\s+/)[1] ?? "5", 10);
      await cmdTop(env, chatId, n);
    } else if (text.startsWith("/country")) {
      const name = text.replace("/country", "").trim();
      await cmdCountry(env, chatId, name);
    } else if (text === "/myfilters") {
      await cmdMyFilters(env, chatId);
    } else if (text === "/status") {
      await cmdStatus(env, chatId);
    } else if (text === "/subscribe") {
      await sendMessage(env.token, chatId, "Send me a filter link from the website (use its 'Subscribe on Telegram' button), or browse with /top <n> and /country <name>. A guided wizard is coming in the next update.");
    } else if (text === "/unsubscribe") {
      await env.kv.delete(`sub:${chatId}`);
      await sendMessage(env.token, chatId, "You are unsubscribed. You won't receive push updates anymore.");
    } else {
      await sendMessage(env.token, chatId, "I don't know that. Try /help");
    }
  } catch (e) {
    console.error("telegram handler error", e);
  }
  return new Response("ok", { status: 200 });
}

async function cmdStart(env: Env, chatId: number, text: string): Promise<void> {
  const match = text.match(/\/start share_(\S+)/);
  if (match) {
    const matrix = await getShare(env.kv, match[1]);
    if (!matrix) {
      await sendMessage(env.token, chatId, "This link expired or is invalid. Build a new filter on the website and try again.");
      return;
    }
    const now = Date.now();
    const rec: SubscriberRecord = { chatId, matrix, createdAt: now, lastNotifiedAt: 0 };
    await putSubscriber(env.kv, rec);
    await sendMessage(env.token, chatId, "You're subscribed! I'll DM you when configs matching your filter go live. Use /myfilters to review, /unsubscribe to stop.");
    return;
  }
  await sendMessage(env.token, chatId, "Welcome to Config Ranker Bot! Use /help to see commands, or subscribe via the website.");
}

async function withResults<T>(env: Env, chatId: number, fn: (nodes: any[]) => Promise<T>): Promise<T | void> {
  const cache = await getResults(env.kv);
  if (!cache) {
    await sendMessage(env.token, chatId, "No data yet — the first ranking run hasn't completed.");
    return;
  }
  return fn(cache.nodes);
}

async function cmdTop(env: Env, chatId: number, n: number): Promise<void> {
  await withResults(env, chatId, async (nodes) => {
    const top = [...nodes].sort((a, b) => b.score - a.score).slice(0, n);
    await sendDocument(env.token, chatId, makeCaption(top, "top"), "top.txt", buildTxt(top));
  });
}

async function cmdCountry(env: Env, chatId: number, name: string): Promise<void> {
  await withResults(env, chatId, async (nodes) => {
    const m = { conditions: [{ field: "country", operator: "eq", value: name }], combinator: "AND", mode: "digest" as const };
    const matches = evaluate(m as any, nodes);
    await sendDocument(env.token, chatId, makeCaption(matches, name), "country.txt", buildTxt(matches));
  });
}

async function cmdMyFilters(env: Env, chatId: number): Promise<void> {
  const sub = await getSubscriber(env.kv, chatId);
  if (!sub) {
    await sendMessage(env.token, chatId, "You have no active subscription. Use /subscribe or the website.");
    return;
  }
  await sendMessage(env.token, chatId, `Active filter: ${sub.matrix.conditions.length} conditions (${sub.matrix.mode} mode). Use /unsubscribe to remove.`);
}

async function cmdStatus(env: Env, chatId: number): Promise<void> {
  const cache = await getResults(env.kv);
  if (!cache) {
    await sendMessage(env.token, chatId, "No data yet.");
    return;
  }
  const ageMin = Math.round((Date.now() - cache.fetchedAt) / 60000);
  await sendMessage(env.token, chatId, `Data age: ${ageMin} min · ${cache.nodes.length} configs cached.`);
}
