import { describe, it, expect, beforeEach } from "vitest";
import { FakeKV } from "./fakes";
import { Wizard } from "../src/handlers/wizard";
import { getConv, getSubscriber } from "../src/kv";

const CHAT = 42;

function makeWizard(kv: FakeKV) {
  return new Wizard(kv, CHAT);
}

async function collect(w: Wizard): Promise<string[]> {
  const sent: string[] = [];
  await w.start((t, _kb) => { sent.push(t); return Promise.resolve(); });
  return sent;
}

describe("Wizard", () => {
  let kv: FakeKV;
  beforeEach(() => { kv = new FakeKV(); });

  it("starts at field step and lists fields", async () => {
    const w = makeWizard(kv);
    const sent = await collect(w);
    const state = await getConv(kv, CHAT);
    expect(state?.step).toBe("field");
    expect(sent[0]).toContain("Step 1");
  });

  it("advances field -> operator -> value", async () => {
    const w = makeWizard(kv);
    await collect(w);
    await w.handleCallback("field:country", () => Promise.resolve());
    expect((await getConv(kv, CHAT))?.step).toBe("operator");
    await w.handleCallback("op:eq", () => Promise.resolve());
    expect((await getConv(kv, CHAT))?.step).toBe("value");
  });

  it("accepts a text value and moves to combinator", async () => {
    const w = makeWizard(kv);
    await collect(w);
    await w.handleCallback("field:score", () => Promise.resolve());
    await w.handleCallback("op:gt", () => Promise.resolve());
    await w.handleValue("50", () => Promise.resolve());
    expect((await getConv(kv, CHAT))?.step).toBe("combinator");
  });

  it("AND + done -> mode step", async () => {
    const w = makeWizard(kv);
    await collect(w);
    await w.handleCallback("field:country", () => Promise.resolve());
    await w.handleCallback("op:eq", () => Promise.resolve());
    await w.handleValue("United Kingdom", () => Promise.resolve());
    await w.handleCallback("comb:AND:done", () => Promise.resolve());
    expect((await getConv(kv, CHAT))?.step).toBe("mode");
  });

  it("confirm writes subscriber and clears conv state", async () => {
    const w = makeWizard(kv);
    await collect(w);
    await w.handleCallback("field:country", () => Promise.resolve());
    await w.handleCallback("op:eq", () => Promise.resolve());
    await w.handleValue("United Kingdom", () => Promise.resolve());
    await w.handleCallback("comb:AND:done", () => Promise.resolve());
    await w.handleCallback("mode:diff", () => Promise.resolve());
    const sub = await getSubscriber(kv, CHAT);
    expect(sub).not.toBeNull();
    expect(sub!.matrix.conditions).toHaveLength(1);
    expect(sub!.matrix.conditions[0].field).toBe("country");
    expect(sub!.matrix.mode).toBe("diff");
    expect(await getConv(kv, CHAT)).toBeNull();
  });

  it("OR + more adds a second condition", async () => {
    const w = makeWizard(kv);
    await collect(w);
    await w.handleCallback("field:country", () => Promise.resolve());
    await w.handleCallback("op:eq", () => Promise.resolve());
    await w.handleValue("United Kingdom", () => Promise.resolve());
    await w.handleCallback("comb:OR:more", () => Promise.resolve());
    expect((await getConv(kv, CHAT))?.step).toBe("field");
    expect((await getConv(kv, CHAT))?.conditions).toHaveLength(1);
  });

  it("digest mode confirm works", async () => {
    const w = makeWizard(kv);
    await collect(w);
    await w.handleCallback("field:score", () => Promise.resolve());
    await w.handleCallback("op:gt", () => Promise.resolve());
    await w.handleValue("50", () => Promise.resolve());
    await w.handleCallback("comb:AND:done", () => Promise.resolve());
    await w.handleCallback("mode:digest", () => Promise.resolve());
    const sub = await getSubscriber(kv, CHAT);
    expect(sub!.matrix.mode).toBe("digest");
  });
});
