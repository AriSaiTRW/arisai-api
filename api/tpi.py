from http.server import BaseHTTPRequestHandler
import json
import urllib.request
import urllib.parse
import time
import os
import math

CACHE_DIR = "/tmp/ca_cache"
CACHE_TTL = 60


# ======================================================
# Cache
# ======================================================

def cache_path(key):
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, key + ".json")


def cache_read(key):
    try:
        p = cache_path(key)
        if not os.path.exists(p):
            return None
        if time.time() - os.path.getmtime(p) > CACHE_TTL:
            return None
        with open(p) as f:
            return json.load(f)
    except Exception:
        return None


def cache_write(key, data):
    try:
        with open(cache_path(key), "w") as f:
            json.dump(data, f)
    except Exception:
        pass


# ======================================================
# Binance
# ======================================================

def fetch_ohlcv(symbol="BTCUSDT", interval="1d", limit=1000, start_ms=None):
    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": str(limit),
    }
    if start_ms is not None:
        params["startTime"] = str(int(start_ms))

    qs = urllib.parse.urlencode(params)
    url = f"https://api.binance.com/api/v3/klines?{qs}"
    req = urllib.request.Request(url, headers={"User-Agent": "AriSaiQuant/1.0"})

    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


# ======================================================
# Helpers
# ======================================================

def ema(src, length):
    if not src:
        return []
    k = 2 / (length + 1)
    out = [src[0]]
    for i in range(1, len(src)):
        out.append(src[i] * k + out[-1] * (1 - k))
    return out


def sma(src, length):
    out = []
    for i in range(len(src)):
        start = max(0, i - length + 1)
        window = src[start:i + 1]
        out.append(sum(window) / len(window))
    return out


def rma(src, length):
    if not src:
        return []
    out = [src[0]]
    for i in range(1, len(src)):
        out.append((out[-1] * (length - 1) + src[i]) / length)
    return out


def true_range(highs, lows, closes):
    tr = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1])
        ))
    return tr


def atr_rma(highs, lows, closes, length):
    return rma(true_range(highs, lows, closes), length)


def atr_sma(highs, lows, closes, length):
    return sma(true_range(highs, lows, closes), length)


def cci(src, length):
    sma_vals = sma(src, length)
    dev = []

    for i in range(len(src)):
        start = max(0, i - length + 1)
        window = src[start:i + 1]
        mean = sum(window) / len(window)
        md = sum(abs(x - mean) for x in window) / len(window)
        dev.append(md)

    out = []
    for i in range(len(src)):
        if dev[i] == 0:
            out.append(0.0)
        else:
            out.append((src[i] - sma_vals[i]) / (0.015 * dev[i]))
    return out


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ======================================================
# MTPI
# ======================================================

def compute_mtpi_score_series(candles):
    closes = [float(c[4]) for c in candles]

    ema12 = ema(closes, 12)
    ema21 = ema(closes, 21)

    out = []
    for i in range(len(closes)):
        spread = (ema12[i] - ema21[i]) / closes[i] if closes[i] else 0.0
        out.append(clamp(spread * 20, -1.0, 1.0))
    return out


def compute_mtpi_trade_series(candles, long_threshold=0.1, short_threshold=-0.1):
    scores = compute_mtpi_score_series(candles)
    signals = []

    for v in scores:
        if v > long_threshold:
            signals.append(1)
        elif v < short_threshold:
            signals.append(-1)
        else:
            signals.append(0)

    return signals


def compute_mtpi(candles):
    closes = [float(c[4]) for c in candles]
    ema12 = ema(closes, 12)
    ema21 = ema(closes, 21)
    score = compute_mtpi_score_series(candles)[-1]

    comp_state = 1 if score > 0.1 else -1 if score < -0.1 else 0

    return {
        "tpi": round(score, 4),
        "components": {
            "ema12": round(ema12[-1], 4),
            "ema21": round(ema21[-1], 4),
            "trend": comp_state
        }
    }


# ======================================================
# LTPI COMPONENTS
# Pine-aligned as closely as possible
# ======================================================
# ======================================================
# LTPI ENGINE (MATCHES YOUR PINE SCRIPT)
# ======================================================

