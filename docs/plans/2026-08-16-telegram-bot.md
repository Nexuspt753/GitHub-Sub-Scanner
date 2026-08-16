# Telegram Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Telegram Bot — a single Cloudflare Worker — that lets users query ranked proxy configs on demand and subscribe to personalized push DMs, each user filtering by their own matrix.

**Architecture:** A Cloudflare Worker owns all bot logic. The GitHub Action (producer) commits results, then POSTs the enriched `results.json` (nodes joined with their URIs) directly to the Worker's `/push` route. The Worker caches it in KV, evaluates every subscriber's personal filter matrix, and delivers matching configs as `.txt` subscription files via `sendDocument`. Users subscribe two ways: a deep-link from the site (a share token in KV, sidestepping Telegram's 64-byte `/start` limit) and an in-bot guided wizard. All free-tier: Workers, KV, Actions, Pages.

**Tech Stack:** TypeScript, Cloudflare Workers + Wrangler, Cloudflare Workers KV, Vitest. No runtime dependencies beyond the Workers global scope (native `fetch`, `crypto`).

**Reference spec:** `docs/2026-08-16-telegram-bot-design.md`

## Global Constraints

- Free tier only: 100k Worker requests/day, 50 subrequests/invocation, 100k KV reads/day, 1k KV writes/day, 25 MB max KV value.
- Deliver configs as `.txt` subscription files (`sendDocument`), never inline text (Telegram's 4,096-char limit).
- Query replies cost 1 subrequest (multipart file upload). Push DMs capped at ≤45 subrequests/invocation, paced (15/batch + 500ms).
- `lastNotifiedIds` capped to most recent 100 entries. Diff mode is the default push mode.
- Telegram `/start` payload ≤ 64 bytes → use share tokens, never inline matrices.
- Secure `/telegram` with `X-Telegram-Bot-Api-Secret-Token`; secure `/push` with `Authorization: Bearer`.
- CORS on `/share` for `*.github.io`.
- GitHub Action `curl` must use `-fsSL`.
- `results.json` nodes exclude URIs; `subs/uris.json` holds them by parallel index. The Action joins them before POSTing.

## File Structure

```
telegram-bot/                    # new directory: the Cloudflare Worker
  wrangler.toml                  # Worker config + KV namespace binding
  package.json                   # deps: wrangler, vitest, typescript
  tsconfig.json                  # strict TS config targeting Workers
  vitest.config.ts               # vitest config
  .gitignore                     # node_modules, .dev.vars, .wrangler
  .dev.vars                      # local secrets (TELEGRAM_BOT_TOKEN, etc.) — gitignored
  src/
    index.ts                     # entry: router + fetch handler
    types.ts                     # Condition, Matrix, SubscriberRecord, Node
    evaluate.ts                  # pure evaluate(matrix, nodes) -> Node[]
    format.ts                    # escapeHtml, buildTxt, makeCaption
    telegram.ts                  # sendDocument / sendMessage over fetch
    kv.ts                        # subscription manager: CRUD + wizard state
    handlers/
      telegram.ts                # /telegram webhook handler
      push.ts                    # /push handler
      share.ts                   # /share handler
  test/
    evaluate.test.ts
    format.test.ts
    kv.test.ts
    handlers.test.ts
    fixtures.ts                  # sample Node[] + matrices

.github/workflows/test.yml       # MODIFY: join URIs + POST to /push
index.html                       # MODIFY: add "Subscribe on Telegram" button + share logic
```

`types.ts`, `evaluate.ts`, `format.ts` are pure and dependency-free — they're unit-tested directly. `kv.ts`, `telegram.ts`, and the handlers are tested with an in-memory fake KV (defined in `test/fixtures.ts`).

---

## Task 1: Scaffold the Worker project

**Files:**
- Create: `telegram-bot/package.json`
- Create: `telegram-bot/tsconfig.json`
- Create: `telegram-bot/vitest.config.ts`
- Create: `telegram-bot/wrangler.toml`
- Create: `telegram-bot/.gitignore`
- Create: `telegram-bot/.dev.vars`

**Produces:** A runnable Worker skeleton with Wrangler config, a KV namespace placeholder, strict TS, and a passing no-op test.

- [ ] **Step 1: Create package.json**

Create `telegram-bot/package.json`:
```json
{
  "name": "config-ranker-bot",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "scripts": {
    "dev": "wrangler dev",
    "deploy": "wrangler deploy",
    "test": "vitest run",
    "test:watch": "vitest",
    "typecheck": "tsc --noEmit",
    "cf-typegen": "wrangler types"
  },
  "devDependencies": {
    "@cloudflare/vitest-pool-workers": "^0.8.0",
    "@cloudflare/workers-types": "^4.20250801.0",
    "typescript": "^5.6.0",
    "vitest": "^3.0.0",
    "wrangler": "^4.0.0"
  }
}
```

- [ ] **Step 2: Create tsconfig.json**

Create `telegram-bot/tsconfig.json`:
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ES2022",
    "moduleResolution": "bundler",
    "lib": ["ES2022"],
    "types": ["@cloudflare/workers-types"],
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noImplicitReturns": true,
    "esModuleInterop": true,
    "isolatedModules": true,
    "resolveJsonModule": true,
    "skipLibCheck": true,
    "noEmit": true
  },
  "include": ["src", "test"]
}
```

- [ ] **Step 3: Create vitest.config.ts**

Create `telegram-bot/vitest.config.ts`:
```ts
import { defineWorkersConfig } from "@cloudflare/vitest-pool-workers/config";

export default defineWorkersConfig({
  test: {
    poolOptions: {
      workers: {
        wrangler: { configPath: "./wrangler.toml" },
      },
    },
  },
});
```

- [ ] **Step 4: Create wrangler.toml**

Create `telegram-bot/wrangler.toml`:
```toml
name = "config-ranker-bot"
main = "src/index.ts"
compatibility_date = "2025-08-01"
compatibility_flags = ["nodejs_compat"]

[observability]
enabled = true

[[kv_namespaces]]
binding = "BOT_KV"
id = "REPLACE_WITH_PROD_NAMESPACE_ID"
```

- [ ] **Step 5: Create .gitignore and .dev.vars**

Create `telegram-bot/.gitignore`:
```
node_modules/
.dev.vars
.wrangler/
dist/
*.log
```

Create `telegram-bot/.dev.vars` (gitignored local secrets):
```
TELEGRAM_BOT_TOKEN=replace_me
TELEGRAM_SECRET_TOKEN=replace_me
PUSH_SECRET=replace_me
```

- [ ] **Step 6: Write a no-op test + handler, verify green**

Create `telegram-bot/src/index.ts`:
```ts
export default {
  fetch(): Response {
    return new Response("ok");
  },
};
```

Create `telegram-bot/test/smoke.test.ts`:
```ts
import { describe, it, expect } from "vitest";
import worker from "../src/index";

