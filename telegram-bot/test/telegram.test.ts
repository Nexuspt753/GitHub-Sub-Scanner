import { describe, it, expect, vi, afterEach } from "vitest";
import { sendDocument, sendMessage } from "../src/telegram";

describe("telegram helpers", () => {
  afterEach(() => { vi.restoreAllMocks(); });

  it("sendMessage calls sendMessage endpoint", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response('{"ok":true}', { status: 200 }));
    await sendMessage("TOKEN", 123, "hello");
    expect(spy).toHaveBeenCalledTimes(1);
    const [url, init] = spy.mock.calls[0];
    expect(String(url)).toContain("sendMessage");
    expect(init!.method).toBe("POST");
    const body = JSON.parse(init!.body as string);
    expect(body.chat_id).toBe(123);
    expect(body.text).toBe("hello");
  });

  it("sendDocument calls sendDocument endpoint with multipart", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response('{"ok":true}', { status: 200 }));
    await sendDocument("TOKEN", 123, "cap", "subs.txt", "uri1\nuri2");
    expect(spy).toHaveBeenCalledTimes(1);
    const [url, init] = spy.mock.calls[0];
    expect(String(url)).toContain("sendDocument");
    const body = init!.body as FormData;
    expect(body.get("chat_id")).toBe("123");
    expect(body.get("caption")).toBe("cap");
    expect(body.get("document")).toBeTruthy();
  });
});
