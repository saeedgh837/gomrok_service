import base64
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# The interpreter running this script: the bundled python.exe in a release, the
# developer's python during development. A hardcoded path breaks the release on
# any machine without that exact install (see panel._resolve_robot_python).
PYTHON = sys.executable
CLOUDFLARED = os.path.join(HERE, ".tools", "cloudflared.exe")
AUTH_RELAY = os.path.join(HERE, "cloudflare_auth_relay.py")
HTTP_PROXY_RELAY = os.path.join(HERE, "cloudflare_http_proxy.py")
URL_PATH = os.path.join(HERE, "cloudflare_public_url.txt")
LOG_PATH = os.path.join(HERE, "cloudflare_tunnel.log")
CONFIG_PATH = os.path.join(HERE, "relay_config.json")
QUICK_TUNNEL_API = "https://api.trycloudflare.com/tunnel"


def require_file(path):
    if not os.path.isfile(path):
        raise FileNotFoundError(path)


def wait_http(url, expected_status, timeout=15):
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = urllib.request.urlopen(url, timeout=2)
            if response.status == expected_status:
                return
        except urllib.error.HTTPError as exc:
            if exc.code == expected_status:
                return
        except OSError:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"service did not become ready: {url}")


def start_process(command):
    return subprocess.Popen(
        command,
        cwd=HERE,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def request_quick_tunnel():
    import urllib.request

    proxy = urllib.request.ProxyHandler({
        "http": "http://127.0.0.1:18081",
        "https": "http://127.0.0.1:18081",
    })
    opener = urllib.request.build_opener(proxy)
    request = urllib.request.Request(QUICK_TUNNEL_API, data=b"", method="POST")
    with opener.open(request, timeout=30) as response:
        payload = json.load(response)
    if not payload.get("success"):
        raise RuntimeError("Cloudflare quick tunnel API returned an error")
    return payload["result"]


def make_token(result):
    payload = {"a": result["account_tag"], "t": result["id"], "s": result["secret"]}
    return base64.b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode()


def wait_registered(process, timeout=60):
    deadline = time.time() + timeout
    position = 0
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"cloudflared exited with code {process.returncode}")
        with open(LOG_PATH, encoding="utf-8", errors="ignore") as handle:
            handle.seek(position)
            chunk = handle.read()
            position = handle.tell()
        if "Registered tunnel connection" in chunk:
            return
        time.sleep(0.25)
    raise TimeoutError("Cloudflare tunnel connection was not registered")


def terminate(process):
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def main():
    for path in (PYTHON, CLOUDFLARED, AUTH_RELAY, HTTP_PROXY_RELAY, CONFIG_PATH):
        require_file(path)

    auth_relay = http_proxy = tunnel = None
    token_path = ""
    try:
        try:
            wait_http("http://127.0.0.1:8751/", 401, timeout=1)
        except RuntimeError:
            auth_relay = start_process([PYTHON, "-u", AUTH_RELAY])
            wait_http("http://127.0.0.1:8751/", 401)

        try:
            wait_http("http://127.0.0.1:18081/", 405, timeout=1)
        except RuntimeError:
            http_proxy = start_process([PYTHON, "-u", HTTP_PROXY_RELAY])
            time.sleep(1)

        result = request_quick_tunnel()
        public_url = "https://" + result["hostname"]
        token = make_token(result)
        token_handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="ascii", prefix="irica-cloudflare-", suffix=".token", delete=False
        )
        token_path = token_handle.name
        token_handle.write(token)
        token_handle.close()

        env = os.environ.copy()
        env["HTTP_PROXY"] = "http://127.0.0.1:18081"
        env["HTTPS_PROXY"] = "http://127.0.0.1:18081"
        with open(LOG_PATH, "w", encoding="utf-8") as log_handle:
            tunnel = subprocess.Popen(
                [CLOUDFLARED, "tunnel", "--no-autoupdate", "--protocol", "http2",
                 "--url", "http://127.0.0.1:8751", "run", "--token-file", token_path],
                cwd=HERE,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            wait_registered(tunnel)
            with open(URL_PATH, "w", encoding="utf-8") as handle:
                handle.write(public_url + "\n")
            print("Cloudflare URL:", public_url, flush=True)
            print("Credentials: relay_config.json", flush=True)
            print("Stop: Ctrl+C", flush=True)
            tunnel.wait()
    except KeyboardInterrupt:
        pass
    finally:
        terminate(tunnel)
        terminate(http_proxy)
        terminate(auth_relay)
        if token_path:
            try:
                os.remove(token_path)
            except OSError:
                pass


if __name__ == "__main__":
    main()
