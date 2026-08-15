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
import ipaddress
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed

XRAY_URL = "https://github.com/XTLS/Xray-core/releases/latest/download/Xray-linux-64.zip"
XRAY_API = "https://api.github.com/repos/XTLS/Xray-core/releases/latest"
XRAY_BIN = "bin/xray"
PROXY_PORT = 10808

# Defaults, overridable via environment variables.
SPEED_BYTES = int(os.environ.get("SPEED_BYTES", "2000000"))   # 2 MB
HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "10"))
WORKERS = int(os.environ.get("WORKERS", "256"))  # Increased from 64 to 256 for significantly faster runs while testing ALL ~32k unique configs. Tests are network-bound (latency/timeouts), not CPU-bound, so this is safe on GitHub runners (up to ~500 workers). GitHub may throttle beyond 200-300 workers — monitor usage and scale down if needed. See README for full guidance.
UA = "Mozilla/5.0 (GitHub Actions; config-ranker)"

# Protocols xray-core cannot run.
#  - UDP/QUIC (hysteria2, tuic): can't even TCP-ping -> pass through untested.
#  - TCP (anytls, ssr): TCP-ping + geolocation only.
UDP_PROTOCOLS = {"hysteria2", "tuic"}
TCP_ONLY_PROTOCOLS = {"anytls", "ssr"}
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


def build_xray_config(node, local_port):
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
        "inbounds": [{"listen": "127.0.0.1", "port": local_port, "protocol": "http"}],
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


def start_xray(node, port):
    cfg_path = f"bin/config_{port}.json"
    with open(cfg_path, "w") as f:
        json.dump(build_xray_config(node, port), f)
    proc = subprocess.Popen(
        [XRAY_BIN, "run", "-c", cfg_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return proc


def wait_for_proxy(port, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = socket.create_connection(("127.0.0.1", port), timeout=1)
            s.close()
            return True
        except Exception:
            time.sleep(0.2)
    return False


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

def opener(use_proxy, port=PROXY_PORT):
    proxy = f"http://127.0.0.1:{port}"
    handler = (urllib.request.ProxyHandler({"http": proxy, "https": proxy})
               if use_proxy else urllib.request.ProxyHandler({}))
    return urllib.request.build_opener(handler)


def http_status(url, use_proxy, port=PROXY_PORT, timeout=HTTP_TIMEOUT):
    """Return (reached, elapsed_ms). Any HTTP response counts as 'reached'."""
    op = opener(use_proxy, port)
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


def speed_test(port=PROXY_PORT):
    url = f"https://speed.cloudflare.com/__down?bytes={SPEED_BYTES}"
    op = opener(True, port)
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
# Geolocation (DNS cached per host; geolocation batched after testing)
# --------------------------------------------------------------------------- #

_DNS_CACHE = {}
_GEO_CACHE = {}
_RDAP_CACHE = {}
_GEOFEED_CACHE = {}
_CACHE_LOCK = threading.Lock()
_GEOFEED_LOCK = threading.Lock()

# ip-api.com/batch geolocates up to 100 IPs per request — the most accurate free
# option, and the batch endpoint sidesteps the usual one-IP-per-request limit.
# Free tier allows ~15 batch requests/minute.
_BATCH_SIZE = 100
_BATCH_INTERVAL = 4.0   # seconds between batch requests


def resolve_ip(host):
    with _CACHE_LOCK:
        if host in _DNS_CACHE:
            return _DNS_CACHE[host]
    ip = None
    try:
        for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP):
            if info[4][0]:
                ip = info[4][0]
                break
    except Exception:
        pass
    with _CACHE_LOCK:
        _DNS_CACHE[host] = ip
    return ip


def _http_json(url, data=None, timeout=8):
    """GET (or JSON POST when `data` is set) and parse JSON, else None."""
    try:
        headers = {"User-Agent": UA}
        if data is not None:
            headers["Content-Type"] = "application/json"
            req = urllib.request.Request(url, data=data, headers=headers)
        else:
            req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:
        return None


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _geo_from_ipapi(d):
    asn = d.get("as") or None
    org = d.get("org") or None
    isp = d.get("isp") or org or asn
    return {
        "country": d.get("country"),
        "region": d.get("regionName"),
        "city": d.get("city"),
        "isp": isp or None,
        "org": org,
        "asn": asn,
        "lat": d.get("lat"),
        "lon": d.get("lon"),
    }


def _batch_ipapi(ips):
    """Geolocate IPs via ip-api.com/batch. Returns {ip: geo} for successes."""
    out = {}
    url = "http://ip-api.com/batch"
    fields = "query,status,message,country,regionName,city,lat,lon,isp,org,as"
    for i, chunk in enumerate(_chunks(ips, _BATCH_SIZE)):
        if i:
            time.sleep(_BATCH_INTERVAL)
        body = json.dumps([{"query": ip, "fields": fields} for ip in chunk]).encode()
        data = _http_json(url, data=body, timeout=20)
        if isinstance(data, list):
            for item in data:
                ip = item.get("query")
                if ip and item.get("status") == "success":
                    out[ip] = _geo_from_ipapi(item)
    return out


def _geo_ipwhois(d):
    if not d or d.get("success") is False:
        return None
    conn = d.get("connection") or {}
    org = conn.get("org")
    asn = conn.get("asn")
    return {
        "country": d.get("country"),
        "region": d.get("region"),
        "city": d.get("city"),
        "isp": org or conn.get("isp"),
        "org": org,
        "asn": (f"AS{asn} {org}".strip() if asn else None),
        "lat": d.get("latitude"),
        "lon": d.get("longitude"),
    }


def _geo_ipapi_co(d):
    if not d or d.get("error"):
        return None
    org = d.get("org")
    asn = d.get("asn")
    return {
        "country": d.get("country_name"),
        "region": d.get("region"),
        "city": d.get("city"),
        "isp": org,
        "org": org,
        "asn": (f"{asn} {org}".strip() if asn else None),
        "lat": d.get("latitude"),
        "lon": d.get("longitude"),
    }


def _fallback_geolocate(ip):
    """Single-IP fallback for anything ip-api.com/batch could not resolve."""
    providers = [
        (f"https://ipwho.is/{ip}", _geo_ipwhois),
        (f"https://ipapi.co/{ip}/json/", _geo_ipapi_co),
    ]
    for url, parse in providers:
        geo = parse(_http_json(url))
        if geo and (geo.get("country") or geo.get("city")):
            return geo
        time.sleep(0.25)
    return {}


# ISO 3166-1 alpha-2 -> full name, matching the names ip-api.com returns.
# 'EU' is ARIN's catch-all for blocks registered to Europe without a country.
_COUNTRY_NAMES = {
    "US": "United States", "CA": "Canada", "DE": "Germany", "FR": "France",
    "NL": "The Netherlands", "GB": "United Kingdom", "RU": "Russia",
    "FI": "Finland", "HK": "Hong Kong", "SG": "Singapore", "JP": "Japan",
    "IR": "Iran", "PL": "Poland", "BR": "Brazil", "CN": "China",
    "AE": "United Arab Emirates", "TR": "T\u00fcrkiye", "IT": "Italy",
    "KR": "South Korea", "SE": "Sweden", "ES": "Spain", "CH": "Switzerland",
    "IN": "India", "IE": "Ireland", "SA": "Saudi Arabia", "UA": "Ukraine",
    "EE": "Estonia", "IL": "Israel", "AU": "Australia", "AT": "Austria",
    "RO": "Romania", "MY": "Malaysia", "PK": "Pakistan", "GR": "Greece",
    "EG": "Egypt", "KW": "Kuwait", "JO": "Jordan", "VN": "Vietnam",
    "MX": "Mexico", "BG": "Bulgaria", "BE": "Belgium", "ZA": "South Africa",
    "CY": "Cyprus", "TH": "Thailand", "CZ": "Czechia", "LT": "Lithuania",
    "KH": "Cambodia", "HU": "Hungary", "BZ": "Belize", "AZ": "Azerbaijan",
    "AR": "Argentina", "NZ": "New Zealand", "AL": "Albania", "CW": "Curacao",
    "LV": "Latvia", "CO": "Colombia", "DZ": "Algeria", "DK": "Denmark",
    "MM": "Myanmar", "MT": "Malta", "MD": "Moldova", "CR": "Costa Rica",
    "PT": "Portugal", "TW": "Taiwan", "ME": "Montenegro", "LU": "Luxembourg",
    "ID": "Indonesia", "PR": "Puerto Rico", "AM": "Armenia", "NO": "Norway",
    "CL": "Chile", "GE": "Georgia", "DO": "Dominican Republic",
    "PA": "Panama", "RS": "Serbia", "SK": "Slovakia", "SI": "Slovenia",
    "HR": "Croatia", "NG": "Nigeria", "KE": "Kenya", "BD": "Bangladesh",
    "NP": "Nepal", "LK": "Sri Lanka", "PH": "Philippines", "IQ": "Iraq",
    "SY": "Syria", "LB": "Lebanon", "KZ": "Kazakhstan", "UZ": "Uzbekistan",
    "BY": "Belarus", "BA": "Bosnia and Herzegovina", "MK": "North Macedonia",
    "IS": "Iceland", "PE": "Peru", "EC": "Ecuador", "UY": "Uruguay",
    "GT": "Guatemala", "HN": "Honduras", "SV": "El Salvador", "NI": "Nicaragua",
    "JM": "Jamaica", "TT": "Trinidad and Tobago", "MA": "Morocco",
    "TN": "Tunisia", "ET": "Ethiopia", "QA": "Qatar",
    "BH": "Bahrain", "OM": "Oman", "YE": "Yemen", "AF": "Afghanistan",
    "EU": "Europe",
}

_RDAP_BASES = (
    "https://rdap.org/ip/",
    "https://rdap.db.ripe.net/ip/",
    "https://rdap.arin.net/registry/ip/",
)


def _country_name(code):
    """Map an ISO country code to the full name used in results.json."""
    code = (code or "").upper()
    if not code:
        return None
    return _COUNTRY_NAMES.get(code, code)


def _rdap_get(ip):
    """Query the IP's registration (RDAP). Returns the JSON object or None."""
    with _CACHE_LOCK:
        if ip in _RDAP_CACHE:
            return _RDAP_CACHE[ip]
    data = None
    for base in _RDAP_BASES:
        try:
            req = urllib.request.Request(
                base + ip,
                headers={"User-Agent": UA, "Accept": "application/rdap+json"},
            )
            with urllib.request.urlopen(req, timeout=6) as r:
                if r.status != 200:
                    continue
                j = json.loads(r.read().decode("utf-8", "ignore"))
                if "handle" in j or "startAddress" in j:
                    data = j
                    break
        except Exception:
            continue
    with _CACHE_LOCK:
        _RDAP_CACHE[ip] = data
    return data


def _geofeed_url(rdap):
    """Extract the RFC 8805 geofeed URL from an RDAP response, if any."""
    for link in rdap.get("links") or []:
        if "geofeed" in (link.get("rel") or ""):
            href = link.get("href")
            if href:
                return href
    return None


def _geofeed_fetch(url):
    """Fetch and parse an RFC 8805 geofeed CSV -> [(network, cc, city), ...]."""
    with _GEOFEED_LOCK:
        if url in _GEOFEED_CACHE:
            return _GEOFEED_CACHE[url]
    entries = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=15) as r:
            text = r.read().decode("utf-8", "ignore")
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2:
                continue
            try:
                net = ipaddress.ip_network(parts[0], strict=False)
            except ValueError:
                continue
            cc = parts[1].upper()
            city = parts[3] if len(parts) > 3 else ""
            entries.append((net, cc, city))
    except Exception:
        entries = []
    with _GEOFEED_LOCK:
        _GEOFEED_CACHE[url] = entries
    return entries


def _geofeed_lookup(ip, url):
    """Longest-prefix match of `ip` in the geofeed. Returns (cc, city) or None."""
    entries = _geofeed_fetch(url)
    if not entries:
        return None
    addr = ipaddress.ip_address(ip)
    best = None
    for net, cc, city in entries:
        if addr.version == net.version and addr in net:
            if best is None or net.prefixlen > best[0].prefixlen:
                best = (net, cc, city)
    if best and best[1]:
        return best[1], best[2]
    return None


def _rdap_override_for(ip):
    """Return geo fields that should override the ip-api result for `ip`.

    Free IP databases lag behind reality for rented/leased datacenter IPs and
    systematically mislabel European hosts as US/Canada. The registration
    (RDAP) plus the owner's self-published geofeed (RFC 8805) carry the
    freshest location, so we prefer them. Best-effort: on any failure we
    return None and leave the ip-api result untouched.
    """
    cur = _GEO_CACHE.get(ip) or {}

    # Anycast edge IPs have no single location; the anycast pass handles them
    # (and we save an RDAP round-trip for the many Cloudflare IPs).
    if _is_cloudflare_anycast(cur):
        return None

    # Only correct the labels we know the free databases get wrong (US/Canada
    # bias on churned hosting IPs). Leave an already-plausible country alone —
    # that also skips most RDAP lookups entirely.
    if cur.get("country") not in ("United States", "Canada", None):
        return None

    data = _rdap_get(ip)
    if not data:
        return None

    # 1. RFC 8805 geofeed — the owner's explicit current location.
    gf_url = _geofeed_url(data)
    if gf_url:
        hit = _geofeed_lookup(ip, gf_url)
        if hit:
            cc, city = hit
            return {"country": _country_name(cc), "region": None,
                    "city": city or None}

    # 2. RDAP registration country — undoes the stale-database US/Canada
    #    mislabel for recently reallocated IPs.
    rdap_cc = (data.get("country") or "").upper()
    if rdap_cc and rdap_cc != "ZZ":
        name = _country_name(rdap_cc)
        if name and name != cur.get("country"):
            return {"country": name, "region": None, "city": None}
    return None


def _apply_rdap_geofeed(ips):
    """Overlay RDAP/geofeed corrections onto _GEO_CACHE for the given IPs."""
    with ThreadPoolExecutor(max_workers=16) as ex:
        futures = {ex.submit(_rdap_override_for, ip): ip for ip in ips}
        for fut in as_completed(futures):
            ip = futures[fut]
            try:
                override = fut.result()
            except Exception:
                override = None
            if override:
                cur = dict(_GEO_CACHE.get(ip) or {})
                cur.update(override)
                _GEO_CACHE[ip] = cur


def _is_cloudflare_anycast(geo):
    """Cloudflare edge IPs are anycast: they have no single physical location."""
    return ((geo.get("isp") or "") == "Cloudflare, Inc."
            or (geo.get("org") or "") == "Cloudflare, Inc.")


def geolocate_all(results):
    """Fill geo fields for every unique IP in `results` (batched, cached)."""
    ips = sorted({r.get("ip") for r in results if r.get("ip")})
    todo = [ip for ip in ips if ip not in _GEO_CACHE]
    if todo:
        log(f"geolocating {len(todo)} unique IPs ...")
        for ip, geo in _batch_ipapi(todo).items():
            _GEO_CACHE[ip] = geo
        remaining = [ip for ip in todo if ip not in _GEO_CACHE]
        if remaining:
            log(f"  falling back for {len(remaining)} IPs ...")
            for ip in remaining:
                _GEO_CACHE[ip] = _fallback_geolocate(ip)
        # Authoritative correction: registration + RFC 8805 geofeeds.
        _apply_rdap_geofeed(todo)

    # Anycast CDN edge IPs route to the nearest PoP, so a single country is
    # meaningless (from GitHub they resolve to a US/CA edge regardless of the
    # user's own location).
    for ip in ips:
        geo = _GEO_CACHE.get(ip)
        if geo and _is_cloudflare_anycast(geo):
            geo["country"] = "Cloudflare (anycast)"
            geo["region"] = None
            geo["city"] = None

    for r in results:
        geo = _GEO_CACHE.get(r.get("ip")) or {}
        for k, v in geo.items():
            if v is not None:
                r[k] = v
    return results


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


def finish_node(r, node):
    r["ip"] = resolve_ip(node["address"])
    r["score"] = compute_score(r)
    return r


def test_node(node, port):
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
        "asn": None, "org": None, "lat": None, "lon": None,
        "error": None,
    }

    # UDP/QUIC protocols (hysteria2, tuic): can't test with xray or TCP-ping.
    if node["protocol"] in UDP_PROTOCOLS:
        r["tested"] = False
        r["alive"] = None
        r["gemini_reachable"] = None
        r["speed_mbps"] = None
        r["note"] = "udp (untested)"
        return finish_node(r, node)

    # TCP protocols xray can't run (anytls, ssr): TCP-ping + geolocate only.
    if node["protocol"] in TCP_ONLY_PROTOCOLS:
        r["tested"] = False
        r["gemini_reachable"] = None
        r["speed_mbps"] = None
        r["note"] = "tcp-only (not testable with xray-core)"
        r["tcp_ping_ms"] = tcp_ping(node["address"], node["port"])
        r["alive"] = r["tcp_ping_ms"] is not None
        return finish_node(r, node)

    # xray-testable protocols: cheap TCP pre-filter before spinning up xray.
    r["tcp_ping_ms"] = tcp_ping(node["address"], node["port"])
    if r["tcp_ping_ms"] is None:
        r["alive"] = False
        r["error"] = "tcp unreachable"
        return finish_node(r, node)

    proc = None
    try:
        proc = start_xray(node, port)
        if wait_for_proxy(port):
            alive, alive_ms = http_status("http://www.gstatic.com/generate_204", True, port)
            r["alive"] = alive
            r["latency_ms"] = alive_ms
            if alive:
                gemini, _ = http_status("https://generativelanguage.googleapis.com/v1beta/models", True, port)
                r["gemini_reachable"] = gemini
                r["speed_mbps"] = speed_test(port)
        else:
            r["error"] = "xray failed to start"
    except Exception as e:
        r["error"] = str(e)
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()

    return finish_node(r, node)


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


