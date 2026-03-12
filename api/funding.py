from http.server import BaseHTTPRequestHandler
import requests
import json
from urllib.parse import urlparse, parse_qs
from collections import defaultdict
from datetime import datetime

class handler(BaseHTTPRequestHandler):

    def do_GET(self):

        params = parse_qs(urlparse(self.path).query)

        symbol = params.get("symbol", ["BTCUSDT"])[0]
        limit  = int(params.get("limit", ["500"])[0])

        url = "https://fapi.binance.com/fapi/v1/fundingRate"

        try:

            r = requests.get(
                url,
                params={"symbol": symbol, "limit": limit},
                timeout=10
            )

            raw = r.json()

            # ------------------------------
            # Group funding by DAY
            # ------------------------------

            daily = defaultdict(list)

            for item in raw:

                rate = float(item["fundingRate"])

                ts = int(item["fundingTime"]) // 1000

                day = ts - (ts % 86400)

                daily[day].append(rate)

            records = []

            for day in sorted(daily.keys()):

                rates = daily[day]

                avg = sum(rates) / len(rates)

                records.append({
                    "time": day,
                    "date": datetime.utcfromtimestamp(day).strftime("%Y-%m-%d"),
                    "fundingRate": round(avg, 6),
                    "annualized": round(avg * 3 * 365 * 100, 4),
                    "sentiment":
                        "Greed" if avg > 0.001
                        else "Fear" if avg < -0.0001
                        else "Neutral"
                })

            result = {
                "symbol": symbol,
                "exchange": "Binance",
                "interval": "1D",
                "count": len(records),
                "data": records
            }

        except Exception as e:

            result = {
                "error": str(e)
            }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        self.wfile.write(json.dumps(result).encode())

    def log_message(self, format, *args):
        return