#!/usr/bin/env python3
"""
service.py — minimal HTTP API exposing the optimizer (item: hosted-service path).

Stdlib only, no deps. The productized version would run this behind the Agent SDK
/ a real web host; this proves the engine works as a callable service.

  python3 service.py            # serves on http://127.0.0.1:8799
  curl -s -X POST localhost:8799/optimize \
       -H 'content-type: application/json' \
       -d '{"model":"/path/to.stl","intent":"balanced"}'
"""

import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/optimize":
            self.send_error(404, "POST /optimize")
            return
        try:
            n = int(self.headers.get("content-length", 0))
            body = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            self.send_error(400, "invalid JSON body")
            return
        model = body.get("model")
        if not model or not os.path.exists(model):
            self._json(400, {"ok": False, "error": "model path missing or not found"})
            return
        cmd = [
            "python3",
            f"{HERE}/optimize.py",
            model,
            "--intent",
            body.get("intent", "balanced"),
            "--no-slice",
        ]
        if body.get("printer"):
            cmd += ["--printer", body["printer"]]
        if body.get("community"):
            cmd += ["--community", body["community"]]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            self._json(200, {"ok": r.returncode == 0, "report": r.stdout + r.stderr})
        except subprocess.TimeoutExpired:
            self._json(504, {"ok": False, "error": "optimize timed out"})

    def do_GET(self):
        if self.path in ("/", "/health"):
            self._json(
                200, {"ok": True, "service": "orca-optimizer", "post": "/optimize"}
            )
        else:
            self.send_error(404)

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8799"))
    print(
        f"orca-optimizer API → http://127.0.0.1:{port}/optimize  (POST {{model,intent}})"
    )
    HTTPServer(("127.0.0.1", port), Handler).serve_forever()
