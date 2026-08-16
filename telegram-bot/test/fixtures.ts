import type { Node, Matrix } from "../src/types";

export const sampleNodes: Node[] = [
  {
    name: "US-1", protocol: "vless", address: "1.2.3.4", port: 443,
    alive: true, tcp_ping_ms: 120, speed_mbps: 45.2,
    gemini_reachable: true, score: 87.2, country: "United States",
    region: "California", city: "Los Angeles", isp: "Amazon",
    uri: "vless://uuid1@1.2.3.4:443?security=none#US-1",
  },
  {
    name: "UK-1", protocol: "trojan", address: "5.6.7.8", port: 443,
    alive: true, tcp_ping_ms: 80, speed_mbps: 30.0,
    gemini_reachable: false, score: 72.5, country: "United Kingdom",
    region: "England", city: "London", isp: "DigitalOcean",
    uri: "trojan://pw@5.6.7.8:443#UK-1",
  },
  {
    name: "DE-1", protocol: "vmess", address: "9.10.11.12", port: 8443,
    alive: false, tcp_ping_ms: 200, speed_mbps: 10.1,
    gemini_reachable: false, score: 0, country: "Germany",
    region: "Hesse", city: "Frankfurt", isp: "Hetzner",
    uri: "vmess://eyJhZGQiOiI5LjEwLjExLjEyIn0=",
  },
  {
    name: "JP-1", protocol: "vless", address: "13.14.15.16", port: 443,
    alive: true, tcp_ping_ms: 150, speed_mbps: 60.5,
    gemini_reachable: true, score: 91.0, country: "Japan",
    region: "Tokyo", city: "Tokyo", isp: "Linode",
    uri: "vless://uuid4@13.14.15.16:443?security=tls#JP-1",
  },
];

export const countryUK: Matrix = {
  conditions: [{ field: "country", operator: "eq", value: "United Kingdom" }],
  combinator: "AND",
  mode: "diff",
};

export const scoreGt50: Matrix = {
  conditions: [{ field: "score", operator: "gt", value: 50 }],
  combinator: "AND",
  mode: "diff",
};

export const complexMatrix: Matrix = {
  conditions: [
    { field: "gemini", operator: "eq", value: true },
    { field: "country", operator: "in", value: ["United States", "United Kingdom", "Japan"] },
  ],
  combinator: "AND",
  mode: "digest",
};
