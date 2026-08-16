import { describe, it, expect } from "vitest";
import { evaluate } from "../src/evaluate";
import {
  sampleNodes, countryUK, scoreGt50, complexMatrix,
} from "./fixtures";
import type { Matrix } from "../src/types";

describe("evaluate", () => {
  it("filters by country eq", () => {
    const r = evaluate(countryUK, sampleNodes);
    expect(r).toHaveLength(1);
    expect(r[0].country).toBe("United Kingdom");
  });

  it("filters by score gt", () => {
    const r = evaluate(scoreGt50, sampleNodes);
    expect(r.every((n) => n.score > 50)).toBe(true);
    expect(r.length).toBeGreaterThan(0);
  });

  it("AND combines conditions", () => {
    const m: Matrix = {
      conditions: [
        { field: "gemini", operator: "eq", value: true },
        { field: "score", operator: "gt", value: 90 },
      ],
      combinator: "AND",
      mode: "diff",
    };
    const r = evaluate(m, sampleNodes);
    expect(r.every((n) => n.gemini_reachable === true && n.score > 90)).toBe(true);
  });

  it("OR combines conditions", () => {
    const m: Matrix = {
      conditions: [
        { field: "country", operator: "eq", value: "Japan" },
        { field: "country", operator: "eq", value: "Germany" },
      ],
      combinator: "OR",
      mode: "diff",
    };
    const r = evaluate(m, sampleNodes);
    expect(r.map((n) => n.country).sort()).toEqual(["Germany", "Japan"]);
  });

  it("supports 'in' operator", () => {
    const r = evaluate(complexMatrix, sampleNodes);
    expect(r.map((n) => n.country).sort()).toEqual(["Japan", "United States"]);
  });

  it("supports neq / lt / lte / gte", () => {
    const lt: Matrix = { conditions: [{ field: "ping", operator: "lt", value: 130 }], combinator: "AND", mode: "diff" };
    expect(evaluate(lt, sampleNodes).every((n) => (n.tcp_ping_ms ?? 999) < 130)).toBe(true);
    const gte: Matrix = { conditions: [{ field: "score", operator: "gte", value: 87.2 }], combinator: "AND", mode: "diff" };
    expect(evaluate(gte, sampleNodes).every((n) => n.score >= 87.2)).toBe(true);
    const neq: Matrix = { conditions: [{ field: "country", operator: "neq", value: "United States" }], combinator: "AND", mode: "diff" };
    expect(evaluate(neq, sampleNodes).every((n) => n.country !== "United States")).toBe(true);
  });

  it("maps gemini field to gemini_reachable", () => {
    const m: Matrix = { conditions: [{ field: "gemini", operator: "eq", value: true }], combinator: "AND", mode: "diff" };
    const r = evaluate(m, sampleNodes);
    expect(r.every((n) => n.gemini_reachable === true)).toBe(true);
  });

  it("maps ping field to tcp_ping_ms and speed to speed_mbps", () => {
    const m: Matrix = { conditions: [{ field: "speed", operator: "gte", value: 45 }], combinator: "AND", mode: "diff" };
    expect(evaluate(m, sampleNodes).every((n) => (n.speed_mbps ?? 0) >= 45)).toBe(true);
  });

  it("returns empty when nothing matches", () => {
    const m: Matrix = { conditions: [{ field: "country", operator: "eq", value: "Atlantis" }], combinator: "AND", mode: "diff" };
    expect(evaluate(m, sampleNodes)).toEqual([]);
  });

  it("empty conditions matches all", () => {
    const m: Matrix = { conditions: [], combinator: "AND", mode: "diff" };
    expect(evaluate(m, sampleNodes)).toHaveLength(sampleNodes.length);
  });
});
