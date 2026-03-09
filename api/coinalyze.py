from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json, urllib.request, time, os
from datetime import datetime, timezone

API_KEY       = "bc2a8a10-1e81-4287-a504-7e6f90650be9"
COINGLASS_KEY = "82773da415884212a16b88e76a17a7ea"

# All exchanges matching Coinalyze "Aggregated Predicted Funding Rate" default settings:
# Binance (A), Bybit (3), OKX (2), BitMEX (0), Huobi (4), Kraken (8), Hyperliquid (B)
PERP_SYMBOLS = (
    "BTCUSDT_PERP.A,BTCUSD_PERP.A,"   # Binance USDT + USD
    "BTCUSDT_PERP.3,BTCUSD_PERP.3,"   # Bybit USDT + USD
    "BTCUSDT_PERP.2,BTCUSD_PERP.2,"   # OKX USDT + USD
    "BTCUSDT_PERP.0,BTCUSD_PERP.0,"   # BitMEX USDT + USD
    "BTCUSDT_PERP.4,BTCUSD_PERP.4,"   # Huobi USDT + USD
    "BTCUSD_PERP.8,"                   # Kraken USD
    "BTCUSD_PERP.B"                    # Hyperliquid USD
)
# Spot symbols with has_buy_sell_data=true (bv field populated in ohlcv-history)
SPOT_SYMBOLS = "BTCUSDT.A,BTCUSD.C,BTCUSDT.3,BTCUSDT.2"

CACHE_DIR    = "/tmp/ca_cache"
CACHE_TTL    = 600

# Coinalyze valid intervals: 1min,5min,15min,30min,1hour,2hour,4hour,6hour,12hour,daily
INTERVAL_MAP = {"1H": "1hour", "4H": "4hour", "1D": "daily", "7D": "daily"}
LOOKBACK     = {"1H": 86400*60, "4H": 86400*250, "1D": 86400*730, "7D": 86400*365*5}


# =========================================================
# Cache helpers
# =========================================================
def cache_path(key):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, key.replace("/", "_").replace(":", "_") + ".json")

def cache_read(key):
    try:
        p = cache_path(key)
        if not os.path.exists(p): return None
        if time.time() - os.path.getmtime(p) > CACHE_TTL: return None
        with open(p, "r") as f: return json.load(f)
    except: return None

def cache_read_stale(key):
    try:
        p = cache_path(key)
        if not os.path.exists(p): return None
        with open(p, "r") as f: return json.load(f)
    except: return None

def cache_write(key, data):
    try:
        with open(cache_path(key), "w") as f: json.dump(data, f)
    except: pass


# =========================================================
# Coinalyze API helper
# =========================================================
def coinalyze_get(path, params):
    from urllib.parse import urlencode
    url = "https://api.coinalyze.net/v1/" + path + "?" + urlencode(params)
    req = urllib.request.Request(url, headers={"api_key": API_KEY})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


# =========================================================
# Live OI weights
# =========================================================
def get_live_oi_weights():
    """
    Fetch current OI per symbol from Coinalyze /open-interest endpoint.
    Returns dict {symbol: weight} normalised to sum=1.
    Cached for 10 min. Used to weight funding rate by actual exchange OI share.
    """
    cache_key = "live_oi_weights"
    cached = cache_read(cache_key)
    if cached: return cached

    try:
        data = coinalyze_get("open-interest", {"symbols": PERP_SYMBOLS, "convert_to_usd": "true"})
        oi_map = {}
        for item in (data if isinstance(data, list) else []):
            sym = item.get("symbol", "")
            val = float(item.get("value", 0) or 0)
            if sym and val > 0:
                oi_map[sym] = val
        total = sum(oi_map.values())
        weights = {k: v / total for k, v in oi_map.items()} if total > 0 else {}
        cache_write(cache_key, weights)
        return weights
    except:
        return {}  # fall back to simple average if OI fetch fails


