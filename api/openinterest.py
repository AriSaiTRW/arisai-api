from http.server import BaseHTTPRequestHandler
import requests
import json
from urllib.parse import urlparse, parse_qs

class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        params   = parse_qs(urlparse(self.path).query)
        symbol   = params.get("symbol",   ["BTCUSDT"])[0]
        period   = params.get("period",   ["1h"])[0]
        limit    = params.get("limit",    ["200"])[0]

        url = "https://fapi.binance.com/futures/data/openInterestHist"
        p = {"symbol": symbol, "period": period, "limit": limit}

        try:
            r = requests.get(url, params=p, timeout=10)
            raw = r.json()

            records = []
            for item in raw:
                records.append({
                    "time":         int(item["timestamp"]) // 1000,
                    "openInterest": float(item["sumOpenInterest"]),
                    "oiValue":      float(item["sumOpenInterestValue"]),
                })

            result = {
                "symbol": symbol,
                "period": period,
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