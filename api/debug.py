from http.server import BaseHTTPRequestHandler
import requests
import json

class handler(BaseHTTPRequestHandler):

    def do_GET(self):
        results = {}

        # Test Binance
        try:
            r = requests.get(
                "https://fapi.binance.com/fapi/v1/fundingRate",
                params={"symbol": "BTCUSDT", "limit": "2"},
                timeout=10
            )
            results["binance"] = {"status": r.status_code, "sample": r.text[:200]}
        except Exception as e:
            results["binance"] = {"error": str(e)}

        # Test CoinMetrics - check each metric
        for name, key in [
            ("mvrv",     "CapMVRVCur"),
            ("sopr",     "SOPR"),
            ("sth_mvrv", "CapMVRVSTHCur"),
            ("realized", "CapRealUSD"),
            ("price",    "PriceUSD"),
        ]:
            try:
                r = requests.get(
                    "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics",
                    params={"assets": "btc", "metrics": key,
                            "frequency": "1d", "page_size": 2},
                    timeout=10
                )
                results[f"cm_{name}"] = {
                    "status": r.status_code,
                    "sample": r.text[:150]
                }
            except Exception as e:
                results[f"cm_{name}"] = {"error": str(e)}

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(results, indent=2).encode())

    def log_message(self, format, *args):
        pass