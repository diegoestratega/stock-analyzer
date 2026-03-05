from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json

from market_data import get_analysis

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        ticker = (qs.get("ticker", [""])[0] or "").upper().strip()
        debug = (qs.get("debug", ["0"])[0] or "0").strip() in ("1", "true", "yes")

        if not ticker:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "ticker is required"}).encode("utf-8"))
            return

        try:
            data = get_analysis(ticker, debug=debug)
            if not data:
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": f"could not analyze {ticker}"}).encode("utf-8"))
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode("utf-8"))

        except Exception as e:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(e)}).encode("utf-8"))
