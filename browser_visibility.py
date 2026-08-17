"""browser_visibility.py — hamahang-kardan-e didan/makhfi-kardan-e panjere-ye robot.

Ghanoon-e mohem: robot HAMISHE morurgar-e vaghei (headed) baz mikonad, vali be
tore PISHFARZ panjere ra be birun-e safhe (off-screen) montaghel mikonad. In tori:

  - hich rendering-i motevaghef nemishavad (captcha/timer/screenshot salem mimanad),
  - vali panjere-ha rooye desktop-e karbar sholoughi nemisazand va focus nemidozdand.

Panel field-e `browser_visible` ra rooye profile set mikonad; watcher-e in module
har chand sanie an-ra mikhanad va panjere ra be safhe mi-avarad ya makhfi mikonad.

Hame chiz best-effort ast: agar panjere/panel/Win32 dar dastres nabashad, robot
bedun-e ekhtelal edame midahad.
"""
import threading
import time

import browser_window as bw

POLL_SECONDS = 2.0
# Baraye chand robot-e hamzaman panjere-ha ra pele-i jabeja mikonim ta rooye ham nayoftand.
_CASCADE_STEP = 36
_CASCADE_ORIGIN = (60, 40)
_CASCADE_SLOTS = 8
_slot_counter = [0]
_slot_lock = threading.Lock()


def _next_slot_position():
    with _slot_lock:
        index = _slot_counter[0] % _CASCADE_SLOTS
        _slot_counter[0] += 1
    return (_CASCADE_ORIGIN[0] + index * _CASCADE_STEP,
            _CASCADE_ORIGIN[1] + index * _CASCADE_STEP)


def chromedriver_pid(driver):
    """PID-e process-e chromedriver-e in session (ya None)."""
    try:
        return int(driver.service.process.pid)
    except (AttributeError, TypeError, ValueError):
        return None


class WindowVisibilityController:
    """Panjere-ye yek driver ra motabegh-e field-e panel makhfi/namayan mikonad."""

    def __init__(self, driver, profile_id="", visible=False, panel_module=None):
        self.driver = driver
        self.profile_id = str(profile_id or "")
        self.desired_visible = bool(visible)
        self._panel = panel_module
        self._hwnd = None
        self._applied = None
        self._position = _next_slot_position()
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = None

    # -- panjere -----------------------------------------------------------
    def _window(self):
        if self._hwnd and bw.window_rect(self._hwnd) is not None:
            return self._hwnd
        pid = chromedriver_pid(self.driver)
        self._hwnd = bw.find_browser_window(pid) if pid else None
        return self._hwnd

    def apply(self, force=False):
        """Vaziat-e fe'li ra rooye panjere e'mal mikonad. True = e'mal shod."""
        with self._lock:
            target = self.desired_visible
            if not force and self._applied == target:
                return True
            hwnd = self._window()
            if not hwnd:
                return False
            ok = bw.show_window(hwnd, self._position) if target else bw.hide_window(hwnd)
            if ok:
                self._applied = target
            return ok

    def set_visible(self, visible, force=False):
        with self._lock:
            self.desired_visible = bool(visible)
        return self.apply(force=force)

    # -- watcher -----------------------------------------------------------
    def _read_panel_flag(self):
        panel = self._panel
        if panel is None:
            try:
                import panel as panel_module
            except ImportError:
                return None
            panel = self._panel = panel_module
        try:
            return panel.get_browser_visibility(self.profile_id)
        except Exception:
            return None

    def _loop(self):
        # Aval-e kar: panjere ra fowran be halat-e darkhast-shode bebar.
        deadline = time.time() + 30
        while not self._stop.is_set() and time.time() < deadline:
            if self.apply(force=True):
                break
            self._stop.wait(0.5)
        while not self._stop.is_set():
            if self.profile_id:
                flag = self._read_panel_flag()
                if flag is not None and bool(flag) != self.desired_visible:
                    self.set_visible(flag)
            else:
                self.apply()
            self._stop.wait(POLL_SECONDS)

    def start(self):
        if self._thread and self._thread.is_alive():
            return self._thread
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="browser-visibility",
                                        daemon=True)
        self._thread.start()
        return self._thread

    def stop(self):
        self._stop.set()

    def rebind(self, driver):
        """Ba'd az sakht-e driver-e jadid (talash-e badi) hamin controller estefade shavad."""
        with self._lock:
            self.driver = driver
            self._hwnd = None
            self._applied = None
        self.apply(force=True)


def start(driver, profile_id="", visible=False, panel_module=None):
    """Controller ra misazad, watcher ra roshan mikonad va barmigardanad."""
    controller = WindowVisibilityController(driver, profile_id, visible, panel_module)
    controller.start()
    return controller
