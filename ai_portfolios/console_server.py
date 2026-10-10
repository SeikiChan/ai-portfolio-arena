"""Local-only Paper account and AI transcript console. No broker write endpoints."""
from __future__ import annotations

import argparse
import json
import secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from .common import APP
from .console_store import ConsoleStore
from .team_exports import read_all_teams, read_gpt_export

STATIC = APP / "console_static"


def create_server(store, port=8788):
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            # Avoid persisting user prompts, secrets, or query values in access logs.
            pass

        def allowed_host(self):
            return self.headers.get("Host") in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

        def reply(self, status, value, mime="application/json; charset=utf-8"):
            content = json.dumps(value, ensure_ascii=False).encode("utf-8") if mime.startswith("application/json") else value
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self):
            if not self.allowed_host():
                self.reply(403, {"error": "Local host only"})
                return
            parsed = urlsplit(self.path)
            static = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"), "/style.css": ("style.css", "text/css; charset=utf-8")}
            try:
                if parsed.path in static:
                    filename, mime = static[parsed.path]
                    self.reply(200, (STATIC / filename).read_bytes(), mime)
                elif parsed.path == "/api/state":
                    self.reply(200, {**store.state(), "csrf_token": token})
                elif parsed.path == "/api/teams/gpt":
                    self.reply(200, read_gpt_export())
                elif parsed.path == "/api/teams":
                    self.reply(200, read_all_teams())
                elif parsed.path == "/api/record":
                    identity = parse_qs(parsed.query).get("id", [""])[0]
                    self.reply(200, store.detail(identity))
                else:
                    self.reply(404, {"error": "Not found"})
            except (ValueError, OSError):
                self.reply(400, {"error": "记录暂不可读，请刷新或查看本地日志"})

        def do_POST(self):
            origin = self.headers.get("Origin")
            valid_origins = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
            if not self.allowed_host() or self.headers.get("X-Console-Token") != token or (origin is not None and origin not in valid_origins):
                self.reply(403, {"error": "Request not authorized for this local console"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 150000 or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                    raise ValueError("Invalid JSON request size/type")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("请求必须为 JSON 对象")
                if self.path == "/api/prompts":
                    self.reply(200, {"prompts": store.save_prompts(payload)})
                elif self.path == "/api/run":
                    if 'gpt' in payload.get('providers', []):
                        raise ValueError('GPT 已迁移独立 worktree；共享网站只展示其结果，请在独立目录运行')
                    self.reply(202, {"id": store.start(payload.get("providers"), payload.get("prompts", {}))})
                elif self.path == "/api/refresh":
                    self.reply(200, {"accounts": store.refresh()})
                else:
                    self.reply(404, {"error": "Not found"})
            except (ValueError, TypeError, KeyError) as exc:
                self.reply(400, {"error": str(exc)})
            except OSError:
                self.reply(500, {"error": "本地文件写入失败"})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8788)
    args = parser.parse_args()
    store = ConsoleStore()
    server = create_server(store, args.port)
    try:
        store.refresh()
    except (ValueError, OSError) as exc:
        print("Account connection: " + str(exc), flush=True)
    print(f"Paper console: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
