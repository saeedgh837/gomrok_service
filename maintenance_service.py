import copy
import hmac
import re
import secrets
import threading
import time

from maintenance_gate import MaintenanceBlocked


_BUSY_APP_STATES = {"downloading", "verifying", "waiting_for_idle", "installing"}
_DRIVER_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+\Z")


class MaintenanceService:
    def __init__(self, *, application, driver_check, driver_install, admission, clock=None):
        self._application = application
        self._driver_check = driver_check
        self._driver_install = driver_install
        self._admission = admission
        self._clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._thread = None
        self._checking = False
        self._last_started = None
        self._checked_at = None
        self._offer_at = None
        self._driver = {"state": "not_checked", "message": "ChromeDriver has not been checked.",
                        "installed_version": "", "browser_version": "", "checked": False,
                        "offer": None}

    def _retire_expired_waiting_offer(self):
        if (self._driver["state"] == "waiting_for_idle" and self._offer_at is not None
                and self._clock() - self._offer_at > 900):
            self._driver.update(state="failed", offer=None,
                                message="The driver offer expired. Check again and confirm the new offer.")
            self._offer_at = None

    def status(self):
        with self._lock:
            self._retire_expired_waiting_offer()
            return {"ok": True, "app": self._application.status(),
                    "driver": copy.deepcopy(self._driver), "checking": self._checking}

    def _launch(self, target, *args):
        thread = threading.Thread(target=target, args=args, daemon=True,
                                  name="irica-maintenance")
        self._thread = thread
        thread.start()

    def check(self, force=False):
        with self._lock:
            self._retire_expired_waiting_offer()
            now = self._clock()
            if (self._checking or self._driver["state"] in {"installing", "waiting_for_idle"}
                    or self._application.status()["state"] in _BUSY_APP_STATES):
                return self.status()
            if ((self._last_started is not None and now - self._last_started < 10)
                    or (not force and self._checked_at is not None
                        and now - self._checked_at < 900)):
                return self.status()
            self._last_started, self._checking = now, True
            self._driver.update(state="checking", message="Checking ChromeDriver availability.",
                                offer=None)
            try:
                self._launch(self._run_check, force)
            except RuntimeError:
                self._checking = False
                self._driver.update(state="failed", checked=True,
                                    message="The update check could not start.")
            return self.status()

    def _run_check(self, force):
        try:
            self._application.check(force=force)
            result = self._driver_check()
            driver = self._checked_driver(result)
        except Exception:
            driver = {"state": "failed", "checked": True, "offer": None,
                      "installed_version": "", "browser_version": "",
                      "message": "The update check failed. Check the connection and retry."}
        with self._lock:
            self._driver = driver
            self._checked_at = self._clock()
            self._offer_at = self._checked_at if driver["offer"] else None
            self._checking = False

    @staticmethod
    def _checked_driver(result):
        failed = {"state": "failed", "checked": True, "offer": None,
                  "installed_version": "", "browser_version": "",
                  "message": "ChromeDriver availability could not be verified. Check again."}
        if not isinstance(result, dict) or result.get("ok") is not True:
            return failed
        chrome, driver = result.get("chrome"), result.get("chromedriver")
        if not isinstance(chrome, dict) or not isinstance(driver, dict):
            return failed
        browser = chrome.get("installed_version")
        current, desired = driver.get("installed_version"), driver.get("matched_version")
        if (not isinstance(browser, str) or len(browser) > 64
                or not _DRIVER_VERSION.fullmatch(browser)
                or not isinstance(desired, str) or len(desired) > 64
                or not _DRIVER_VERSION.fullmatch(desired)):
            return failed
        desired_key = tuple(map(int, desired.split(".")))
        if desired_key[0] != int(browser.split(".")[0]):
            return failed
        current_key = (tuple(map(int, current.split(".")))
                       if isinstance(current, str) and len(current) <= 64
                       and _DRIVER_VERSION.fullmatch(current) else None)
        if current_key is not None and current_key > desired_key:
            return failed
        base = {"state": "available", "checked": True,
                "message": "A ChromeDriver update is available. Confirmation is required.",
                "installed_version": current if current_key is not None else "",
                "browser_version": browser,
                "offer": {"offer_id": secrets.token_hex(24), "version": desired}}
        if current_key == desired_key:
            base.update(state="current", offer=None,
                        message="ChromeDriver matches the latest build for installed Chrome.")
        return base

    def confirm_app(self, offer_id):
        with self._lock:
            self._retire_expired_waiting_offer()
            if self._checking or self._driver["state"] in {"installing", "waiting_for_idle"}:
                return {"ok": False, "errors": ["Finish the other maintenance operation first."],
                        "app": self._application.status()}
            result = self._application.confirm(offer_id)
            ok = (result["state"] != "failed" and
                  result.get("code") != "manual_install_required")
            return {"ok": ok, "app": result}

    def confirm_driver(self, offer_id, *, consent=False):
        with self._lock:
            self._retire_expired_waiting_offer()
            offer = self._driver.get("offer")
            if (consent is not True or not isinstance(offer_id, str) or not offer_id.isascii()
                    or not offer or not hmac.compare_digest(offer_id, offer["offer_id"])
                    or self._offer_at is None or self._clock() - self._offer_at > 900):
                return {"ok": False, "errors": ["Check again and confirm the current driver offer."],
                        "driver": copy.deepcopy(self._driver)}
            if self._driver["state"] == "installing":
                return {"ok": True, "driver": copy.deepcopy(self._driver)}
            if (self._checking or self._application.status()["state"] in _BUSY_APP_STATES
                    or self._driver["state"] not in {"available", "waiting_for_idle"}):
                return {"ok": False, "errors": ["Finish the other maintenance operation first."],
                        "driver": copy.deepcopy(self._driver)}
            self._driver.update(state="installing", message="Installing the confirmed ChromeDriver build.")
            try:
                self._launch(self._run_driver_install, offer["version"])
            except RuntimeError:
                self._driver.update(state="failed", offer=None,
                                    message="The ChromeDriver update could not start.")
                return {"ok": False, "driver": copy.deepcopy(self._driver)}
            return {"ok": True, "driver": copy.deepcopy(self._driver)}

    def _run_driver_install(self, version):
        state, message = "failed", "ChromeDriver installation failed. Check again before retrying."
        installed = None
        try:
            with self._admission():
                result = self._driver_install(consent=True, desired_version=version)
            if isinstance(result, dict):
                if result.get("status") in {"updated", "up-to-date"} and result.get("version") == version:
                    state, message, installed = "current", "The confirmed ChromeDriver build is installed.", version
                elif result.get("status") in {"busy", "driver-in-use"}:
                    state, message = "waiting_for_idle", "Finish active work, then retry the confirmed update."
        except MaintenanceBlocked:
            state, message = "waiting_for_idle", "Installation is blocked. Finish active work and retry."
        except Exception:
            pass
        with self._lock:
            self._driver.update(state=state, message=message)
            if installed is not None:
                self._driver["installed_version"] = installed
            if state != "waiting_for_idle":
                self._driver["offer"] = None
                self._offer_at = None


