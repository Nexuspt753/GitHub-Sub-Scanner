import { describe, it, expect, beforeEach } from "vitest";
import { FakeKV } from "./fakes";
import {
  getSubscriber, putSubscriber, deleteSubscriber, listSubscribers,
  getResults, putResults, getConv, putConv, deleteConv,
  getShare, putShare,
} from "../src/kv";
import type { SubscriberRecord } from "../src/types";
import { sampleNodes } from "./fixtures";

describe("kv manager", () => {
  let kv: FakeKV;
  beforeEach(() => { kv = new FakeKV(); });

  it("gets/puts/deletes a subscriber", async () => {
    expect(await getSubscriber(kv, 1)).toBeNull();
    const rec: SubscriberRecord = { chatId: 1, matrix: { conditions: [], combinator: "AND", mode: "diff" }, createdAt: 1, lastNotifiedAt: 0 };
    await putSubscriber(kv, rec);
    expect(await getSubscriber(kv, 1)).toEqual(rec);
    await deleteSubscriber(kv, 1);
    expect(await getSubscriber(kv, 1)).toBeNull();
  });

  it("lists subscribers by prefix", async () => {
    await putSubscriber(kv, { chatId: 1, matrix: { conditions: [], combinator: "AND", mode: "diff" }, createdAt: 1, lastNotifiedAt: 0 });
    await putSubscriber(kv, { chatId: 2, matrix: { conditions: [], combinator: "AND", mode: "diff" }, createdAt: 2, lastNotifiedAt: 0 });
    const list = await listSubscribers(kv);
    expect(list).toHaveLength(2);
    expect(list.map((s) => s.chatId).sort((a, b) => a - b)).toEqual([1, 2]);
  });

  it("gets/puts results cache", async () => {
    expect(await getResults(kv)).toBeNull();
    await putResults(kv, sampleNodes);
    const got = await getResults(kv);
    expect(got).not.toBeNull();
    expect(got!.nodes).toHaveLength(sampleNodes.length);
    expect(got!.fetchedAt).toBeGreaterThan(0);
  });

  it("manages conv wizard state", async () => {
    expect(await getConv(kv, 1)).toBeNull();
    await putConv(kv, 1, { step: "field", conditions: [] });
    expect(await getConv(kv, 1)).toEqual({ step: "field", conditions: [] });
    await deleteConv(kv, 1);
    expect(await getConv(kv, 1)).toBeNull();
  });

  it("manages share tokens", async () => {
    await putShare(kv, "abc", { conditions: [], combinator: "AND", mode: "diff" });
    expect(await getShare(kv, "abc")).toEqual({ conditions: [], combinator: "AND", mode: "diff" });
    expect(await getShare(kv, "missing")).toBeNull();
  });
});