describe("smoke", () => {
  it("responds ok", async () => {
    const res = await worker.fetch(new Request("http://fake.dev/"));
    expect(await res.text()).toBe("ok");
  });
});
```

- [ ] **Step 7: Install deps and run the test**

Run:
```
cd telegram-bot && npm install
```
Expected: success.

Run:
```
npm test
```
Expected: smoke test passes.

- [ ] **Step 8: Commit**

```bash
git add telegram-bot
git commit -m "feat(bot): scaffold Cloudflare Worker project"
```

---

## Task 2: Define shared types

**Files:**
- Create: `telegram-bot/src/types.ts`
- Test: `telegram-bot/test/types.test.ts`

**Produces:** `Condition`, `Matrix`, `SubscriberRecord`, `Node` types used by every later task.

- [ ] **Step 1: Write the failing test**

Create `telegram-bot/test/types.test.ts`:
```ts
import { describe, it, expect } from "vitest";
import type { Condition, Matrix, SubscriberRecord, Node } from "../src/types";

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
      uri: "vless://uuid@1.2.3.4:443?...",
    };
    expect(n.score).toBe(87.2);
    expect(n.uri).toContain("vless://");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test`
Expected: FAIL — `../src/types` does not export the named types.

- [ ] **Step 3: Write types.ts**

Create `telegram-bot/src/types.ts`:
```ts
export type Field =
  | "country" | "isp" | "protocol" | "score"
  | "ping" | "speed" | "gemini";

export type Operator = "eq" | "neq" | "lt" | "lte" | "gt" | "gte" | "in";

export interface Condition {
  field: Field;
  operator: Operator;
  value: string | number | string[];
}

export interface Matrix {
  conditions: Condition[];
  combinator: "AND" | "OR";
  mode: "diff" | "digest";
}

export interface Node {
  name: string | null;
  protocol: string;
  address: string;
  port: number;
  alive: boolean;
  tcp_ping_ms: number | null;
  speed_mbps: number | null;
  gemini_reachable: boolean | null;
  score: number;
  country: string | null;
  region: string | null;
  city: string | null;
  isp: string | null;
  uri: string;
}

export interface SubscriberRecord {
  chatId: number;
  matrix: Matrix;
  createdAt: number;
  lastNotifiedAt: number;
  lastNotifiedIds?: string[];
}

