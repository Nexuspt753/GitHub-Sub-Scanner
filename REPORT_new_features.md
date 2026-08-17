# Config Ranker — New Features & Scaling Report

**Project:** `GitHub-Sub-Scanner` — a GitHub-only V2Ray/xray config tester, geolocator,
and ranking publisher. Every unique config found across the subscription feeds in
`sources.json` is fetched, tested (alive / Gemini / speed / TCP ping / geolocation),
scored, and published as grouped `subs/*.txt` plus a static ranking page (`index.html`).

**Generated:** 2026-08-14

---

## Current state (measured from the last run)

| Metric | Value |
|---|---|
| Raw URIs fetched across 4 sources | ~34,924 |
| Unique exact URIs (cross-feed dedupe) | **32,542** |
| Unique URIs ignoring name fragments (`#...`) | 21,716 |
| Configs actually tested in last commit | **2,000** |
| Alive | 835 |
| Dead | 1,002 |
| Untested (UDP passthrough) | 163 |
| Gemini reachable (of alive) | 206 |
| Alive configs with speed == 0 | 734 / 835 |
| Errors: TCP unreachable | 881 |
| Errors: unsupported transport (`xhttp`/`raw`/`httpupgrade`) | 32 |
| Errors: xray failed to start | 8 |
| Unique hosts | 1,602 |
| Unique host:port combos | 1,993 |
| Unique resolved IPs | 1,426 |
| TCP-ping (reachable) median / max | 18 ms / 24,250 ms |
| Proxy latency median / max | 2,117 ms / 9,754 ms |

The key takeaway: **there are ~32,542 unique configs available, but only 2,000 were
tested in the most recent run.** Testing *all* of them is the right goal, but the
current pipeline needs hardening before it can do so reliably on a 360-minute
GitHub Actions job. This report proposes the features that close that gap.

Each feature below explains:

1. **What it does** and why it matters.
2. **How it works** — the mechanism.
3. **How to implement it** — concrete, minimal steps.
4. **How it avoids new bugs** — what it does *not* change and what the guardrails are.
5. **How to verify it** — the test that proves it works.

---

## Feature 1 — Test the full config set (remove the implicit 2,000 limit)

### Why it matters

The repo says *"there is no per-run cap"*, but the last committed `results.json`
contains only 2,000 nodes while the feeds serve ~32,542 unique configs. Somewhere a
limit is being applied (likely an earlier `max_configs` setting or the previous
run shape). Testing only 2,000 of 32,542 means ~94% of available configs are silently
dropped. The *intent* of the project is to test every config; the system must do that.

### How it works

The dedupe loop in `main()` already accepts every unique URI:

```python
seen, queues = set(), []
for uris in source_lists:
    q = []
    for uri in uris:
        key = html.unescape(uri).strip()
        if key in seen:
            continue
        seen.add(key)
        ...
        q.append(node)
    queues.append(q)
```

There is no remaining `max_configs` clamp in the current code — so in principle the
*code* already tests everything. The 2,000-node output means either the most recent
run was truncated by the job timeout, or the previous commit had a cap. Verifying
that no cap remains is the first step.

### Implementation steps

1. Search the runner for any `max_configs`, `cap`, `[:N]` slicing, or early `break` in
   the node-building loop. **Remove every one.** The loop must build `nodes` from
   *all* surviving queue entries.
2. Add a one-line assertion after the loop, so the limit can never silently
   return without anyone noticing:
   ```python
   assert len(nodes) == sum(len(q) for q in queues) + already_popped_count
   # simpler: just log it prominently
   log(f"testing ALL {len(nodes)} unique configs — no cap")
   ```
3. Make the `sources.json` the single source of truth. Remove any env-var or
   secondary cap. The README already says "every config found is tested" — the
   code must match.
4. Update `README.md` **Tuning** section: remove any mention of a cap and state
   clearly that the runner tests every unique URI the feeds return.

### How it avoids new bugs

