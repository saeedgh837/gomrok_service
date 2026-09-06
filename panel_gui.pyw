"""Silent launcher for the IRICA panel (run with pythonw.exe — no console).

Used by the installer's shortcuts. Responsibilities:
  * start panel.py's main() without a console window
  * if the panel fails to start (port taken by a NON-panel process, broken
    runtime, missing file), show a readable message box instead of dying
    silently and append the traceback to %LOCALAPPDATA%\\irica\\panel_crash.log
  * normal exits (the single-instance gate, Ctrl+C in a console, shutdown)
    stay silent
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONNOUSERSITE", "1")


def _log_crash(text):
    try:
        import app_paths
        log_dir = app_paths.data_dir()
    except Exception:
        log_dir = os.path.join(os.environ.get("LOCALAPPDATA", HERE), "irica")
    try:
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "panel_crash.log"), "a",
                  encoding="utf-8") as f:
            f.write(text + "\n")
    except OSError:
        pass


def _alert(title, text):
    try:
        import ctypes
        # MB_ICONWARNING | MB_OK | MB_SETFOREGROUND
        ctypes.windll.user32.MessageBoxW(0, text, title, 0x30 | 0x0 | 0x10000)
    except Exception:
        pass


def main():
    import panel
    try:
        panel.main()
    except SystemExit:
        raise
    except OSError as exc:
        # A bind failure (WinError 10048) means something NON-panel holds the
        # port: the single-instance gate already handled the panel case.
        text = traceback.format_exc()
        _log_crash(text)
        _alert("IRICA — خطا در اجرای پنل",
               "پنل اجرا نشد. احتمالاً برنامهٔ دیگری روی پورت 8750 است.\n\n"
               f"{exc}\n\nجزئیات کامل در panel_crash.log ذخیره شد.")
    except Exception:
        text = traceback.format_exc()
        _log_crash(text)
        _alert("IRICA — خطا در اجرای پنل",
               "پنل با خطا متوقف شد. جزئیات کامل در panel_crash.log ذخیره شد.\n\n"
               + text[-1200:])


if __name__ == "__main__":
    main()
