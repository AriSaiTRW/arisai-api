from http.server import BaseHTTPRequestHandler
import requests
import json
from urllib.parse import urlparse, parse_qs

class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        symbol = params.get("symbol", ["BTCUSDT"])[0]
        limit  = params.get("limit",  ["200"])[0]

        url = "https://fapi.binance.com/fapi/v1/fundingRate"
        p = {"symbol": symbol, "limit": limit}

        try:
            r = requests.get(url, params=p, timeout=10)
            raw = r.json()

            records = []
            for item in raw:
                rate = float(item["fundingRate"])
                records.append({
                    "time":        int(item["fundingTime"]) // 1000,
                    "fundingRate": round(rate, 6),
                    "annualized":  round(rate * 3 * 365 * 100, 4),
                    "sentiment":   "Greed"   if rate >  0.001
                                   else "Fear" if rate < -0.0001
                                   else "Neutral"
                })

            result = {
                "symbol": symbol,
                "count":  len(records),
                "data":   records,
                "source": "Binance"
            }

        except Exception as e:
            result = {"error": str(e)}

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())

    def log_message(self, format, *args):
        pass