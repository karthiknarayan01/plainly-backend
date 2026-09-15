# Stub. Real logic (claim job via SKIP LOCKED, run the generate->judge
# ->retry loop from the design doc §09, orchestrated with LangGraph) is
# the next piece of work. This stub just proves the container deploys
# and stays up — which on Cloud Run means listening on $PORT, even for
# a service that's conceptually a background poller, not a web server.

import os
from http.server import BaseHTTPRequestHandler, HTTPServer


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args):
        pass  # keep stub logs quiet


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    print(f"plainly-worker stub running on :{port} — no jobs processed yet")
    HTTPServer(("0.0.0.0", port), HealthHandler).serve_forever()
