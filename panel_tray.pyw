"""Desktop-shortcut launcher for the IRICA panel (run with pythonw.exe).

The operator double-clicks the desktop shortcut and currently sees nothing
(the panel runs silently in the background). This launcher closes that gap:

  1. probe http://127.0.0.1:8750/ — if the panel already answers, skip spawn
     (idempotent double-clicks)
  2. otherwise spawn panel_gui.pyw detached and poll the port until it
     answers (panel_gui shows a MessageBox + crash log on real failures)
  3. once up: fire a Windows toast notification ("service started") and open
     the /launch page — a clean Persian card with the panel links and a
     close-service button — in the default browser

Failure modes handled:
  * port held by a non-panel service -> panel_gui's gate refuses; the probe
    never shows project markup -> MessageBox with the crash-log path
  * panel too slow / crashed -> bounded poll, then the same MessageBox
  * old panel without /launch -> fall back to /site-1
  * toast failure -> never blocks or fails the launch (best-effort)
  * no default browser -> MessageBox fallback
"""
import os
import subprocess
import sys
import time
import traceback
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONNOUSERSITE", "1")

PANEL_URL = "http://127.0.0.1:8750"
START_TIMEOUT = 25.0


def _log(text):
    """Append diagnostics to the panel data dir (best-effort)."""
    try:
        import app_paths
        log_dir = app_paths.data_dir()
    except Exception:
        log_dir = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "irica")
    try:
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "panel_tray.log"), "a",
                  encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass


def _alert(title, text):
    try:
        import ctypes
        # MB_ICONWARNING | MB_OK | MB_SETFOREGROUND | MB_TOPMOST
        ctypes.windll.user32.MessageBoxW(0, text, title,
                                         0x30 | 0x0 | 0x10000 | 0x40000)
    except Exception:
        pass


def _probe():
    """True when THIS project's panel answers on the port."""
    try:
        with urllib.request.urlopen(PANEL_URL + "/", timeout=1.5) as resp:
            body = resp.read(65536).decode("utf-8", "replace")
        return "site-1" in body or "irica-version" in body
    except Exception:
        return False


def _spawn_panel():
    """Start panel_gui.pyw detached (pythonw, no console)."""
    pythonw = os.path.join(HERE, "python", "pythonw.exe")
    exe = pythonw if os.path.isfile(pythonw) else sys.executable
    script = os.path.join(HERE, "panel_gui.pyw")
    target = script if os.path.isfile(script) else os.path.join(HERE, "panel.py")
    flags = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    subprocess.Popen([exe, target], cwd=HERE, creationflags=flags,
                     close_fds=True)


def _toast(title, message):
    """Best-effort Windows toast (WinRT via PowerShell). Never raises."""
    try:
        ps = (
            "[Windows.UI.Notifications.ToastNotificationManager, "
            "Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null;"
            "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument,"
            " ContentType = WindowsRuntime] | Out-Null;"
            "$xml=[Windows.UI.Notifications.ToastNotificationManager]"
            "::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]"
            "::ToastText02);"
            "$t=$xml.GetElementsByTagName('text');"
            f"$t.Item(0).AppendChild($xml.CreateTextNode('{title}')) | Out-Null;"
            f"$t.Item(1).AppendChild($xml.CreateTextNode('{message}')) | Out-Null;"
            "$toast=[Windows.UI.Notifications.ToastNotification]::new($xml);"
            "[Windows.UI.Notifications.ToastNotificationManager]"
            "::CreateToastNotifier('IRICA.Panel').Show($toast)"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            timeout=8, capture_output=True,
            creationflags=0x08000000)  # CREATE_NO_WINDOW
    except Exception as exc:
        _log(f"toast skipped: {exc}")


def _open_launch_page():
    """Open /launch in the default browser; fall back cross-version."""
    from urllib.parse import urlsplit
    for path in ("/launch", "/site-1"):
        try:
            req = urllib.request.Request(PANEL_URL + path, method="GET")
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    os.startfile(PANEL_URL + path)  # noqa: S606
                    return
        except Exception:
            continue
    _alert("IRICA",
           "پنل فعال است ولی مرورگر باز نشد.\n"
           "این آدرس را دستی در مرورگر باز کنید:\n" + PANEL_URL + "/launch")


def main():
    already_up = _probe()
    if not already_up:
        _spawn_panel()
        deadline = time.time() + START_TIMEOUT
        while time.time() < deadline:
            if _probe():
                break
            time.sleep(0.5)
        else:
            _alert(
                "IRICA — خطا در اجرای سرویس",
                "سرویس رجیستری بعد از ۲۵ ثانیه بالا نیامد.\n\n"
                "جزئیات در panel_crash.log داخل پوشهٔ داده ذخیره شده است.\n"
                "اگر برنامهٔ دیگری روی پورت 8750 است، آن را ببندید و "
                "میان‌بر را دوباره اجرا کنید.")
            return 1
    _toast("IRICA", "سرویس رجیستری فعال شد ✓")
    _open_launch_page()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        _log("tray crash:\n" + traceback.format_exc())
        _alert("IRICA — خطا در اجرای سرویس",
               "اجرای سرویس با خطا متوقف شد.\nجزئیات در panel_tray.log "
               "داخل پوشهٔ داده ذخیره شد.")
        sys.exit(1)
