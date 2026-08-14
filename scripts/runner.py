#!/usr/bin/env python3
"""
Fetch, test, geolocate, and rank V2Ray/xray configs.

Runs inside GitHub Actions (no server, no local machine). For each config it:
  1. Starts a local xray-core instance with an HTTP proxy on 127.0.0.1:10808.
  2. Tests "alive"  -> can it reach a known-good endpoint through the proxy.
  3. Tests Gemini  -> can it reach Google's Gemini API through the proxy.
  4. Measures speed -> download throughput through the proxy.
  5. TCP-pings the server directly, and geolocates the server IP.
  6. Writes results.json (committed by the workflow, rendered by index.html).

Everything is measured from GitHub's network, NOT from any end user's location.
"""

import base64
import html
import json
import os
import re
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

XRAY_URL = "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip"
XRAY_API = "https://api.github.com/repos/XTLS/Xray-core/releases/latest"
XRAY_BIN = "bin/xray"
PROXY_PORT = 10808
PROXY_URL = f"http://127.0.0.1:{PROXY_PORT}"

# Defaults, overridable via environment variables.
SPEED_BYTES = int(os.environ.get("SPEED_BYTES", "10000000"))  # 10 MB
HTTP_TIMEOUT = 15
UA = "Mozilla/5.0 (GitHub Actions; config-ranker)"

# Protocols xray-core cannot run; we only TCP-ping + geolocate these.
TCP_ONLY_PROTOCOLS = {"hysteria2", "anytls", "tuic", "ssr"}
URI_RE = re.compile(
    r"(?:vmess|vless|trojan|ssr|ss|shadowsocks|hy2|hysteria2|anytls|tuic)://[^\s\"'<>]+"
)


def log(msg):
    print(msg, flush=True)


# --------------------------------------------------------------------------- #
# Sources & URI parsing
# --------------------------------------------------------------------------- #

def b64decode_flex(s):
    """base64-decode, tolerating missing padding / embedded whitespace."""
    s = "".join(s.split())
    for pad in ("", "=", "=="):
        try:
            return base64.b64decode(s + pad, validate=True).decode("utf-8", "ignore")
        except Exception:
            continue
    return s


def fetch_text(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "ignore")


def extract_uris(text):
    """Pull v2ray-style URIs out of a (possibly base64) subscription blob."""
    text = text.strip()
    uris = URI_RE.findall(text)
    if uris:
        return uris
    decoded = b64decode_flex(text)
    return URI_RE.findall(decoded)


def fetch_source(url):
    log(f"  fetching source: {url}")
    try:
        return extract_uris(fetch_text(url))
    except Exception as e:
        log(f"  !! source failed: {e}")
        return []


def parse_uri(uri):
    uri = html.unescape(uri)
    if uri.startswith("vmess://"):
        return parse_vmess(uri)
    if uri.startswith("vless://"):
        return parse_vless(uri)
    if uri.startswith("trojan://"):
        return parse_trojan(uri)
    if uri.startswith("ss://") or uri.startswith("shadowsocks://"):
        return parse_ss(uri)
    if uri.startswith("ssr://"):
        return parse_ssr(uri)
    if uri.startswith("hy2://") or uri.startswith("hysteria2://"):
        return parse_generic(uri, "hysteria2")
    if uri.startswith("anytls://"):
        return parse_generic(uri, "anytls")
    if uri.startswith("tuic://"):
        return parse_generic(uri, "tuic")
    raise ValueError(f"unknown scheme in {uri[:20]!r}")


def parse_vmess(uri):
    data = json.loads(b64decode_flex(uri[len("vmess://"):]))
    tls = str(data.get("tls", "")).lower()
    return {
        "protocol": "vmess",
        "name": data.get("ps", "vmess"),
        "address": data.get("add") or data.get("host"),
        "port": int(data.get("port", 443)),
        "uuid": data.get("id"),
        "aid": int(data.get("aid", 0) or 0),
        "scy": data.get("scy", "auto"),
        "network": data.get("net", "tcp"),
        "security": "tls" if tls in ("tls", "1", "true") else "none",
        "path": data.get("path"),
        "host_header": data.get("host"),
        "sni": data.get("sni"),
        "alpn": data.get("alpn"),
        "fp": data.get("fp"),
    }


