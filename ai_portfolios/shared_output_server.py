"""Local read-only web view over the independent Claude and Codex team exports."""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .peer_review import read_discussions
from .team_exports import read_all_teams

STATIC = Path(__file__).resolve().parent / "shared_static"


def create_server(port=8790):
    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, content, content_type="application/json; charset=utf-8"):
            if isinstance(content, str):
                content = content.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; connect-src 'self'; style-src 'self'; script-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            if self.client_address[0] not in {"127.0.0.1", "::1"}:
                self.reply(403, "Local host only")
                return
            path = urlsplit(self.path).path
            static = {"/": ("index.html", "text/html; charset=utf-8"),
                      "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                      "/style.css": ("style.css", "text/css; charset=utf-8")}
            if path in static:
                filename, mime = static[path]
                self.reply(200, (STATIC / filename).read_bytes(), mime)
            elif path == "/api/teams":
                try:
                    result = read_all_teams()
                    result["discussions"] = read_discussions()
                    self.reply(200, json.dumps(result, ensure_ascii=False))
                except (ValueError, OSError) as exc:
                    self.reply(503, json.dumps({"error": str(exc)[:200]}, ensure_ascii=False))
            else:
                self.reply(404, "Not found")

        def do_POST(self):
            self.reply(405, "Read-only dashboard")

        def log_message(self, format, *args):
            return

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8790)
    args = parser.parse_args()
    server = create_server(args.port)
    print(f"Shared Paper output: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
