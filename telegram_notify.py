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
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import app_paths  # noqa: E402  (needs _HERE on sys.path first)

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


def _api(method, cfg, base=None):
    base = (base or cfg["api_base"]).rstrip("/")
    token = cfg["bot_token"]
    return f"{base}/bot{token}/{method}"


def _bases(cfg):
    """Return the configured Telegram API endpoint."""
    primary = str(cfg.get("api_base") or "").rstrip("/")
    return [primary] if primary else []


def _chat_ids(cfg):
    values = cfg.get("chat_ids") or ([cfg.get("chat_id")] if cfg.get("chat_id") else [])
    return list(dict.fromkeys(str(x).strip() for x in values if str(x).strip()))


def _target_chat_ids(cfg, extra_chat_ids=None):
    return list(dict.fromkeys(_chat_ids(cfg) + [
        str(x).strip() for x in (extra_chat_ids or []) if str(x).strip()
    ]))


def _post_confirmed(method, cfg, data, files_factory=None, attempts=4, deadline=None):
    import requests
    last_error = None
    bases = _bases(cfg)
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
                response = requests.post(_api(method, cfg, base), data=data, files=files,
                                         timeout=request_timeout)
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


def _fanout(method, cfg, chat_ids, payload_for, files_factory=None, deadline=None):
    """Be hame-ye chat-ha mifrestad. chat-e kharab (chat not found / block) baes-e
    shekast-e KOL-e ersal nemishavad — faghat agar HICH chat-i movaffagh nashod
    exception partab mishavad. `deadline` (monotonic) budge-ye zamani-ye koll ast."""
    results = []
    failures = []
    for chat_id in chat_ids:
        try:
            results.append(_post_confirmed(method, cfg, payload_for(chat_id),
                                           files_factory=files_factory,
                                           deadline=deadline))
        except Exception as exc:
            failures.append((chat_id, exc))
            level = "NA-MOTABAR" if _is_permanent_chat_error(exc) else "KHATA"
            print(f">>> telegram [{level}] chat_id={chat_id}: {str(exc).splitlines()[0][:150]}")
    if results:
        return results
    detail = "; ".join(f"{cid}: {exc}" for cid, exc in failures) or "no chat_ids configured"
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
                 actor=None):
    """Payam mifrestad. `timeout` (saniye) budge-ye koll ast; agar bogozarad
    TimeoutError partab mishavad (baraye jaha ke robot nabayad gir konad).
    `actor` overrides the attribution name for this message."""
    cfg = ensure_config()
    text = actor_prefix(actor) + text
    deadline = (time.monotonic() + timeout) if timeout else None
    return _fanout("sendMessage", cfg, _target_chat_ids(cfg, extra_chat_ids),
                   lambda chat_id: {"chat_id": chat_id, "text": text,
                                    "parse_mode": parse_mode},
                   deadline=deadline)


def send_photo(photo_path, caption="", parse_mode="HTML", extra_chat_ids=None,
               timeout=None, actor=None):
    """Aks mifrestad. `timeout` (saniye) budge-ye koll ast; agar bogozarad
    TimeoutError partab mishavad (aks az ghabl mahali zakhire shode)."""
    cfg = ensure_config()
    caption = actor_prefix(actor) + caption
    deadline = (time.monotonic() + timeout) if timeout else None
    return _fanout("sendPhoto", cfg, _target_chat_ids(cfg, extra_chat_ids),
                   lambda chat_id: {"chat_id": chat_id, "caption": caption,
                                    "parse_mode": parse_mode},
                   files_factory=lambda: {"photo": open(photo_path, "rb")},
                   deadline=deadline)


def rand5():
    return str(random.randint(10000, 99999))