def parse_vless(uri):
    rest = uri[len("vless://"):]
    main, _, frag = rest.partition("#")
    name = urllib.parse.unquote(frag) if frag else "vless"
    userinfo, _, hostport = main.partition("@")
    address, _, port = hostport.partition(":")
    port = int(port.split("?")[0]) if port else 443
    query = {}
    if "?" in hostport:
        query = dict(urllib.parse.parse_qsl(hostport.split("?", 1)[1]))
    return {
        "protocol": "vless",
        "name": name,
        "address": address,
        "port": port,
        "uuid": userinfo,
        "network": query.get("type", "tcp"),
        "security": query.get("security", "none"),
        "path": query.get("path"),
        "host_header": query.get("host"),
        "sni": query.get("sni"),
        "alpn": query.get("alpn"),
        "fp": query.get("fp") or query.get("spx"),
        "encryption": query.get("encryption", "none"),
        "flow": query.get("flow"),
        "pbk": query.get("pbk"),
        "sid": query.get("sid"),
    }


def parse_trojan(uri):
    rest = uri[len("trojan://"):]
    main, _, frag = rest.partition("#")
    name = urllib.parse.unquote(frag) if frag else "trojan"
    userinfo, _, hostport = main.partition("@")
    password = urllib.parse.unquote(userinfo)
    address, _, port = hostport.partition(":")
    port = int(port.split("?")[0]) if port else 443
    query = {}
    if "?" in hostport:
        query = dict(urllib.parse.parse_qsl(hostport.split("?", 1)[1]))
    return {
        "protocol": "trojan",
        "name": name,
        "address": address,
        "port": port,
        "password": password,
        "network": query.get("type", "tcp"),
        "security": query.get("security", "none"),
        "path": query.get("path"),
        "host_header": query.get("host"),
        "sni": query.get("sni"),
        "alpn": query.get("alpn"),
        "fp": query.get("fp"),
    }


def parse_ss(uri):
    rest = uri.split("://", 1)[1]
    main, _, frag = rest.partition("#")
    name = urllib.parse.unquote(frag) if frag else "ss"
    if "@" in main:
        userinfo, _, hostport = main.partition("@")
    else:
        decoded = b64decode_flex(main)
        userinfo, _, hostport = decoded.partition("@")
    if ":" not in userinfo:
        userinfo = b64decode_flex(userinfo)
    method, _, password = userinfo.partition(":")
    address, _, port = hostport.partition(":")
    return {
        "protocol": "shadowsocks",
        "name": name,
        "address": address,
        "port": int(port) if port else 443,
        "method": method,
        "password": password,
        "network": "tcp",
        "security": "none",
    }


def parse_generic(uri, proto):
    """Minimal parser for protocols xray can't run (hy2/anytls/tuic)."""
    rest = uri.split("://", 1)[1]
    main, _, frag = rest.partition("#")
    name = urllib.parse.unquote(frag) if frag else proto
    userinfo, _, hostport = main.partition("@")
    if not hostport:
        hostport = userinfo
    hostport = hostport.split("?", 1)[0].split("/", 1)[0]
    address, _, port = hostport.partition(":")
    m = re.match(r"\d+", port) if port else None
    port = int(m.group(0)) if m else 443
    return {"protocol": proto, "name": name, "address": address, "port": port}


def parse_ssr(uri):
    """Minimal SSR parser: decode base64 to get host:port and remarks."""
    body = uri[len("ssr://"):].split("#", 1)[0]
    decoded = b64decode_flex(body)
    base, _, params = decoded.partition("/?")
    parts = base.split(":")
    if len(parts) < 6:
        raise ValueError("malformed ssr uri")
    name = "ssr"
    if params:
        q = dict(urllib.parse.parse_qsl(params))
        if q.get("remarks"):
            name = b64decode_flex(q["remarks"]) or name
    frag = uri.split("#", 1)
    if len(frag) > 1 and frag[1]:
        name = urllib.parse.unquote(frag[1]) or name
    return {"protocol": "ssr", "name": name, "address": parts[0],
            "port": int(parts[1])}


