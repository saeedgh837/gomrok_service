"""estelam_shot.py
Capture helper for the inquiry robots: at the verdict moment, save the
result element's picture (element screenshot, full-page CDP fallback) into
DATA_DIR/screenshots, one newest file per device per service, and hand the
path back so the robot can write it into estelam_store.json and attach it to
the Telegram result message.

Why this lives here and not in gomrok.py: gomrok.py is read-only for this
work (contract), and estelam_hamta.py does not import it — so both robots
import this small editable module instead of duplicating logic.
"""
import os
import re
import time

import app_paths

_SAFE = re.compile(r"[^0-9a-zA-Z_-]+")


def _safe_part(text):
    """ASCII-safe slug for a filename segment (serials can be anything)."""
    t = str(text or "").strip()
    if not t:
        return ""
    return _SAFE.sub("_", t)[:40].strip("_")


def shot_path(service, device_id, serial):
    """Newest-file-per-(service,device) path in DATA_DIR/screenshots.

    Stable name = one slot per device per service, so every new capture
    overwrites the previous one and no history accumulates on disk.

    Returns "" when there is no device id to key the slot on. A CLI
    single-serial run has no panel row to show the picture, and letting every
    such run fall back to one shared slot would make two concurrent runs
    overwrite each other's evidence.
    """
    if service not in ("gomrok", "hamta"):
        service = "gomrok"
    dev = _safe_part(device_id)
    if not dev:
        return ""
    directory = app_paths.data_subdir("screenshots")
    return os.path.join(
        directory,
        f"estelam_{service}_{dev}_{_safe_part(serial) or 'na'}.png")


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_SETTLE_REPEATS = 2
_SETTLE_TIMEOUT = 2.0


def _say(text):
    """Diagnostic that cannot become the reason the inquiry failed.

    Every capture path here is best-effort, but a bare print() is not: a full
    disk, or a console without UTF-8 when a robot is run by hand instead of by
    the panel, makes print() itself raise. That exception would escape into
    run_from_store's blanket handler and overwrite a verdict the robot had
    already determined with "robot error".
    """
    try:
        print(text)
    except Exception:
        pass


def _write_png(path, png, what):
    """Validate the PNG magic, then write the file atomically.

    A stale or obscured element can hand back zero-byte or truncated bytes,
    which would otherwise be recorded as a successful capture and later render
    as a broken image. Writing to a temp file and replacing keeps a kill
    mid-write from truncating the previous, still-valid capture in the slot.
    """
    if not png or png[:8] != _PNG_MAGIC:
        _say(f">>> ({what} returned invalid PNG: {len(png or b'')} bytes)")
        return False
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(png)
        os.replace(tmp, path)
        return True
    except Exception as e:
        _say(f">>> ({what} could not be written: {e})")
        _unlink_quietly(tmp)
        return False