def slug(s):
    if not s:
        return "unknown"
    out = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-").lower()
    return out or "unknown"


def write_subscription(path, uris):
    """Write a plain-text subscription: one URI per line."""
    if not uris:
        return 0
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("\n".join(uris) + "\n")
    return len(uris)


def write_subscriptions(results, repo):
    """Emit grouped subscription files and a manifest listing them."""
    # Include anything not known-dead (alive=True, or alive=None = UDP untested).
    alive = [r for r in results if r.get("uri") and r.get("alive") is not False]
    base_raw = f"https://raw.githubusercontent.com/{repo}/main"
    groups = []

    def add(group_id, name, path, uris, desc=""):
        n = write_subscription(path, uris)
        if n:
            groups.append({"id": group_id, "name": name, "path": path,
                           "count": n, "url": f"{base_raw}/{path}", "desc": desc})

    # 1. Everything not-known-dead, and the Gemini-capable subset.
    add("all", "All", "subs/all.txt",
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

    # Dedupe by exact config URI — distinct configs sharing a host:port (e.g.
    # VLESS+Reality short-ids on one port) are all kept, while byte-identical
    # duplicates across feeds are skipped. Then round-robin so every source is
    # fairly represented. There is no per-run cap: every config is tested.
    seen, queues = set(), []
    for uris in source_lists:
        q = []
        for uri in uris:
            key = html.unescape(uri).strip()
            if key in seen:
                continue
            seen.add(key)
            try:
                node = parse_uri(uri)
                node["uri"] = uri
            except Exception:
                continue
            q.append(node)
        queues.append(q)

    nodes = []
    i = 0
    while any(queues):
        q = queues[i % len(queues)]
        if q:
            nodes.append(q.pop(0))
        i += 1
    log(f"testing {len(nodes)} unique configs")

    ensure_xray()

    results = [None] * len(nodes)
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {}
        for i, node in enumerate(nodes):
            port = PROXY_PORT + (i % WORKERS)
            if i % 250 == 0:
                log(f"[{i + 1}/{len(nodes)}] queued {node['name']} "
                    f"({node['protocol']} {node['address']}:{node['port']})")
            futs[ex.submit(test_node, node, port)] = i
        for fut in as_completed(futs):
            i = futs[fut]
            try:
                r = fut.result()
            except Exception as e:
                node = nodes[i]
                r = {"name": node.get("name", ""), "uri": node.get("uri"),
                     "protocol": node["protocol"], "address": node["address"],
                     "port": node["port"], "alive": False, "tested": True,
                     "gemini_reachable": False, "speed_mbps": 0.0,
                     "tcp_ping_ms": None, "ip": None,
                     "country": None, "region": None, "city": None, "isp": None,
                     "asn": None, "org": None, "lat": None, "lon": None,
                     "error": f"worker crash: {e}", "score": 0.0}
            results[i] = r
            state = ("ALIVE" if r["alive"] is True else
                     ("untested" if r["alive"] is None else
                      ("error: " + (r.get("error") or "dead"))))
            log(f"      -> {state}  ping={r.get('tcp_ping_ms')}ms  "
                f"speed={r.get('speed_mbps')}Mbps  gemini={r.get('gemini_reachable')}  "
                f"score={r.get('score')} "
                f"[{r.get('name')}]")

    geolocate_all(results)
    log(f"geolocated {sum(1 for r in results if r.get('country'))}/{len(results)} configs")

    # Write a slim results.json for the website. The page only needs a handful
    # of display fields; the per-node URI (by far the largest field) is moved to
    # subs/uris.json and fetched only when the subscription builder is used.
    # Compact separators also cut the JSON size roughly in half vs. indent=2.
    web_nodes = [{
        "name": r.get("name"), "protocol": r.get("protocol"),
        "address": r.get("address"), "port": r.get("port"),
        "alive": r.get("alive"), "tcp_ping_ms": r.get("tcp_ping_ms"),
        "speed_mbps": r.get("speed_mbps"),
        "gemini_reachable": r.get("gemini_reachable"),
        "score": r.get("score"), "country": r.get("country"),
        "region": r.get("region"), "city": r.get("city"),
        "isp": r.get("isp"),
    } for r in results]
    uris = [r.get("uri") or "" for r in results]

    payload = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "node_count": len(results),
        "alive_count": sum(1 for r in results if r["alive"]),
        "gemini_count": sum(1 for r in results if r["gemini_reachable"]),
        "nodes": web_nodes,
    }
    with open("results.json", "w") as f:
        json.dump(payload, f, separators=(",", ":"))
    log(f"wrote results.json ({payload['alive_count']}/{payload['node_count']} alive)")

    # GITHUB_REPOSITORY is set automatically on Actions (owner = your GitHub username).
    repo = os.environ.get("GITHUB_REPOSITORY", "Nexuspt753/REPO")
    write_subscriptions(results, repo)

    os.makedirs("subs", exist_ok=True)
    with open("subs/uris.json", "w") as f:
        json.dump(uris, f, separators=(",", ":"))
    log(f"wrote subs/uris.json ({len(uris)} URIs)")


if __name__ == "__main__":
    main()
