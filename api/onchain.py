# api/onchain.py
from http.server import BaseHTTPRequestHandler
import requests
import json
from urllib.parse import urlparse, parse_qs
from datetime import datetime, timezone

CM_BASE = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"

def mvrv_interp(v):
    if v < 1:   return "Undervalued — Historically strong accumulation zone"
    if v < 2.4: return "Fair Value — Healthy market range"
    if v < 3.7: return "Elevated — Consider reducing exposure"
    return           "Overvalued — Historically near cycle tops"

def fg_interp(v):
    if v <= 20: return "Extreme Fear — Historically strong buy zone"
    if v <= 40: return "Fear — Market oversold"
    if v <= 60: return "Neutral — No clear signal"
    if v <= 80: return "Greed — Start being cautious"
    return           "Extreme Greed — Historically near local tops"

def puell_interp(v):
    if v < 0.5: return "Undervalued — Miners under extreme stress. Historically a bottom."
    if v < 1.0: return "Accumulation zone — Miner revenue below yearly average"
    if v < 2.0: return "Fair Value — Normal miner revenue"
    if v < 4.0: return "Elevated — Miner revenue high. Watch for distribution."
    return           "Overvalued — Historically near cycle tops"

def dom_interp(v):
    if v > 60:  return f"BTC Dominance {v:.1f}% — Bitcoin season. Altcoins bleeding."
    if v > 50:  return f"BTC Dominance {v:.1f}% — BTC leading. Cautious altcoin exposure."
    return           f"BTC Dominance {v:.1f}% — Altcoin season possible."

def fetch_cm(metric_key, days):
    r = requests.get(CM_BASE, params={
        "assets":    "btc",
        "metrics":   metric_key,
        "frequency": "1d",
        "page_size": min(days, 3000),
    }, timeout=20)
    r.raise_for_status()
    records = []
    for item in r.json().get("data", []):
        val = item.get(metric_key)
        if val is not None:
            try:
                records.append({
                    "time":  item["time"][:10],
                    "value": round(float(val), 6)
                })
            except:
                pass
    return records

def get_mvrv(days):
    records = fetch_cm("CapMVRVCur", days)
    interp  = mvrv_interp(records[-1]["value"]) if records else None
    return records, interp

def get_realized_price(days):
    price_data = fetch_cm("PriceUSD", days)
    mvrv_data  = fetch_cm("CapMVRVCur", days)
    mvrv_by_date = {r["time"]: r["value"] for r in mvrv_data}
    records = []
    for p in price_data:
        mvrv_val = mvrv_by_date.get(p["time"])
        if mvrv_val and mvrv_val > 0:
            records.append({
                "time":  p["time"],
                "value": round(p["value"] / mvrv_val, 2)
            })
    interp = None
    if records:
        rp = records[-1]["value"]
        interp = f"Avg cost basis of all BTC holders: ${rp:,.0f}"
    return records, interp

def get_fear_greed(days):
    limit = min(days, 365)
    r = requests.get(
        "https://api.alternative.me/fng/",
        params={"limit": limit, "format": "json"},
        timeout=10
    )
    r.raise_for_status()
    data = r.json().get("data", [])
    records = []
    for item in data:
        ts  = int(item["timestamp"])
        val = int(item["value"])
        records.append({
            "time":  datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d"),
            "value": val,
            "label": item["value_classification"]
        })
    records.reverse()
    interp = fg_interp(records[-1]["value"]) if records else None
    return records, interp