def _unlink_quietly(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def _result_element(driver, selector):
    """First match that is actually rendered and carries text.

    The gomrok feedback container is present-but-empty by design (the robot
    blanks its textContent between attempts and leaves the node in place), and
    a page can carry more than one of them. find_element would hand back the
    first node whatever its state, so a blank-but-valid PNG could be recorded
    as the verdict's evidence. Returns None when nothing usable matched.
    """
    for el in driver.find_elements("css selector", selector):
        try:
            if not (el.text or "").strip():
                continue
            size = el.size or {}
            if not (size.get("width") and size.get("height")):
                continue
            return el
        except Exception:
            continue
    return None


def _wait_settled(driver, el, timeout=_SETTLE_TIMEOUT):
    """Wait until the element's box stops moving, rather than sleeping blind.

    scrollIntoView can animate, so capturing immediately risks a mid-scroll
    frame. Poll the box and return as soon as it repeats, with a hard cap so a
    permanently animating page cannot stall the inquiry.
    """
    deadline = time.monotonic() + timeout
    last = None
    repeats = 0
    while time.monotonic() < deadline:
        try:
            box = driver.execute_script(
                "var r=arguments[0].getBoundingClientRect();"
                "return [Math.round(r.top),Math.round(r.left),"
                "Math.round(r.width),Math.round(r.height)];", el)
        except Exception:
            return
        if box == last:
            repeats += 1
            if repeats >= _SETTLE_REPEATS:
                return
        else:
            repeats = 0
            last = box
        time.sleep(0.05)


def capture_element(driver, selector, path):
    """Scroll the result element into view, let it settle, then screenshot it.

    Returns True on success. Fails closed (returns False, no raise) so an
    unreachable element never blocks the result message itself.
    """
    try:
        el = _result_element(driver, selector)
        if el is None:
            _say(f">>> (no rendered result element matched [{selector}])")
            return False
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center'});", el)
        _wait_settled(driver, el)
        return _write_png(path, el.screenshot_as_png, "element screenshot")
    except Exception as e:
        _say(f">>> (element screenshot failed [{selector}]: {e})")
        return False


def capture_page(driver, path):
    """Full-page CDP capture, with save_screenshot as the last resort.

    Deliberately NOT imported from gomrok.py: the hamta robot keeps a narrow
    sys.path/sys.modules discipline because the two solver packages share
    module names, and importing gomrok (which pulls captcha_collector and the
    gomrok solver chain) mid-run risks re-aliasing live_solve for the rest of
    the --from-store loop. The CDP call is identical to gomrok's, minus its
    profile-banner hiding (the inquiry sites have none).
    """
    try:
        import base64
        res = driver.execute_cdp_cmd("Page.captureScreenshot", {
            "captureBeyondViewport": True,
            "fromSurface": True,
        })
        return _write_png(path, base64.b64decode(res["data"]),
                          "CDP screenshot")
    except Exception as e:
        _say(f">>> (CDP page screenshot failed: {e}; "
              f"falling back to save_screenshot)")
    # save_screenshot writes the file itself, so it cannot go through
    # _write_png. Read the bytes back and validate them the same way instead
    # of trusting the driver's return value.
    tmp = path + ".tmp"
    try:
        if not driver.save_screenshot(tmp):
            return False
        with open(tmp, "rb") as f:
            head = f.read(8)
        if head != _PNG_MAGIC:
            _say(">>> (save_screenshot produced a non-PNG file)")
            _unlink_quietly(tmp)
            return False
        os.replace(tmp, path)
        return True
    except Exception as e2:
        _say(f">>> (save_screenshot also failed: {e2})")
        _unlink_quietly(tmp)
        return False


def capture_result(driver, service, device_id, serial,
                   element_selector=None, _path=None):
    """Capture the result element; fall back to the whole page.

    Returns (path, exact): the saved path, or "" on failure, plus whether the
    picture is the result element itself rather than the whole page.

    Callers use `exact` to decide what may leave the machine. A full-page
    gomrok capture still shows the traveler's national ID and birth date in
    the form above the verdict, so it is panel-only evidence and must not be
    attached to the Telegram report, which fans out to every configured chat.

    Never raises: a failed capture must not break the inquiry.

    `_path` overrides the derived path. Production code leaves it None (the
    stable per-device name is the point); tests pass a temp path so the suite
    never writes into the live data directory.
    """
    try:
        path = _path or shot_path(service, device_id, serial)
    except Exception as e:
        _say(f">>> (could not build the screenshot path: {e})")
        return "", False
    if not path:
        return "", False
    if element_selector and capture_element(driver, element_selector, path):
        _say(f">>> screenshot saved: {path}")
        return path, True
    if capture_page(driver, path):
        _say(f">>> screenshot saved (full page): {path}")
        return path, False
    return "", False


def discard(path):
    """Delete a stored capture, e.g. when its device row goes away.

    Best-effort and quiet: a missing file is the desired end state anyway.

    Never raises. Returns True when the file is gone (or there was nothing to
    delete) and False when the unlink failed, so a caller that keeps a durable
    log can record the failure instead of watching it scroll away on the
    console.
    """
    if not path:
        return True
    try:
        if os.path.isfile(path):
            os.unlink(path)
        return True
    except OSError as e:
        _say(f">>> (could not delete the screenshot {path}: {e})")
        return False


def attach(photo_path, caption):
    """Send one photo with the Persian result text as its caption.

    Returns True on success. Photo send is best-effort: on any failure the
    caller still sends its plain text message, so the verdict always lands.
    """
    try:
        import telegram_notify as tg
        # v1.10: the result screenshot is part of the inquiry report, so a
        # configured report channel gets the same photo as the configured
        # chats (additive — see telegram_notify.report_extra_chat_ids).
        tg.send_photo(photo_path, caption=caption, timeout=20,
                      extra_chat_ids=tg.report_extra_chat_ids())
        return True
    except Exception as e:
        _say(f">>> (photo send failed: {e})")
        return False
