"""browser_window.py — jabejayi-ye panjere-ye morurgar rooye Windows (bedun-e Selenium).

Chera Win32 va na Selenium?
  `driver.set_window_position()` az hamun session-e chromedriver estefade mikonad ke
  thread-e asli-ye robot ba an kar mikonad. Seda zadan-e an az yek thread-e digar
  mitavanad command-ha ra dar ham konad. Jabejayi ba SetWindowPos dar sath-e OS
  anjam mishavad va HICH ta'siri rooye session-e automation nadarad.

Ravesh-e makhfi kardan: panjere be birun az mahdude-ye hame-ye monitor-ha montaghel
mishavad (off-screen). Barkhalaf-e minimize, Windows/Chrome an-ra throttle nemikonad,
pas captcha/timer-ha/screenshot-ha daghighan mesl-e halat-e adi kar mikonand.

Hame-ye tavabe' "best effort" hastand: rooye non-Windows ya dar soorat-e nabudan-e
panjere, False/None barmigardanand va HARGEZ exception be biroon nemidahand.
"""
import ctypes
import sys
from ctypes import wintypes

IS_WINDOWS = sys.platform.startswith("win")

# Class-e panjere-ye asli-ye Chrome/Edge (Chromium)
CHROMIUM_WINDOW_CLASS = "Chrome_WidgetWin_1"

_TH32CS_SNAPPROCESS = 0x00000002
_MAX_PATH = 260
_SWP_NOSIZE = 0x0001
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_ASYNCWINDOWPOS = 0x4000
_SW_SHOWNOACTIVATE = 4
_SW_RESTORE = 9
_SM_XVIRTUALSCREEN = 76
_SM_YVIRTUALSCREEN = 77
_SM_CXVIRTUALSCREEN = 78
_SM_CYVIRTUALSCREEN = 79
_HWND_TOP = 0

# Fasele-ye ezafe ba lbe-ye virtual screen ta panjere ghat'an dide nashavad.
_OFFSCREEN_MARGIN = 64
# Hadaghal andaze baraye tashkhis-e panjere-ye asli (na popup/tooltip)
_MIN_MAIN_WINDOW_W = 200
_MIN_MAIN_WINDOW_H = 200


class _PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * _MAX_PATH),
    ]


def _kernel32():
    return ctypes.WinDLL("kernel32", use_last_error=True)


def _user32():
    return ctypes.WinDLL("user32", use_last_error=True)


def _snapshot_processes():
    """[(pid, parent_pid, exe_name_lower), ...] — khali agar natavanim bekhanim."""
    if not IS_WINDOWS:
        return []
    k32 = _kernel32()
    handle = k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if handle in (0, -1) or handle == ctypes.c_void_p(-1).value:
        return []
    rows = []
    try:
        entry = _PROCESSENTRY32()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32)
        if not k32.Process32First(handle, ctypes.byref(entry)):
            return []
        while True:
            name = entry.szExeFile.decode("latin-1", "replace").lower()
            rows.append((int(entry.th32ProcessID), int(entry.th32ParentProcessID), name))
            if not k32.Process32Next(handle, ctypes.byref(entry)):
                break
    finally:
        k32.CloseHandle(handle)
    return rows


def descendant_pids(root_pid, exe_filter=None):
    """Hame-ye PID-haye zir-derakht-e root_pid (khod-e root ham shamel mishavad)."""
    try:
        root_pid = int(root_pid or 0)
    except (TypeError, ValueError):
        return []
    if not root_pid:
        return []
    rows = _snapshot_processes()
    if not rows:
        return []
    children = {}
    names = {}
    for pid, parent, name in rows:
        children.setdefault(parent, []).append(pid)
        names[pid] = name
    found, queue, seen = [], [root_pid], {root_pid}
    while queue:
        pid = queue.pop()
        if exe_filter is None or names.get(pid, "") == exe_filter.lower():
            found.append(pid)
        for child in children.get(pid, []):
            if child not in seen:
                seen.add(child)
                queue.append(child)
    return found


