import type { SubscriberRecord } from "../types";
import { evaluate } from "../evaluate";
import { makeCaption, buildMihomoYaml } from "../format";
import { sendDocument } from "../telegram";
import { putResults, listSubscribers } from "../kv";

interface Env {
  kv: any;
  token: string;
  pushSecret: string;
}

async function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

export async function handlePush(env: Env, request: Request): Promise<Response> {
  const auth = request.headers.get("authorization");
  if (!auth || auth !== `Bearer ${env.pushSecret}`) {
    return new Response("unauthorized", { status: 401 });
  }
  let body: any;
  try {
    body = await request.json();
  } catch {
    return new Response("invalid json", { status: 400 });
  }
  if (!body || !Array.isArray(body.nodes)) {
    return new Response("missing nodes", { status: 400 });
  }

  await putResults(env.kv, body.nodes);
  const subscribers = await listSubscribers(env.kv);

  const BATCH = 15;
  const cap = Math.min(subscribers.length, 45);
  for (let i = 0; i < cap; i += BATCH) {
    const batch = subscribers.slice(i, i + BATCH);
    await Promise.allSettled(batch.map((s) => sendToSubscriber(env, s, body.nodes)));
    if (i + BATCH < cap) await sleep(500);
  }

  if (subscribers.length > 45) {
    console.warn(`push: ${subscribers.length - 45} subscribers deferred (cap 45)`);
  }
  return new Response("ok", { status: 200 });
}

async function sendToSubscriber(env: Env, sub: SubscriberRecord, nodes: any[]): Promise<void> {
  const matches = evaluate(sub.matrix, nodes);
  if (matches.length === 0) return;

  let toSend = matches;
  if (sub.matrix.mode === "diff") {
    const seen = new Set(sub.lastNotifiedIds ?? []);
    toSend = matches.filter((m) => !seen.has(matchId(m)));
    if (toSend.length === 0) return;
  } else {
    toSend = matches.slice(0, 5);
  }

  const ids = toSend.map(matchId);
  const updated: SubscriberRecord = {
    ...sub,
    lastNotifiedAt: Date.now(),
    lastNotifiedIds: capIds([...ids, ...(sub.lastNotifiedIds ?? [])]),
  };

  try {
    const txt = toSend.map((m) => m.uri).join("\n");
    const yaml = buildMihomoYaml(toSend);
    await sendDocument(env.token, sub.chatId, labelFor(matches),
      yaml ? "subscription.yaml" : "subscription.txt",
      yaml || txt, yaml ? "application/yaml" : "text/plain");
    await env.kv.put(`sub:${sub.chatId}`, JSON.stringify(updated));
  } catch (e: any) {
    if (String(e?.message ?? e).includes("403")) {
      await env.kv.delete(`sub:${sub.chatId}`);
      return;
    }
    throw e;
  }
}

function matchId(m: any): string {
  return `${m.country}/${m.name}/${m.address}:${m.port}`;
}

function capIds(ids: string[]): string[] {
  return Array.from(new Set(ids)).slice(0, 100);
}

function labelFor(matches: any[]): string {
  return makeCaption(matches, "your filter");
}
