import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { FakeKV } from "./fakes";
import { handlePush } from "../src/handlers/push";
import { handleShare } from "../src/handlers/share";
import { handleTelegram } from "../src/handlers/telegram";
import { putSubscriber, putShare, putResults } from "../src/kv";
import { sampleNodes, scoreGt50, countryUK } from "./fixtures";
import type { SubscriberRecord, ResultsPayload } from "../src/types";

const SECRET = "pushsecret";

beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response('{"ok":true}', { status: 200 }),
  );
});
afterEach(() => {
  vi.restoreAllMocks();
});

function makeEnv(kv: FakeKV, token = "TOKEN") {
  return { kv, token, pushSecret: SECRET };
}

describe("handlePush", () => {
  let kv: FakeKV;
  beforeEach(() => { kv = new FakeKV(); });

  it("rejects missing bearer", async () => {
    const res = await handlePush(makeEnv(kv), new Request("http://fake/push", {
      method: "POST", body: "{}", headers: {},
    }));
    expect(res.status).toBe(401);
  });

  it("rejects wrong bearer", async () => {
    const res = await handlePush(makeEnv(kv), new Request("http://fake/push", {
      method: "POST", body: "{}", headers: { authorization: "Bearer wrong" },
    }));
    expect(res.status).toBe(401);
  });

  it("stores results cache on success", async () => {
    const body: ResultsPayload = { nodes: sampleNodes };
    const res = await handlePush(makeEnv(kv), new Request("http://fake/push", {
      method: "POST",
      body: JSON.stringify(body),
      headers: { authorization: `Bearer ${SECRET}` },
    }));
    expect(res.status).toBe(200);
    const cached = await kv.get("results:cache", "json") as any;
    expect(cached).not.toBeNull();
    expect(cached.nodes).toHaveLength(sampleNodes.length);
  });

  it("caps lastNotifiedIds to 100", async () => {
    const many = Array.from({ length: 150 }, (_, i) => `id-${i}`);
    const rec: SubscriberRecord = { chatId: 1, matrix: scoreGt50, createdAt: 1, lastNotifiedAt: 0, lastNotifiedIds: many };
    await putSubscriber(kv, rec);
    const body: ResultsPayload = { nodes: sampleNodes };
    await handlePush(makeEnv(kv), new Request("http://fake/push", {
      method: "POST",
      body: JSON.stringify(body),
      headers: { authorization: `Bearer ${SECRET}` },
    }));
    const updated = await kv.get("sub:1", "json") as SubscriberRecord;
    expect(updated.lastNotifiedIds!.length).toBeLessThanOrEqual(100);
  });

  it("diff mode skips already-notified without modifying record", async () => {
    const rec: SubscriberRecord = {
      chatId: 1, matrix: countryUK, createdAt: 1, lastNotifiedAt: 0,
      lastNotifiedIds: ["United Kingdom/UK-1/5.6.7.8:443"],
    };
    await putSubscriber(kv, rec);
    const body: ResultsPayload = { nodes: sampleNodes };
    await handlePush(makeEnv(kv), new Request("http://fake/push", {
      method: "POST",
      body: JSON.stringify(body),
      headers: { authorization: `Bearer ${SECRET}` },
    }));
    const updated = await kv.get("sub:1", "json") as SubscriberRecord;
    // all matches already notified -> early return, record untouched
    expect(updated.lastNotifiedAt).toBe(0);
  });

  it("digest mode sends top 5 regardless of prior notifications", async () => {
    const rec: SubscriberRecord = {
      chatId: 1, matrix: { ...scoreGt50, mode: "digest" }, createdAt: 1, lastNotifiedAt: 0,
      lastNotifiedIds: ["United States/US-1/1.2.3.4:443"],
    };
    await putSubscriber(kv, rec);
    const body: ResultsPayload = { nodes: sampleNodes };
    await handlePush(makeEnv(kv), new Request("http://fake/push", {
      method: "POST",
      body: JSON.stringify(body),
      headers: { authorization: `Bearer ${SECRET}` },
    }));
    const updated = await kv.get("sub:1", "json") as SubscriberRecord;
    expect(updated.lastNotifiedAt).toBeGreaterThan(0);
  });
});