def find_main_window(pids):
    """HWND-e bozorgtarin panjere-ye top-level-e ghabel-e didan-e in process-ha."""
    if not IS_WINDOWS or not pids:
        return None
    wanted = {int(p) for p in pids}
    u32 = _user32()
    best = {"hwnd": None, "area": 0}
    enum_proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        pid = wintypes.DWORD(0)
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if int(pid.value) not in wanted:
            return True
        if not u32.IsWindowVisible(hwnd):
            return True
        buffer = ctypes.create_unicode_buffer(256)
        u32.GetClassNameW(hwnd, buffer, 256)
        if buffer.value != CHROMIUM_WINDOW_CLASS:
            return True
        rect = wintypes.RECT()
        if not u32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return True
        width, height = rect.right - rect.left, rect.bottom - rect.top
        if width < _MIN_MAIN_WINDOW_W or height < _MIN_MAIN_WINDOW_H:
            return True
        area = width * height
        if area > best["area"]:
            best["area"], best["hwnd"] = area, hwnd
        return True

    try:
        u32.EnumWindows(enum_proto(callback), 0)
    except OSError:
        return None
    return best["hwnd"]


def find_browser_window(chromedriver_pid):
    """Panjere-ye morurgar-e marbut be in chromedriver ra peyda mikonad."""
    pids = descendant_pids(chromedriver_pid)
    return find_main_window(pids) if pids else None


def offscreen_position():
    """(x, y)-e birun az hame-ye monitor-ha."""
    if not IS_WINDOWS:
        return (-32000, -32000)
    u32 = _user32()
    x = u32.GetSystemMetrics(_SM_XVIRTUALSCREEN)
    y = u32.GetSystemMetrics(_SM_YVIRTUALSCREEN)
    width = u32.GetSystemMetrics(_SM_CXVIRTUALSCREEN)
    height = u32.GetSystemMetrics(_SM_CYVIRTUALSCREEN)
    if not width or not height:
        return (-32000, -32000)
    return (int(x + width + _OFFSCREEN_MARGIN), int(y + height + _OFFSCREEN_MARGIN))


def window_rect(hwnd):
    """(left, top, width, height) ya None."""
    if not IS_WINDOWS or not hwnd:
        return None
    rect = wintypes.RECT()
    if not _user32().GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return (int(rect.left), int(rect.top),
            int(rect.right - rect.left), int(rect.bottom - rect.top))


def is_offscreen(hwnd):
    """True agar panjere kamelan birun az virtual screen bashad."""
    rect = window_rect(hwnd)
    if not rect or not IS_WINDOWS:
        return False
    u32 = _user32()
    vx = u32.GetSystemMetrics(_SM_XVIRTUALSCREEN)
    vy = u32.GetSystemMetrics(_SM_YVIRTUALSCREEN)
    vw = u32.GetSystemMetrics(_SM_CXVIRTUALSCREEN)
    vh = u32.GetSystemMetrics(_SM_CYVIRTUALSCREEN)
    left, top, width, height = rect
    return left >= vx + vw or top >= vy + vh or left + width <= vx or top + height <= vy


def move_window(hwnd, x, y, activate=False):
    """Panjere ra be (x, y) montaghel mikonad (andaze/z-order dast nemikhorad)."""
    if not IS_WINDOWS or not hwnd:
        return False
    flags = _SWP_NOSIZE | _SWP_NOZORDER | _SWP_ASYNCWINDOWPOS
    if not activate:
        flags |= _SWP_NOACTIVATE
    ok = bool(_user32().SetWindowPos(hwnd, _HWND_TOP, int(x), int(y), 0, 0, flags))
    return ok


def hide_window(hwnd):
    """Panjere ra be birun-e safhe mibarad (rendering motevaghef NEMISHAVAD)."""
    x, y = offscreen_position()
    return move_window(hwnd, x, y, activate=False)


def show_window(hwnd, position=None):
    """Panjere ra be safhe barmigardanad va focus midahad."""
    if not IS_WINDOWS or not hwnd:
        return False
    u32 = _user32()
    u32.ShowWindow(hwnd, _SW_RESTORE)
    if position is None:
        position = (60, 40)
    moved = move_window(hwnd, position[0], position[1], activate=True)
    try:
        u32.SetForegroundWindow(hwnd)
    except OSError:
        pass
    u32.ShowWindow(hwnd, _SW_SHOWNOACTIVATE)
    return moved
