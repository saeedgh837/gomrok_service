"""Restricted reverse proxy from Cloudflare Tunnel to Telegram Bot API."""
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "relay_config.json")
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 8752
TELEGRAM_ORIGIN = "https://api.telegram.org"
DEFAULT_OUTBOUND_PROXY = "http://127.0.0.1:18081"
MAX_BODY_BYTES = 55 * 1024 * 1024
BOT_PATH_RE = re.compile(r"^/bot[0-9]+:[A-Za-z0-9_-]+/[A-Za-z][A-Za-z0-9_]*$")


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as handle:
            config = json.load(handle)
    except Exception:
        config = {}
    return {
        "outbound_proxy": str(
            config.get("telegram_outbound_proxy") or DEFAULT_OUTBOUND_PROXY
        ).strip(),
    }


class TelegramRelayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # One persistent Session: reuses the CONNECT tunnel through the local HTTP
    # proxy so each poll doesn't open a fresh SOCKS connection upstream (the
    # upstream relay drops connections under churn, surfacing as ProxyError).
    _session = requests.Session()
    _session.trust_env = False

    def _send(self, status, body, content_type="application/json; charset=utf-8"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _proxy(self):
        parsed = urlsplit(self.path)
        if not BOT_PATH_RE.fullmatch(parsed.path):
            self._send(404, b'{"ok":false,"description":"not found"}')
            return

        try:
            length = int(self.headers.get("Content-Length", "0") or 0)
        except ValueError:
            self._send(400, b'{"ok":false,"description":"invalid content length"}')
            return
        if length < 0 or length > MAX_BODY_BYTES:
            self._send(413, b'{"ok":false,"description":"payload too large"}')
            return

        body = self.rfile.read(length) if length else None
        target = TELEGRAM_ORIGIN + parsed.path
        if parsed.query:
            target += "?" + parsed.query
        headers = {
            key: value for key, value in self.headers.items()
            if key.lower() not in {
                "host", "connection", "proxy-connection", "content-length",
                "transfer-encoding", "cf-connecting-ip", "x-forwarded-for",
            }
        }
        config = load_config()
        proxy = config["outbound_proxy"]
        proxies = {"http": proxy, "https": proxy} if proxy else None
        # multipart/upload az masir-e SOCKS gahi vasat-e ersal ghat mishavad
        # (SSLError/ProxyError). baraye body-haye bozorg retry-e bishtar lazem ast.
        max_attempts = 8 if (body and len(body) > 4096) else 3

        try:
            response = None
            last_error = None
            for _attempt in range(max_attempts):
                try:
                    response = self._session.request(
                        self.command,
                        target,
                        data=body,
                        headers=headers,
                        proxies=proxies,
                        timeout=(15, 75),
                        allow_redirects=False,
                    )
                    break
                except requests.RequestException as exc:
                    # Transient upstream failures (ProxyError/reset) under the
                    # shared SOCKS chain: retry with backoff before failing.
                    last_error = exc
                    self._session.close()
                    self._session = requests.Session()
                    self._session.trust_env = False
                    time.sleep(min(0.5 * (_attempt + 1), 3.0))
            if response is None:
                raise last_error
            response_headers = {
                key: value for key, value in response.headers.items()
                if key.lower() not in {
                    "connection", "transfer-encoding", "content-length",
                    "content-encoding", "server", "date",
                }
            }
            self.send_response(response.status_code)
            for key, value in response_headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(response.content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(response.content)
        except requests.RequestException as exc:
            payload = json.dumps({
                "ok": False,
                "description": f"telegram upstream unavailable: {type(exc).__name__}",
            }).encode("utf-8")
            self._send(502, payload)

    do_GET = _proxy
    do_POST = _proxy
    do_HEAD = _proxy

    def log_message(self, _format, *_args):
        return


def main():
    load_config()
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), TelegramRelayHandler)
    print(
        f"Telegram API relay: http://{LISTEN_HOST}:{LISTEN_PORT} -> {TELEGRAM_ORIGIN}",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
