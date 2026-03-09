from http.server import BaseHTTPRequestHandler
import json
import urllib.request
import urllib.parse
import time
import os
import math

CACHE_DIR = "/tmp/ca_cache"
CACHE_TTL = 600  # aligned with frontend 15-min refresh


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

def fetch_ohlcv(symbol="BTCUSDT", interval="1d", start_ms=None):

    all_data = []
    limit = 1000
    start = start_ms

    while True:

        params = {
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        }

        if start:
            params["startTime"] = int(start)

        qs = urllib.parse.urlencode(params)
        url = f"https://api.binance.com/api/v3/klines?{qs}"

        req = urllib.request.Request(
            url,
            headers={"User-Agent": "AriSaiQuant/1.0"}
        )

        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read())

        if not data:
            break

        all_data.extend(data)

        if len(data) < limit:
            break

        start = data[-1][0] + 1

        time.sleep(0.2)

    return all_data[:-1]


# ======================================================
# Helpers
# ======================================================

def rsi(src, length):
    gains = [0]
    losses = [0]
    for i in range(1, len(src)):
        change = src[i] - src[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))
    avg_gain = rma(gains, length)
    avg_loss = rma(losses, length)
    out = []
    for g, l in zip(avg_gain, avg_loss):
        if l == 0:
            out.append(100 if g > 0 else 50)
        else:
            rs = g / l
            out.append(100 - (100 / (1 + rs)))
    return out


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


def wma(src, length):
    out = []
    for i in range(len(src)):
        start = max(0, i - length + 1)
        window = src[start:i + 1]
        weights = list(range(1, len(window) + 1))
        denom = sum(weights)
        out.append(sum(v * w for v, w in zip(window, weights)) / denom)
    return out


def linreg(src, length):
    out = []
    for i in range(len(src)):
        if i < length:
            out.append(src[i])
            continue
        y = src[i - length + 1:i + 1]
        x = list(range(len(y)))
        xm = sum(x) / len(x)
        ym = sum(y) / len(y)
        num = sum((xi - xm) * (yi - ym) for xi, yi in zip(x, y))
        den = sum((xi - xm) ** 2 for xi in x)
        slope = num / den if den != 0 else 0
        intercept = ym - slope * xm
        out.append(slope * (len(y) - 1) + intercept)
    return out


def ma_generic(src, length, type):
    if type == "SMA":
        return sma(src, length)
    if type == "EMA":
        return ema(src, length)
    if type == "WMA":
        return wma(src, length)
    if type == "SMMA":
        return rma(src, length)
    if type == "VWMA":
        return wma(src, length)  # approximation without volume
    if type == "DEMA":
        e1 = ema(src, length)
        e2 = ema(e1, length)
        return [2 * a - b for a, b in zip(e1, e2)]
    if type == "TEMA":
        e1 = ema(src, length)
        e2 = ema(e1, length)
        e3 = ema(e2, length)
        return [3 * (a - b) + c for a, b, c in zip(e1, e2, e3)]
    if type == "LSMA":
        return linreg(src, length)
    return sma(src, length)


def psar(high, low, start=0.006, increment=0.012, maximum=0.020):
    """
    Parabolic SAR matching Pine ta.sar(start, increment, maximum).
    Includes SAR clamping to prior bars' extremes.
    """
    n = len(high)
    if n == 0:
        return []

    out = [0.0] * n
    bull = True
    af = start
    ep = high[0]
    sar_val = low[0]
    out[0] = sar_val

    for i in range(1, n):
        # Project SAR forward
        sar_val = sar_val + af * (ep - sar_val)

        # Clamp SAR so it doesn't exceed prior bars' extremes
        if bull:
            sar_val = min(sar_val, low[i - 1])
            if i >= 2:
                sar_val = min(sar_val, low[i - 2])
            # Check for reversal
            if low[i] < sar_val:
                bull = False
                sar_val = ep
                ep = low[i]
                af = start
            else:
                if high[i] > ep:
                    ep = high[i]
                    af = min(af + increment, maximum)
        else:
            sar_val = max(sar_val, high[i - 1])
            if i >= 2:
                sar_val = max(sar_val, high[i - 2])
            # Check for reversal
            if high[i] > sar_val:
                bull = True
                sar_val = ep
                ep = high[i]
                af = start
            else:
                if low[i] < ep:
                    ep = low[i]
                    af = min(af + increment, maximum)

        out[i] = sar_val

    return out


