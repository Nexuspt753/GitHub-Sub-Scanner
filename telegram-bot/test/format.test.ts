import { describe, it, expect } from "vitest";
import { escapeHtml, buildTxt, makeCaption } from "../src/format";
import { sampleNodes } from "./fixtures";

describe("format", () => {
  it("escapeHtml escapes &, <, >", () => {
    expect(escapeHtml("a < b & c > d")).toBe("a &lt; b &amp; c &gt; d");
  });

  it("escapeHtml handles null/undefined", () => {
    expect(escapeHtml(null)).toBe("");
    expect(escapeHtml(undefined)).toBe("");
  });

  it("buildTxt joins URIs one per line", () => {
    const txt = buildTxt(sampleNodes.slice(0, 2));
    expect(txt).toBe(
      "vless://uuid1@1.2.3.4:443?security=none#US-1\ntrojan://pw@5.6.7.8:443#UK-1",
    );
  });

  it("buildTxt returns empty string for no nodes", () => {
    expect(buildTxt([])).toBe("");
  });

  it("makeCaption summarizes the match", () => {
    const c = makeCaption(sampleNodes.slice(0, 3), "US, UK");
    expect(c).toContain("3 configs");
    expect(c).toContain("US, UK");
  });

  it("makeCaption handles empty", () => {
    expect(makeCaption([], "x")).toBe("0 configs");
  });

  it("makeCaption escapes html in label", () => {
    const c = makeCaption(sampleNodes.slice(0, 1), "a<b");
    expect(c).toContain("a&lt;b");
  });
});