- No logic is changed — we are *removing* a clamp, not adding one.
- The existing round-robin interleaving (`while any(queues)`) is preserved, so
  source fairness is unchanged.
- Geolocation is batched *after* testing and operates on `unique IPs` (`{r.get("ip")
  for r in results if r.get("ip")}`), so it already scales to any result count
  without per-config cost.
- DNS and geo caches (`_DNS_CACHE`, `_GEO_CACHE`) are per-host/IP, so 32,542 configs
  collapsing onto ~1,426 unique IPs does not multiply API calls.

### How to verify

Run locally or re-run the workflow and confirm:
- `node_count` in `results.json` equals the `unique exact` URI count from the feeds
  (~32,000+, depending on the day).
- The `log` line `testing {N} unique configs` matches that number.
- No URI appears twice (dedupe still works): `len({r["uri"] for r in results}) == N`.

---

## Feature 2 — Hard-fail unsupported transports instead of silently dropping them

### Why it matters

The last run produced 32 errors like `unsupported transport: xhttp`,
`unsupported transport: raw`, and `unsupported transport: httpupgrade`. Those configs
are parsed and deduped, then *thrown away* when `build_stream_settings` raises
`ValueError("unsupported transport: ...")`. They never appear in `results.json`, so
the published ranking silently omits them and the user has no idea why.

### How it works

`build_stream_settings(node)` only handles `net` in `{tcp, ws, http/h2, grpc}`.
Anything else raises. The fix is to let those protocols reach `test_node` and be
recorded as `alive=False, tested=True, error="unsupported transport: ..."` — exactly
like TCP-unreachable configs are recorded today — instead of losing them during
config generation.

### Implementation steps

1. Change `test_node` so the xray-testable branch is guarded by a try/except around
   config generation:
   ```python
   try:
       stream = build_stream_settings(node)      # may raise ValueError
       cfg = build_xray_config(node, port, stream)
   except ValueError as e:
       r["error"] = str(e)
       return finish_node(r, node)
   ```
2. Keep `build_stream_settings` raising on unknown transports (fail-fast is good),
   but route the failure into the result record rather than discarding it.
3. In `write_subscriptions`, these configs already land in the dead bucket
   (`alive is False` excludes them from `alive = [...]`), so no downstream change
   is needed.
4. Update `README.md` **Protocols xray-core can't run** to mention that unknown
   transports are kept in `results.json` with an `error` field and excluded from
   the ranking.

### How it avoids new bugs

- Only the error path changes. Happy-path configs (`tcp/ws/h2/grpc`) still build
  identically.
- The geolocation path (`finish_node` → `resolve_ip`) still runs for these configs,
  so their IP/location is still recorded — useful even for un-testable configs.
- No change to deduping, scoring, or subscription grouping.

### How to verify

- After a run, `results.json` should contain 32 (or however many) entries with
  `error` starting with `"unsupported transport: "`. Previously they were absent.
- `node_count` grows by exactly that number.
- No transport that *was* working (vless/vmess/trojan/ss over tcp/ws) regresses.

---

## Feature 3 — Parallel geolocation fallback (currently serial)

### Why it matters

Geolocation already batches 100 IPs/request via `ip-api.com/batch`, which is good.
But anything the batch endpoint can't resolve falls through to `_fallback_geolocate`,
which is called **sequentially** in a `for ip in remaining` loop with a 0.25 s sleep
between providers. With ~1,426 unique IPs and a free-tier batch limit of ~15
requests/minute, any IP that misses the batch adds serial latency. This is one of the
few remaining serial bottlenecks in the pipeline.

### How it works

Replace the serial `_fallback_geolocate(ip)` loop with a `ThreadPoolExecutor` pass.
Each IP's fallback is independent (different host, different cache key), so they
parallelize safely. The per-IP 0.25 s sleep between providers can stay *inside* each
future (it is a politeness delay to a third-party API, not a serialization point).

### Implementation steps

