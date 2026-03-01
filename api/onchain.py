# api/onchain.py
# -------------------------------------------------------
# AriSai Quant — On-Chain Metrics Endpoint
# Source: CoinMetrics Community API (completely free)
# Endpoint: GET /api/onchain?metric=mvrv
#
# Available metrics:
#   mvrv          → Market Value to Realized Value
#   realized_price → Realized Price (avg cost basis of all BTC)
#   sopr          → Spent Output Profit Ratio
#   sth_mvrv      → Short Term Holder MVRV
#   nupl          → Net Unrealized Profit/Loss
# -------------------------------------------------------

from http.server import BaseHTTPRequestHandler
import requests
import json
from urllib.parse import urlparse, parse_qs


# CoinMetrics metric names mapped to our friendly names
METRIC_MAP = {
    "mvrv":           "CapMVRVCur",        # MVRV Ratio
    "realized_price": "CapRealUSD",        # Realized Cap (we'll convert to price)
    "sopr":           "SoprAll",           # SOPR
    "sth_mvrv":       "CapMVRVSTHCur",     # Short Term Holder MVRV
    "nupl":           "CapUnlstdPercUSD",  # NUPL proxy
    "supply":         "SplyCur",           # Current BTC Supply (for realized price calc)
}


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        # -------------------------------------------
        # 1. Read query parameters
        # -------------------------------------------
        params = parse_qs(urlparse(self.path).query)
        metric = params.get("metric", ["mvrv"])[0]
        days   = params.get("days",   ["365"])[0]

        # -------------------------------------------
        # 2. Validate metric name
        # -------------------------------------------
        if metric not in METRIC_MAP:
            result = {
                "error":    f"Unknown metric '{metric}'",
                "available": list(METRIC_MAP.keys())
            }
            self._send(result)
            return

        coinmetrics_metric = METRIC_MAP[metric]

        # -------------------------------------------
        # 3. Call CoinMetrics Community API
        # No API key needed — this is their free public API
        # -------------------------------------------
        url = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
        cm_params = {
            "assets":     "btc",
            "metrics":    coinmetrics_metric,
            "frequency":  "1d",
            "page_size":  days,
            "pretty":     "false"
        }

        try:
            response = requests.get(url, params=cm_params, timeout=15)
            raw = response.json()

            # -------------------------------------------
            # 4. Parse the response
            # CoinMetrics format: {"data": [{"time": "2024-01-01", "btc": {"MetricName": value}}]}
            # -------------------------------------------
            records = []
            for item in raw.get("data", []):
                value = item.get(coinmetrics_metric)
                if value is not None:
                    records.append({
                        "time":  item["time"][:10],  # Keep just the date YYYY-MM-DD
                        "value": round(float(value), 6)
                    })

            # -------------------------------------------
            # 5. Add interpretation zones for MVRV
            # MVRV < 1 = Undervalued (historically good buy)
            # MVRV 1-2.4 = Fair value
            # MVRV > 3.7 = Overvalued (historically good sell)
            # -------------------------------------------
            interpretation = None
            if metric == "mvrv" and records:
                latest = records[-1]["value"]
                if latest < 1:
                    interpretation = "Undervalued — Historically strong buy zone"
                elif latest < 2.4:
                    interpretation = "Fair Value"
                elif latest < 3.7:
                    interpretation = "Elevated — Exercise caution"
                else:
                    interpretation = "Overvalued — Historically near cycle tops"

            result = {
                "metric":         metric,
                "coinmetricsKey": coinmetrics_metric,
                "count":          len(records),
                "latest":         records[-1] if records else None,
                "interpretation": interpretation,
                "data":           records,
                "source":         "CoinMetrics Community (Free)"
            }

        except Exception as e:
            result = {"error": str(e), "source": "CoinMetrics"}

        self._send(result)

    def _send(self, data: dict):
        """Helper to send JSON response"""
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        pass