import { describe, it, expect } from "vitest";
import worker from "../src/index";

describe("smoke", () => {
  it("responds ok", async () => {
    const res = await worker.fetch(new Request("http://fake.dev/"), {} as any, {} as any);
    expect(await res.text()).toBe("ok");
  });
});
