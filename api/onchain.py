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
        "page_size": min(days, 3000),  # CoinMetrics max
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
    # Alternative.me hard cap is 365 days
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
    # Blockchain.com safe max ~1500 days
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
    # Free endpoint: /global gives current dominance
    # /coins/bitcoin/market_chart gives BTC market cap history
    # We compute dominance from both sources

    # 1. Get current dominance from /global
    r_global = requests.get(
        "https://api.coingecko.com/api/v3/global",
        timeout=15
    )
    r_global.raise_for_status()
    current_dom = r_global.json().get("data", {}).get("market_cap_percentage", {}).get("btc", 0)

    # 2. Get BTC market cap history
    cg_days = min(days, 365)
    r_btc = requests.get(
        "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart",
        params={"vs_currency": "usd", "days": cg_days},
        timeout=15
    )
    r_btc.raise_for_status()
    btc_caps = r_btc.json().get("market_caps", [])

    # 3. Get total crypto market cap history via a stablecoin-excluded proxy
    #    CoinGecko free tier doesn't give total market cap history directly,
    #    so we estimate dominance by anchoring the ratio to the current known value
    #    and scaling BTC market cap changes backward

    if not btc_caps or current_dom <= 0:
        today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
        records = [{"time": today, "value": round(current_dom, 2)}]
        interp = dom_interp(current_dom)
        return records, interp

    # Use the latest BTC market cap as anchor
    latest_btc_cap = btc_caps[-1][1]
    # Derive implied total market cap from current dominance
    implied_total = latest_btc_cap / (current_dom / 100) if current_dom > 0 else 1

    records = []
    seen = {}
    for item in btc_caps:
        if isinstance(item, list) and len(item) == 2 and item[1] and item[1] > 0:
            dt = datetime.fromtimestamp(item[0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            # Estimate: dominance scales proportionally with BTC cap vs implied total
            # This is an approximation — accurate for recent data, rougher for older
            est_dom = (item[1] / implied_total) * 100
            est_dom = max(30, min(80, est_dom))  # clamp to reasonable range
            seen[dt] = {"time": dt, "value": round(est_dom, 2)}

    # Override the latest point with the actual known value
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    seen[today] = {"time": today, "value": round(current_dom, 2)}

    records = sorted(seen.values(), key=lambda x: x["time"])
    records = records[-days:]
    interp = dom_interp(records[-1]["value"]) if records else None
    return records, interp

class handler(BaseHTTPRequestHandler):

    METRICS = {
        "mvrv":           get_mvrv,
        "realized_price": get_realized_price,
        "fear_greed":     get_fear_greed,
        "puell":          get_puell,
        "btc_dominance":  get_btc_dominance,
    }

    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        metric = params.get("metric", ["mvrv"])[0]
        days   = int(params.get("days", ["365"])[0])
        days   = min(days, 3000)  # hard cap

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
                "source":         "CoinMetrics + Alternative.me + Blockchain.com + CoinGecko"
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