def check_service_health(*, cloud_url=None, telegram=None, proxy=None, session=None):
    import requests
    from cloud_sync.config import normalize_api_base
    from cloud_sync.errors import ConfigError

    telegram, proxy = telegram or {}, proxy or {}
    owned = session is None
    session = session or requests.Session()
    session.trust_env = False
    session.auth = session.cert = None
    session.cookies.clear()
    session.proxies.clear()
    session.params.clear()
    steps = []

    def probe(identifier, name, url, scope, proxies=None, telegram_probe=False):
        row = {"id": identifier, "name": name, "scope": scope, "readiness": "unknown"}
        if not url:
            row.update(status="not_configured", message="This service is not configured.")
            return row
        try:
            url = normalize_api_base(url)
        except (ConfigError, ValueError, TypeError):
            row.update(status="unknown", message="The configured service endpoint is invalid.")
            return row
        started = time.monotonic()
        response = None
        try:
            response = session.get(url, proxies=proxies or {}, timeout=(3, 5),
                                   allow_redirects=False, verify=True, stream=True)
            row["latency_ms"] = int((time.monotonic() - started) * 1000)
            status = response.status_code
            if status in (401, 403):
                row.update(status="auth_denied", message="The endpoint denied this unauthenticated transport probe.")
            elif status == 200:
                row.update(status="reachable" if telegram_probe else "ok",
                           message=("Transport is reachable; bot access and message delivery were not tested."
                                    if telegram_probe else "Transport is reachable; application and database readiness were not tested."))
            elif telegram_probe and status in (400, 404, 405):
                row.update(status="reachable", message="Transport responded; bot access and message delivery were not tested.")
            else:
                row.update(status="error", message="The endpoint returned an unexpected transport response.")
        except requests.Timeout:
            row.update(status="timeout", message="The service transport probe timed out.")
        except requests.RequestException:
            row.update(status="error", message="The service transport could not be reached.")
        finally:
            if response is not None:
                try:
                    response.close()
                except requests.RequestException:
                    row.update(status="error", message="The service transport response could not be closed safely.")
        return row

    try:
        cloud_probe = str(cloud_url).rstrip("/") + "/v1/health/live" if cloud_url else None
        steps.append(probe("cloud", "Cloud service", cloud_probe,
                           "HTTP liveness only; no authentication or database test."))
        mode = str(telegram.get("mode") or "").lower()
        if mode == "host":
            endpoint, routes = telegram.get("relay_url"), {}
        elif mode in {"direct", "proxy"}:
            endpoint = telegram.get("api_base") or "https://api.telegram.org"
            route = telegram.get("proxy_url") if mode == "proxy" else None
            routes = {"http": route, "https": route} if route else {}
        else:
            endpoint, routes = None, {}
        steps.append(probe("telegram", "Telegram transport", endpoint,
                           "Unauthenticated transport only; no token, secret or message sent.",
                           proxies=routes, telegram_probe=True))
        route = proxy.get("proxy_url") if proxy.get("enabled") else None
        steps.append(probe("proxy", "Optional proxy",
                           "https://googlechromelabs.github.io" if route else None,
                           "HTTPS transport through the configured optional proxy.",
                           proxies={"http": route, "https": route} if route else {}))
    finally:
        if owned:
            session.close()
    failures = {"error", "timeout", "auth_denied"}
    overall = ("error" if any(row["status"] in failures for row in steps) else
               "warning" if any(row["status"] in {"unknown", "reachable", "not_configured"}
                                and row["id"] != "proxy" for row in steps) else "ok")
    return {"ok": True, "overall": overall, "steps": steps,
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}