def stdev(src, length):
    out = []
    for i in range(len(src)):
        start = max(0, i - length + 1)
        window = src[start:i + 1]
        mean = sum(window) / len(window)
        var = sum((x - mean) ** 2 for x in window) / len(window)
        out.append(math.sqrt(var))
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
# MTPI ENGINE — matched to Pine: AriSai_TRW / UniAriSai
# ======================================================

def compute_mtpi_engine(candles):

    opens  = [float(c[1]) for c in candles]
    highs  = [float(c[2]) for c in candles]
    lows   = [float(c[3]) for c in candles]
    closes = [float(c[4]) for c in candles]

    hl2   = [(h + l) / 2 for h, l in zip(highs, lows)]
    ohlc4 = [(o + h + l + c) / 4 for o, h, l, c in zip(opens, highs, lows, closes)]

    n = len(closes)

    # ──────────────────────────────────────────────────
    # 1) PARABOLIC SAR
    # Pine: ta.sar(0.006, 0.012, 0.020)
    # Signal on dir crossover (psar crosses above/below close)
    # ──────────────────────────────────────────────────

    ps = psar(highs, lows, start=0.006, increment=0.012, maximum=0.020)

    s1 = [0]
    for i in range(1, n):
        dir_curr = 1 if ps[i] < closes[i] else -1
        dir_prev = 1 if ps[i - 1] < closes[i - 1] else -1

        if dir_curr == 1 and dir_prev == -1:
            s1.append(1)
        elif dir_curr == -1 and dir_prev == 1:
            s1.append(-1)
        else:
            s1.append(s1[-1])

    # ──────────────────────────────────────────────────
    # 2) INVERTED SD-DEMA RSI
    # Pine: dema(close,40), rsi(dema,10), stdev(dema,40)
    # ──────────────────────────────────────────────────

    dema_vals = ma_generic(closes, 40, "DEMA")
    rsi_vals  = rsi(dema_vals, 10)
    sd_vals   = stdev(dema_vals, 40)

    s2 = [0]
    for i in range(n):
        upper = dema_vals[i] + sd_vals[i]
        sups  = closes[i] < upper

        long_inv  = rsi_vals[i] > 70 and not sups
        short_inv = rsi_vals[i] < 55

        if long_inv and not short_inv:
            s2.append(1)
        elif short_inv:
            s2.append(-1)
        else:
            s2.append(s2[-1])
    s2 = s2[1:]

    # ──────────────────────────────────────────────────
    # 3) EWMA Z-SCORE
    # Pine: wma(close,24), ema(wma,19), stdev(wma,19)
    # ──────────────────────────────────────────────────

    w     = wma(closes, 24)
    meanE = ema(w, 19)
    stdE  = stdev(w, 19)

    s3 = [0]
    for i in range(n):
        z = (w[i] - meanE[i]) / stdE[i] if stdE[i] != 0 else 0

        long_ewma  = z > 1.5
        short_ewma = z < 0.0

        if long_ewma and not short_ewma:
            s3.append(1)
        elif short_ewma:
            s3.append(-1)
        else:
            s3.append(s3[-1])
    s3 = s3[1:]

    # ──────────────────────────────────────────────────
    # 4) MARKTQUANT SUPERTREND
    # Pine:
    #   mqHigh     = ta.highest(ohlc4, 35)
    #   mqAlpha    = mqHigh * 0.94
    #   mqBeta     = mqHigh * 0.98
    #   mqAvgRange = math.avg(mqAlpha[12], mqBeta[20])
    #   mqMAVal    = ma_generic(close, 60, "VWMA")
    #   mqG  = close > mqAvgRange ? 1 : close < mqAvgRange ? -1 : 0
    #   mqH  = close > mqMAVal    ? 1 : close < mqMAVal    ? -1 : 0
    #   mqScore = math.avg(mqG, mqH)
    #   longMQ  = ta.crossover(mqScore, 0)
    #   shortMQ = ta.crossunder(mqScore, 0)
    # ──────────────────────────────────────────────────

    # highest(ohlc4, 35)
    mqHigh = [max(ohlc4[max(0, i - 34):i + 1]) for i in range(n)]

    mqAlpha = [x * 0.94 for x in mqHigh]
    mqBeta  = [x * 0.98 for x in mqHigh]

    # mqAvgRange = avg(mqAlpha[12], mqBeta[20]) — lagged lookback
    mqAvgRange = [0.0] * n
    for i in range(n):
        alpha_12 = mqAlpha[i - 12] if i >= 12 else mqAlpha[0]
        beta_20  = mqBeta[i - 20]  if i >= 20 else mqBeta[0]
        mqAvgRange[i] = (alpha_12 + beta_20) / 2

    mqMA = ma_generic(closes, 60, "VWMA")

    # Compute mqScore series
    mqScore = [0.0] * n
    for i in range(n):
        mqG = 1 if closes[i] > mqAvgRange[i] else (-1 if closes[i] < mqAvgRange[i] else 0)
        mqH = 1 if closes[i] > mqMA[i] else (-1 if closes[i] < mqMA[i] else 0)
        mqScore[i] = (mqG + mqH) / 2

    # Signal on crossover/crossunder of mqScore with 0
    s4 = [0]
    for i in range(1, n):
        cross_over  = mqScore[i] > 0 and mqScore[i - 1] <= 0
        cross_under = mqScore[i] < 0 and mqScore[i - 1] >= 0

        if cross_over and not cross_under:
            s4.append(1)
        elif cross_under:
            s4.append(-1)
        else:
            s4.append(s4[-1])

    # ──────────────────────────────────────────────────
    # 5) TRIPLE MA & TREND
    # Pine:
    #   fl(s, e, val) => sum(hl2 > val[i] ? 1 : -1, i=s..e)
    #   flVal = fl(16, 55, tma)
    #   newState = flVal > 27 ? 1 : flVal < 10 ? -1 : 0
    #   s5 flips only on bullishFlip / bearishFlip
    # ──────────────────────────────────────────────────

    ma1 = ma_generic(hl2, 34, "DEMA")
    ma2 = ma_generic(ma1, 34, "DEMA")
    ma3 = ma_generic(ma2, 34, "DEMA")
    tma = [3 * (a - b) + c for a, b, c in zip(ma1, ma2, ma3)]

    s5 = [0]
    prev_state = 0  # Pine: var int prevState = 0

    for i in range(1, n):
        if i < 55:
            s5.append(0)
            continue

        # fl(16, 55, tma): Pine loops i=16 to 55 inclusive = 40 values
        # val[j] in Pine means tma j bars ago = tma[i-j]
        fl_val = 0
        for j in range(16, 56):  # 16..55 inclusive = 40 iterations
            fl_val += 1 if hl2[i] > tma[i - j] else -1

        new_state = 1 if fl_val > 27 else (-1 if fl_val < 10 else 0)

        bullish_flip = prev_state != 1  and new_state == 1
        bearish_flip = prev_state != -1 and new_state == -1

        if bullish_flip or bearish_flip:
            prev_state = new_state

        if bullish_flip:
            s5.append(1)
        elif bearish_flip:
            s5.append(-1)
        else:
            s5.append(s5[-1])

    # ──────────────────────────────────────────────────
    # AGGREGATE
    # tpi = avg(s1..s5), signal = >0.1 bull, <-0.1 bear
    # ──────────────────────────────────────────────────

    score  = []
    signal = []

    for a, b, c, d, e in zip(s1, s2, s3, s4, s5):
        val = (a + b + c + d + e) / 5
        score.append(val)

        if val > 0.1:
            signal.append(1)
        elif val < -0.1:
            signal.append(-1)
        else:
            signal.append(0)

    return {
        "score": score,
        "signal": signal,
        "components": {
            "psar":    s1[-1],
            "inv_rsi": s2[-1],
            "ewma":    s3[-1],
            "mq":      s4[-1],
            "tma":     s5[-1],
        }
    }


