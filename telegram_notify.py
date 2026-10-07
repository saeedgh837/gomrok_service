"""
telegram_notify.py
Ersal-e payam va aks be telegram az tarigh-e worker-e cloudflare (reverse-proxy).
Config: telegram_config.json dar poshe-ye dade (app_paths.data_dir()) — birun az
posheh-ye release, ta update-e barnameh tanzimat-ra pak nakonad.
"""
import json
import os
import random
import sys
import tempfile
import time
import urllib.parse

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import app_paths  # noqa: E402  (needs _HERE on sys.path first)
import log_utils  # noqa: E402  (same)

_CFG_PATH = app_paths.data_path("telegram_config.json")


def _load_cfg():
    with open(_CFG_PATH, encoding="utf-8") as f:
        return json.load(f)


def _is_placeholder(v):
    return (not v) or (isinstance(v, str) and v.strip().startswith("<") and v.strip().endswith(">"))


def ensure_config():
    """Config-ra mikhanad; agar nabود ya field-ha placeholder (<...>) bud,
    az karbar SOAL mikonad va zakhire mikonad. barmigardanad dict-e config."""
    cfg = {}
    if os.path.exists(_CFG_PATH):
        try:
            cfg = _load_cfg()
        except Exception:
            cfg = {}
    changed = False
    prompts = [
        ("api_base", "API-e jaygozin-e telegram (base URL-e worker-e cloudflare, "
                     "mesl https://xxx.workers.dev): "),
        ("bot_token", "Token-e bot: "),
    ]
    for key, msg in prompts:
        if _is_placeholder(cfg.get(key)):
            val = input(msg).strip()
            cfg[key] = val
            changed = True
    if not _chat_ids(cfg):
        cfg["chat_ids"] = [x.strip() for x in input("Chat ID-ha (comma): ").split(",") if x.strip()]
        changed = True
    if changed:
        with open(_CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        print(f">>> telegram_config.json zakhire shod: {_CFG_PATH}")
    return cfg


def _resolve_mode(cfg):
    """Determine delivery route: 'host' (PHP relay), 'proxy', or 'direct'."""
    mode = str(cfg.get("mode") or "").strip().lower()
    if mode in ("host", "proxy", "direct"):
        return mode
    if cfg.get("relay_url"):
        return "host"
    if cfg.get("proxy_url"):
        return "proxy"
    if cfg.get("api_base"):
        return "direct"
    return "host"


def _api(method, cfg, base=None):
    mode = _resolve_mode(cfg)
    token = str(cfg.get("bot_token") or "")
    if mode == "host":
        relay = (base or cfg.get("relay_url") or "https://api.oneserver.in/telegram.php").rstrip("/")
        return f"{relay}?method={method}&token={token}"
    base = (base or cfg.get("api_base") or "https://api.telegram.org").rstrip("/")
    return f"{base}/bot{token}/{method}"


def _bases(cfg):
    """Return the configured Telegram API endpoint."""
    mode = _resolve_mode(cfg)
    if mode == "host":
        relay = str(cfg.get("relay_url") or "").rstrip("/")
        return [relay] if relay else []
    if mode == "proxy":
        return ["https://api.telegram.org"]
    primary = str(cfg.get("api_base") or "").rstrip("/")
    return [primary] if primary else []


def _chat_ids(cfg):
    values = cfg.get("chat_ids") or ([cfg.get("chat_id")] if cfg.get("chat_id") else [])
    return list(dict.fromkeys(str(x).strip() for x in values if str(x).strip()))


def _target_chat_ids(cfg, extra_chat_ids=None):
    return list(dict.fromkeys(_chat_ids(cfg) + [
        str(x).strip() for x in (extra_chat_ids or []) if str(x).strip()
    ]))


def report_chat_id():
    """The optional «گزارشها» channel from the CRM settings ("" = off).

    `crm` is imported here and not at module level: this module is imported by
    the robot processes and by panel, while `crm` imports app_paths and reads
    the store — a top-level import would be a cycle waiting to happen. The CRM
    store is the single source of truth (the settings tab is its only writer),
    so no second copy of the channel is kept here. A store that cannot be read
    or holds a non-numeric value reads as "not configured" (see
    crm.report_chat_id), which keeps delivery exactly as it was without it."""
    try:
        import crm
        return str(crm.report_chat_id() or "").strip()
    except Exception:
        return ""


def report_extra_chat_ids():
    """`[channel]` when one is configured, else None — ADDITIVE destinations.

    Every caller passes this as `extra_chat_ids`, never as `chat_id`: the
    configured chat list must keep receiving reports and inquiry results, and
    the channel only ever gets a copy. An unconfigured channel returns None so
    the fanout target list is byte-for-byte what it was before v1.10.
    `_target_chat_ids` de-duplicates, so a channel that is also in the
    configured list is not messaged twice."""
    channel = report_chat_id()
    return [channel] if channel else None


def _post_confirmed(method, cfg, data, files_factory=None, attempts=4, deadline=None):
    import requests
    last_error = None
    bases = _bases(cfg)
    mode = _resolve_mode(cfg)
    post_kwargs = {}
    if mode == "host":
        headers = {}
        secret = str(cfg.get("relay_secret") or "").strip()
        if secret:
            headers["X-Relay-Secret"] = secret
        token = str(cfg.get("bot_token") or "").strip()
        if token:
            headers["X-Telegram-Bot-Token"] = token
        if headers:
            post_kwargs["headers"] = headers
        post_kwargs["proxies"] = {"http": None, "https": None}
    elif mode == "proxy":
        proxy_url = str(cfg.get("proxy_url") or "").strip()
        if proxy_url:
            post_kwargs["proxies"] = {"http": proxy_url, "https": proxy_url}

    for attempt in range(1, attempts + 1):
        if deadline is not None and time.monotonic() >= deadline:
            raise TimeoutError(
                f"Telegram deadline reached after {attempt - 1} attempts: {last_error}")
        base = bases[(attempt - 1) % len(bases)]   # masir-ha ra jabejaa emtehan kon
        # timeout-e har request az budge-ye baghimande bishtar nashavad
        request_timeout = 150 if files_factory else 20
        if deadline is not None:
            remaining = max(1.0, deadline - time.monotonic())
            request_timeout = min(request_timeout, remaining)
        try:
            files = files_factory() if files_factory else None
            try:
                response = requests.post(
                    _api(method, cfg, base),
                    data=data,
                    files=files,
                    timeout=request_timeout,
                    **post_kwargs,
                )
            finally:
                if files:
                    for value in files.values():
                        value.close()
            body = {}
            try:
                body = response.json() or {}
            except ValueError:
                pass
            if body.get("ok") and (body.get("result") or {}).get("message_id"):
                return body
            description = body.get("description")
            if description and _is_permanent_chat_error(description):
                # chat-e na-motabar: retry fayde nadarad
                raise RuntimeError(description)
            response.raise_for_status()
            last_error = RuntimeError(description or "Telegram delivery not confirmed")
        except Exception as exc:
            if _is_permanent_chat_error(exc):
                raise
            last_error = exc
        if attempt < attempts:
            backoff = min(2 ** (attempt - 1), 5)
            if deadline is not None:
                backoff = min(backoff, max(0.0, deadline - time.monotonic()))
            if backoff > 0:
                time.sleep(backoff)
    raise RuntimeError(f"Telegram delivery failed after {attempts} attempts: {last_error}")


_PERMANENT_CHAT_ERRORS = (
    "chat not found", "bot was blocked", "user is deactivated",
    "bot was kicked", "peer_id_invalid", "chat_id is empty",
)


def _is_permanent_chat_error(error):
    text = str(error or "").lower()
    return any(marker in text for marker in _PERMANENT_CHAT_ERRORS)


def _redact(text, token, secret=None):
    """Replace the bot token and relay secret with *** wherever they appear in `text`."""
    text = str(text or "")
    for s in (token, secret):
        s = str(s or "").strip()
        if not s or _is_placeholder(s):
            continue
        for needle in (s, urllib.parse.quote(s, safe="")):
            if needle:
                text = text.replace(needle, "***")
    return text


def _error_line(exc):
    """First line of an error's message ("" when it has none)."""
    lines = str(exc).splitlines()
    return lines[0] if lines else ""


def delivery_log_path():
    """Path of the delivery audit log (JSONL, one record per missed send)."""
    return app_paths.data_path("telegram_delivery.jsonl")


def _report_missed_destinations(method, total, delivered, failures, elapsed, budget,
                                token=None):
    """One summary line + one JSONL record for a fanout that missed someone.

    _fanout reports success as soon as ONE destination has taken the message,
    so a destination that received nothing is invisible to the caller — that is
    how the starved receipt target went unnoticed. Error text is redacted of the
    token before it is stored; the record carries ids and error text only: no
    api_base, no message body. `delivered` is the list of chat_ids that DID
    receive it; this only runs when at least one did not."""
    note = f"{budget:.1f}s budge" if budget is not None else "biz budge"
    missed = ", ".join(str(chat_id) for chat_id, _exc in failures)
    print(f">>> telegram [NAGHS] {len(failures)} az {total} maghsad ersal nashod "
          f"({method}, {note}): {missed}")
    failed = []
    for chat_id, exc in failures:
        failed.append({"chat_id": str(chat_id),
                       "error": _redact(_error_line(exc), token)[:200],
                       "permanent": _is_permanent_chat_error(exc)})
    record = {
        "ts": time.time(),
        "event": "telegram_delivery",
        "method": method,
        "ok": False,
        "total": total,
        "delivered": [str(chat_id) for chat_id in delivered],
        "failed": failed,
        "elapsed_sec": elapsed,
        "budget_sec": budget,
    }
    try:
        log_utils.append_jsonl(delivery_log_path(), record)
    except Exception:
        pass   # a log that cannot be written must never break a send


def _fanout(method, cfg, chat_ids, payload_for, files_factory=None, deadline=None):
    """Be hame-ye chat-ha mifrestad. chat-e kharab (chat not found / block) baes-e
    shekast-e KOL-e ersal nemishavad — faghat agar HICH chat-i movaffagh nashod
    exception partab mishavad. `deadline` (monotonic) budge-ye zamani-ye koll ast.

    Each destination gets its own slice of the caller's budget (see target_deadline
    below): one slow destination must not eat the whole budget and leave the rest
    never attempted. With `deadline=None` the behaviour is unchanged."""
    results = []
    delivered = []
    failures = []
    started = time.monotonic()      # same clock as `deadline`
    clock = time.perf_counter()     # monotonic is ~15ms-granular on Windows
    total = len(chat_ids)
    token = cfg.get("bot_token")    # never let it reach stdout / the record / the raise
    secret = cfg.get("relay_secret")
    for index, chat_id in enumerate(chat_ids):
        # Anti-starvation: an equal slice of what is LEFT, per destination
        # still to go. Without a caller budget nothing changes.
        target_deadline = deadline
        if deadline is not None:
            now = time.monotonic()
            target_deadline = min(deadline,
                                  now + (deadline - now) / (total - index))
        try:
            results.append(_post_confirmed(method, cfg, payload_for(chat_id),
                                           files_factory=files_factory,
                                           deadline=target_deadline))
            delivered.append(chat_id)
        except Exception as exc:
            failures.append((chat_id, exc))
            level = "NA-MOTABAR" if _is_permanent_chat_error(exc) else "KHATA"
            # redact BEFORE truncating: a cut-off token is still a secret
            print(f">>> telegram [{level}] chat_id={chat_id}: "
                  f"{_redact(_error_line(exc), token, secret)[:150]}")
    if failures:
        _report_missed_destinations(
            method, total, delivered, failures,
            elapsed=time.perf_counter() - clock,
            budget=(deadline - started) if deadline is not None else None,
            token=token)
    if results:
        return results
    detail = "; ".join(f"{cid}: {_redact(exc, token, secret)}"
                       for cid, exc in failures) or "no chat_ids configured"
    if failures and all(isinstance(exc, TimeoutError) for _cid, exc in failures):
        raise TimeoutError(f"Telegram delivery timed out for all chats: {detail}")
    raise RuntimeError(f"Telegram delivery failed for all chats: {detail}")


import html as _html


def _actor_name():
    """Acting panel user for attribution; worker processes get IRICA_ACTOR from
    the panel when spawned. Anything outside the panel is "سیستم"."""
    who = (os.environ.get("IRICA_ACTOR") or "").strip()
    return who or "سیستم"


def actor_prefix(who=None):
    """HTML prefix naming the acting user; prepended to every outbound text."""
    name = (who or _actor_name())
    return "<b>کاربر: " + _html.escape(str(name)) + "</b>\n"


def send_message(text, parse_mode="HTML", extra_chat_ids=None, timeout=None,
                 actor=None, chat_id=None):
    """Payam mifrestad. `timeout` (saniye) budge-ye koll ast; agar bogozarad
    TimeoutError partab mishavad (baraye jaha ke robot nabayad gir konad).
    `actor` overrides the attribution name for this message.
    `chat_id` (v1.5 B10) targets EXACTLY that one chat: the configured chat
    list and extra_chat_ids are ignored (sensitive registration data must
    go only to its dedicated destination, never fan out)."""
    cfg = ensure_config()
    text = actor_prefix(actor) + text
    deadline = (time.monotonic() + timeout) if timeout else None
    if chat_id:
        target = str(chat_id).strip()
        if not target:
            raise ValueError("chat_id must be a non-empty chat id")
        targets = [target]
    else:
        targets = _target_chat_ids(cfg, extra_chat_ids)
    return _fanout("sendMessage", cfg, targets,
                   lambda chat_id: {"chat_id": chat_id, "text": text,
                                    "parse_mode": parse_mode},
                   deadline=deadline)


PHOTO_MAX_EDGE = 2000
PHOTO_JPEG_QUALITY = 85
PHOTO_SHRINK_MIN_BYTES = 300 * 1024


def _remove_file(path):
    """Best-effort delete; a leftover temp file must never break a send."""
    if not path:
        return
    try:
        os.remove(path)
    except OSError:
        pass


def _shrink_photo(photo_path):
    """(upload_path, temp_path) — a smaller copy of a big photo, original otherwise.

    A full-page receipt capture is ~1.4 MB against ~150 KB for a payment-link
    shot, and every destination only gets a slice of the caller's budget: on a
    slow link the big upload can burn a whole slice and lose that destination.
    Downscaling to PHOTO_MAX_EDGE and saving JPEG keeps the upload inside it.

    NEVER raises and NEVER modifies `photo_path` (the file on disk stays
    byte-identical): a missing/unreadable file, a file already below
    PHOTO_SHRINK_MIN_BYTES, a missing Pillow, a decode or an encode error all
    return (photo_path, None), and the caller uploads the original."""
    try:
        if not photo_path or not os.path.isfile(photo_path):
            return photo_path, None
        if os.path.getsize(photo_path) < PHOTO_SHRINK_MIN_BYTES:
            return photo_path, None
        from PIL import Image
        with Image.open(photo_path) as image:
            image.load()
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            width, height = image.size
            longest = max(width, height)
            if longest > PHOTO_MAX_EDGE:
                scale = PHOTO_MAX_EDGE / float(longest)
                image = image.resize((max(1, int(round(width * scale))),
                                      max(1, int(round(height * scale)))),
                                     Image.LANCZOS)
            handle, temp_path = tempfile.mkstemp(prefix="tg_photo_",
                                                 suffix=".jpg")
            os.close(handle)
            try:
                image.save(temp_path, format="JPEG", quality=PHOTO_JPEG_QUALITY)
            except Exception:
                _remove_file(temp_path)
                return photo_path, None
        return temp_path, temp_path
    except Exception:
        return photo_path, None


def send_photo(photo_path, caption="", parse_mode="HTML", extra_chat_ids=None,
               timeout=None, actor=None):
    """Aks mifrestad. `timeout` (saniye) budge-ye koll ast; agar bogozarad
    TimeoutError partab mishavad (aks az ghabl mahali zakhire shode).

    A big photo is shrunk ONCE per call, not per attempt: every attempt uploads
    the same bytes and the encode is the expensive part. The temp copy is
    removed in the finally below, also when the send raises."""
    cfg = ensure_config()
    caption = actor_prefix(actor) + caption
    deadline = (time.monotonic() + timeout) if timeout else None
    upload_path, temp_path = _shrink_photo(photo_path)
    try:
        return _fanout("sendPhoto", cfg, _target_chat_ids(cfg, extra_chat_ids),
                       lambda chat_id: {"chat_id": chat_id, "caption": caption,
                                        "parse_mode": parse_mode},
                       files_factory=lambda: {"photo": open(upload_path, "rb")},
                       deadline=deadline)
    finally:
        _remove_file(temp_path)


def rand5():
    return str(random.randint(10000, 99999))
