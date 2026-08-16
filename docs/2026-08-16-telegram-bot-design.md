# Telegram Bot for Config Ranking — Design Spec

**Date:** 2026-08-16
**Status:** Approved for implementation plan
**Source project:** GitHub-Sub-Scanner (static GitHub Pages site + GitHub Action that
fetches, tests, geolocates, and scores V2Ray/xray proxy configs, publishing `results.json`).

## 1. Overview & Goals

Add a **Telegram Bot** to the Config Ranking project so users can:

1. **Query on demand** — send a command, get matching config URIs back.
2. **Subscribe to personalized push** — each user defines a *filter matrix* (e.g.
   `score > 50 AND country = UK AND gemini = yes`) and gets a DM after every
   ranking run with fresh configs matching **their** criteria.

### Constraints

- **Free.** Entirely on free tiers: Cloudflare Workers (100k req/day), Cloudflare KV
  (100k reads/day, 1k writes/day), GitHub Actions, GitHub Pages.
- **No new server.** The bot logic lives in a single Cloudflare Worker.
- **Minimal change to existing project.** The GitHub Action gains one webhook step;
  the site gains one button. `runner.py`, scoring, and Pages hosting are untouched.

### Out of scope (for now)

Inline queries, admin broadcast, multi-language support, analytics/dashboard. These
layer on later without rework.

## 2. Architecture

Three boxes. The GitHub Action is the **producer**; the Cloudflare Worker is the
**bot** (single owner of all bot logic); **Telegram** is the user-facing channel.

```
 ┌────────────────┐  ① POST /push (results.json body, Bearer token)  ┌────────────────┐
 │ GitHub Action  │ ───────────────────────────────────────────────▶ │   Cloudflare   │
 │ (producer)     │                                                 │   Worker       │
 │ runs every 2h  │                                                 │   (the bot)    │
 └────────────────┘                                                 │                │
                                                                    │  ┌──────────┐  │
                                                                    │  │ Telegram │  │
                                                                    │  │ webhook  │  │──────────┐
                                                                    │  │ handler  │  │          │
                                                                    │  └────┬─────┘  │          │
                                                                    │       │        │          │
                                                                    │       ▼        │          │
                                                                    │  ┌──────────┐  │          │
                                                                    │  │  query / │  │          │
                                                                    │  │  push    │──┼── ③ ──▶ Telegram API
                                                                    │  │  engine  │  │  DM      │  (sendDocument)
                                                                    │  └────┬─────┘  │          │
                                                                    │       │        │          │
                                                                    │       ▼        │          │
                                                                    │  ┌──────────┐  │          │
                                                                    │  │  CF KV   │  │          │
                                                                    │  └──────────┘  │          │
                                                                    └───────▲────────┘          │
                                                            ┌───────────────┴──────────────────┘
                                                            │ ② secret_token on /telegram
                                                    ┌───────┴───────┐
                                                    │   Telegram     │
                                                    │  (user msgs)   │
                                                    └────────────────┘
```

### The two flows

- **Push (personalized):** ① Action commits `results.json` → fires authenticated
  `POST /push` with the JSON **in the body** (no fetch to Pages — avoids CDN
  propagation delay and guarantees zero stale reads) → Worker stores it in KV as
  `results:cache` → reads every subscriber's matrix from KV → evaluates each → ③ DMs
  matching configs via Telegram API. Timing is decoupled: push fires the instant
  results land, not on a separate schedule.

