import { describe, it, expect } from "vitest";
import { escapeHtml, buildTxt, makeCaption, buildMihomoYaml } from "../src/format";
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

  it("buildMihomoYaml drops unsupported protocols", () => {
    const nodes = [
      ...sampleNodes,
      { ...sampleNodes[0], uri: "ssr://a:b:c:d:e:f:g:h" } as any,
      { ...sampleNodes[0], uri: "anytls://u@1.1.1.1:443#x" } as any,
      { ...sampleNodes[0], uri: "tuic://u@2.2.2.2:443?password=1#x" } as any,
    ];
    const yaml = buildMihomoYaml(nodes);
    // Only the 4 supported sample URIs survive (vmess/vless/trojan/vless).
    expect((yaml.match(/^\s*- type:/gm) || []).length).toBe(4);
    expect(yaml).toContain("proxies:");
    expect(yaml).toContain("proxy-groups:");
    expect(yaml).toContain("MATCH,Proxy");
  });

  it("buildMihomoYaml returns empty string when nothing parses", () => {
    expect(buildMihomoYaml([{ ...sampleNodes[0], uri: "ssr://bad" } as any])).toBe("");
  });

  it("buildMihomoYaml makes names unique", () => {
    const dup = { ...sampleNodes[0] };
    const yaml = buildMihomoYaml([sampleNodes[0], dup]);
    const names = (yaml.match(/^\s*name: (.+)$/gm) || []).map((m) => m.replace(/^\s*name: /, ""));
    expect(new Set(names).size).toBe(names.length);
  });
});