# ======================================================
# LTPI ENGINE (MATCHES YOUR PINE SCRIPT)
# ======================================================

def compute_ltpi_engine(candles):

    opens  = [float(c[1]) for c in candles]
    highs  = [float(c[2]) for c in candles]
    lows   = [float(c[3]) for c in candles]
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

        buy = curr > afr[i - 1] and not (afr[i - 1] > afr[i - 2]) if i > 1 else False
        sell = curr < afr[i - 1] and not (afr[i - 1] < afr[i - 2]) if i > 1 else False

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
    src = [(o + h + l + c) / 4 for o, h, l, c in zip(opens, highs, lows, closes)]

    upperb = [src[0]]
    lowerb = [src[0]]
    midb = [src[0]]

    for i in range(1, n):

        prev_upper = upperb[-1]
        prev_lower = lowerb[-1]

        s = src[i]
        s1 = src[i - 1]

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
        midb.append((upper + lower) / 2)

    atrtrend = [0]
    lastState = 0

    for i in range(1, n):

        trendUp = midb[i] > midb[i - 1]
        trendDown = midb[i] < midb[i - 1]

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

    hl2 = [(h + l) / 2 for h, l in zip(highs, lows)]

    upper = [hl2[i] + factor * atr_st[i] for i in range(n)]
    lower = [hl2[i] - factor * atr_st[i] for i in range(n)]

    final_upper = [upper[0]]
    final_lower = [lower[0]]
    direction = [1]

    for i in range(1, n):

        fu = upper[i] if (upper[i] < final_upper[i - 1] or closes[i - 1] > final_upper[i - 1]) else final_upper[i - 1]
        fl = lower[i] if (lower[i] > final_lower[i - 1] or closes[i - 1] < final_lower[i - 1]) else final_lower[i - 1]

        final_upper.append(fu)
        final_lower.append(fl)

        prev_dir = direction[i - 1]

        if prev_dir > 0:
            direction.append(-1 if closes[i] > final_upper[i - 1] else 1)
        else:
            direction.append(1 if closes[i] < final_lower[i - 1] else -1)

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

    score = []
    signal = []

    for a, b, c, d in zip(afrtrend, atrtrend, supatrend, magictt):

        val = (a + b + c + d) / 4
        score.append(val)

        if val > 0.1:
            signal.append(1)
        elif val < -0.1:
            signal.append(-1)
        else:
            signal.append(0)

    return {
        "score": score,
        "signal": signal,
        "components": {
            "afr": afrtrend[-1],
            "trendbands": atrtrend[-1],
            "supertrend": supatrend[-1],
            "magictrend": magictt[-1]
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

    result = {}

    # ── MTPI ──────────────────────────────────────────
    try:
        mtpi_start = int(time.mktime(time.strptime("2023-01-01", "%Y-%m-%d")) * 1000)
        daily_fast = fetch_ohlcv(symbol="BTCUSDT", interval="1d", start_ms=mtpi_start)

        mtpi_engine = compute_mtpi_engine(daily_fast)

        mtpi_score_series = mtpi_engine["score"]
        mtpi_trade_series = mtpi_engine["signal"]
        mtpi_components   = mtpi_engine["components"]

        result["mtpi"]                = round(mtpi_score_series[-1], 4)
        result["mtpi_components"]     = mtpi_components
        result["mtpi_history"]        = build_history(daily_fast, mtpi_trade_series)
        result["mtpi_score_history"]  = build_history(daily_fast, mtpi_score_series)
    except Exception as e:
        result["mtpi_error"] = str(e)

    # ── LTPI ──────────────────────────────────────────
    try:
        ltpi_start_ms = int(time.mktime(time.strptime("2018-01-01", "%Y-%m-%d")) * 1000)

        daily_ltpi = fetch_ohlcv(
            symbol="BTCUSDT",
            interval="1d",
            start_ms=ltpi_start_ms
        )

        ltpi_result = compute_ltpi_engine(daily_ltpi)
        ltpi_score_series = ltpi_result["score"]
        ltpi_trade_series = ltpi_result["signal"]
        ltpi_components   = ltpi_result["components"]

        ltpi_value = ltpi_score_series[-1]

        if ltpi_value >= 0.75:
            ltpi_regime = "STRONG_BULL"
        elif ltpi_value > 0:
            ltpi_regime = "BULL"
        elif ltpi_value <= -0.75:
            ltpi_regime = "STRONG_BEAR"
        elif ltpi_value < 0:
            ltpi_regime = "BEAR"
        else:
            ltpi_regime = "NEUTRAL"

        result["ltpi"]                = round(ltpi_value, 4)
        result["ltpi_regime"]         = ltpi_regime
        result["ltpi_components"]     = ltpi_components
        result["ltpi_history"]        = build_history(daily_ltpi, ltpi_trade_series)
        result["ltpi_score_history"]  = build_history(daily_ltpi, ltpi_score_series)
        result["ltpi_debug"]          = ltpi_components
    except Exception as e:
        result["ltpi_error"] = str(e)

    return result
    import datetime
    result["computed_at"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())

    return result
# ======================================================
# HTTP API
# ======================================================

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        cache_key = "tpi_v21"

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