- **Query (on-demand):** ② user sends `/top 10` → Telegram POSTs to `/telegram` →
  Worker reads `results:cache` from KV → evaluates → sends the matches as a **.txt
  subscription file** via `sendDocument`, with a short caption (e.g. "12 configs ·
  score>50 · UK"). The file is a plain-text v2ray subscription (one URI per line) —
  directly paste-able into v2rayN / v2rayNG / Hiddify / Streisand. This costs **1
  subrequest** per query (multipart upload), which is negligible for interactive use.

### Why push is webhook-triggered, not cron

A cron would drift relative to the Action and could fire mid-run. Triggering push
from the Action (Approach C) means push runs exactly when fresh data exists. The
cost is one extra authenticated webhook step in the Action.

## 3. Data Model (Cloudflare KV)

| Key | Value | TTL |
|---|---|---|
| `results:cache` | `{ fetchedAt: number, data: ResultsJson }` — latest ranking, repopulated every `/push` | none (overwritten) |
| `sub:<chatId>` | `{ chatId, matrix, createdAt, lastNotifiedAt, lastNotifiedIds? }` (lastNotifiedIds capped to most recent 100 — see §6) | none |
| `conv:<chatId>` | `{ step, partialMatrix }` — transient in-bot wizard state | `expirationTtl: 600` (10 min, self-cleanup) |
| `share:<token>` | `{ matrix }` — deep-link token from the site | `expirationTtl: 604800` (7 days) |

### Matrix shape

A matrix is a flat list of conditions plus a combinator — a direct serialization of
the site's existing "Build your subscription" builder:

```ts
interface Condition {
  field: "country" | "isp" | "protocol" | "score" | "ping" | "speed" | "gemini";
  operator: "eq" | "neq" | "lt" | "lte" | "gt" | "gte" | "in";
  value: string | number | string[];
}

interface Matrix {
  conditions: Condition[];
  combinator: "AND" | "OR";
  mode: "diff" | "digest"; // push behavior
}
```

The query/push engine is a pure function `evaluate(matrix, results): Match[]`, where
`Match` is one entry from the existing `results.json` shape (a scored, geolocated
config with at least `{ id, uri, score, country, protocol, ping, speed, gemini }` —
see the committed `results.json` for the exact schema). Both the site deep-link and
the in-bot builder produce the same matrix shape, so they share one evaluation path.

## 4. Worker Components

| Component | Role |
|---|---|
| **Router** | Dispatches by path: `/telegram`, `/push`, `/share`, `/health`. Rejects all else. |
| **Telegram webhook handler** | Validates `X-Telegram-Bot-Api-Secret-Token`; parses update (command or inline callback); dispatches to query engine or subscription manager. Sends results as .txt files via `sendDocument` (1 subrequest/query). |
| **Query / push engine** | Pure, stateless: `evaluate(matrix, results) → matches[]`. Used by both query (reply) and push (DM). Highest-value unit to test. |
| **Subscription manager** | CRUD on `sub:<chatId>`: store, list, update matrix, delete. Holds `conv:<chatId>` wizard state. Powers `/subscribe`, `/unsubscribe`, `/myfilters`, `/start`. |

### Public routes

| Route | Source | Auth |
|---|---|---|
| `POST /telegram` | Telegram | `secret_token` header |
| `POST /push` | GitHub Action | `Authorization: Bearer <PUSH_SECRET>` |
| `POST /share` | browser (site) | CORS (caller is `*.github.io`) |
| `GET /health` | anyone | none — returns `results:cache` freshness |

## 5. Subscription UX

### Path 1 — Deep-link from the site (zero new UI)

The site's "Build your subscription" panel gets a **"Subscribe on Telegram"**
button. On click, the site makes a client-side `POST /share` (with CORS) carrying
the full matrix JSON. The Worker generates a short random token, writes
`share:<token>` to KV (7-day TTL), and returns:

```json
{ "token": "a3f9k2", "url": "https://t.me/<bot>?start=share_a3f9k2" }
```

The site opens that URL. The `/start=share_<token>` handler reads the token → looks
up the matrix → writes `sub:<chatId>` → confirms.

**Why a token, not an inline matrix:** Telegram's `/start <payload>` parameter is
capped at **64 bytes** (charset `A-Za-z0-9_-`). A serialized matrix easily exceeds
that. A token is always tiny regardless of complexity, avoids a fragile mini-DSL
grammar and escaping bugs, and naturally doubles as a shareable preset link
(`t.me/<bot>?start=share_<token>` works for anyone).

### Path 2 — In-bot guided builder

`/subscribe` starts a wizard driven by inline keyboards (no free-text parsing):

1. Pick a **field** (country / protocol / score / ping / speed / gemini).
2. Pick an **operator** (=, ≠, <, ≤, >, ≥, in).
3. Enter a **value** (text/number, or multi-select for `in`).
4. **Add another?** AND / OR combinator; repeat from step 1, or finish.
5. **Review** the rendered matrix, pick push mode (diff / digest), confirm.

Multi-step state lives in `conv:<chatId>` with `expirationTtl: 600` — abandoned
wizards self-cleanup, no garbage-collection code. On confirm, state moves to
`sub:<chatId>` and the transient record is deleted.

Both paths produce the identical `sub:<chatId>` record.

### Other commands

| Command | Action |
|---|---|
| `/start` | welcome + help; if `start=share_<token>`, import that matrix |
| `/subscribe` | launch in-bot wizard |
| `/unsubscribe` | delete `sub:<chatId>`, confirm |
| `/myfilters` | echo active matrix + push mode |
| `/top N` | query: top N by score |
| `/country <name>` | query: configs in country |
| `/status` | how fresh `results:cache` is, subscriber count |
| `/help` | command list |

## 6. Push Behavior

After `/push` stores `results:cache`, the push engine enumerates subscribers via
`KV.list({ prefix: "sub:" })` (one page holds up to 1000 keys; paginate with the
cursor past that). For each subscriber, it runs `evaluate(matrix, results)` and
behaves per the subscriber's `mode`:

Both modes deliver configs as a **.txt subscription file** (`sendDocument`) with a
caption summarizing the match. This sidesteps Telegram's 4,096-character `sendMessage`
limit (10+ formatted proxy URIs with scores/flags would overflow it), keeps each push
to one clean message instead of a flood, and produces a file the user's client can
import directly. The .txt payload is the same one-URI-per-line subscription the site
already generates under `subs/`.

- **Diff mode (default).** DM only matches whose IDs are **not** in the subscriber's
  `lastNotifiedIds`. Silent when nothing is new. After a successful send, updates
  `lastNotifiedIds` (capped to the most recent 100 entries — see §7) and
  `lastNotifiedAt`. Prevents alert fatigue from configs that stay alive across many
  runs.
- **Digest mode.** Every push, send the current top matches (e.g. best-scoring 5)
  with a "2h digest" caption, regardless of prior alerts. Predictable cadence.

### Bounding `lastNotifiedIds` growth

In diff mode, appending IDs every 2 hours would grow `lastNotifiedIds` indefinitely
over months. Cap it to the most recent 100 entries after each push:

```ts
const updatedIds = Array.from(
  new Set([...currentMatchIds, ...(subscriber.lastNotifiedIds || [])])
).slice(0, 100);
```

A 100-entry cap is far larger than any single run's matches, so it never suppresses
a legitimately new alert; it only bounds KV value size.

### Outbound DM pacing

Telegram enforces ~30 msg/sec. Firing 45 concurrent `fetch()` uploads in one
`Promise.allSettled` tick can trigger `429 Too Many Requests`. Pace the broadcast in
small batches (e.g. 15 DMs per batch with a ~500ms pause between batches), or iterate
sequentially with `for…of` (45 sequential uploads take ~2–3s total, well within the
Worker's CPU timeout).

### Free-tier guardrail: subrequest cap

The Workers free tier allows **50 outbound subrequests per invocation**. Each DM is
one subrequest, so a push with N subscribers = N subrequests.

- Cap sends at **≤ 45 per invocation**, chunked with `Promise.allSettled`.
- v1 caps the subscriber list at 45. If `KV.list` returns more than 45 `sub:*` keys,
  the push processes the first 45 and logs a warning that the rest are deferred —
  raising the cap requires the Cloudflare Queue growth path (out of scope for v1, but
  the code leaves a seam for it).

### Auto-pruning blocked users

A DM to a user who blocked the bot returns `403 Forbidden`. In the `allSettled`
handler, a 403 triggers `KV.delete('sub:' + chatId)` — the subscriber list stays
clean automatically and stops wasting subrequests on dead chats.

## 7. Error Handling

- **`/telegram`** — always return HTTP 200 fast (Telegram retries on non-200). Validate
  `secret_token` first; reject spoofed updates with 403. Parse errors and unknown
  commands get a friendly reply ("I don't know that — try /help"), never a crash.
- **`/push`** — validate `Authorization: Bearer`; 401 on mismatch. Invalid JSON or
  missing fields → 400. Per-subscriber DM failures are caught by `allSettled`; one
  blocked bot doesn't kill the broadcast.
- **`/share`** — 400 on invalid matrix; CORS preflight handled. On token collision
  (astronomically unlikely), regenerate.
- **KV staleness guard** — if `results:cache` is missing (first deploy / TTL edge),
  queries reply "no data yet — the first ranking run hasn't completed" instead of
  crashing.

### HTML entity escaping

Config names, remarks, and protocol strings from `results.json` frequently contain
`<`, `>`, `&`. With `parse_mode: "HTML"`, unescaped characters cause Telegram to
reject the message with `400 Bad Request: can't parse entities`. All dynamic text is
routed through:

```ts
function escapeHtml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}
```

## 8. Deployment & Secrets

| Secret | Where it lives | Used by |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Worker secret (`wrangler secret put`) | outbound DMs |
| `TELEGRAM_SECRET_TOKEN` | Worker secret; also passed to `setWebhook` | validates `/telegram` |
| `PUSH_SECRET` | Worker secret **and** GitHub Action secret | authenticates `/push` |

One-time setup:

1. Create the bot via **@BotFather** (free) → get `TELEGRAM_BOT_TOKEN`.
2. Create KV namespace: `wrangler kv:namespace create BOT_KV` → bind as `BOT_KV`.
3. Put the three secrets via `wrangler secret put`.
4. Deploy: `wrangler deploy`.
5. Set the Telegram webhook with `secret_token`:
   `POST https://api.telegram.org/bot<TOKEN>/setWebhook` with the Worker's
   `/telegram` URL and `secret_token`.
6. Add one step to the GitHub Action (at the end of the workflow, after committing
   `results.json`):
   ```bash
   curl -fsSL -X POST https://<worker>/push \
     -H "Authorization: Bearer ${{ secrets.PUSH_SECRET }}" \
     -H "Content-Type: application/json" \
     --data-binary @results.json
   ```
   `-f` makes the Action step fail with a non-zero exit code if the Worker returns
   HTTP 4xx/5xx, surfacing auth or parse failures immediately in the Actions UI.

All of this is free. GitHub Pages remains the public archive and subscription-file
host; it is no longer on the runtime data path (the Action sends data directly to
the Worker, eliminating Pages CDN propagation delay as a failure mode).

## 9. Testing

| Layer | What | Tool |
|---|---|---|
| Pure logic | `evaluate(matrix, results)` correctness across fixtures | `vitest` (ms, no runtime mock needed) |
| Push logic | diff mode suppresses already-notified; digest mode doesn't | `vitest` |
| Handlers + KV | routing, auth rejection, wizard state transitions | `@cloudflare/vitest-pool-workers` (real KV bindings in Node) |
| Manual smoke | drive the real bot in a private Telegram chat: wizard, `/myfilters`, `/top 5`, wait for a push | Telegram client |

`results.json` fixtures for tests can be sampled directly from a real run committed
to the repo.

## 10. Free-Tier Budget

| Resource | Free limit | This design's usage |
|---|---|---|
| Worker requests | 100k/day | ~12 push invocations + user queries — negligible |
| Worker subrequests | 50/invocation | 1 per query (file upload) + ≤ 45 per push (paced) |
| KV reads | 100k/day | one per query + one per push subscriber |
| KV writes | 1/kday | one per `/push` + one per new subscriber + one per `/share` — far under limit |
| Action minutes | free for public repos | one extra `curl` step — seconds |