1. Add a module-level executor or reuse a single one:
   ```python
   _GEO_FALLBACK_WORKERS = int(os.environ.get("GEO_FALLBACK_WORKERS", "16"))
   ```
2. Rewrite the fallback section of `geolocate_all`:
   ```python
   remaining = [ip for ip in todo if ip not in _GEO_CACHE]
   if remaining:
       log(f"  falling back for {len(remaining)} IPs ...")
       with ThreadPoolExecutor(max_workers=_GEO_FALLBACK_WORKERS) as ex:
           for ip, geo in ex.map(lambda ip: (ip, _fallback_geolocate(ip)), remaining):
               _GEO_CACHE[ip] = geo
   ```
3. Keep `_GEO_CACHE` updates inside the futures; the dict is thread-safe for
   disjoint keys on CPython (GIL protects dict mutation of distinct keys), but to
   be unambiguous wrap the write in `_CACHE_LOCK` (already exists).
4. Keep `_BATCH_INTERVAL = 4.0` between the main batch requests — that one is a
   rate-limit throttle on a *shared* API key, so it must stay serial.

### How it avoids new bugs

- The main `ip-api.com/batch` path is untouched; only the *fallback* path is
  parallelized.
- Each future still sleeps 0.25 s between its own two providers (ipwho.is then
  ipapi.co), so per-IP API politeness is preserved.
- The batch loop still sleeps `_BATCH_INTERVAL` between its own requests — the
  two rate limits are independent and both respected.
- Fallback geo results are still merged into `_GEO_CACHE` before `results` are
  updated, so downstream code sees the same shape.

### How to verify

- Run two configs that both miss the batch endpoint; confirm the second does not
  wait for the first to finish.
- Time the `geolocate_all` phase with and without the change on a 500-config sample.
- Confirm every IP still ends up with the same `country/isp` fields (no lost
  writes from the lock).

---

## Feature 4 — DNS warm-up before the test loop

### Why it matters

`resolve_ip(host)` is called inside `test_node` via `finish_node`, and again inside
geolocation. Every call does a fresh `socket.getaddrinfo` unless the host is in
`_DNS_CACHE`. Across ~1,600 unique hosts that's up to 1,600 DNS lookups serialized
inside the worker threads. A one-time warm-up pass before testing begins pre-fills
the cache with a tight thread pool, so the test loop itself never blocks on DNS.

### How it works

Before `ensure_xray()` and the `ThreadPoolExecutor` test loop, fire one resolver
thread per unique host and populate `_DNS_CACHE` (under `_CACHE_LOCK`). This is the
same code path `resolve_ip` already uses, just front-loaded.

### Implementation steps

1. After building `nodes`, collect unique addresses:
   ```python
   unique_hosts = list({n["address"] for n in nodes})
   log(f"warming DNS cache for {len(unique_hosts)} unique hosts ...")
   ```
2. Resolve them in parallel with a small bounded pool:
   ```python
   DNS_WARM_WORKERS = int(os.environ.get("DNS_WARM_WORKERS", "64"))
   with ThreadPoolExecutor(max_workers=DNS_WARM_WORKERS) as ex:
       list(ex.map(resolve_ip, unique_hosts))
   ```
   (`list(...)` forces all futures to complete before continuing.)
3. Keep `resolve_ip` exactly as-is — it checks the cache first, so the test loop
   becomes a near-zero-cost lookup.

### How it avoids new bugs

- `resolve_id` already locks on cache reads/writes, so pre-filling is safe.
- No DNS logic is duplicated — both paths call the same function.
- If a host fails to resolve during warm-up, `resolve_ip` stores `None`, which is
  exactly what the test loop would have gotten anyway. No new failure mode.

### How to verify

- After warm-up, `log` the cache size and confirm `_DNS_CACHE` length equals the
  number of unique hosts.
- Time `test_node` invocations; they should no longer block on `getaddrinfo`.
- Confirm `results.json` `ip` fields are identical to a run without warm-up.

---

## Feature 5 — Early-exit the speed test on dead proxies