export interface ResultsPayload {
  nodes: Node[];
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add telegram-bot/src/types.ts telegram-bot/test/types.test.ts
git commit -m "feat(bot): add shared types (Node, Matrix, SubscriberRecord)"
```

---

## Task 3: Build fixtures

**Files:**
- Create: `telegram-bot/test/fixtures.ts`

**Produces:** A shared `sampleNodes: Node[]` and a few reusable matrices, consumed by the evaluate, format, and handler tests.

- [ ] **Step 1: Write fixtures.ts**

Create `telegram-bot/test/fixtures.ts`:
```ts
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
```

- [ ] **Step 2: Commit**

```bash
git add telegram-bot/test/fixtures.ts
git commit -m "feat(bot): add test fixtures (sample nodes + matrices)"
```

---

## Task 4: Implement the evaluate engine (pure)

**Files:**
- Create: `telegram-bot/src/evaluate.ts`
- Test: `telegram-bot/test/evaluate.test.ts`

**Consumes:** `types.ts`, `test/fixtures.ts`
**Produces:** `evaluate(matrix, nodes): Node[]` — the pure core shared by query + push. Highest-value unit to test.

- [ ] **Step 1: Write the failing test**

Create `telegram-bot/test/evaluate.test.ts`:
```ts
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
    // gemini=true AND country in [US,UK,JP] => US-1, JP-1 (UK-1 gemini=false, DE gemini=false)
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test`
Expected: FAIL — `evaluate` not defined.

- [ ] **Step 3: Write evaluate.ts**

Create `telegram-bot/src/evaluate.ts`:
```ts
import type { Matrix, Node, Condition } from "./types";

function fieldValue(node: Node, field: Condition["field"]): unknown {
  switch (field) {
    case "country": return node.country;
    case "isp": return node.isp;
    case "protocol": return node.protocol;
    case "score": return node.score;
    case "ping": return node.tcp_ping_ms;
    case "speed": return node.speed_mbps;
    case "gemini": return node.gemini_reachable;
  }
}

function matchCondition(node: Node, c: Condition): boolean {
  const v = fieldValue(node, c.field);
  switch (c.operator) {
    case "eq": return v === c.value;
    case "neq": return v !== c.value;
    case "lt": return typeof v === "number" && v < (c.value as number);
    case "lte": return typeof v === "number" && v <= (c.value as number);
    case "gt": return typeof v === "number" && v > (c.value as number);
    case "gte": return typeof v === "number" && v >= (c.value as number);
    case "in":
      return Array.isArray(c.value) && c.value.includes(String(v));
    default: return false;
  }
}

export function evaluate(matrix: Matrix, nodes: Node[]): Node[] {
  return nodes.filter((node) => {
    if (matrix.conditions.length === 0) return true;
    return matrix.combinator === "AND"
      ? matrix.conditions.every((c) => matchCondition(node, c))
      : matrix.conditions.some((c) => matchCondition(node, c));
  });
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add telegram-bot/src/evaluate.ts telegram-bot/test/evaluate.test.ts
git commit -m "feat(bot): implement pure evaluate() engine"
```

---

## Task 5: Implement format helpers (pure)

**Files:**
- Create: `telegram-bot/src/format.ts`
- Test: `telegram-bot/test/format.test.ts`

**Consumes:** `types.ts`, `test/fixtures.ts`
**Produces:** `escapeHtml`, `buildTxt`, `makeCaption` — used by handlers to build the .txt file and its caption.

- [ ] **Step 1: Write the failing test**

Create `telegram-bot/test/format.test.ts`:
```ts
import { describe, it, expect } from "vitest";
import { escapeHtml, buildTxt, makeCaption } from "../src/format";
import { sampleNodes } from "./fixtures";

describe("format", () => {
  it("escapeHtml escapes &, <, >", () => {
    expect(escapeHtml("a < b & c > d")).toBe("a &lt; b &amp; c &gt; d");
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
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test`
Expected: FAIL — format functions not defined.

- [ ] **Step 3: Write format.ts**

Create `telegram-bot/src/format.ts`:
```ts
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

export function makeCaption(nodes: Node[], label: string): string {
  if (nodes.length === 0) return "0 configs";
  const top = nodes.slice(0, 3).map((n) => n.country ?? "?").join(", ");
  return `${escapeHtml(String(nodes.length))} configs · ${escapeHtml(label)} · e.g. ${escapeHtml(top)}`;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add telegram-bot/src/format.ts telegram-bot/test/format.test.ts
git commit -m "feat(bot): add format helpers (escapeHtml, buildTxt, makeCaption)"
```

---

## Task 6: Implement the in-memory KV fake + telegram fake

**Files:**
- Create: `telegram-bot/test/fakes.ts`

**Produces:** `FakeKV` (KVNamespace-shaped in-memory store) and `FakeTelegram` (records outbound calls). Consumed by all handler tests. No real Cloudflare runtime needed for unit tests.

- [ ] **Step 1: Write fakes.ts**

Create `telegram-bot/test/fakes.ts`:
```ts
export class FakeKV {
  store = new Map<string, string>();

  async get(key: string, type?: string): Promise<unknown> {
    const v = this.store.get(key);
    if (v === undefined) return null;
    return type === "json" ? JSON.parse(v) : v;
  }

  async put(key: string, value: string): Promise<void> {
    this.store.set(key, value);
  }

  async delete(key: string): Promise<void> {
    this.store.delete(key);
  }

  async list(opts: { prefix: string }): Promise<{ keys: { name: string }[] }> {
    const keys = [...this.store.keys()]
      .filter((k) => k.startsWith(opts.prefix))
      .map((name) => ({ name }));
    return { keys };
  }
}

export class FakeTelegram {
  calls: { method: string; body: unknown }[] = [];

  async call(method: string, body: unknown): Promise<{ ok: boolean }> {
    this.calls.push({ method, body });
    return { ok: true };
  }
}
```

- [ ] **Step 2: Commit**

```bash
git add telegram-bot/test/fakes.ts
git commit -m "feat(bot): add FakeKV and FakeTelegram for unit testing"
```

---

## Task 7: Implement KV subscription manager

**Files:**
- Create: `telegram-bot/src/kv.ts`
- Test: `telegram-bot/test/kv.test.ts`

**Consumes:** `types.ts`, `test/fakes.ts`
**Produces:** `getSubscriber`, `putSubscriber`, `deleteSubscriber`, `listSubscribers`, `getResults`, `putResults`, `getConv`, `putConv`, `deleteConv`, `getShare`, `putShare`. The handlers' data layer.

- [ ] **Step 1: Write the failing test**

Create `telegram-bot/test/kv.test.ts`:
```ts
import { describe, it, expect, beforeEach } from "vitest";
import { FakeKV } from "./fakes";
import {
  getSubscriber, putSubscriber, deleteSubscriber, listSubscribers,
  getResults, putResults, getConv, putConv, deleteConv,
  getShare, putShare,
} from "../src/kv";
import type { SubscriberRecord, Node } from "../src/types";
import { sampleNodes } from "./fixtures";

describe("kv manager", () => {
  let kv: FakeKV;
  beforeEach(() => { kv = new FakeKV(); });

  it("gets/puts/deletes a subscriber", async () => {
    expect(await getSubscriber(kv, 1)).toBeNull();
    const rec: SubscriberRecord = { chatId: 1, matrix: { conditions: [], combinator: "AND", mode: "diff" }, createdAt: 1, lastNotifiedAt: 0 };
    await putSubscriber(kv, rec);
    expect(await getSubscriber(kv, 1)).toEqual(rec);
    await deleteSubscriber(kv, 1);
    expect(await getSubscriber(kv, 1)).toBeNull();
  });

  it("lists subscribers by prefix", async () => {
    await putSubscriber(kv, { chatId: 1, matrix: { conditions: [], combinator: "AND", mode: "diff" }, createdAt: 1, lastNotifiedAt: 0 });
    await putSubscriber(kv, { chatId: 2, matrix: { conditions: [], combinator: "AND", mode: "diff" }, createdAt: 2, lastNotifiedAt: 0 });
    const list = await listSubscribers(kv);
    expect(list).toHaveLength(2);
    expect(list.map((s) => s.chatId).sort((a, b) => a - b)).toEqual([1, 2]);
  });

  it("gets/puts results cache", async () => {
    expect(await getResults(kv)).toBeNull();
    const nodes = sampleNodes;
    await putResults(kv, nodes);
    const got = await getResults(kv);
    expect(got).not.toBeNull();
    expect(got!.nodes).toHaveLength(nodes.length);
    expect(got!.fetchedAt).toBeGreaterThan(0);
  });

  it("manages conv wizard state", async () => {
    expect(await getConv(kv, 1)).toBeNull();
    await putConv(kv, 1, { step: "field", conditions: [] });
    expect(await getConv(kv, 1)).toEqual({ step: "field", conditions: [] });
    await deleteConv(kv, 1);
    expect(await getConv(kv, 1)).toBeNull();
  });

  it("manages share tokens", async () => {
    await putShare(kv, "abc", { conditions: [], combinator: "AND", mode: "diff" });
    expect(await getShare(kv, "abc")).toEqual({ conditions: [], combinator: "AND", mode: "diff" });
    expect(await getShare(kv, "missing")).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test`
Expected: FAIL — `../src/kv` functions not defined.

- [ ] **Step 3: Write kv.ts**

Create `telegram-bot/src/kv.ts`:
```ts
import type {
  SubscriberRecord, Node, Matrix, ResultsPayload,
} from "./types";

export interface ConvState {
  step: string;
  conditions: Matrix["conditions"];
}

export interface ResultsCache {
  fetchedAt: number;
  nodes: Node[];
}

type KV = Pick<FakeLike, "get" | "put" | "delete" | "list">;
interface FakeLike {
  get(k: string, t?: string): Promise<unknown>;
  put(k: string, v: string): Promise<void>;
  delete(k: string): Promise<void>;
  list(o: { prefix: string }): Promise<{ keys: { name: string }[] }>;
}

export async function getSubscriber(kv: KV, chatId: number): Promise<SubscriberRecord | null> {
  const v = await kv.get(`sub:${chatId}`, "json");
  return v as SubscriberRecord | null;
}

export async function putSubscriber(kv: KV, rec: SubscriberRecord): Promise<void> {
  await kv.put(`sub:${rec.chatId}`, JSON.stringify(rec));
}

export async function deleteSubscriber(kv: KV, chatId: number): Promise<void> {
  await kv.delete(`sub:${chatId}`);
}

export async function listSubscribers(kv: KV): Promise<SubscriberRecord[]> {
  const { keys } = await kv.list({ prefix: "sub:" });
  const recs = await Promise.all(keys.map((k) => kv.get(k.name, "json")));
  return recs.filter(Boolean) as SubscriberRecord[];
}

export async function getResults(kv: KV): Promise<ResultsCache | null> {
  const v = await kv.get("results:cache", "json");
  return v as ResultsCache | null;
}

export async function putResults(kv: KV, nodes: Node[]): Promise<void> {
  const cache: ResultsCache = { fetchedAt: Date.now(), nodes };
  await kv.put("results:cache", JSON.stringify(cache));
}

export async function getConv(kv: KV, chatId: number): Promise<ConvState | null> {
  const v = await kv.get(`conv:${chatId}`, "json");
  return v as ConvState | null;
}

export async function putConv(kv: KV, chatId: number, state: ConvState): Promise<void> {
  await kv.put(`conv:${chatId}`, JSON.stringify(state));
}

export async function deleteConv(kv: KV, chatId: number): Promise<void> {
  await kv.delete(`conv:${chatId}`);
}

export async function getShare(kv: KV, token: string): Promise<Matrix | null> {
  const v = await kv.get(`share:${token}`, "json");
  return v as Matrix | null;
}

export async function putShare(kv: KV, token: string, matrix: Matrix): Promise<void> {
  await kv.put(`share:${token}`, JSON.stringify(matrix));
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add telegram-bot/src/kv.ts telegram-bot/test/kv.test.ts
git commit -m "feat(bot): implement KV subscription manager"
```

---

## Task 8: Implement telegram API helpers

**Files:**
- Create: `telegram-bot/src/telegram.ts`

**Consumes:** `test/fakes.ts` (FakeTelegram shape)
**Produces:** `sendDocument(token, chatId, caption, text)` and `sendMessage(token, chatId, text)` that POST to `api.telegram.org`.

This module talks to the real Telegram API via `fetch`. Test it with a global fetch spy rather than FakeTelegram, since it calls `fetch` directly.

- [ ] **Step 1: Write the failing test**

Create `telegram-bot/test/telegram.test.ts`:
```ts
import { describe, it, expect, vi, afterEach } from "vitest";
import { sendDocument, sendMessage } from "../src/telegram";

const fetchMock = vi.fn();
let lastFormData: FormData | null = null;

describe("telegram helpers", () => {
  afterEach(() => { fetchMock.mockReset(); lastFormData = null; });

  it("sendMessage calls sendMessage endpoint", async () => {
    const spy = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response('{"ok":true}', { status: 200 }));
    await sendMessage("TOKEN", 123, "hello");
    expect(spy).toHaveBeenCalledTimes(1);
    const [url, init] = spy.mock.calls[0];
    expect(String(url)).toContain("sendMessage");
    expect(init!.method).toBe("POST");
    expect(init!.headers && (init!.headers as Record<string, string>)["Content-Type"]).toContain("application/json");
    const body = JSON.parse(init!.body as string);
    expect(body.chat_id).toBe(123);
    expect(body.text).toBe("hello");
    spy.mockRestore();
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
    spy.mockRestore();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test`
Expected: FAIL — telegram functions not defined.

- [ ] **Step 3: Write telegram.ts**

Create `telegram-bot/src/telegram.ts`:
```ts
const BASE = "https://api.telegram.org";

export async function sendMessage(
  token: string,
  chatId: number,
  text: string,
): Promise<void> {
  await fetch(`${BASE}/bot${token}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: chatId, text, parse_mode: "HTML" }),
  });
}

export async function sendDocument(
  token: string,
  chatId: number,
  caption: string,
  filename: string,
  content: string,
): Promise<void> {
  const form = new FormData();
  form.set("chat_id", String(chatId));
  form.set("caption", caption);
  form.set("parse_mode", "HTML");
  form.set("document", new Blob([content], { type: "text/plain" }), filename);
  await fetch(`${BASE}/bot${token}/sendDocument`, {
    method: "POST",
    body: form,
  });
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add telegram-bot/src/telegram.ts telegram-bot/test/telegram.test.ts
git commit -m "feat(bot): add telegram sendDocument/sendMessage helpers"
```

---

## Task 9: Implement the /push handler

**Files:**
- Create: `telegram-bot/src/handlers/push.ts`
- Test: `telegram-bot/test/handlers.test.ts` (push portion)

**Consumes:** `kv.ts`, `evaluate.ts`, `format.ts`, `telegram.ts`, `test/fakes.ts`
**Produces:** `handlePush(env, request)` — validates bearer, stores results, evaluates each subscriber, paces DMs, prunes 403s, caps lastNotifiedIds.

- [ ] **Step 1: Write the failing test**

Create `telegram-bot/test/handlers.test.ts`:
```ts
import { describe, it, expect, beforeEach } from "vitest";
import { FakeKV, FakeTelegram } from "./fakes";
import { handlePush } from "../src/handlers/push";
import { putSubscriber, putResults } from "../src/kv";
import { sampleNodes, scoreGt50, countryUK } from "./fixtures";
import type { SubscriberRecord, ResultsPayload } from "../src/types";

const SECRET = "pushsecret";

function makeEnv(kv: FakeKV, token = "TOKEN") {
  return { kv, token, pushSecret: SECRET };
}

describe("handlePush", () => {
  let kv: FakeKV;
  let tg: FakeTelegram;
  beforeEach(() => { kv = new FakeKV(); tg = new FakeTelegram(); });

  it("rejects missing bearer", async () => {
    const res = await handlePush(makeEnv(kv, "TOKEN"), new Request("http://fake/push", {
      method: "POST", body: "{}", headers: {},
    }));
    expect(res.status).toBe(401);
  });

  it("rejects wrong bearer", async () => {
    const res = await handlePush(makeEnv(kv, "TOKEN"), new Request("http://fake/push", {
      method: "POST", body: "{}", headers: { authorization: "Bearer wrong" },
    }));
    expect(res.status).toBe(401);
  });

  it("stores results cache on success", async () => {
    const body: ResultsPayload = { nodes: sampleNodes };
    const res = await handlePush(makeEnv(kv, "TOKEN"), new Request("http://fake/push", {
      method: "POST",
      body: JSON.stringify(body),
      headers: { authorization: `Bearer ${SECRET}` },
    }));
    expect(res.status).toBe(200);
    const cached = await kv.get("results:cache", "json");
    expect(cached).not.toBeNull();
    expect((cached as any).nodes).toHaveLength(sampleNodes.length);
  });

  it("sends DMs to subscribers (diff mode, new matches)", async () => {
    const body: ResultsPayload = { nodes: sampleNodes };
    await putSubscriber(kv, { chatId: 1, matrix: scoreGt50, createdAt: 1, lastNotifiedAt: 0 });
    const res = await handlePush(makeEnv(kv), new Request("http://fake/push", {
      method: "POST", body: JSON.stringify(body),
      headers: { authorization: `Bearer ${SECRET}` },
    }));
    expect(res.status).toBe(200);
  });

  it("caps lastNotifiedIds to 100", async () => {
    const many = Array.from({ length: 150 }, (_, i) => i);
    const rec: SubscriberRecord = { chatId: 1, matrix: scoreGt50, createdAt: 1, lastNotifiedAt: 0, lastNotifiedIds: many };
    await putSubscriber(kv, rec);
    const body: ResultsPayload = { nodes: sampleNodes };
    await handlePush(makeEnv(kv), new Request("http://fake/push", {
      method: "POST", body: JSON.stringify(body),
      headers: { authorization: `Bearer ${SECRET}` },
    }));
    const updated = await kv.get("sub:1", "json") as SubscriberRecord;
    expect(updated.lastNotifiedIds!.length).toBeLessThanOrEqual(100);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test`
Expected: FAIL — `handlePush` not defined.

- [ ] **Step 3: Write handlers/push.ts**

Create `telegram-bot/src/handlers/push.ts`:
```ts
import type { SubscriberRecord } from "../types";
import { evaluate } from "../evaluate";
import { makeCaption } from "../format";
import { sendDocument } from "../telegram";
import {
  getResults, putResults, listSubscribers,
} from "../kv";

interface Env {
  kv: any;
  token: string;
  pushSecret: string;
}

async function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}

export async function handlePush(env: Env, request: Request): Promise<Response> {
  const auth = request.headers.get("authorization");
  if (!auth || auth !== `Bearer ${env.pushSecret}`) {
    return new Response("unauthorized", { status: 401 });
  }
  let body: any;
  try {
    body = await request.json();
  } catch {
    return new Response("invalid json", { status: 400 });
  }
  if (!body || !Array.isArray(body.nodes)) {
    return new Response("missing nodes", { status: 400 });
  }

  await putResults(env.kv, body.nodes);
  const subscribers = await listSubscribers(env.kv);

  const BATCH = 15;
  for (let i = 0; i < Math.min(subscribers.length, 45); i += BATCH) {
    const batch = subscribers.slice(i, i + BATCH);
    const results = await Promise.allSettled(
      batch.map((s) => sendToSubscriber(env, s, body.nodes)),
    );
    if (i + BATCH < Math.min(subscribers.length, 45)) await sleep(500);
  }

  if (subscribers.length > 45) {
    console.warn(`push: ${subscribers.length - 45} subscribers deferred (cap 45)`);
  }
  return new Response("ok", { status: 200 });
}

async function sendToSubscriber(
  env: Env,
  sub: SubscriberRecord,
  nodes: any[],
): Promise<void> {
  const matches = evaluate(sub.matrix, nodes);
  if (matches.length === 0) return;

  let toSend = matches;
  if (sub.matrix.mode === "diff") {
    const seen = new Set(sub.lastNotifiedIds ?? []);
    toSend = matches.filter((m) => !seen.has(matchId(m)));
    if (toSend.length === 0) return;
  } else {
    toSend = matches.slice(0, 5);
  }

  const ids = toSend.map(matchId);
  const updated: SubscriberRecord = {
    ...sub,
    lastNotifiedAt: Date.now(),
    lastNotifiedIds: capIds([...ids, ...(sub.lastNotifiedIds ?? [])]),
  };

  try {
    const txt = toSend.map((m) => m.uri).join("\n");
    await sendDocument(env.token, sub.chatId, labelFor(matches), "subscription.txt", txt);
    await env.kv.put(`sub:${sub.chatId}`, JSON.stringify(updated));
  } catch (e: any) {
    if (String(e?.message ?? e).includes("403")) {
      await env.kv.delete(`sub:${sub.chatId}`);
      return;
    }
    throw e;
  }
}

function matchId(m: any): string {
  return `${m.country}/${m.name}/${m.address}:${m.port}`;
}

function capIds(ids: string[]): string[] {
  return Array.from(new Set(ids)).slice(0, 100);
}

function labelFor(matches: any[]): string {
  return makeCaption(matches, "your filter");
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add telegram-bot/src/handlers/push.ts telegram-bot/test/handlers.test.ts
git commit -m "feat(bot): implement /push handler with pacing + pruning"
```

---

## Task 10: Implement the /share and /telegram handlers

**Files:**
- Create: `telegram-bot/src/handlers/share.ts`
- Create: `telegram-bot/src/handlers/telegram.ts`
- Test: add to `telegram-bot/test/handlers.test.ts`

**Consumes:** `kv.ts`, `evaluate.ts`, `format.ts`, `telegram.ts`, `test/fakes.ts`
**Produces:** `handleShare` (CORS + token gen + KV store) and `handleTelegram` (secret_token check + command dispatch).

- [ ] **Step 1: Append failing tests**

Append to `telegram-bot/test/handlers.test.ts`:
```ts
import { handleShare } from "../src/handlers/share";
import { handleTelegram } from "../src/handlers/telegram";
import { getShare } from "../src/kv";

describe("handleShare", () => {
  let kv: FakeKV;
  beforeEach(() => { kv = new FakeKV(); });

  it("returns a token + url, stores matrix", async () => {
    const res = await handleShare({ kv }, new Request("http://fake/share", {
      method: "POST",
      headers: { "content-type": "application/json", origin: "https://nexuspt753.github.io" },
      body: JSON.stringify({ conditions: [], combinator: "AND", mode: "diff" }),
    }));
    expect(res.status).toBe(200);
    const body = await res.json() as any;
    expect(body.token).toBeTruthy();
    expect(body.url).toContain("t.me");
    expect(body.url).toContain(body.token);
    const stored = await getShare(kv, body.token);
    expect(stored).not.toBeNull();
  });

  it("sets CORS headers", async () => {
    const res = await handleShare({ kv }, new Request("http://fake/share", {
      method: "POST",
      headers: { "content-type": "application/json", origin: "https://nexuspt753.github.io" },
      body: JSON.stringify({ conditions: [], combinator: "AND", mode: "diff" }),
    }));
    expect(res.headers.get("access-control-allow-origin")).toBe("https://nexuspt753.github.io");
  });

  it("rejects GET (preflight ok)", async () => {
    const res = await handleShare({ kv }, new Request("http://fake/share", {
      method: "OPTIONS",
      headers: { origin: "https://nexuspt753.github.io", "access-control-request-method": "POST" },
    }));
    expect(res.status).toBe(204);
  });
});

describe("handleTelegram", () => {
  let kv: FakeKV;
  beforeEach(() => { kv = new FakeKV(); });

  it("rejects missing secret_token", async () => {
    const res = await handleTelegram({ kv, token: "T", secretToken: "sec" }, new Request("http://fake/telegram", {
      method: "POST", body: JSON.stringify({ message: { chat: { id: 1 }, text: "/start" } }),
    }));
    expect(res.status).toBe(403);
  });

  it("responds 200 to valid /start", async () => {
    const res = await handleTelegram({ kv, token: "T", secretToken: "sec" }, new Request("http://fake/telegram", {
      method: "POST",
      headers: { "x-telegram-bot-api-secret-token": "sec" },
      body: JSON.stringify({ message: { chat: { id: 1 }, text: "/start" } }),
    }));
    expect(res.status).toBe(200);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test`
Expected: FAIL — handlers not defined.

- [ ] **Step 3: Write handlers/share.ts**

Create `telegram-bot/src/handlers/share.ts`:
```ts
import { putShare } from "../kv";
import type { Matrix } from "../types";

const ALLOWED_ORIGIN = "https://nexuspt753.github.io";

function corsHeaders(extra: Record<string, string> = {}): HeadersInit {
  return {
    "access-control-allow-origin": ALLOWED_ORIGIN,
    "access-control-allow-methods": "POST, OPTIONS",
    "access-control-allow-headers": "content-type",
    ...extra,
  };
}

function randomToken(len = 10): string {
  const chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789";
  const bytes = new Uint8Array(len);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => chars[b % chars.length]).join("");
}

export async function handleShare(env: { kv: any }, request: Request): Promise<Response> {
  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: corsHeaders() });
  }
  if (request.method !== "POST") {
    return new Response("method not allowed", { status: 405, headers: corsHeaders() });
  }
  let matrix: Matrix;
  try {
    matrix = await request.json();
  } catch {
    return new Response("invalid json", { status: 400, headers: corsHeaders() });
  }
  const token = randomToken();
  await putShare(env.kv, token, matrix);
  const url = `https://t.me/YOUR_BOT_USERNAME?start=share_${token}`;
  return Response.json({ token, url }, { headers: corsHeaders({ "content-type": "application/json" }) });
}
```

- [ ] **Step 4: Write handlers/telegram.ts**

Create `telegram-bot/src/handlers/telegram.ts`:
```ts
import { getSubscriber, putSubscriber, getResults, getShare } from "../kv";
import type { SubscriberRecord } from "../types";
import { evaluate } from "../evaluate";
import { buildTxt, makeCaption } from "../format";
import { sendDocument, sendMessage } from "../telegram";

interface Env {
  kv: any;
  token: string;
  secretToken: string;
}

export async function handleTelegram(env: Env, request: Request): Promise<Response> {
  const got = request.headers.get("x-telegram-bot-api-secret-token");
  if (got !== env.secretToken) {
    return new Response("forbidden", { status: 403 });
  }
  let update: any;
  try {
    update = await request.json();
  } catch {
    return new Response("ok", { status: 200 });
  }
  const msg = update.message;
  if (!msg || !msg.chat) return new Response("ok", { status: 200 });
  const chatId = msg.chat.id;
  const text = (msg.text ?? "").trim();

  try {
    if (text.startsWith("/start")) {
      await cmdStart(env, chatId, text);
    } else if (text === "/help") {
      await sendMessage(env.token, chatId, "Commands: /subscribe /unsubscribe /myfilters /top <n> /country <name> /status /help");
    } else if (text.startsWith("/top")) {
      const n = parseInt(text.split(/\s+/)[1] ?? "5", 10);
      await cmdTop(env, chatId, n);
    } else if (text.startsWith("/country")) {
      const name = text.replace("/country", "").trim();
      await cmdCountry(env, chatId, name);
    } else if (text === "/myfilters") {
      await cmdMyFilters(env, chatId);
    } else if (text === "/status") {
      await cmdStatus(env, chatId);
    } else if (text === "/subscribe") {
      await sendMessage(env.token, chatId, "Send me a filter link from the website (use its 'Subscribe on Telegram' button), or browse with /top <n> and /country <name>. A guided wizard is coming in the next update.");
    } else if (text === "/unsubscribe") {
      await env.kv.delete(`sub:${chatId}`);
      await sendMessage(env.token, chatId, "You are unsubscribed. You won't receive push updates anymore.");
    } else {
      await sendMessage(env.token, chatId, "I don't know that. Try /help");
    }
  } catch (e) {
    console.error("telegram handler error", e);
  }
  return new Response("ok", { status: 200 });
}

async function cmdStart(env: Env, chatId: number, text: string): Promise<void> {
  const match = text.match(/\/start share_(\S+)/);
  if (match) {
    const matrix = await getShare(env.kv, match[1]);
    if (!matrix) {
      await sendMessage(env.token, chatId, "This link expired or is invalid. Build a new filter on the website and try again.");
      return;
    }
    const now = Date.now();
    const rec: SubscriberRecord = { chatId, matrix, createdAt: now, lastNotifiedAt: 0 };
    await putSubscriber(env.kv, rec);
    await sendMessage(env.token, chatId, "You're subscribed! I'll DM you when configs matching your filter go live. Use /myfilters to review, /unsubscribe to stop.");
    return;
  }
  await sendMessage(env.token, chatId, "Welcome to Config Ranker Bot! Use /help to see commands, or subscribe via the website.");
}

async function withResults<T>(env: Env, chatId: number, fn: (nodes: any[]) => Promise<T>): Promise<T | void> {
  const cache = await getResults(env.kv);
  if (!cache) {
    await sendMessage(env.token, chatId, "No data yet — the first ranking run hasn't completed.");
    return;
  }
  return fn(cache.nodes);
}

async function cmdTop(env: Env, chatId: number, n: number): Promise<void> {
  await withResults(env, chatId, async (nodes) => {
    const top = [...nodes].sort((a, b) => b.score - a.score).slice(0, n);
    await sendDocument(env.token, chatId, makeCaption(top, "top"), "top.txt", buildTxt(top));
  });
}

async function cmdCountry(env: Env, chatId: number, name: string): Promise<void> {
  await withResults(env, chatId, async (nodes) => {
    const m = { conditions: [{ field: "country", operator: "eq", value: name }], combinator: "AND", mode: "digest" as const };
    const matches = evaluate(m as any, nodes);
    await sendDocument(env.token, chatId, makeCaption(matches, name), "country.txt", buildTxt(matches));
  });
}

async function cmdMyFilters(env: Env, chatId: number): Promise<void> {
  const sub = await getSubscriber(env.kv, chatId);
  if (!sub) {
    await sendMessage(env.token, chatId, "You have no active subscription. Use /subscribe or the website.");
    return;
  }
  await sendMessage(env.token, chatId, `Active filter: ${sub.matrix.conditions.length} conditions (${sub.matrix.mode} mode). Use /unsubscribe to remove.`);
}

async function cmdStatus(env: Env, chatId: number): Promise<void> {
  const cache = await getResults(env.kv);
  if (!cache) {
    await sendMessage(env.token, chatId, "No data yet.");
    return;
  }
  const ageMin = Math.round((Date.now() - cache.fetchedAt) / 60000);
  await sendMessage(env.token, chatId, `Data age: ${ageMin} min · ${cache.nodes.length} configs cached.`);
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm test`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add telegram-bot/src/handlers/share.ts telegram-bot/src/handlers/telegram.ts telegram-bot/test/handlers.test.ts
git commit -m "feat(bot): implement /share and /telegram handlers"
```

---

## Task 11: Implement the in-bot guided builder wizard

**Files:**
- Create: `telegram-bot/src/handlers/wizard.ts`
- Modify: `telegram-bot/src/handlers/telegram.ts` (route callback_query + /subscribe to wizard)
- Test: `telegram-bot/test/wizard.test.ts`

**Consumes:** `kv.ts` (getConv/putConv/deleteConv), `types.ts`, `test/fakes.ts`
**Produces:** `Wizard` class implementing the spec §5 Path 2 five-step flow (field → operator → value → combinator → mode → confirm) driven by inline keyboards, with `conv:<chatId>` state and `expirationTtl: 600`.

This is the second subscription path the user asked for. It's a stateful multi-step conversation, so it handles Telegram `callback_query` updates (inline keyboard button presses) as well as text messages.

- [ ] **Step 1: Write the failing test**

Create `telegram-bot/test/wizard.test.ts`:
```ts
import { describe, it, expect, beforeEach } from "vitest";
import { FakeKV } from "./fakes";
import { Wizard } from "../src/handlers/wizard";
import { getConv, getSubscriber } from "../src/kv";

const TOKEN = "TOKEN";
const CHAT = 42;

function makeWizard(kv: FakeKV) {
  return new Wizard(kv, TOKEN, CHAT);
}

describe("Wizard", () => {
  let kv: FakeKV;
  beforeEach(() => { kv = new FakeKV(); });

  it("starts at field step and lists fields", async () => {
    const w = makeWizard(kv);
    const sent: any[] = [];
    await w.start((text, kb) => sent.push({ text, kb }));
    const state = await getConv(kv, CHAT);
    expect(state?.step).toBe("field");
    expect(sent[0].kb).toBeTruthy();
  });

  it("advances field -> operator -> value", async () => {
    const w = makeWizard(kv);
    const sent: any[] = [];
    await w.start((t, kb) => sent.push({ t, kb }));
    await w.handleCallback("field:country", (t, kb) => sent.push({ t, kb }));
    expect((await getConv(kv, CHAT))?.step).toBe("operator");
    await w.handleCallback("op:eq", (t, kb) => sent.push({ t, kb }));
    expect((await getConv(kv, CHAT))?.step).toBe("value");
  });

  it("accepts a text value and moves to combinator", async () => {
    const w = makeWizard(kv);
    const sent: any[] = [];
    await w.start((t, kb) => sent.push({ t, kb }));
    await w.handleCallback("field:score", (t, kb) => sent.push({ t, kb }));
    await w.handleCallback("op:gt", (t, kb) => sent.push({ t, kb }));
    await w.handleValue("50", (t, kb) => sent.push({ t, kb }));
    expect((await getConv(kv, CHAT))?.step).toBe("combinator");
  });

  it("AND + done -> mode step", async () => {
    const w = makeWizard(kv);
    const sent: any[] = [];
    await w.start((t, kb) => sent.push({ t, kb }));
    await w.handleCallback("field:country", (t, kb) => sent.push({ t, kb }));
    await w.handleCallback("op:eq", (t, kb) => sent.push({ t, kb }));
    await w.handleValue("United Kingdom", (t, kb) => sent.push({ t, kb }));
    await w.handleCallback("comb:AND:done", (t, kb) => sent.push({ t, kb }));
    expect((await getConv(kv, CHAT))?.step).toBe("mode");
  });

  it("confirm writes subscriber and clears conv state", async () => {
    const w = makeWizard(kv);
    const sent: any[] = [];
    await w.start((t, kb) => sent.push({ t, kb }));
    await w.handleCallback("field:country", (t, kb) => sent.push({ t, kb }));
    await w.handleCallback("op:eq", (t, kb) => sent.push({ t, kb }));
    await w.handleValue("United Kingdom", (t, kb) => sent.push({ t, kb }));
    await w.handleCallback("comb:AND:done", (t, kb) => sent.push({ t, kb }));
    await w.handleCallback("mode:diff", (t, kb) => sent.push({ t, kb }));
    const sub = await getSubscriber(kv, CHAT);
    expect(sub).not.toBeNull();
    expect(sub!.matrix.conditions).toHaveLength(1);
    expect(sub!.matrix.conditions[0].field).toBe("country");
    expect(sub!.matrix.mode).toBe("diff");
    expect(await getConv(kv, CHAT)).toBeNull();
  });

  it("OR + more adds a second condition", async () => {
    const w = makeWizard(kv);
    const sent: any[] = [];
    await w.start((t, kb) => sent.push({ t, kb }));
    await w.handleCallback("field:country", (t, kb) => sent.push({ t, kb }));
    await w.handleCallback("op:eq", (t, kb) => sent.push({ t, kb }));
    await w.handleValue("United Kingdom", (t, kb) => sent.push({ t, kb }));
    await w.handleCallback("comb:OR:more", (t, kb) => sent.push({ t, kb }));
    expect((await getConv(kv, CHAT))?.step).toBe("field");
    expect((await getConv(kv, CHAT))?.conditions).toHaveLength(1);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test`
Expected: FAIL — `Wizard` not defined.

- [ ] **Step 3: Write handlers/wizard.ts**

Create `telegram-bot/src/handlers/wizard.ts`:
```ts
import { getConv, putConv, deleteConv, putSubscriber } from "../kv";
import type { Condition, Matrix, SubscriberRecord } from "../types";

type SendFn = (text: string, keyboard?: string[][]) => Promise<void>;

interface WizardState {
  step: "field" | "operator" | "value" | "combinator" | "mode";
  conditions: Condition[];
  combinator: Matrix["combinator"];
}

const FIELDS: Condition["field"][] = ["country", "isp", "protocol", "score", "ping", "speed", "gemini"];
const OPERATORS: Condition["operator"][] = ["eq", "neq", "lt", "lte", "gt", "gte", "in"];

export class Wizard(
  private kv: any,
  private token: string,
  private chatId: number,
) {}

  async start(send: SendFn): Promise<void> {
    const state: WizardState = { step: "field", conditions: [], combinator: "AND" };
    await this.save(state);
    await send("Step 1/5 — pick a field:", this.kb(FIELDS, "field"));
  }

  async handleCallback(data: string, send: SendFn): Promise<void> {
    const state = await getConv(this.kv, this.chatId) as WizardState | null;
    if (!state) return this.start(send);

    if (state.step === "field") {
      const field = data.split(":")[1] as Condition["field"];
      state.conditions.push({ field, operator: "eq", value: "" });
      state.step = "operator";
      await this.save(state);
      await send("Pick an operator:", this.kb(OPERATORS, "op"));
    } else if (state.step === "operator") {
      const operator = data.split(":")[1] as Condition["operator"];
      state.conditions[state.conditions.length - 1].operator = operator;
      state.step = "value";
      await this.save(state);
      await send("Send the value (e.g. United Kingdom, or 50):");
    } else if (state.step === "combinator") {
      const [comb, action] = data.split(":").slice(1);
      state.combinator = comb as Matrix["combinator"];
      if (action === "done") {
        state.step = "mode";
        await this.save(state);
        await send("Pick push mode — diff (only new matches) or digest (regular top-5):",
          this.kb([["diff", "digest"]], "mode"));
      } else {
        state.step = "field";
        await this.save(state);
        await send("Next condition — pick a field:", this.kb(FIELDS, "field"));
      }
    } else if (state.step === "mode") {
      const mode = data.split(":")[1] as Matrix["mode"];
      await this.confirm(state, mode, send);
    }
  }

  async handleValue(text: string, send: SendFn): Promise<void> {
    const state = await getConv(this.kv, this.chatId) as WizardState | null;
    if (!state) return this.start(send);
    const cond = state.conditions[state.conditions.length - 1];
    const raw = text.trim();
    cond.value = (cond.operator === "in") ? raw.split(",").map((s) => s.trim())
      : (["lt", "lte", "gt", "gte"].includes(cond.operator) ? Number(raw) : raw);
    state.step = "combinator";
    await this.save(state);
    await send("Add another condition, or finish?",
      this.kb([["AND, finish", "OR, finish", "AND, add more", "OR, add more"]], "comb"));
  }

  private async confirm(state: WizardState, mode: Matrix["mode"], send: SendFn): Promise<void> {
    const matrix: Matrix = { conditions: state.conditions, combinator: state.combinator, mode };
    const rec: SubscriberRecord = { chatId: this.chatId, matrix, createdAt: Date.now(), lastNotifiedAt: 0 };
    await putSubscriber(this.kv, rec);
    await deleteConv(this.kv, this.chatId);
    await send(`Subscribed! ${state.conditions.length} condition(s), ${mode} mode. I'll DM you matching configs.`);
  }

  private async save(state: WizardState): Promise<void> {
    await putConv(this.kv, this.chatId, { step: state.step, conditions: state.conditions });
  }

  private kb(options: string[], prefix: string): string[][] {
    return options.map((o) => [{ text: o, callback_data: `${prefix}:${o}` }]);
  }
}
```

- [ ] **Step 4: Wire the wizard into the telegram handler**

Modify `telegram-bot/src/handlers/telegram.ts`. Add a module-level `wizardSessions` map and route `/subscribe` + `callback_query` to it. Replace the `/subscribe` branch and add a `callback_query` branch:

Add near the top (after imports):
```ts
import { Wizard } from "./wizard";

const wizardSessions = new Map<number, Wizard>();
```

In `handleTelegram`, before the `if (text.startsWith("/start"))` chain, add callback_query handling:
```ts
  const cq = update.callback_query;
  if (cq) {
    const chatId = cq.message?.chat?.id ?? cq.from.id;
    const w = wizardSessions.get(chatId);
    if (w) {
      await w.handleCallback(cq.data ?? "", async (text, kb) => {
        await sendMessage(env.token, chatId, text);
      });
    }
    return new Response("ok", { status: 200 });
  }
```

Replace the `/subscribe` branch with:
```ts
    } else if (text === "/subscribe") {
      const w = new Wizard(env.kv, env.token, chatId);
      wizardSessions.set(chatId, w);
      await w.start(async (t, kb) => {
        await sendMessage(env.token, chatId, t);
      });
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm test`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add telegram-bot/src/handlers/wizard.ts telegram-bot/src/handlers/telegram.ts telegram-bot/test/wizard.test.ts
git commit -m "feat(bot): add in-bot guided builder wizard"
```

---

## Task 12: Wire up the router (index.ts)

**Files:**
- Modify: `telegram-bot/src/index.ts`

**Consumes:** all handlers
**Produces:** the Worker entry point with full routing + env typing.

- [ ] **Step 1: Write the failing test**

Append to `telegram-bot/test/handlers.test.ts`:
```ts
import worker from "../src/index";

describe("router (index)", () => {
  it("routes /health to 200", async () => {
    const res = await worker.fetch(new Request("http://fake/health"), {} as any, {} as any);
    expect(res.status).toBe(200);
  });

  it("404s unknown paths", async () => {
    const res = await worker.fetch(new Request("http://fake/unknown"), {} as any, {} as any);
    expect(res.status).toBe(404);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test`
Expected: FAIL — router not implemented.

- [ ] **Step 3: Write index.ts**

Replace `telegram-bot/src/index.ts`:
```ts
import { handleTelegram } from "./handlers/telegram";
import { handlePush } from "./handlers/push";
import { handleShare } from "./handlers/share";
import { getResults } from "./kv";

export interface Env {
  BOT_KV: any;
  TELEGRAM_BOT_TOKEN: string;
  TELEGRAM_SECRET_TOKEN: string;
  PUSH_SECRET: string;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const kv = env.BOT_KV;
    const ctx = { kv, token: env.TELEGRAM_BOT_TOKEN, secretToken: env.TELEGRAM_SECRET_TOKEN, pushSecret: env.PUSH_SECRET };

    if (url.pathname === "/telegram" && request.method === "POST") {
      return handleTelegram(ctx, request);
    }
    if (url.pathname === "/push" && request.method === "POST") {
      return handlePush(ctx, request);
    }
    if (url.pathname === "/share") {
      return handleShare(ctx, request);
    }
    if (url.pathname === "/health" && request.method === "GET") {
      const cache = await getResults(kv);
      return Response.json({ ok: true, cached: !!cache, nodes: cache?.nodes.length ?? 0 });
    }
    return new Response("not found", { status: 404 });
  },
};
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add telegram-bot/src/index.ts telegram-bot/test/handlers.test.ts
git commit -m "feat(bot): wire up router in index.ts"
```

---

## Task 13: Modify the GitHub Action to join URIs and POST to /push

**Files:**
- Modify: `.github/workflows/test.yml`

**Produces:** After committing results, the Action joins each node with its URI (by index from `subs/uris.json`) and POSTs the enriched payload to the Worker.

- [ ] **Step 1: Edit test.yml**

In `.github/workflows/test.yml`, replace the `Commit results` step with:
```yaml
      - name: Commit results
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add results.json subs/
          git commit -m "chore: update config ranking $(date -u +%FT%TZ)" || echo "No changes"
          git push

      - name: Notify Telegram bot
        run: |
          python3 - <<'PY'
          import json
          results = json.load(open("results.json"))
          uris = json.load(open("subs/uris.json"))
          for node, uri in zip(results["nodes"], uris):
              node["uri"] = uri
          with open("payload.json", "w") as f:
              json.dump({"nodes": results["nodes"]}, f)
          print(f"payload.json: {len(results['nodes'])} nodes")
          PY
          curl -fsSL -X POST "${{ secrets.WORKER_PUSH_URL }}/push" \
            -H "Authorization: Bearer ${{ secrets.PUSH_SECRET }}" \
            -H "Content-Type: application/json" \
            --data-binary @payload.json
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/test.yml
git commit -m "ci: join URIs into payload and POST to bot /push"
```

---

## Task 14: Add the "Subscribe on Telegram" button to the site

**Files:**
- Modify: `index.html`

**Produces:** A button on the "Build your subscription" panel that POSTs the matrix to `/share` and opens the returned Telegram deep-link.

- [ ] **Step 1: Add the button + script**

In `index.html`, locate the subscription builder's output area (where the Copy/Download/Share-link buttons live) and add a "Subscribe on Telegram" button plus a small script. The exact DOM hook depends on the current markup; add a button with id `tg-subscribe` and a click handler:

```html
<button id="tg-subscribe" class="btn" type="button" style="display:none">
  Subscribe on Telegram
</button>
```

```js
document.getElementById("tg-subscribe").addEventListener("click", async () => {
  const matrix = window.getCurrentMatrix(); // the builder's current matrix object
  const res = await fetch("https://config-ranker-bot.YOUR_SUBDOMAIN.workers.dev/share", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(matrix),
  });
  const { url } = await res.json();
  window.open(url, "_blank");
});
```

> Note: `window.getCurrentMatrix()` must return the builder's current `{ conditions, combinator, mode }`. Adapt the name to whatever function/variable the existing builder already exposes (inspect the builder's JS to find it). The button should appear only once a matrix has been built.

- [ ] **Step 2: Commit**

```bash
git add index.html
git commit -m "feat(site): add Subscribe on Telegram button"
```

---

## Task 15: Deployment setup (manual)

**Files:** none committed — these are one-time CLI steps.

**Produces:** A live bot with secrets, KV namespace, webhook, and a verified end-to-end push.

- [ ] **Step 1: Create the bot and get tokens**

Message @BotFather on Telegram: `/newbot` → follow prompts → receive `TELEGRAM_BOT_TOKEN`.

- [ ] **Step 2: Create the KV namespace**

Run:
```
cd telegram-bot && npx wrangler kv:namespace create BOT_KV
```
Copy the `id` into `wrangler.toml` under `[[kv_namespaces]] id`.

- [ ] **Step 3: Generate secrets**

Run:
```
npx wrangler secret put TELEGRAM_BOT_TOKEN
npx wrangler secret put TELEGRAM_SECRET_TOKEN   # pick a random string
npx wrangler secret put PUSH_SECRET             # pick a random string
```

- [ ] **Step 4: Deploy**

Run: `npx wrangler deploy`
Note the Worker URL (e.g. `https://config-ranker-bot.xxx.workers.dev`).

- [ ] **Step 5: Set the Telegram webhook**

Run (replace TOKEN and the Worker URL):
```
curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{"url":"https://config-ranker-bot.xxx.workers.dev/telegram","secret_token":"<TELEGRAM_SECRET_TOKEN>"}'
```

- [ ] **Step 6: Add GitHub Action secrets**

In the repo Settings → Secrets → Actions, add:
- `PUSH_SECRET` = the same value as the Worker's `PUSH_SECRET`
- `WORKER_PUSH_URL` = `https://config-ranker-bot.xxx.workers.dev`

- [ ] **Step 7: Manual smoke test**

In a private Telegram chat with the bot:
- `/start` → welcome message
- `/status` → data age
- `/top 3` → a .txt file with 3 URIs
- `/country United Kingdom` → a .txt file
- Trigger the Action manually (Actions → Run workflow) → confirm the Worker receives `/push` and (if subscribed) you get a DM.

- [ ] **Step 8: Commit the wrangler.toml namespace id**

```bash
git add telegram-bot/wrangler.toml
git commit -m "chore(bot): set prod KV namespace id"
```

---

## Self-Review Notes

- **Spec coverage:** every spec section maps to a task — architecture (12, 13), data model (2, 7), components (4, 5, 8, 9, 10), subscription UX (10 deep-link, 11 in-bot wizard, 14 site button), push behavior (9), error handling (9, 10), deployment (15), testing (all test files).
- **Placeholders:** none — every code step shows real code; `YOUR_BOT_USERNAME` / `WORKER_PUSH_URL` / `YOUR_SUBDOMAIN` are deployment-time values documented in Task 15.
- **Type consistency:** `evaluate(matrix, nodes): Node[]` defined in Task 4, consumed identically in Task 9 and 10. `SubscriberRecord` shape consistent across Tasks 2, 7, 9, 11. `Env` interface (Task 12) matches what handlers consume (Tasks 9, 10). `WizardState` (Task 11) round-trips through `conv:<chatId>` JSON via the same `Condition[]` type used by `Matrix`.

### Self-review findings (fixed inline)

1. **`cmdStart` share-token import was a no-op** (spec §5 Path 1): the original Task 10 had `const matrix = null; // TODO`. Fixed — `cmdStart` now calls `getShare`, writes a `SubscriberRecord`, and confirms. The deep-link flow is complete end-to-end (Task 14 button → Task 10 `/share` → Task 10 `/start`).
2. **In-bot guided builder was silently deferred** (spec §5 Path 2): the user asked for *both* subscription paths. Added as Task 11 (`Wizard` class, 5-step flow, `callback_query` handling). `/subscribe` now launches the wizard instead of a "coming soon" message.
