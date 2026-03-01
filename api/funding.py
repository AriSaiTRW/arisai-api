# api/funding.py
# -------------------------------------------------------
# AriSai Quant — Funding Rate Endpoint
# Source: Bybit Public API (no key needed)
# Endpoint: GET /api/funding?symbol=BTCUSDT
# -------------------------------------------------------

from http.server import BaseHTTPRequestHandler
import requests
import json
from urllib.parse import urlparse, parse_qs


class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        # -------------------------------------------
        # 1. Read query parameters from the URL
        # e.g. /api/funding?symbol=BTCUSDT&limit=200
        # -------------------------------------------
        params = parse_qs(urlparse(self.path).query)
        symbol = params.get("symbol", ["BTCUSDT"])[0]
        limit  = params.get("limit",  ["200"])[0]

        # -------------------------------------------
        # 2. Call Bybit's public funding rate API
        # No API key required
        # -------------------------------------------
        url = "https://api.bybit.com/v5/market/funding/history"
        bybit_params = {
            "category": "linear",
            "symbol":   symbol,
            "limit":    limit
        }

        try:
            response = requests.get(url, params=bybit_params, timeout=10)
            raw = response.json()

            # -------------------------------------------
            # 3. Parse and clean the data
            # -------------------------------------------
            records = []
            for item in raw.get("result", {}).get("list", []):
                funding_rate = float(item["fundingRate"])
                records.append({
                    # Convert ms timestamp to seconds for JavaScript
                    "time":          int(item["fundingRateTimestamp"]) // 1000,
                    "fundingRate":   round(funding_rate, 6),
                    # Annualized = rate × 3 payments/day × 365 days × 100%
                    "annualized":    round(funding_rate * 3 * 365 * 100, 4),
                    # Simple sentiment label
                    "sentiment":     "Greed" if funding_rate > 0.001
                                     else "Fear" if funding_rate < -0.0001
                                     else "Neutral"
                })

            # Reverse so oldest first (better for charts)
            records.reverse()

            result = {
                "symbol": symbol,
                "count":  len(records),
                "data":   records,
                "source": "Bybit"
            }

        except Exception as e:
            result = {"error": str(e), "source": "Bybit"}

        # -------------------------------------------
        # 4. Send the response as JSON
        # -------------------------------------------
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    def log_message(self, format, *args):
        pass  # Suppress default server logs