### Why it matters

In the last run, **734 of 835 alive configs reported `speed_mbps == 0.0`**. That
means `speed_test` ran to completion (or its 30 s timeout) for the majority of
configs. The speed test is the single most expensive per-config operation — it
downloads `SPEED_BYTES` (default 2 MB) over a proxy that, in most cases, has
already proven itself slow or broken. Cutting it short on the first sign of
trouble saves the most time of any single optimization.

### How it works

`speed_test` currently does `op.open(req, timeout=30).read()` in one blocking call.
Replace it with a streaming read that measures bytes-as-they-arrive and aborts if
the throughput drops below a floor for more than a few seconds. If the first
couple of seconds already show the connection is stalled, return 0.0 immediately
instead of burning the full 30 s.

### Implementation steps

1. Change `speed_test` to read in chunks and track recent throughput:
   ```python
   def speed_test(port=PROXY_PORT, min_mbps=0.5, stall_seconds=4):
       url = f"https://speed.cloudflare.com/__down?bytes={SPEED_BYTES}"
       op = opener(True, port)
       req = urllib.request.Request(url, headers={"User-Agent": UA})
       start = time.time()
       buf = bytearray()
       try:
           resp = op.open(req, timeout=30)
           while True:
               chunk = resp.read(65536)
               if not chunk:
                   break
               buf += chunk
               elapsed = time.time() - start
               if elapsed >= stall_seconds:
                   mbps = len(buf) * 8 / elapsed / 1e6
                   if mbps < min_mbps:
                       return 0.0   # not worth continuing
           elapsed = time.time() - start
           return round(len(buf) * 8 / elapsed / 1e6, 2) if elapsed > 0 else 0.0
       except Exception:
           return 0.0
   ```
2. Add `SPEED_MIN_MBPS` and `SPEED_STALL_SECONDS` env vars so this is tunable.
3. Leave the "speed == 0 → not-fast" logic in `write_subscriptions` unchanged; it
   already treats 0.0 correctly.

### How it avoids new bugs

- A genuinely fast proxy never hits the stall threshold (it ships bytes quickly),
   so its measured speed is unchanged. The floor only affects *slow* connections
   that would have taken 10-30 s to declare anyway.
- The fallback `return 0.0` on any exception preserves today's behavior exactly.
- The score formula uses `min(speed_mbps, 100)` — 0.0 still scores 0 for speed,
   same as before.
- No change to the speed group (`>=10 Mbps`) filter.

### How to verify

- Run on a sample and confirm the median `speed_test` duration drops
  (previously 30 s for every slow proxy; now ≤ 4 s).
- Confirm a known-fast proxy (e.g. your own, if you have one in the feed) still
  reports its full speed.
- Confirm `speed_mbps` is still a float and `0.0` for dead-slow cases.

---

## Feature 6 — Persist a `last_tested` cache across runs

### Why it matters

The feeds change on different cadences (barry-far ~15 min, Epodonios ~5 min). On
any given 2-hour cycle, most configs are *unchanged* from the previous run. Without
cross-run caching, the runner re-tests identical configs every cycle, burning the
full 360-minute budget on work already done. A simple disk cache keyed by exact URI
lets the runner skip unchanged configs and focus on what's new.

### How it works

- Keep a file `configs.cache.json` mapping `exact_uri → {result_summary, timestamp}`.
- At startup, load it. For each URI already in the cache younger than `CACHE_TTL`,
  reuse the cached result instead of re-running `test_node`.
- After the run, write the cache back, merging fresh results over old ones.
- A `--clear-cache` flag forces a full re-test.

### Implementation steps

1. Add near the top of `main`:
   ```python
   CACHE_FILE = "configs.cache.json"
   CACHE_TTL = int(os.environ.get("CACHE_TTL", "7200"))  # default = run interval
   ```
