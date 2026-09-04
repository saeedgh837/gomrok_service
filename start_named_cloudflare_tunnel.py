import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
# The interpreter running this script: the bundled python.exe in a release, the
# developer's python during development. A hardcoded path breaks the release on
# any machine without that exact install (see panel._resolve_robot_python).
PYTHON = sys.executable
CLOUDFLARED = os.path.join(HERE, ".tools", "cloudflared.exe")
AUTH_RELAY = os.path.join(HERE, "cloudflare_auth_relay.py")
CONFIG = os.path.join(HERE, "cloudflare_named_tunnel.yml")
LOG = os.path.join(HERE, "cloudflare_named_tunnel.log")


def wait_relay(url, expected_status, timeout=15):
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            response = urllib.request.urlopen(url, timeout=2)
            if response.status == expected_status:
                return True
        except urllib.error.HTTPError as exc:
            if exc.code == expected_status:
                return True
        except OSError:
            pass
        time.sleep(0.25)
    return False


def wait_auth_relay(timeout=15):
    return wait_relay("http://127.0.0.1:8751/", 401, timeout)


def start_hidden(command):
    return subprocess.Popen(
        command,
        cwd=HERE,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def terminate(process):
    if process and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def main():
    for path in (PYTHON, CLOUDFLARED, AUTH_RELAY, CONFIG):
        if not os.path.isfile(path):
            raise FileNotFoundError(path)

    relay = None
    tunnel = None
    try:
        if not wait_auth_relay(timeout=1):
            relay = start_hidden([PYTHON, "-u", AUTH_RELAY])
            if not wait_auth_relay():
                raise RuntimeError("authenticated relay did not start")
        env = os.environ.copy()
        env["TUNNEL_PROTOCOL"] = "http2"
        with open(LOG, "w", encoding="utf-8") as log:
            tunnel = subprocess.Popen(
                [CLOUDFLARED, "tunnel", "--config", CONFIG, "run"],
                cwd=HERE,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            print("Panel URL: https://register.techbysaeed.ir", flush=True)
            print("Panel credentials: relay_config.json", flush=True)
            print("Stop: Ctrl+C", flush=True)
            tunnel.wait()
    except KeyboardInterrupt:
        pass
    finally:
        terminate(tunnel)
        terminate(relay)


if __name__ == "__main__":
    main()
