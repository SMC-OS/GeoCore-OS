"""Measure how long a Stripe subscription read can take under failure, bounded client vs SDK defaults.

Uses a LOCAL fake Stripe HTTP server (no network, no credentials). Reports total elapsed seconds per failure mode.
Usage: python scripts/qa/measure_stripe_read_bounds.py [--with-default]   (--with-default waits about 4 minutes)
"""
import json, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
import stripe
from app.billing.service import _build_bounded_read_client

MODE = {"m": "ok", "hits": 0}
SUB = {"id": "sub_x", "object": "subscription", "status": "active", "metadata": {}}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        MODE["hits"] += 1
        m = MODE["m"]
        if m == "hang":
            time.sleep(400)  # never answers within any client timeout
        if m == "429":
            body, code = {"error": {"type": "invalid_request_error", "code": "rate_limit", "message": "slow down"}}, 429
        elif m == "503":
            body, code = {"error": {"type": "api_error", "message": "temporary"}}, 503
        else:
            body, code = SUB, 200
        data = json.dumps(body).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(data)))
        if code == 429: self.send_header("Stripe-Should-Retry", "true")
        self.end_headers(); self.wfile.write(data)


srv = ThreadingHTTPServer(("127.0.0.1", 0), H); srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{srv.server_address[1]}"


def run(label, client):
    t = time.time(); MODE["hits"] = 0
    try:
        client.v1.subscriptions.retrieve("sub_x"); out = "ok"
    except Exception as e:
        out = type(e).__name__
    print(f"{label:34s} outcome={out:22s} elapsed={time.time()-t:7.2f}s attempts={MODE['hits']}", flush=True)


bounded = _build_bounded_read_client(stripe, "sk_test_local", base_addresses={"api": BASE})
for mode in ("ok", "429", "503", "hang"):
    MODE["m"] = mode
    run(f"bounded client, server={mode}", bounded)
if "--with-default" in sys.argv:
    default = stripe.StripeClient("sk_test_local", base_addresses={"api": BASE})
    for mode in ("429", "503", "hang"):
        MODE["m"] = mode
        run(f"SDK default client, server={mode}", default)