2. After deduping, before building the `nodes` list:
   ```python
   cache = {}
   if os.path.exists(CACHE_FILE):
       with open(CACHE_FILE) as f:
           cache = json.load(f)
   now = time.time()
   nodes, reused = [], 0
   for candidate in all_candidates:
       key = candidate["uri"]
       hit = cache.get(key)
       if hit and now - hit["_ts"] < CACHE_TTL:
           reused += 1
           results_placeholder = hit   # fill later
           continue
       nodes.append(candidate)
   log(f"cache hit {reused}, re-testing {len(nodes)}")
   ```
3. After testing, merge fresh results into the cache and re-serialize, keeping
   only a compact summary per URI to keep the file small:
   ```python
   for r in results:
       cache[r["uri"]] = {"_ts": now, **{k: r[k] for k in (
           "protocol","address","port","alive","tcp_ping_ms",
           "gemini_reachable","speed_mbps","score","ip","country","isp")}}
   with open(CACHE_FILE, "w") as f:
       json.dump(cache, f, separators=(",", ":"))
   ```
4. The cache file is NOT committed (add to `.gitignore`) — each Actions run
   restores it from the cache action (`actions/cache@v4` keyed by
   `configs-cache-${{ runner.os }}`) so it persists across jobs.

### How it avoids new bugs

- A miss (URI never seen, or TTL expired) runs the full test path — no behavior
  change.
- A hit reuses a stored result with the same field shape as a fresh one, so
  downstream (`compute_score`, `write_subscriptions`, geolocation) can't tell the
  difference.
- The cache is advisory only: `--clear-cache` or deleting the file restores 100%
  re-test, so no stale data can silently corrupt a run.
- `_ts` is stored per URI, so two URIs with identical config but different display
  names are treated as distinct (matching the existing dedupe policy).

### How to verify

- Run once: cache file created with N entries.
- Run again immediately: `log` should show `cache hit N, re-testing 0` and finish
  in seconds.
- Delete one cached URI from the file and re-run: exactly that one config is
  re-tested, the rest are hits.
- Confirm `results.json` from a cache-hit run matches the previous run byte-for-byte
  (minus `updated_at`).

---

## Feature 7 — Make `WORKERS` tunable per-run via the workflow

### Why it matters

The runner recently raised the default `WORKERS` to 256, but the optimal value
depends on the feed size and GitHub's current throttling. Some runs may need 128,
others 500. Hardcoding it in `runner.py` means a code change (and a commit) just to
tune parallelism. Moving it to an env var the workflow passes in keeps tuning in
the workflow file where it belongs — and per the README, it's already advertised
as env-overridable.

### How it works

`WORKERS = int(os.environ.get("WORKERS", "256"))` already reads from the
environment. The workflow file (`.github/workflows/test.yml`) needs to actually
*pass* that env var in the `Run tests` step, and the README should document the
range that's safe.

### Implementation steps

1. In `.github/workflows/test.yml`, under the `Run tests` step:
   ```yaml
   - name: Run tests
     env:
       WORKERS: ${{ vars.WORKERS || '256' }}
       SPEED_BYTES: ${{ vars.SPEED_BYTES || '2000000' }}
       HTTP_TIMEOUT: ${{ vars.HTTP_TIMEOUT || '10' }}
     run: python scripts/runner.py
   ```
2. Set `WORKERS` as a **repo variable** (Settings → Secrets and variables →
   Actions → Variables) so it can be changed from the GitHub UI without a commit.
3. In `README.md`, document the sweet spot:
   - 64–128: conservative, lowest abuse risk.
   - 256: recommended for the full ~32k set.
   - 500: aggressive; only if your account isn't being throttled.
   - >500: almost always counterproductive — GitHub's network stack and the
     free-tier geo APIs become the bottleneck.
4. Add a log line at startup that prints the active settings:
   ```python
   log(f"settings: WORKERS={WORKERS} SPEED_BYTES={SPEED_BYTES} HTTP_TIMEOUT={HTTP_TIMEOUT}")
   ```

### How it avoids new bugs