describe("handleShare", () => {
  let kv: FakeKV;
  beforeEach(() => { kv = new FakeKV(); });

  it("returns a token + url, stores matrix", async () => {
    const res = await handleShare({ kv }, new Request("http://fake/share", {
      method: "POST",
      headers: { "content-type": "application/json", origin: "https://nexuspt753.github.io" },
      body: JSON.stringify({ conditions: [], combinator: "AND", mode: "diff" }),
    }));
    expect(res.status).toBe(200);
    const body = await res.json() as any;
    expect(body.token).toBeTruthy();
    expect(body.url).toContain("t.me");
    expect(body.url).toContain(body.token);
    const stored = await kv.get(`share:${body.token}`, "json");
    expect(stored).not.toBeNull();
  });

  it("sets CORS headers", async () => {
    const res = await handleShare({ kv }, new Request("http://fake/share", {
      method: "POST",
      headers: { "content-type": "application/json", origin: "https://nexuspt753.github.io" },
      body: JSON.stringify({ conditions: [], combinator: "AND", mode: "diff" }),
    }));
    expect(res.headers.get("access-control-allow-origin")).toBe("https://nexuspt753.github.io");
  });

  it("handles OPTIONS preflight", async () => {
    const res = await handleShare({ kv }, new Request("http://fake/share", {
      method: "OPTIONS",
      headers: { origin: "https://nexuspt753.github.io", "access-control-request-method": "POST" },
    }));
    expect(res.status).toBe(204);
  });
});

describe("handleTelegram", () => {
  let kv: FakeKV;
  beforeEach(() => { kv = new FakeKV(); });

  it("rejects missing secret_token", async () => {
    const res = await handleTelegram({ kv, token: "T", secretToken: "sec" }, new Request("http://fake/telegram", {
      method: "POST", body: JSON.stringify({ message: { chat: { id: 1 }, text: "/start" } }),
    }));
    expect(res.status).toBe(403);
  });

  it("responds 200 to valid /start", async () => {
    const res = await handleTelegram({ kv, token: "T", secretToken: "sec" }, new Request("http://fake/telegram", {
      method: "POST",
      headers: { "x-telegram-bot-api-secret-token": "sec" },
      body: JSON.stringify({ message: { chat: { id: 1 }, text: "/start" } }),
    }));
    expect(res.status).toBe(200);
  });

  it("/start share_<token> imports the matrix and subscribes", async () => {
    await putShare(kv, "abc123", { conditions: [{ field: "country", operator: "eq", value: "United Kingdom" }], combinator: "AND", mode: "diff" });
    const res = await handleTelegram({ kv, token: "T", secretToken: "sec" }, new Request("http://fake/telegram", {
      method: "POST",
      headers: { "x-telegram-bot-api-secret-token": "sec" },
      body: JSON.stringify({ message: { chat: { id: 1 }, text: "/start share_abc123" } }),
    }));
    expect(res.status).toBe(200);
    const sub = await kv.get("sub:1", "json") as SubscriberRecord;
    expect(sub).not.toBeNull();
    expect(sub.matrix.conditions[0].value).toBe("United Kingdom");
  });

  it("/start with expired token replies gracefully", async () => {
    const res = await handleTelegram({ kv, token: "T", secretToken: "sec" }, new Request("http://fake/telegram", {
      method: "POST",
      headers: { "x-telegram-bot-api-secret-token": "sec" },
      body: JSON.stringify({ message: { chat: { id: 1 }, text: "/start share_gone" } }),
    }));
    expect(res.status).toBe(200);
    expect(await kv.get("sub:1", "json")).toBeNull();
  });

  it("/top N sends a document with top N by score", async () => {
    await putResults(kv, sampleNodes);
    const res = await handleTelegram({ kv, token: "T", secretToken: "sec" }, new Request("http://fake/telegram", {
      method: "POST",
      headers: { "x-telegram-bot-api-secret-token": "sec" },
      body: JSON.stringify({ message: { chat: { id: 1 }, text: "/top 2" } }),
    }));
    expect(res.status).toBe(200);
  });

  it("/unsubscribe deletes the subscriber", async () => {
    await putSubscriber(kv, { chatId: 1, matrix: { conditions: [], combinator: "AND", mode: "diff" }, createdAt: 1, lastNotifiedAt: 0 });
    const res = await handleTelegram({ kv, token: "T", secretToken: "sec" }, new Request("http://fake/telegram", {
      method: "POST",
      headers: { "x-telegram-bot-api-secret-token": "sec" },
      body: JSON.stringify({ message: { chat: { id: 1 }, text: "/unsubscribe" } }),
    }));
    expect(res.status).toBe(200);
    expect(await kv.get("sub:1", "json")).toBeNull();
  });
});
