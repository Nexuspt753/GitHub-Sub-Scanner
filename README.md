# Config Ranking

A GitHub-only wrapper that fetches V2Ray/xray configs from public subscription
URLs, tests them, geolocates them, and publishes a public ranking — **no server,
no laptop, no cost** (public repo = free Actions minutes).

## How it works

```
sources.json ──▶ GitHub Actions (runner.py) ──▶ results.json ──▶ GitHub Pages (index.html)
    (feeds)        fetch → xray → test → score      (committed)       (public ranking)
```

The workflow (`.github/workflows/test.yml`) runs every 2 hours (and on manual
`workflow_dispatch`). Each run:

1. Downloads the config feeds listed in `sources.json` and parses
   `vmess:// vless:// trojan:// ss://` URIs (base64 or plain).
2. Downloads `xray-core` and starts a local HTTP proxy for each config.
3. Runs these tests (all measured **from GitHub's datacenter**, see caveats):
   - **Alive** — can it reach `gstatic.com/generate_204` through the proxy?
   - **Gemini** — can it reach Google's Gemini API through the proxy?
   - **Speed** — download throughput through the proxy (Cloudflare speed file).
   - **TCP ping** — direct connection time to the server.
   - **Location** — geolocates the server IP (country / region / city / ISP).
4. Scores each config and writes `results.json`, which the workflow commits back.
5. GitHub Pages serves `index.html`, which renders `results.json` as a sortable table.

### Subscription groups

Each run also regenerates a set of **base64-encoded v2ray subscriptions** under
`subs/`, one file per group, plus `subs/manifest.json`. Groups:

| Group | Path |
|---|---|
| All (alive) | `subs/all.txt` |
| Can reach Gemini | `subs/gemini.txt` |
| By protocol | `subs/protocol/{vmess,vless,trojan,ss}.txt` |
| By country (city in name) | `subs/country/{country}.txt` |
| By ISP / datacenter | `subs/isp/{isp}.txt` |
| Top by score | `subs/top.txt` |
| Low latency (<250 ms) | `subs/low-latency.txt` |
| Fast (>=10 Mbps) | `subs/fast.txt` |

The site lists every group with a copy button, so a user pastes the link into
their client. Only configs that tested **alive** are included.

Protocols xray-core can't run are still passed through: `anytls`/`ssr` get a
TCP-ping + location check (marked `tcp-only`), while UDP-based `hysteria2`/`tuic`
are included untested (no liveness check). All are excluded from the Gemini,
speed, and top groups.

### Custom subscription builder

The site also has a **Build your subscription** panel. Add any number of
conditions (field + operator + value) — e.g. *Gemini reachable = yes*,
*Score > 60*, *TCP ping < 200 ms*, *Country = United States* — and combine them
with AND or OR. The matching configs are turned into a base64 v2ray
subscription you can grab three ways:

- **Copy subscription (base64)** — paste into any client that imports a
  base64 subscription from the clipboard (v2rayN / v2rayNG / Hiddify /
  Streisand).
- **Download .txt** — save the payload as a file to import or self-host.
- **Copy share link** — a URL that re-opens the page with your exact matrix
  (bookmark or share it).

Because the site is fully static (no server), it can't return a different
subscription per visitor from GitHub Pages; the base64 payload and the
shareable matrix link are how you hand the same result to a client.

## Setup

1. Push this repo to GitHub (public repo).
2. `sources.json` is pre-wired to **Delta-Kronecker**, **barry-far**,
   **Epodonios**, and **mheidari98**. Add or remove sources as needed — each
   `url` must point to a **raw** file containing v2ray URIs (plain text or
   base64-encoded). Every config found is tested — there is no per-run cap.
3. Enable Pages: **Settings → Pages → Source: Deploy from a branch → `main` → `/ (root)`**.
4. Run the workflow once: **Actions → "Test & Rank Configs" → Run workflow**.
5. Your site is at `https://Nexuspt753.github.io/<repo>/`.

## Scoring

- Dead (not reachable through proxy) → score `0`.
- `score = min(speed_mbps, 100) + max(0, 30 - tcp_ping_ms/10) + (50 if Gemini reachable)`.

## Caveats — read this

- **Measurements are from GitHub's network, not from you or your users.**
  A config that is "fast" or "alive" from GitHub says nothing about how it will
  perform from any other country or ISP. Treat this as an uptime/health monitor,
  not a real-world speed test.
- **ICMP ping is unavailable** on GitHub runners, so "ping" is TCP connect time.
- **Geolocation** uses free APIs (`ip-api.com` batch with `ipwho.is` and
  `ipapi.co` fallback). Free IP databases lag behind reality for the
  rented/leased datacenter IPs that dominate these feeds, so results are
  corrected against the authoritative IP registration (RDAP) and the owner's
  self-published RFC 8805 geofeed where available. Cloudflare edge IPs are
  anycast (they have no single physical location, and from GitHub they resolve
  to a US/CA edge regardless of your own location), so they are labelled
  `Cloudflare (anycast)` rather than a country. A few IPs may still come back
  blank or imperfect — there is no perfectly accurate free source for churned
  hosting IPs.
- **GitHub ToS / abuse risk.** Actions is not intended for continuous network
  probing or for running proxy clients against arbitrary third-party servers.
  Aggressively scanning feeds of strangers' servers can get the repo/account
  flagged or terminated. Schedule conservatively and only test configs you have
  a right to test.
- **Legal.** V2Ray is widely used to bypass national firewalls, which is illegal
  in several jurisdictions. Hosting a public aggregator/tester puts you in that
  space — you assume the responsibility.

## Tuning

All overridable via environment variables (set them in the workflow, or under
repo Settings → Secrets → Actions):
- `WORKERS` (default `64`) — parallel test workers. Tests are network-bound
  (latency/timeouts), not CPU-bound, so this can go well above the 2-vCPU count.
- `SPEED_BYTES` (default `2000000`) — bytes downloaded for the speed test.
- `HTTP_TIMEOUT` (default `10`) — seconds per HTTP request.
Every config found is tested — there is no per-run cap. Geolocation is batched
via `ip-api.com` (100 IPs/request) with per-IP fallback, and DNS/geo results are
cached per host/IP.

> ⚠️ **Scale caveat.** Testing every config means tens of thousands of probes
> against third-party servers and gigabytes of speed-test data every run, which
> raises the risk of GitHub flagging the account for abuse and can exceed the
> workflow's time limit. If GitHub throttles you, lengthen the schedule
> interval, trim `sources.json`, lower `SPEED_BYTES`, or reduce `WORKERS`.

## Files

- `.github/workflows/test.yml` — scheduled/manual workflow + commit step.
- `scripts/runner.py` — fetch, parse, test, geolocate, score.
- `sources.json` — feed list and per-run cap.
- `index.html` — static ranking page (GitHub Pages).
- `results.json` — generated output, committed by the workflow.
