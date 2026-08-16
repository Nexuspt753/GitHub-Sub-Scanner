import { describe, it, expect } from "vitest";
import { FakeKV } from "./fakes";

describe("smoke", () => {
  it("routes /health ok", async () => {
    const mod = await import("../src/index");
    const worker = mod.default as any;
    const res = await worker.fetch(new Request("http://fake.dev/health"), { BOT_KV: new FakeKV() }, {});
    expect(res.status).toBe(200);
  });
});
