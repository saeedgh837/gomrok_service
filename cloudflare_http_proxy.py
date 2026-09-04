import base64
import importlib.util
import os
import select
import socket
import socketserver
import struct
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import app_paths  # noqa: E402  (needs HERE on sys.path first)

# 1) manba'-e ASLI: file dar poshe-ye dade (birun az release; dar git nemiravad)
LOCAL_UPSTREAM_CONFIG = app_paths.data_path("upstream_proxy.json")
# 2) fallback-e ghadimi (mahalli — agar file-e proje nabud)
UPSTREAM_CONFIG = r"D:\browser-agent\work-with-whatsapp\config.py"
LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 18081


def load_upstream_config():
    """(host, port, user, pass) — avval az upstream_proxy.json-e proje,
    vagarna az config-e ghadimi-ye biroon-e proje."""
    if os.path.isfile(LOCAL_UPSTREAM_CONFIG):
        import json
        with open(LOCAL_UPSTREAM_CONFIG, encoding="utf-8") as handle:
            cfg = json.load(handle) or {}
        host = str(cfg.get("host") or "").strip()
        port = int(cfg.get("port") or 0)
        if host and port:
            return host, port, str(cfg.get("username") or ""), str(cfg.get("password") or "")
    spec = importlib.util.spec_from_file_location("cloudflare_upstream_config", UPSTREAM_CONFIG)
    if spec is None or spec.loader is None:
        raise FileNotFoundError(
            f"upstream proxy config peyda nashod: {LOCAL_UPSTREAM_CONFIG} "
            f"(ya {UPSTREAM_CONFIG})")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return (
        module.UPSTREAM_HOST,
        int(module.UPSTREAM_PORT),
        module.UPSTREAM_USER,
        module.UPSTREAM_PASS,
    )


def recv_exact(sock, size):
    chunks = []
    while sum(map(len, chunks)) < size:
        chunk = sock.recv(size - sum(map(len, chunks)))
        if not chunk:
            raise ConnectionError("socket closed")
        chunks.append(chunk)
    return b"".join(chunks)


def open_socks_connection(host, port):
    upstream_host, upstream_port, username, password = load_upstream_config()
    sock = socket.create_connection((upstream_host, upstream_port), timeout=20)
    sock.sendall(b"\x05\x01\x02")
    if recv_exact(sock, 2) != b"\x05\x02":
        sock.close()
        raise ConnectionError("upstream SOCKS auth method rejected")
    user, secret = username.encode(), password.encode()
    sock.sendall(b"\x01" + bytes([len(user)]) + user + bytes([len(secret)]) + secret)
    if recv_exact(sock, 2) != b"\x01\x00":
        sock.close()
        raise ConnectionError("upstream SOCKS authentication failed")
    host_bytes = host.encode("idna")
    sock.sendall(b"\x05\x01\x00\x03" + bytes([len(host_bytes)]) + host_bytes + struct.pack("!H", port))
    reply = recv_exact(sock, 4)
    if reply[1] != 0:
        sock.close()
        raise ConnectionError(f"upstream SOCKS connect failed: {reply[1]}")
    atyp = reply[3]
    if atyp == 1:
        recv_exact(sock, 4)
    elif atyp == 3:
        recv_exact(sock, recv_exact(sock, 1)[0])
    elif atyp == 4:
        recv_exact(sock, 16)
    recv_exact(sock, 2)
    return sock


class HttpConnectHandler(socketserver.BaseRequestHandler):
    def handle(self):
        upstream = None
        try:
            request = b""
            while b"\r\n\r\n" not in request and len(request) < 65536:
                chunk = self.request.recv(4096)
                if not chunk:
                    return
                request += chunk
            line = request.split(b"\r\n", 1)[0].decode("latin1")
            method, target, _version = line.split(" ", 2)
            if method.upper() != "CONNECT":
                self.request.sendall(b"HTTP/1.1 405 Method Not Allowed\r\nConnection: close\r\n\r\n")
                return
            host, port_text = target.rsplit(":", 1)
            upstream = open_socks_connection(host, int(port_text))
            self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            # MOHEM: socket-ha BLOCKING mimanand. ghablan setblocking(False) bood
            # va sendall roo-ye socket-e non-blocking baraye body-haye bozorg
            # (upload-e multipart) BlockingIOError midad -> connection ghat
            # mishod va relay 502 barmigardand. select faghat baraye readiness.
            self.request.settimeout(None)
            upstream.settimeout(None)
            sockets = [self.request, upstream]
            while True:
                readable, _, errors = select.select(sockets, [], sockets, 300)
                if errors or not readable:
                    return
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    (upstream if source is self.request else self.request).sendall(data)
        except Exception:
            try:
                self.request.sendall(b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
        finally:
            if upstream:
                upstream.close()

    def log_message(self, *_args):
        return


class RelayServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_server():
    server = RelayServer((LISTEN_HOST, LISTEN_PORT), HttpConnectHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    server = start_server()
    print(f"HTTP CONNECT relay: http://{LISTEN_HOST}:{LISTEN_PORT}", flush=True)
    threading.Event().wait()
