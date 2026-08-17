import type { Node } from "./types";

export function escapeHtml(str: string | null | undefined): string {
  return String(str ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

export function buildTxt(nodes: Node[]): string {
  return nodes.map((n) => n.uri).join("\n");
}

// ---------------------------------------------------------------------------
// Mihomo / Clash YAML builder
// ---------------------------------------------------------------------------
// WhiteVPN-Desktop and Mihomo read a Clash-style YAML directly, so we convert
// the matched share links to proxy dicts, drop unsupported protocols
// (ssr/anytls/tuic), make names YAML-safe + unique, and emit a config with
// select/url-test groups + a MATCH rule. Unsupported links are skipped.

function atobUtf8(b64: string): string {
  try { return decodeURIComponent(escape(atob(b64))); } catch { return atob(b64); }
}

function parseQuery(q: string): Record<string, string> {
  const out: Record<string, string> = {};
  q.split("&").forEach((kv) => {
    const i = kv.indexOf("=");
    if (i < 0) return;
    out[kv.slice(0, i)] = decodeURIComponent(kv.slice(i + 1));
  });
  return out;
}

function splitUserInfoHost(uri: string, scheme: string) {
  const rest = uri.slice(scheme.length + 3);
  const hashI = rest.indexOf("#");
  const name = hashI >= 0 ? decodeURIComponent(rest.slice(hashI + 1)) : "";
  let main = hashI >= 0 ? rest.slice(0, hashI) : rest;
  const qI = main.indexOf("?");
  const query = qI >= 0 ? parseQuery(main.slice(qI + 1)) : {};
  main = qI >= 0 ? main.slice(0, qI) : main;
  const atI = main.indexOf("@");
  let user = "";
  let hostport = main;
  if (atI >= 0) { user = main.slice(0, atI); hostport = main.slice(atI + 1); }
  const colonI = hostport.lastIndexOf(":");
  const port = colonI >= 0 ? hostport.slice(colonI + 1) : "443";
  const host = colonI >= 0 ? hostport.slice(0, colonI) : hostport;
  return { user, host, port: parseInt(port, 10) || 443, query, name };
}

function attachTransport(p: any, net: string, path?: string, host?: string): void {
  net = net || "tcp";
  if (net === "tcp" || !net) return;
  if (net === "ws" || net === "httpupgrade") {
    const opts: any = { path: path || "/", headers: { "User-Agent": "Mozilla/5.0" } };
    if (host) opts.headers.Host = host;
    p.network = net; p["ws-opts"] = opts;
  } else if (net === "grpc") {
    p.network = "grpc"; p["grpc-opts"] = { "grpc-service-name": path || "" };
  } else if (net === "h2" || net === "http") {
    p.network = net; p[net + "-opts"] = { path: [path || "/"], headers: host ? { Host: host } : {} };
  } else if (net === "xhttp") {
    p.network = "xhttp"; p["xhttp-opts"] = { path: path || "/", ...(host ? { host } : {}) };
  }
}

function convVmess(uri: string): any {
  const json = JSON.parse(atobUtf8(uri.slice("vmess://".length)));
  const tls = String(json.tls || "").toLowerCase();
  const p: any = { type: "vmess", name: json.ps || "vmess", server: json.add || json.host,
    port: parseInt(json.port, 10) || 443, uuid: json.id,
    alterId: parseInt(json.aid, 10) || 0, cipher: json.scy || "auto",
    udp: true, "xudp": true, "skip-cert-verify": false };
  if (tls === "tls" || tls === "1" || tls === "true") p.tls = true;
  if (json.sni) p.servername = json.sni;
  if (json.alpn) p.alpn = String(json.alpn).split(",");
  if (json.net && json.net !== "tcp") attachTransport(p, json.net, json.path, json.host);
  return p;
}
function convVless(uri: string): any {
  const { user, host, port, query, name } = splitUserInfoHost(uri, "vless");
  const p: any = { type: "vless", name: name || "vless", server: host, port, uuid: user, udp: true, "xudp": true };
  const sec = (query.security || "none").toLowerCase();
  if (sec === "tls" || sec === "reality") {
    p.tls = true; p["client-fingerprint"] = query.fp || "chrome";
    if (query.alpn) p.alpn = String(query.alpn).split(",");
    if (query.sni) p.servername = query.sni;
    if (query.pbk) { const r: any = { "public-key": query.pbk }; if (query.sid) r["short-id"] = query.sid; p["reality-opts"] = r; }
  }
  if (query.flow) p.flow = query.flow.toLowerCase();
  attachTransport(p, query.type || "tcp", query.path, query.host);
  return p;
}
function convTrojan(uri: string): any {
  const { user, host, port, query, name } = splitUserInfoHost(uri, "trojan");
  const p: any = { type: "trojan", name: name || "trojan", server: host, port, password: decodeURIComponent(user), udp: true };
  const sec = (query.security || "").toLowerCase();
  if (sec === "tls" || sec === "reality" || query.sni) p.tls = true;
  if (query.sni) p.sni = query.sni;
  if (query.allowInsecure) p["skip-cert-verify"] = String(query.allowInsecure).toLowerCase() === "1" || String(query.allowInsecure).toLowerCase() === "true";
  if (query.alpn) p.alpn = String(query.alpn).split(",");
  p["client-fingerprint"] = query.fp || "chrome";
  attachTransport(p, query.type || "tcp", query.path, query.host);
  return p;
}
function convSS(uri: string): any {
  const rest = uri.slice("ss://".length).split("#")[0];
  let user: string; let hostport: string;
  if (rest.includes("@")) { const i = rest.indexOf("@"); user = rest.slice(0, i); hostport = rest.slice(i + 1); }
  else { const d = atobUtf8(rest); const i = d.indexOf("@"); user = d.slice(0, i); hostport = d.slice(i + 1); }
  if (!user.includes(":")) user = atobUtf8(user);
  const ci = user.indexOf(":");
  const method = user.slice(0, ci); const password = user.slice(ci + 1);
  const hp = hostport.split(":").length > 1 ? hostport.split(":") : [hostport, "443"];
  return { type: "ss", name: decodeURIComponent(uri.split("#")[1] || "ss"), server: hp[0],
    port: parseInt(hp[1], 10) || 443, cipher: method, password, udp: true };
}
function convHy2(uri: string): any {
  const { user, host, port, query, name } = splitUserInfoHost(uri, "hysteria2");
  const p: any = { type: "hysteria2", name: name || "hysteria2", server: host, port, password: decodeURIComponent(user) };
  if (query.sni) p.sni = query.sni;
  if (query.allowInsecure || query.insecure) p["skip-cert-verify"] = true;
  if (query.alpn) p.alpn = String(query.alpn).split(",");
  if (query.mport) p.ports = query.mport;
  if (query.obfs) { p.obfs = query.obfs; if (query["obfs-password"]) p["obfs-password"] = query["obfs-password"]; }
  return p;
}

function convertUri(uri: string): any | null {
  uri = (uri || "").trim();
  if (!uri) return null;
  try {
    if (uri.startsWith("vmess://")) return convVmess(uri);
    if (uri.startsWith("vless://")) return convVless(uri);
    if (uri.startsWith("trojan://")) return convTrojan(uri);
    if (uri.startsWith("ss://") || uri.startsWith("shadowsocks://")) return convSS(uri);
    if (uri.startsWith("hy2://") || uri.startsWith("hysteria2://")) return convHy2(uri);
  } catch { return null; }
  return null; // ssr / anytls / tuic intentionally dropped
}

function sanitizeName(name: string): string {
  return String(name || "").replace(/\r?\n/g, " ").replace(/^[\s@`]+/, "").trim();
}

function yamlScalar(v: any): string {
  if (typeof v === "boolean") return v ? "true" : "false";
  if (v === null || v === undefined) return "null";
  if (typeof v === "number") return String(v);
  const s = String(v);
  if (s === "" || /^[\s\-?:,#\[\]{}*&!|>%@`"']/.test(s) || /:\s|#\s|:$|#$/.test(s) ||
      s !== s.trim() || ["true", "false", "null", "yes", "no", "~"].includes(s) ||
      /^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/.test(s)) {
    return '"' + s.replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
  }
  return s;
}

function yamlDump(obj: any, indent = 0): string {
  const pad = "  ".repeat(indent);
  if (Array.isArray(obj)) {
    if (!obj.length) return pad + "[]";
    return obj.map((item: any) => {
      if (item && typeof item === "object" && Object.keys(item).length) {
        const inner = yamlDump(item, indent + 1);
        const nl = inner.indexOf("\n");
        const first = nl >= 0 ? inner.slice(0, nl) : inner;
        const rest = nl >= 0 ? inner.slice(nl + 1) : "";
        return pad + "- " + first.trim() + (rest ? "\n" + rest : "");
      }
      return pad + "- " + yamlScalar(item);
    }).join("\n");
  }
  if (obj && typeof obj === "object") {
    const keys = Object.keys(obj);
    if (!keys.length) return pad + "{}";
    return keys.map((k) => {
      const v = obj[k];
      const key = /^[A-Za-z0-9_.\-/]+$/.test(k) ? k : yamlScalar(k);
      if (v && typeof v === "object" && (Array.isArray(v) ? v.length : Object.keys(v).length)) {
        return pad + key + ":\n" + yamlDump(v, indent + 1);
      }
      return pad + key + ": " + yamlScalar(v);
    }).join("\n");
  }
  return pad + yamlScalar(obj);
}

export function buildMihomoYaml(nodes: Node[]): string {
  const seen = new Set<string>();
  const proxies: any[] = [];
  for (const n of nodes) {
    const p = convertUri(n.uri);
    if (!p) continue;
    let name = sanitizeName(p.name) || "node";
    if (seen.has(name)) {
      let i = 1;
      while (seen.has(name + "-" + String(i).padStart(2, "0"))) i++;
      name = name + "-" + String(i).padStart(2, "0");
    }
    seen.add(name);
    p.name = name;
    proxies.push(p);
  }
  if (!proxies.length) return "";
  const names = proxies.map((p) => p.name);
  const doc: any = {
    proxies,
    "proxy-groups": [
      { name: "Proxy", type: "select", proxies: ["Auto"].concat(names) },
      { name: "Auto", type: "url-test", url: "https://connectivitycheck.gstatic.com/generate_204",
        interval: 300, tolerance: 100, proxies: names },
    ],
    rules: ["MATCH,Proxy"],
  };
  return yamlDump(doc, 0) + "\n";
}

export function makeCaption(nodes: Node[], label: string): string {
  if (nodes.length === 0) return "0 configs";
  const top = nodes.slice(0, 3).map((n) => n.country ?? "?").join(", ");
  return `${escapeHtml(String(nodes.length))} configs · ${escapeHtml(label)} · e.g. ${escapeHtml(top)}`;
}