# =========================================================
# Funding rate
# =========================================================
def funding_by_interval(interval_key):
    cache_key = "funding_iv7_" + interval_key
    cached = cache_read(cache_key)
    if cached: return cached

    interval = INTERVAL_MAP.get(interval_key, "daily")
    now      = int(time.time())
    frm      = now - LOOKBACK.get(interval_key, 86400*365)

    data = coinalyze_get("predicted-funding-rate-history", {
        "symbols":  PERP_SYMBOLS,
        "interval": interval,
        "from":     frm,
        "to":       now
    })

    oi_weights = get_live_oi_weights()

    by_time = {}
    for sd in (data if isinstance(data, list) else []):
        sym = sd.get("symbol", "")
        w   = oi_weights.get(sym, None)
        for c in sd.get("history", []):
            t      = c.get("t", 0)
            val    = float(c.get("c", 0))
            weight = w if w is not None else 1.0
            if t not in by_time:
                by_time[t] = {"weighted": 0.0, "total_w": 0.0}
            by_time[t]["weighted"] += val * weight
            by_time[t]["total_w"]  += weight

    ppd = {"1H": 24.0, "4H": 6.0, "1D": 3.0, "7D": 3.0}.get(interval_key, 3.0)
    MA_LENGTH = 10

    result = []
    raw_series = []
    for t in sorted(by_time.keys()):
        tw  = by_time[t]["total_w"]
        raw = by_time[t]["weighted"] / tw if tw > 0 else 0.0
        raw_series.append(raw)
        ann = raw * ppd * 365.0 * 100.0
        fmt = "%Y-%m-%dT%H:%M:%S" if interval_key in ("1H", "4H") else "%Y-%m-%d"
        iso = datetime.fromtimestamp(t, tz=timezone.utc).strftime(fmt)
        result.append({"time": iso, "raw": raw, "annualized": ann})

    # actual_latest = true last rate (before smoothing) — used for card display value
    actual_latest = result[-1].copy() if result else {"annualized": 0.0, "raw": 0.0}

    # Apply MA10 to chart series only
    for i, row in enumerate(result):
        window = raw_series[max(0, i - MA_LENGTH + 1): i + 1]
        ma_raw = sum(window) / len(window)
        row["raw"] = ma_raw
        row["annualized"] = ma_raw * ppd * 365.0 * 100.0

    out = {"data": result, "latest": actual_latest, "interval": interval_key}
    cache_write(cache_key, out)
    return out


def funding_legacy(days):
    cache_key = "funding_leg2_" + str(days)
    cached = cache_read(cache_key)
    if cached: return cached

    now = int(time.time())
    frm = now - 86400 * int(days)

    data = coinalyze_get("predicted-funding-rate-history", {
        "symbols":  PERP_SYMBOLS,
        "interval": "daily",
        "from":     frm,
        "to":       now
    })

    by_time = {}
    for sd in (data if isinstance(data, list) else []):
        for c in sd.get("history", []):
            t   = c.get("t", 0)
            val = float(c.get("c", 0))
            oi  = float(c.get("oi", 1) or 1)
            if t not in by_time:
                by_time[t] = {"weighted": 0.0, "oi": 0.0}
            by_time[t]["weighted"] += float(val) * oi
            by_time[t]["oi"]       += oi   # FIX: was "total_w" — KeyError on every cold call

    result = []
    for t in sorted(by_time.keys()):
        tw  = by_time[t]["oi"]            # FIX: was by_time[t]["total_w"]
        raw = by_time[t]["weighted"] / tw if tw > 0 else 0.0
        ann = raw * 3.0 * 365.0 * 100.0
        iso = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
        result.append({"time": iso, "raw": raw, "annualized": ann})

    latest = result[-1] if result else {"annualized": 0.0, "raw": 0.0}
    out = {"data": result, "latest": latest}
    cache_write(cache_key, out)
    return out


