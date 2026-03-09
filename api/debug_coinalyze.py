from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json, urllib.request, time
from datetime import datetime, timezone

API_KEY      = "bc2a8a10-1e81-4287-a504-7e6f90650be9"
PERP_SYMBOLS = "BTCUSDT_PERP.A,BTCUSDT_PERP.3,BTCUSDT_PERP.2,BTCUSD_PERP.0"
SPOT_SYMBOLS = "BTCUSDT.6,BTCUSD.4"

def ca_get(path, params):
    url = "https://api.coinalyze.net/v1/" + path + "?" + "&".join(
        str(k) + "=" + str(v) for k, v in params.items()
    )
    req = urllib.request.Request(url, headers={"api_key": API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read()
            return {"status": r.status, "url": url, "response": json.loads(body)[:2] if isinstance(json.loads(body), list) else json.loads(body)}
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        return {"status": e.code, "url": url, "error": e.reason, "body": body}
    except Exception as e:
        return {"status": 0, "url": url, "error": str(e)}

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        now = int(time.time())
        frm = now - 86400 * 30  # 30 days back

        results = {}

        # Test 1: funding-rate-history with daily interval
        results["test1_funding_daily"] = ca_get("funding-rate-history", {
            "symbols": "BTCUSDT_PERP.A",
            "interval": "D",
            "from": frm,
            "to": now
        })

        # Test 2: funding-rate-history with convert_rate
        results["test2_funding_convert"] = ca_get("funding-rate-history", {
            "symbols": "BTCUSDT_PERP.A",
            "interval": "D",
            "from": frm,
            "to": now,
            "convert_rate": "true"
        })

        # Test 3: funding-rate-history hourly
        results["test3_funding_hourly"] = ca_get("funding-rate-history", {
            "symbols": "BTCUSDT_PERP.A",
            "interval": "60",
            "from": now - 86400 * 3,
            "to": now
        })

        # Test 4: ohlcv spot
        results["test4_ohlcv_spot"] = ca_get("ohlcv-history", {
            "symbols": "BTCUSDT.6",
            "interval": "D",
            "from": frm,
            "to": now
        })

        # Test 5: multi symbol funding
        results["test5_multi_funding"] = ca_get("funding-rate-history", {
            "symbols": PERP_SYMBOLS,
            "interval": "D",
            "from": frm,
            "to": now
        })

        # Test 6: check what endpoints exist
        results["test6_single_no_dates"] = ca_get("funding-rate-history", {
            "symbols": "BTCUSDT_PERP.A",
            "interval": "D"
        })

        body = json.dumps(results, indent=2).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass