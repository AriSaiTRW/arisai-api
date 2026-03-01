# api/longshort.py
# -------------------------------------------------------
# AriSai Quant — Long/Short Ratio Endpoint
# Source: Bybit Public API (no key needed)
# Endpoint: GET /api/longshort?symbol=BTCUSDT&period=1d
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
        params = parse_qs(urlparse(self.path).query)
        symbol = params.get("symbol", ["BTCUSDT"])[0]
        period = params.get("period", ["1d"])[0]
        limit  = params.get("limit",  ["200"])[0]

        # -------------------------------------------
        # 2. Call Bybit Long/Short Ratio API
        # -------------------------------------------
        url = "https://api.bybit.com/v5/market/account-ratio"
        bybit_params = {
            "category": "linear",
            "symbol":   symbol,
            "period":   period,
            "limit":    limit
        }

        try:
            response = requests.get(url, params=bybit_params, timeout=10)
            raw = response.json()

            # -------------------------------------------
            # 3. Parse and calculate ratio
            # -------------------------------------------
            records = []
            for item in raw.get("result", {}).get("list", []):
                buy_ratio  = float(item["buyRatio"])
                sell_ratio = float(item["sellRatio"])
                records.append({
                    "time":       int(item["timestamp"]) // 1000,
                    "longRatio":  round(buy_ratio * 100, 2),   # as %
                    "shortRatio": round(sell_ratio * 100, 2),  # as %
                    # Ratio > 1 means more longs, < 1 means more shorts
                    "lsRatio":    round(buy_ratio / max(sell_ratio, 0.0001), 4)
                })

            records.reverse()

            result = {
                "symbol": symbol,
                "period": period,
                "count":  len(records),
                "data":   records,
                "source": "Bybit"
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