# api/openinterest.py
# -------------------------------------------------------
# AriSai Quant — Open Interest Endpoint
# Source: Bybit Public API (no key needed)
# Endpoint: GET /api/openinterest?symbol=BTCUSDT&interval=1h
# -------------------------------------------------------

from http.server import BaseHTTPRequestHandler
import requests
import json
from urllib.parse import urlparse, parse_qs


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        # -------------------------------------------
        # 1. Read query parameters
        # -------------------------------------------
        params   = parse_qs(urlparse(self.path).query)
        symbol   = params.get("symbol",   ["BTCUSDT"])[0]
        interval = params.get("interval", ["1h"])[0]
        limit    = params.get("limit",    ["200"])[0]

        # -------------------------------------------
        # 2. Call Bybit Open Interest API
        # -------------------------------------------
        url = "https://api.bybit.com/v5/market/open-interest"
        bybit_params = {
            "category":     "linear",
            "symbol":       symbol,
            "intervalTime": interval,
            "limit":        limit
        }

        try:
            response = requests.get(url, params=bybit_params, timeout=10)
            raw = response.json()

            # -------------------------------------------
            # 3. Parse the data
            # -------------------------------------------
            records = []
            for item in raw.get("result", {}).get("list", []):
                records.append({
                    "time":         int(item["timestamp"]) // 1000,
                    "openInterest": float(item["openInterest"]),
                })

            records.reverse()  # Oldest first

            result = {
                "symbol":   symbol,
                "interval": interval,
                "count":    len(records),
                "data":     records,
                "source":   "Bybit"
            }

        except Exception as e:
            result = {"error": str(e), "source": "Bybit"}

        # -------------------------------------------
        # 4. Send JSON response
        # -------------------------------------------
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    def log_message(self, format, *args):
        pass