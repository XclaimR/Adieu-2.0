"""
Tiny dev server for the Adieu chat UI. Standard library only, no installs.

- Serves index.html (and any other static files in this folder)
- Proxies /api/* to the FastAPI backend, so the backend needs no CORS changes

Usage:
    python3 serve.py                      # UI on http://localhost:5173
    BACKEND_URL=http://localhost:8000 PORT=5173 python3 serve.py
"""
import os
import urllib.error
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
PORT = int(os.getenv("PORT", "5173"))
HERE = os.path.dirname(os.path.abspath(__file__))


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=HERE, **kwargs)

    def _proxy(self):
        target = BACKEND_URL + self.path[len("/api"):]
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(target, data=body, method=self.command)
        req.add_header("Content-Type", self.headers.get("Content-Type", "application/json"))
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                status, data = resp.status, resp.read()
                ctype = resp.headers.get("Content-Type", "application/json")
        except urllib.error.HTTPError as e:
            status, data = e.code, e.read()
            ctype = e.headers.get("Content-Type", "text/plain")
        except urllib.error.URLError as e:
            status = 502
            data = f"Could not reach backend at {BACKEND_URL}: {e.reason}".encode()
            ctype = "text/plain"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._proxy()
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith("/api/"):
            return self._proxy()
        self.send_error(404)


if __name__ == "__main__":
    print(f"Adieu UI on http://localhost:{PORT}  (proxying /api -> {BACKEND_URL})")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
