import { describe, it, expect } from "vitest";
import type { Matrix, SubscriberRecord, Node } from "../src/types";

describe("types", () => {
  it("constructs a valid diff matrix", () => {
    const m: Matrix = {
      conditions: [
        { field: "country", operator: "eq", value: "United Kingdom" },
        { field: "score", operator: "gt", value: 50 },
      ],
      combinator: "AND",
      mode: "diff",
    };
    expect(m.conditions).toHaveLength(2);
    expect(m.mode).toBe("diff");
  });

  it("constructs a valid digest matrix with 'in' operator", () => {
    const m: Matrix = {
      conditions: [{ field: "country", operator: "in", value: ["US", "DE"] }],
      combinator: "OR",
      mode: "digest",
    };
    expect(Array.isArray(m.conditions[0].value)).toBe(true);
  });

  it("constructs a subscriber record", () => {
    const s: SubscriberRecord = {
      chatId: 123456,
      matrix: { conditions: [], combinator: "AND", mode: "diff" },
      createdAt: Date.now(),
      lastNotifiedAt: 0,
      lastNotifiedIds: ["abc"],
    };
    expect(s.chatId).toBe(123456);
  });

  it("constructs a node (mirrors results.json shape + uri)", () => {
    const n: Node = {
      name: "US-1", protocol: "vless", address: "1.2.3.4", port: 443,
      alive: true, tcp_ping_ms: 120, speed_mbps: 45.2,
      gemini_reachable: true, score: 87.2, country: "United States",
      region: "California", city: "Los Angeles", isp: "Amazon",
      uri: "vless://uuid1@1.2.3.4:443?security=none#US-1",
    };
    expect(n.score).toBe(87.2);
    expect(n.uri).toContain("vless://");
  });
});