def get_puell(days):
    fetch_days = min(days + 365, 1500)
    r = requests.get(
        "https://api.blockchain.info/charts/miners-revenue",
        params={
            "timespan": f"{fetch_days}days",
            "sampled":  "true",
            "metadata": "false",
            "cors":     "true",
            "format":   "json"
        },
        timeout=20
    )
    r.raise_for_status()
    raw = r.json().get("values", [])
    if len(raw) < 365:
        return [], None
    revenues = [float(item["y"]) for item in raw]
    dates    = [
        datetime.fromtimestamp(item["x"], tz=timezone.utc).strftime("%Y-%m-%d")
        for item in raw
    ]
    records = []
    for i in range(365, len(revenues)):
        ma_365 = sum(revenues[i-365:i]) / 365
        if ma_365 > 0:
            records.append({
                "time":  dates[i],
                "value": round(revenues[i] / ma_365, 4)
            })
    records = records[-days:]
    interp  = puell_interp(records[-1]["value"]) if records else None
    return records, interp

def get_btc_dominance(days):
    r_global = requests.get(
        "https://api.coingecko.com/api/v3/global",
        timeout=15
    )
    r_global.raise_for_status()
    current_dom = r_global.json().get("data", {}).get("market_cap_percentage", {}).get("btc", 0)

    cg_days = min(days, 365)
    r_btc = requests.get(
        "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart",
        params={"vs_currency": "usd", "days": cg_days},
        timeout=15
    )
    r_btc.raise_for_status()
    btc_caps = r_btc.json().get("market_caps", [])

    if not btc_caps or current_dom <= 0:
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        records = [{"time": today, "value": round(current_dom, 2)}]
        interp = dom_interp(current_dom)
        return records, interp

    latest_btc_cap = btc_caps[-1][1]
    implied_total = latest_btc_cap / (current_dom / 100) if current_dom > 0 else 1

    records = []
    seen = {}
    for item in btc_caps:
        if isinstance(item, list) and len(item) == 2 and item[1] and item[1] > 0:
            dt = datetime.fromtimestamp(item[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            est_dom = (item[1] / implied_total) * 100
            est_dom = max(30, min(80, est_dom))
            seen[dt] = {"time": dt, "value": round(est_dom, 2)}

    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    seen[today] = {"time": today, "value": round(current_dom, 2)}

    records = sorted(seen.values(), key=lambda x: x["time"])
    records = records[-days:]
    interp = dom_interp(records[-1]["value"]) if records else None
    return records, interp


# ======================================================
# NEW: Thermocap Multiple
# Market Cap / Cumulative Miner Revenue
# ======================================================

def get_thermocap_ratio(days):
    # Fetch max available daily miner revenue for cumulative sum
    rev_data = fetch_cm("RevUSD", 3000)
    mktcap_data = fetch_cm("CapMrktCurUSD", days)

    if not rev_data or not mktcap_data:
        return [], None

    # Build cumulative revenue (thermocap)
    cum_rev = {}
    running = 0
    for r in rev_data:
        running += r["value"]
        cum_rev[r["time"]] = running

    # Thermocap Multiple = Market Cap / Cumulative Miner Revenue
    records = []
    for m in mktcap_data:
        cr = cum_rev.get(m["time"])
        if cr and cr > 0:
            records.append({
                "time": m["time"],
                "value": round(m["value"] / cr, 2)
            })

    records = records[-days:]

    interp = None
    if records:
        v = records[-1]["value"]
        if v < 8:
            interp = f"Thermocap {v:.1f}x — Undervalued. Strong accumulation zone."
        elif v < 16:
            interp = f"Thermocap {v:.1f}x — Fair value range."
        elif v < 32:
            interp = f"Thermocap {v:.1f}x — Elevated. Late cycle positioning."
        else:
            interp = f"Thermocap {v:.1f}x — Overheated. Historically near cycle tops."

    return records, interp


# ======================================================
# NEW: Stablecoin Supply (USDT + USDC)
# ======================================================

def get_stablecoin_supply(days):
    cg_days = min(days, 365)

    by_date = {}
    for coin_id in ["tether", "usd-coin"]:
        try:
            r = requests.get(
                f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart",
                params={"vs_currency": "usd", "days": cg_days},
                timeout=15
            )
            r.raise_for_status()
            for item in r.json().get("market_caps", []):
                if isinstance(item, list) and len(item) == 2 and item[1]:
                    dt = datetime.fromtimestamp(
                        item[0] / 1000, tz=timezone.utc
                    ).strftime("%Y-%m-%d")
                    by_date[dt] = by_date.get(dt, 0) + item[1]
        except Exception:
            pass

    records = [{"time": k, "value": round(v, 0)}
               for k, v in sorted(by_date.items())]
    records = records[-days:]

    interp = None
    if records:
        v = records[-1]["value"]
        label = f"Stablecoin supply ${v / 1e9:.1f}B — "
        if len(records) >= 30:
            prev = records[-30]["value"]
            if prev > 0:
                change = ((v - prev) / prev) * 100
                if change > 3:
                    label += f"Growing (+{change:.1f}% 30d). Liquidity expanding."
                elif change < -3:
                    label += f"Contracting ({change:.1f}% 30d). Liquidity draining."
                else:
                    label += f"Stable ({change:+.1f}% 30d). Neutral liquidity."
            else:
                label += "USDT + USDC combined."
        else:
            label += "USDT + USDC combined market cap."
        interp = label

    return records, interp


# ======================================================
# NEW: Long/Short Ratio (Binance Global)
# ======================================================

def get_long_short_ratio(days):
    limit = min(days, 500)
    r = requests.get(
        "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
        params={"symbol": "BTCUSDT", "period": "1d", "limit": limit},
        headers={"User-Agent": "AriSaiQuant/1.0"},
        timeout=15
    )
    r.raise_for_status()
    data = r.json()

    records = []
    for item in data:
        ts = int(item["timestamp"]) / 1000
        records.append({
            "time": datetime.fromtimestamp(
                ts, tz=timezone.utc
            ).strftime("%Y-%m-%d"),
            "value": round(float(item["longShortRatio"]), 4),
            "long_pct": round(float(item.get("longAccount", 0)) * 100, 1),
            "short_pct": round(float(item.get("shortAccount", 0)) * 100, 1),
        })

    records.sort(key=lambda x: x["time"])
    records = records[-days:]

    interp = None
    if records:
        v = records[-1]["value"]
        lp = records[-1].get("long_pct", 0)
        sp = records[-1].get("short_pct", 0)
        base = f"L/S {v:.2f} ({lp:.0f}%L / {sp:.0f}%S) — "
        if v > 2.0:
            interp = base + "Extreme long bias. Crowded trade. Squeeze risk."
        elif v > 1.3:
            interp = base + "Long-biased. Market optimistic."
        elif v > 0.7:
            interp = base + "Balanced positioning. No strong directional bias."
        elif v > 0.5:
            interp = base + "Short-biased. Market cautious."
        else:
            interp = base + "Extreme short bias. Contrarian long signal."

    return records, interp


# ======================================================
# Handler
# ======================================================

class handler(BaseHTTPRequestHandler):

    METRICS = {
        "mvrv":              get_mvrv,
        "realized_price":    get_realized_price,
        "fear_greed":        get_fear_greed,
        "puell":             get_puell,
        "btc_dominance":     get_btc_dominance,
        "thermocap":         get_thermocap_ratio,
        "stablecoin_supply": get_stablecoin_supply,
        "long_short_ratio":  get_long_short_ratio,
    }

    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        metric = params.get("metric", ["mvrv"])[0]
        days   = int(params.get("days", ["365"])[0])
        days   = min(days, 3000)

        if metric not in self.METRICS:
            self._send({
                "error":     f"Unknown metric '{metric}'",
                "available": list(self.METRICS.keys())
            })
            return

        try:
            records, interp = self.METRICS[metric](days)
            latest = records[-1] if records else None
            result = {
                "metric":         metric,
                "count":          len(records),
                "latest":         latest,
                "interpretation": interp,
                "data":           records,
                "source":         "CoinMetrics + Alternative.me + Blockchain.com + CoinGecko + Binance"
            }
        except Exception as e:
            result = {"error": str(e), "metric": metric}

        self._send(result)

    def _send(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        pass