# --------------------------------------------------------------------------- #
# xray client config generation
# --------------------------------------------------------------------------- #

def build_stream_settings(node):
    net = (node.get("network") or "tcp").lower()
    security = (node.get("security") or "none").lower()
    ss = {"network": net, "security": security}

    if net == "ws":
        ws = {"path": node.get("path") or "/"}
        if node.get("host_header"):
            ws["headers"] = {"Host": node["host_header"]}
        ss["wsSettings"] = ws
    elif net in ("http", "h2"):
        http = {}
        if node.get("host_header"):
            http["host"] = [node["host_header"]]
        if node.get("path"):
            http["path"] = node["path"]
        ss["httpSettings"] = http
    elif net == "grpc":
        ss["grpcSettings"] = {"serviceName": node.get("path") or ""}
    elif net != "tcp":
        raise ValueError(f"unsupported transport: {net}")

    if security == "tls":
        tls = {"allowInsecure": False}
        if node.get("sni"):
            tls["serverName"] = node["sni"]
        if node.get("alpn"):
            tls["alpn"] = node["alpn"].split(",")
        ss["tlsSettings"] = tls
    elif security == "reality":
        tls = {"allowInsecure": False}
        if node.get("sni"):
            tls["serverName"] = node["sni"]
        if node.get("fp"):
            tls["fingerprint"] = node["fp"]
        ss["tlsSettings"] = tls
        reality = {"show": False}
        if node.get("pbk"):
            reality["publicKey"] = node["pbk"]
        if node.get("sid"):
            reality["shortId"] = node["sid"]
        ss["realitySettings"] = reality
    return ss


def build_xray_config(node):
    address = node["address"]
    port = node["port"]
    stream = build_stream_settings(node)

    if node["protocol"] == "vmess":
        user = {"id": node["uuid"], "security": node.get("scy", "auto"),
                "alterId": node.get("aid", 0)}
        out = {"protocol": "vmess",
               "settings": {"vnext": [{"address": address, "port": port, "users": [user]}]},
               "streamSettings": stream}
    elif node["protocol"] == "vless":
        user = {"id": node["uuid"], "encryption": node.get("encryption", "none")}
        if node.get("flow"):
            user["flow"] = node["flow"]
        out = {"protocol": "vless",
               "settings": {"vnext": [{"address": address, "port": port, "users": [user]}]},
               "streamSettings": stream}
    elif node["protocol"] == "trojan":
        out = {"protocol": "trojan",
               "settings": {"servers": [{"address": address, "port": port,
                                          "password": node["password"]}]},
               "streamSettings": stream}
    elif node["protocol"] == "shadowsocks":
        out = {"protocol": "shadowsocks",
               "settings": {"servers": [{"address": address, "port": port,
                                         "method": node["method"], "password": node["password"]}]}}
    else:
        raise ValueError(f"unsupported protocol: {node['protocol']}")

    return {
        "log": {"loglevel": "warning"},
        "inbounds": [{"listen": "127.0.0.1", "port": PROXY_PORT, "protocol": "http"}],
        "outbounds": [out],
    }


# --------------------------------------------------------------------------- #
# xray process management
# --------------------------------------------------------------------------- #

