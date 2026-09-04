import base64
import hmac
import http.client
import json
import os
import secrets
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import app_paths  # noqa: E402  (needs HERE on sys.path first)

CONFIG_PATH = app_paths.data_path("relay_config.json")
UPSTREAM_HOST = "127.0.0.1"
UPSTREAM_PORT = 8750
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8751


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as handle:
        config = json.load(handle)
    username = str(config.get("username") or "").strip()
    password = str(config.get("password") or "")
    if not username or not password:
        raise ValueError("relay username/password is missing")
    return username, password


def expected_authorization():
    username, password = load_config()
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return f"Basic {token}"


class RelayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _authorized(self):
        supplied = self.headers.get("Authorization", "")
        return hmac.compare_digest(supplied, expected_authorization())

    def _deny(self):
        body = "دسترسی به پنل نیازمند نام کاربری و رمز عبور است.".encode("utf-8")
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="IRICA Panel", charset="UTF-8"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _proxy(self):
        if not self._authorized():
            self._deny()
            return

        parsed = urlsplit(self.path)
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        length = int(self.headers.get("Content-Length", "0") or 0)
        body = self.rfile.read(length) if length else None
        headers = {
            key: value for key, value in self.headers.items()
            if key.lower() not in {"host", "authorization", "connection", "proxy-connection", "content-length"}
        }
        if body is not None:
            headers["Content-Length"] = str(len(body))
        headers["Host"] = f"{UPSTREAM_HOST}:{UPSTREAM_PORT}"
        headers["X-Forwarded-Proto"] = self.headers.get("X-Forwarded-Proto", "https")

        connection = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=30)
        try:
            connection.request(self.command, target, body=body, headers=headers)
            response = connection.getresponse()
            payload = response.read()
            self.send_response(response.status, response.reason)
            for key, value in response.getheaders():
                if key.lower() not in {"connection", "transfer-encoding", "content-length", "server", "date"}:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
        except (OSError, http.client.HTTPException, socket.timeout):
            payload = b"Local panel is unavailable"
            self.send_response(502)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        finally:
            connection.close()

    do_GET = _proxy
    do_POST = _proxy
    do_PUT = _proxy
    do_PATCH = _proxy
    do_DELETE = _proxy
    do_HEAD = _proxy

    def log_message(self, _format, *_args):
        return


def main():
    load_config()
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), RelayHandler)
    print(f"Authenticated relay: http://{LISTEN_HOST}:{LISTEN_PORT} -> http://{UPSTREAM_HOST}:{UPSTREAM_PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