def compute_ltpi_engine(candles):

    opens = [float(c[1]) for c in candles]
    highs = [float(c[2]) for c in candles]
    lows = [float(c[3]) for c in candles]
    closes = [float(c[4]) for c in candles]

    n = len(closes)

    # --------------------------------------------------
    # AFR
    # --------------------------------------------------

    p = 54
    atr_factor = 3

    atr = atr_rma(highs, lows, closes, p)

    afr = [closes[0]]
    afrtrend = [0]

    for i in range(1, n):

        e = atr[i] * atr_factor

        atr_factoryHigh = closes[i] + e
        atr_factoryLow = closes[i] - e

        prev_afr = afr[-1]

        if atr_factoryLow > prev_afr:
            curr = atr_factoryLow
        elif atr_factoryHigh < prev_afr:
            curr = atr_factoryHigh
        else:
            curr = prev_afr

        afr.append(curr)

        buy = curr > afr[i-1] and not (afr[i-1] > afr[i-2]) if i > 1 else False
        sell = curr < afr[i-1] and not (afr[i-1] < afr[i-2]) if i > 1 else False

        if buy:
            afrtrend.append(1)
        elif sell:
            afrtrend.append(-1)
        else:
            afrtrend.append(afrtrend[-1])

    # --------------------------------------------------
    # Trend Bands
    # --------------------------------------------------

    length = 50
    mult = 6.3

    atr_tb = atr_rma(highs, lows, closes, length)
    src = [(o+h+l+c)/4 for o,h,l,c in zip(opens,highs,lows,closes)]

    upperb = [0]
    lowerb = [0]
    midb = [0]

    for i in range(1,n):

        prev_upper = upperb[-1]
        prev_lower = lowerb[-1]

        s = src[i]
        s1 = src[i-1]

        delta = atr_tb[i] * mult

        if s > prev_upper:

            upper = max(prev_upper, max(s, s1))
            lower = upper - delta

            if lower < prev_lower or (lower > prev_lower and upper == prev_upper):
                lower = prev_lower

        elif s < prev_lower:

            lower = min(prev_lower, min(s, s1))
            upper = lower + delta

            if upper > prev_upper or (upper < prev_upper and lower == prev_lower):
                upper = prev_upper

        else:

            upper = prev_upper
            lower = prev_lower

        upperb.append(upper)
        lowerb.append(lower)
        midb.append((upper+lower)/2)

    atrtrend = [0]
    lastState = 0

    for i in range(1,n):

        trendUp = midb[i] > midb[i-1]
        trendDown = midb[i] < midb[i-1]

        prevState = lastState
        lastState = 1 if trendUp else -1 if trendDown else prevState

        buyCond = trendUp and prevState == -1
        sellCond = trendDown and prevState == 1

        if buyCond:
            atrtrend.append(1)
        elif sellCond:
            atrtrend.append(-1)
        else:
            atrtrend.append(atrtrend[-1])

    # --------------------------------------------------
    # Supertrend
    # --------------------------------------------------

    atrPeriod = 26
    factor = 6

    atr_st = atr_rma(highs, lows, closes, atrPeriod)

    hl2 = [(h+l)/2 for h,l in zip(highs,lows)]

    upper = [hl2[i] + factor*atr_st[i] for i in range(n)]
    lower = [hl2[i] - factor*atr_st[i] for i in range(n)]

    final_upper=[upper[0]]
    final_lower=[lower[0]]
    direction=[1]

    for i in range(1,n):

        fu = upper[i] if (upper[i] < final_upper[i-1] or closes[i-1] > final_upper[i-1]) else final_upper[i-1]
        fl = lower[i] if (lower[i] > final_lower[i-1] or closes[i-1] < final_lower[i-1]) else final_lower[i-1]

        final_upper.append(fu)
        final_lower.append(fl)

        prev_dir = direction[i-1]

        if prev_dir > 0:
            direction.append(-1 if closes[i] > final_upper[i-1] else 1)
        else:
            direction.append(1 if closes[i] < final_lower[i-1] else -1)

    supatrend = [1 if d < 0 else -1 for d in direction]

    # --------------------------------------------------
    # MagicTrend
    # --------------------------------------------------

    period = 270
    cci_vals = cci(closes, period)

    magictt = [1 if v >= 0 else -1 for v in cci_vals]

    # --------------------------------------------------
    # LTPI Score
    # --------------------------------------------------

    score=[]
    signal=[]

    for a,b,c,d in zip(afrtrend,atrtrend,supatrend,magictt):

        val = (a+b+c+d)/4
        score.append(val)

        if val>0.1:
            signal.append(1)
        elif val<-0.1:
            signal.append(-1)
        else:
            signal.append(0)

    return {

        "score":score,
        "signal":signal,

        "components":{
            "afr":afrtrend[-1],
            "trendbands":atrtrend[-1],
            "supertrend":supatrend[-1],
            "magictrend":magictt[-1]
        }
    }


# ======================================================
# History builders
# ======================================================

def build_history(candles, signals):
    history = []
    for i in range(min(len(candles), len(signals))):
        history.append({
            "time": time.strftime("%Y-%m-%d", time.gmtime(candles[i][0] / 1000)),
            "price": float(candles[i][4]),
            "signal": signals[i]
        })
    return history


# ======================================================
# Compute all
# ======================================================

def compute_all_indicators():
    # MTPI candles
    daily_fast = fetch_ohlcv(symbol="BTCUSDT", interval="1d", limit=1000)

    # LTPI candles
    daily_ltpi = fetch_ohlcv(symbol="BTCUSDT", interval="1d", limit=2000)

    # MTPI
    mtpi_score_series = compute_mtpi_score_series(daily_fast)
    mtpi_trade_series = compute_mtpi_trade_series(daily_fast)
    mtpi = compute_mtpi(daily_fast)

    # LTPI
    ltpi_result = compute_ltpi_engine(daily_ltpi)
    ltpi_score_series = ltpi_result["score"]
    ltpi_trade_series = ltpi_result["signal"]
    ltpi_components = ltpi_result["components"]

    return {
        "mtpi": round(mtpi_score_series[-1], 4),
        "ltpi": round(ltpi_score_series[-1], 4),

        "mtpi_components": mtpi["components"],
        "ltpi_components": ltpi_components,

        "mtpi_history": build_history(daily_fast, mtpi_trade_series),
        "ltpi_history": build_history(daily_ltpi, ltpi_trade_series),

        "mtpi_score_history": build_history(daily_fast, mtpi_score_series),
        "ltpi_score_history": build_history(daily_ltpi, ltpi_score_series),

        "ltpi_debug": ltpi_components
    }


# ======================================================
# HTTP API
# ======================================================

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        cache_key = "tpi_v16"

        cached = cache_read(cache_key)
        if cached:
            self.respond(200, cached)
            return

        try:
            result = compute_all_indicators()
            cache_write(cache_key, result)
            self.respond(200, result)

        except Exception as e:
            self.respond(500, {"error": str(e)})

    def respond(self, code, data):
        body = json.dumps(data).encode()

        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()

        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, *args):
        pass