def ensure_xray():
    if os.path.exists(XRAY_BIN):
        return
    os.makedirs("bin", exist_ok=True)
    zip_path = "bin/xray.zip"
    try:
        log("  downloading xray-core ...")
        req = urllib.request.Request(XRAY_URL, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r, open(zip_path, "wb") as f:
            f.write(r.read())
    except Exception:
        # Fallback: resolve the asset via the GitHub API.
        log("  direct download failed, falling back to GitHub API ...")
        req = urllib.request.Request(XRAY_API, headers={"User-Agent": UA,
                                                        "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            release = json.loads(r.read().decode())
        asset = next(a for a in release["assets"] if a["name"] == "Xray-linux-64.zip")
        req = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as r, open(zip_path, "wb") as f:
            f.write(r.read())
    with zipfile.ZipFile(zip_path) as z:
        z.extractall("bin")
    os.chmod(XRAY_BIN, 0o755)
    log("  xray-core ready.")


def start_xray(node):
    cfg_path = "bin/config.json"
    with open(cfg_path, "w") as f:
        json.dump(build_xray_config(node), f)
    proc = subprocess.Popen(
        [XRAY_BIN, "run", "-c", cfg_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return proc


def wait_for_proxy(timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", PROXY_PORT), timeout=1)
            s.close()
            return True
        except Exception:
            time.sleep(0.2)
    return False


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

def opener(use_proxy):
    handler = (urllib.request.ProxyHandler({"http": PROXY_URL, "https": PROXY_URL})
               if use_proxy else urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(handler)


def http_status(url, use_proxy, timeout=HTTP_TIMEOUT):
    """Return (reached, elapsed_ms). Any HTTP response counts as 'reached'."""
    op = opener(use_proxy)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    start = time.time()
    try:
        resp = op.open(req, timeout=timeout)
        resp.read()
        return True, round((time.time() - start) * 1000, 1)
    except urllib.error.HTTPError:
        # Got an HTTP response (e.g. 401 from Gemini without a key) -> reachable.
        return True, round((time.time() - start) * 1000, 1)
    except Exception:
        return False, None


def tcp_ping(host, port, timeout=8):
    start = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return round((time.time() - start) * 1000, 1)
    except Exception:
        return None


def speed_test():
    url = f"https://speed.cloudflare.com/__down?bytes={SPEED_BYTES}"
    op = opener(use_proxy=True)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    start = time.time()
    try:
        data = op.open(req, timeout=30).read()
        elapsed = time.time() - start
        if elapsed <= 0:
            return 0.0
        return round(len(data) * 8 / elapsed / 1e6, 2)  # Mbps
    except Exception:
        return 0.0


# --------------------------------------------------------------------------- #
# Geolocation
# --------------------------------------------------------------------------- #

def resolve_ip(host):
    try:
        for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP):
            if info[4][0]:
                return info[4][0]
    except Exception:
        pass
    return None


def geolocate(ip):
    if not ip:
        return {}
    apis = [
        ("ip-api", f"http://ip-api.com/json/{ip}?fields=status,country,regionName,city,isp,lat,lon"),
        ("ipapi.co", f"https://ipapi.co/{ip}/json/"),
        ("ipinfo", f"https://ipinfo.io/{ip}/json"),
    ]
    for name, url in apis:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            data = json.loads(urllib.request.urlopen(req, timeout=8).read().decode())
            if name == "ip-api":
                if data.get("status") == "success":
                    return {"country": data.get("country"), "region": data.get("regionName"),
                            "city": data.get("city"), "isp": data.get("isp"),
                            "lat": data.get("lat"), "lon": data.get("lon")}
            elif name == "ipapi.co":
                if not data.get("error"):
                    return {"country": data.get("country_name"), "region": data.get("region"),
                            "city": data.get("city"), "isp": data.get("org"),
                            "lat": data.get("latitude"), "lon": data.get("longitude")}
            else:  # ipinfo
                if data.get("ip"):
                    loc = (data.get("loc") or "").split(",")
                    return {"country": data.get("country"), "region": data.get("region"),
                            "city": data.get("city"), "isp": data.get("org"),
                            "lat": loc[0] if len(loc) == 2 else None,
                            "lon": loc[1] if len(loc) == 2 else None}
        except Exception:
            continue
    return {}


# --------------------------------------------------------------------------- #
# Scoring & orchestration
# --------------------------------------------------------------------------- #

def compute_score(r):
    if not r.get("alive"):
        return 0.0
    score = min(r.get("speed_mbps") or 0, 100)          # throughput, capped
    ping = r.get("tcp_ping_ms")
    if ping is not None:
        score += max(0.0, 30.0 - ping / 10.0)            # latency bonus
    if r.get("gemini_reachable"):
        score += 50.0                                    # Gemini bonus
    return round(score, 1)


def test_node(node):
    r = {
        "name": node.get("name", ""),
        "uri": node.get("uri"),
        "protocol": node["protocol"],
        "address": node["address"],
        "port": node["port"],
        "alive": False,
        "latency_ms": None,
        "tcp_ping_ms": None,
        "gemini_reachable": False,
        "speed_mbps": 0.0,
        "tested": True,
        "note": None,
        "ip": None,
        "country": None, "region": None, "city": None, "isp": None,
        "error": None,
    }

    # Protocols xray-core can't run: TCP-ping + geolocate only (pass-through).
    if node["protocol"] in TCP_ONLY_PROTOCOLS:
        r["tested"] = False
        r["note"] = "tcp-only (not testable with xray-core)"
        r["gemini_reachable"] = None
        r["speed_mbps"] = None
        r["tcp_ping_ms"] = tcp_ping(node["address"], node["port"])
        r["alive"] = r["tcp_ping_ms"] is not None
        ip = resolve_ip(node["address"])
        r["ip"] = ip
        r.update(geolocate(ip))
        r["score"] = compute_score(r)
        return r

    proc = None
    try:
        proc = start_xray(node)
        if wait_for_proxy():
            alive, alive_ms = http_status("http://www.gstatic.com/generate_204", use_proxy=True)
            r["alive"] = alive
            r["latency_ms"] = alive_ms
            if alive:
                gemini, _ = http_status("https://generativelanguage.googleapis.com/v1beta/models", use_proxy=True)
                r["gemini_reachable"] = gemini
                r["speed_mbps"] = speed_test()
        else:
            r["error"] = "xray failed to start"
        r["tcp_ping_ms"] = tcp_ping(node["address"], node["port"])
    except Exception as e:
        r["error"] = str(e)
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

    ip = resolve_ip(node["address"])
    r["ip"] = ip
    r.update(geolocate(ip))
    r["score"] = compute_score(r)
    return r


def rename_uri(uri, name):
    """Return the same config URI with its display name changed to `name`."""
    if uri.startswith("vmess://"):
        data = json.loads(b64decode_flex(uri[len("vmess://"):]))
        data["ps"] = name
        enc = base64.b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()
        return "vmess://" + enc
    scheme, _, rest = uri.partition("://")
    main = rest.split("#", 1)[0]
    return f"{scheme}://{main}#{urllib.parse.quote(name, safe='')}"


def b64sub(uris):
    """Encode a list of URIs into a standard v2ray subscription payload."""
    return base64.b64encode("\n".join(uris).encode()).decode()


def slug(s):
    if not s:
        return "unknown"
    out = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return out or "unknown"


def write_subscription(path, uris):
    if not uris:
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(b64sub(uris))
    return len(uris)


def write_subscriptions(results, repo):
    """Emit grouped subscription files and a manifest listing them."""
    alive = [r for r in results if r.get("alive") and r.get("uri")]
    base_raw = f"https://raw.githubusercontent.com/{repo}/main"
    groups = []

    def add(group_id, name, path, uris, desc=""):
        n = write_subscription(path, uris)
        if n:
            groups.append({"id": group_id, "name": name, "path": path,
                           "count": n, "url": f"{base_raw}/{path}", "desc": desc})

    # 1. Everything alive, and the Gemini-capable subset.
    add("all", "All (alive)", "subs/all.txt",
        [rename_uri(r["uri"], r["name"]) for r in alive])
    gemini = [r for r in alive if r.get("gemini_reachable")]
    add("gemini", "Can reach Gemini", "subs/gemini.txt",
        [rename_uri(r["uri"], r["name"]) for r in gemini])

    # 2. By protocol.
    for proto in sorted({r["protocol"] for r in alive}):
        items = [r for r in alive if r["protocol"] == proto]
        add(f"protocol-{slug(proto)}", f"Protocol · {proto}",
            f"subs/protocol/{slug(proto)}.txt",
            [rename_uri(r["uri"], r["name"]) for r in items])

    # 3. By country (city prefixed into the name).
    for cc in sorted({slug(r.get("country") or "unknown") for r in alive}):
        items = [r for r in alive if slug(r.get("country") or "unknown") == cc]
        label = items[0].get("country") or "Unknown"
        add(f"country-{cc}", f"Country · {label}", f"subs/country/{cc}.txt",
            [rename_uri(r["uri"],
                        f"{r['city']} · {r['name']}" if r.get("city") else r["name"])
             for r in items])

    # 4. By ISP / datacenter.
    for isp in sorted({slug(r.get("isp") or "unknown") for r in alive}):
        items = [r for r in alive if slug(r.get("isp") or "unknown") == isp]
        label = items[0].get("isp") or "Unknown"
        add(f"isp-{isp}", f"ISP/DC · {label}", f"subs/isp/{isp}.txt",
            [rename_uri(r["uri"], r["name"]) for r in items])

    # 5. Curated by quality (fully-tested configs only, for a fair score).
    full = [r for r in alive if r.get("tested")]
    by_score = sorted(full, key=lambda r: r.get("score") or 0, reverse=True)
    add("top", "Top by score (fully tested)", "subs/top.txt",
        [rename_uri(r["uri"], r["name"]) for r in by_score[:15]])
    add("low-latency", "Low latency (<250 ms)", "subs/low-latency.txt",
        [rename_uri(r["uri"], r["name"]) for r in alive
         if r.get("tcp_ping_ms") is not None and r["tcp_ping_ms"] < 250])
    add("fast", "Fast (>=10 Mbps)", "subs/fast.txt",
        [rename_uri(r["uri"], r["name"]) for r in alive
         if (r.get("speed_mbps") or 0) >= 10])

    manifest = {"updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "base_raw": base_raw, "groups": groups}
    os.makedirs("subs", exist_ok=True)
    with open("subs/manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)
    log(f"wrote {len(groups)} subscription groups")
    return manifest


def main():
    with open("sources.json") as f:
        sources = json.load(f)

    source_lists = []
    total_raw = 0
    for s in sources.get("sources", []):
        uris = fetch_source(s["url"])
        total_raw += len(uris)
        source_lists.append(uris)
    log(f"found {total_raw} raw URIs across {len(source_lists)} sources")

    # Dedupe per source, then round-robin so every source is fairly represented.
    seen, queues = set(), []
    for uris in source_lists:
        q = []
        for uri in uris:
            try:
                node = parse_uri(uri)
                node["uri"] = uri
            except Exception:
                continue
            key = (node["address"], node["port"], node["protocol"])
            if key in seen:
                continue
            seen.add(key)
            q.append(node)
        queues.append(q)

    maxc = int(sources.get("max_configs", 30))
    nodes = []
    i = 0
    while len(nodes) < maxc and any(queues):
        q = queues[i % len(queues)]
        if q:
            nodes.append(q.pop(0))
        i += 1
    log(f"testing {len(nodes)} unique configs")

    ensure_xray()

    results = []
    for i, node in enumerate(nodes):
        log(f"[{i + 1}/{len(nodes)}] {node['name']}  "
            f"({node['protocol']} {node['address']}:{node['port']})")
        r = test_node(node)
        state = "ALIVE" if r["alive"] else ("error: " + (r["error"] or "dead"))
        log(f"      -> {state}  ping={r['tcp_ping_ms']}ms  "
            f"speed={r['speed_mbps']}Mbps  gemini={r['gemini_reachable']}  "
            f"loc={r.get('city')}, {r.get('country')}  score={r['score']}")
        results.append(r)

    payload = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "node_count": len(results),
        "alive_count": sum(1 for r in results if r["alive"]),
        "gemini_count": sum(1 for r in results if r["gemini_reachable"]),
        "nodes": results,
    }
    with open("results.json", "w") as f:
        json.dump(payload, f, indent=2)
    log(f"wrote results.json ({payload['alive_count']}/{payload['node_count']} alive)")

    # GITHUB_REPOSITORY is set automatically on Actions (owner = your GitHub username).
    repo = os.environ.get("GITHUB_REPOSITORY", "Nexuspt753/REPO")
    write_subscriptions(results, repo)


if __name__ == "__main__":
    main()