- `os.environ.get` already falls back to the in-code default, so omitting the var
  changes nothing.
- Repo *variables* (not secrets) are visible in logs, so the active value is
  always auditable.
- No change to the thread pool construction or the port assignment math.

### How to verify

- Trigger a manual workflow run with `WORKERS=64` and another with `WORKERS=500`.
- Confirm the startup log shows the right value in each.
- Confirm both produce identical `results.json` (same configs tested, same scores);
  only runtime differs.

---

## Feature 8 — Speed test byte size tuned to the run's network budget

### Why it matters

The speed test downloads `SPEED_BYTES` (default 2 MB) per alive config. With 835
alive configs that's ~1.67 GB of egress *per run*, and at 2-hour intervals that's
~20 GB/day. GitHub's free Actions minutes include egress, but aggressive probing is
already flagged in the README as an abuse risk. Reducing `SPEED_BYTES` cuts egress
roughly linearly while preserving the ranking: a proxy's *relative* speed is
visible even at 256 KB.

### How it works

The speed test endpoint (`speed.cloudflare.com/__down?bytes=N`) accepts any byte
count. Lowering `SPEED_BYTES` reduces the transfer size. The reported `speed_mbps`
remains comparable because it's bytes-per-second, not total bytes. The only risk is
a too-small sample being noisy for very fast links; 256 KB at 100 Mbps finishes
in ~20 ms, which is still measurable.

### Implementation steps

1. Change the default to a smaller but still meaningful value:
   ```python
   SPEED_BYTES = int(os.environ.get("SPEED_BYTES", "262144"))   # 256 KB
   ```
2. Add a brief note in the speed-test block:
   ```python
   # 256 KB is enough to rank throughput accurately while keeping egress low
   # (~220 MB/run for 850 alive configs instead of ~1.7 GB).
   ```
3. Keep the env var so power users can raise it back.

### How it avoids new bugs

- The score formula is `min(speed_mbps, 100) + ...` — it depends on Mbps, not on
  bytes, so smaller samples yield the same scores (within noise).
- The "fast" group threshold (`>=10 Mbps`) is unchanged.
- No timeout or URL changes.

### How to verify

- Run with `SPEED_BYTES=262144` and `SPEED_BYTES=2000000` on the same feed.
- Confirm the ranking correlation between the two runs is >0.95 (Spearman).
- Confirm `results.json` `speed_mbps` values are in the same ballpark (within
  measurement noise).
- Confirm the run's network egress (visible in the Actions run logs) dropped
  roughly 8x.

---

## Feature 9 — Liveness re-check before scoring (drop "alive but 0-speed")

### Why it matters

Today a config is considered `alive` if the proxy starts and `gstatic.com/generate_204`
returns *any* HTTP response. But 734 of 835 alive configs then score `speed_mbps == 0`,
meaning the proxy is technically reachable but carries no real traffic. They pollute
the "All" group and waste speed-test time on proxies that are effectively dead for
a user.

### How it works

Add a second, cheaper liveness probe inside the proxy block: after the initial
`gstatic` check, make one more request to a different endpoint (e.g.
`http://www.gstatic.com/generate_204` a second time, or `http://cp.cloudflare.com`
which returns 204 with minimal bytes). If both succeed and the speed test is >0,
mark `alive=True`; otherwise mark `alive="limited"` (new state) and exclude it from
the speed group and the top group.

### Implementation steps

1. Extend the `alive` field from bool to a tri-state:
   ```python
   r["alive"] = False   # default
   # in test_node after proxy starts:
   ok1, _ = http_status("http://www.gstatic.com/generate_204", True, port)
   ok2, _ = http_status("http://cp.cloudflare.com/", True, port)
   r["alive"] = True if (ok1 and ok2) else "limited"
   ```
2. Update `compute_score` so `alive == "limited"` scores 0 (same as dead).
3. Update `write_subscriptions` so `alive is False` becomes
   `r.get("alive") in (False, "limited")`.