# =========================================================
# CVD
# =========================================================
def cvd_data(days=None, interval_key='1D'):
    """
    Fetch aggregated CVD for spot and perp separately.
    interval_key: '1H'|'4H'|'1D'
    """
    iv_map   = {'1H': '1hour', '4H': '4hour', '1D': 'daily'}
    lk_map   = {'1H': 86400*60, '4H': 86400*250, '1D': 86400*2000}
    interval = iv_map.get(interval_key, 'daily')
    lookback = lk_map.get(interval_key, 86400*2000)
    is_intraday = interval_key in ('1H', '4H')

    cache_key = f"cvd4_{interval_key}"
    cached = cache_read(cache_key)
    if cached: return cached

    now = int(time.time())
    frm = now - lookback

    spot_raw = coinalyze_get("ohlcv-history", {
        "symbols":  SPOT_SYMBOLS,
        "interval": interval,
        "from":     frm,
        "to":       now
    })
    perp_raw = coinalyze_get("ohlcv-history", {
        "symbols":  PERP_SYMBOLS,
        "interval": interval,
        "from":     frm,
        "to":       now
    })

    fmt = "%Y-%m-%dT%H:%M:%S" if is_intraday else "%Y-%m-%d"

    def build_cvd(raw):
        by_ts = {}
        for sd in (raw if isinstance(raw, list) else []):
            for c in sd.get("history", []):
                t  = c["t"]
                bv = float(c.get("bv", 0) or 0)
                v  = float(c.get("v",  0) or 0)
                o_ = float(c.get("o",  0) or 0)
                h_ = float(c.get("h",  0) or 0)
                l_ = float(c.get("l",  0) or 0)
                cl = float(c.get("c",  0) or 0)
                if bv > 0 and v > 0:
                    delta = 2.0 * bv - v
                elif v > 0:
                    rng = h_ - l_
                    buy_ratio = (cl - l_) / rng if rng > 0 else (0.6 if cl >= o_ else 0.4)
                    delta = (2.0 * buy_ratio - 1.0) * v
                else:
                    continue
                by_ts[t] = by_ts.get(t, 0.0) + delta

        cum = 0.0
        result = []
        for t in sorted(by_ts):
            cum += by_ts[t]
            iso = datetime.fromtimestamp(t, tz=timezone.utc).strftime(fmt)
            result.append({"time": iso, "cvd": cum})
        return result

    spot_map = {x["time"]: x["cvd"] for x in build_cvd(spot_raw)}
    perp_map = {x["time"]: x["cvd"] for x in build_cvd(perp_raw)}
    all_times = sorted(set(list(spot_map) + list(perp_map)))
    result = [{"time": t, "spot_cvd": spot_map.get(t, 0.0), "perp_cvd": perp_map.get(t, 0.0)} for t in all_times]
    latest = result[-1] if result else {}

    out = {"data": result, "latest": latest, "interval": interval_key}
    cache_write(cache_key, out)
    return out


# =========================================================
# Long / Short ratio
# =========================================================
def long_short_ratio(days):
    cache_key = "ls_" + str(days)
    cached = cache_read(cache_key)
    if cached: return cached

    now = int(time.time())
    frm = now - 86400 * int(days)

    data = coinalyze_get("long-short-ratio", {
        "symbols":  "BTCUSDT_PERP.A",
        "interval": "daily",
        "from":     frm,
        "to":       now
    })

    by_time = {}
    for sd in (data if isinstance(data, list) else []):
        for c in sd.get("history", []):
            t  = c.get("t", 0)
            ls = c.get("l", None)
            if ls is not None:
                by_time[t] = float(ls)

    if len(by_time) < 30:
        data2 = coinalyze_get("long-short-ratio", {
            "symbols":  "BTCUSDT_PERP.A,BTCUSDT_PERP.3,BTCUSD_PERP.0",
            "interval": "daily",
            "from":     frm,
            "to":       now
        })
        multi = {}
        for sd in (data2 if isinstance(data2, list) else []):
            for c in sd.get("history", []):
                t  = c.get("t", 0)
                ls = c.get("l", None)
                if ls is not None:
                    if t not in multi: multi[t] = []
                    multi[t].append(float(ls))
        for t, vals in multi.items():
            if t not in by_time:
                by_time[t] = sum(vals) / len(vals)

    result = []
    for t in sorted(by_time.keys()):
        long_pct  = by_time[t]
        short_pct = 1.0 - long_pct
        ls_ratio  = long_pct / short_pct if short_pct > 0 else 1.0
        iso = datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
        result.append({
            "time":     iso,
            "lsRatio":  round(ls_ratio, 4),
            "longPct":  round(long_pct * 100, 2),
            "shortPct": round(short_pct * 100, 2)
        })

    latest = result[-1] if result else {"lsRatio": 1.0, "longPct": 50.0, "shortPct": 50.0}
    out = {"data": result, "latest": latest, "source": "Coinalyze"}
    cache_write(cache_key, out)
    return out


