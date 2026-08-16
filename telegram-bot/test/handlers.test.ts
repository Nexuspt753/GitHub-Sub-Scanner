import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { FakeKV } from "./fakes";
import { handlePush } from "../src/handlers/push";
import { putSubscriber } from "../src/kv";
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