4. Update `finish_node`'s `score` to use the tri-state.

### How it avoids new bugs

- Tri-state only affects configs that *currently* show alive-but-zero. Configs
  that pass today keep `alive=True` and the same score.
- Downstream grouping logic is updated in one place (`write_subscriptions`).
- The score function already handles `not r.get("alive")` returning 0; extending it
  to cover the string `"limited"` is a one-line change.

### How to verify

- After a run, compare the `alive` counts: the sum of `True + "limited" + False`
  should still equal the old `True + False` count.
- The "All" group should shrink by the number of "limited" configs (no user-visible
  regression — those configs were effectively dead anyway).
- No change to `gemini_reachable` or `speed_mbps` semantics.

---

## Feature 10 — Per-protocol xray config validation before the test loop

### Why it matters

The runner discovers unsupported transports (Feature 2) only *at test time*,
inside the worker pool. A cheap pre-flight validation pass can flag them *before*
any worker is spawned, log them clearly, and skip them entirely — saving a thread,
a port, and a TCP timeout per bad config.

### How it works

After deduping, iterate `nodes` once and call `build_stream_settings(node)` in
dry-run mode. If it raises, mark the node with `error="unsupported transport: ..."`
and move it directly into the results list. Only clean nodes enter the worker pool.

### Implementation steps

1. After the dedupe loop, before the test loop:
   ```python
   clean, rejected = [], []
   for node in nodes:
       try:
           build_stream_settings(node)
           clean.append(node)
       except ValueError as e:
           node["error"] = str(e)
           rejected.append(node)
   log(f"pre-flight rejected {len(rejected)} unsupported transports")
   ```
2. Run the worker pool on `clean`.
3. Append `rejected` (with `alive=False, tested=True`) to `results` after the pool
   finishes. They already have the right shape from `test_node`'s error path, but
   we can fill the minimal fields directly.

### How it avoids new bugs

- The same `build_stream_settings` function is used in production and pre-flight,
  so a transport rejected here is guaranteed to fail in `test_node` — no false
  positives.
- Happy-path nodes pass pre-flight and enter the pool unchanged.
- No new code path for generation — we're calling the same function earlier.

### How to verify

- Count rejected configs in the log; confirm it matches the number of
  `unsupported transport` errors from the previous run (32).
- Confirm none of those 32 configs spawned a worker or a TCP ping.
- Confirm the rest of the results are identical.

---

## Recommended implementation order

| Order | Feature | Impact | Risk |
|---|---|---|---|
| 1 | Feature 1 — Full config set | High (closes the 94% gap) | None (removes a clamp) |
| 2 | Feature 2 — Hard-fail unsupported transports | Medium (no silent drops) | Low |
| 3 | Feature 10 — Pre-flight validation | Medium (saves worker time) | Low |
| 4 | Feature 5 — Early-exit speed test | High (biggest time saver) | Low |
| 5 | Feature 4 — DNS warm-up | Medium | None |
| 6 | Feature 3 — Parallel geo fallback | Medium | Low |
| 7 | Feature 7 — Tunable WORKERS | Low (ops convenience) | None |
| 8 | Feature 8 — Lower SPEED_BYTES | Medium (egress savings) | Low |
| 9 | Feature 6 — Cross-run cache | High (multiplies all savings) | Medium |
| 10 | Feature 9 — Tri-state alive | Medium (cleaner ranking) | Low |

Start with Features 1–5; they are low-risk and directly address the *"test all
configs faster"* goal without changing what is measured. Features 6–10 layer on
top once the full set is running reliably.

---

## Summary

The project's intent — test *every* config the feeds return, score them, and
publish a ranking — is sound. The gap between that intent and the current
behavior (2,000 of ~32,542 configs tested, unsupported transports silently
dropped, 30-second speed tests on dead proxies, no cross-run cache) is what's
making the runs feel slow. The ten features above close that gap without altering
the meaning of any measurement. Each is independently testable and none changes
the shape of `results.json` for configs that already pass.