# =========================================================
# BTC price
# =========================================================
def btc_price_data():
    cache_key = "btc_price"
    cached = cache_read(cache_key)
    if cached: return cached

    try:
        url = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics?assets=btc&metrics=PriceUSD&frequency=1d&page_size=5000"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=20) as r:
            j = json.loads(r.read())
        prices = {}
        for row in (j.get("data") or []):
            if row.get("PriceUSD"):
                prices[row["time"][:10]] = float(row["PriceUSD"])
        out = {"prices": prices, "count": len(prices)}
        cache_write(cache_key, out)
        return out
    except Exception as e:
        stale = cache_read_stale(cache_key)
        if stale: return stale
        return {"prices": {}, "error": str(e)}


# =========================================================
# Open Interest history
# =========================================================
def oi_history(interval_key='1D'):
    iv_map      = {'1H': '1hour', '4H': '4hour', '1D': 'daily'}
    lk_map      = {'1H': 86400*60, '4H': 86400*250, '1D': 86400*365*8}
    interval    = iv_map.get(interval_key, 'daily')
    lookback    = lk_map.get(interval_key, 86400*365*8)
    is_intraday = interval_key in ('1H', '4H')

    cache_key = f"ca_oi_v2_{interval_key}"
    cached = cache_read(cache_key)
    if cached: return cached

    now = int(time.time())
    frm = int(datetime(2018, 1, 1, tzinfo=timezone.utc).timestamp()) if not is_intraday else now - lookback

    data = coinalyze_get("open-interest-history", {
        "symbols":        PERP_SYMBOLS,
        "interval":       interval,
        "from":           frm,
        "to":             now,
        "convert_to_usd": "true"
    })

    fmt   = "%Y-%m-%dT%H:%M:%S" if is_intraday else "%Y-%m-%d"
    by_ts = {}
    for sd in (data if isinstance(data, list) else []):
        for c in sd.get("history", []):
            t   = c["t"]
            val = float(c.get("c", 0) or 0)
            by_ts[t] = by_ts.get(t, 0.0) + val

    result = [
        {"time": datetime.fromtimestamp(t, tz=timezone.utc).strftime(fmt), "value": by_ts[t]}
        for t in sorted(by_ts)
    ]
    latest = result[-1] if result else {"value": 0}
    out = {"data": result, "latest": latest, "interval": interval_key}
    cache_write(cache_key, out)
    return out


# =========================================================
# HTTP handler
# =========================================================
class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        parsed   = urlparse(self.path)
        params   = parse_qs(parsed.query)
        metric   = params.get("metric",   ["funding"])[0]
        interval = params.get("interval", ["1D"])[0]
        days     = int(params.get("days", ["365"])[0])

        cache_key = None
        try:
            if metric == "funding":
                if "interval" in params:
                    result    = funding_by_interval(interval)
                    cache_key = "funding_iv7_" + interval
                else:
                    result    = funding_legacy(days)
                    cache_key = "funding_leg2_" + str(days)

            elif metric == "cvd":
                iv        = params.get("interval_key", ["1D"])[0]
                result    = cvd_data(interval_key=iv)
                cache_key = f"cvd4_{iv}"

            elif metric == "ls":
                result    = long_short_ratio(days)
                cache_key = "ls_" + str(days)

            elif metric == "oi_history":
                iv        = params.get("interval_key", ["1D"])[0]
                result    = oi_history(interval_key=iv)
                cache_key = f"ca_oi_v2_{iv}"

            elif metric == "btc_price":
                result    = btc_price_data()
                cache_key = "btc_price"

            else:
                result = {"error": "unknown metric: " + metric}

            body = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)

        except Exception as e:
            stale = cache_read_stale(cache_key) if cache_key else None
            if stale:
                stale["_stale"] = True
                body = json.dumps(stale).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
            else:
                err = json.dumps({"error": str(e)}).encode()
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(err)

    def log_message(self, *args):
        pass