"""End-to-end: a permanently rejected IMEI marks the profile unusable.

The field report: on the customs login page the robot receives

    خطا در استعلام imei از سامانه همتا : شناسه (IMEI) وارد شده قبلا در شبکه
    داخلی کشور ثبت و فعال شده و امکان ورود آن وجود ندارد

That device can never be entered, so the profile must be taken out of service —
not retried. This walks the whole chain with REAL files, no mocked filesystem:

  1. the classifier calls the message fatal and NOT retryable;
  2. `gomrok._mark_panel_profile_unusable` writes through to `profiles.json`;
  3. the panel then REFUSES to start that profile (HTTP 409);
  4. the profile card renders the block reason for the operator;
  5. a genuine transient HAMTA failure is still retried, and leaves the profile
     runnable — the fix must not turn every inquiry error into a dead profile.

Run:
    "C:/Program Files/Python310/python.exe" lab/scripts/live_check_blocked_imei.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "solver"))

FIELD_MESSAGE = ("خطا در استعلام imei از سامانه همتا : شناسه (IMEI) وارد شده "
                 "قبلا در شبکه داخلی کشور ثبت و فعال شده و امکان ورود آن وجود ندارد")
TRANSIENT_MESSAGE = ("خطا در استعلام imei از سامانه همتا : "
                     "خطا در دریافت پاسخ از سازمان مربوطه")

rows = []


def check(label, ok, detail=""):
    rows.append((label, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"   {detail}" if detail else ""))


def fresh_store(tmp, profile_id="p-blocked"):
    path = os.path.join(tmp, "profiles.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"active_id": profile_id, "profiles": [{
            "id": profile_id, "title": "device already on the network",
            "enabled": True, "run_status": "busy", "runner_id": "r1",
            "runner_pid": 4242, "registration_status": "pending",
            "national": "0000000000", "birth": "1375/01/01",
            "mobile": "09120000000", "imeis": ["352506099000001"],
        }]}, handle, ensure_ascii=False)
    return path


def main():
    import gomrok
    import panel

    print("=== 1. classification ===")
    check("the field message is fatal",
          gomrok.is_imei_fatal_error([FIELD_MESSAGE]))
    check("the field message is NOT retryable",
          not gomrok.is_imei_retryable_error([FIELD_MESSAGE]))
    check("a stale captcha warning cannot rescue it",
          gomrok.is_imei_fatal_error(["کد امنیتی صحیح نمی باشد", FIELD_MESSAGE])
          and not gomrok.is_imei_retryable_error(
              ["کد امنیتی صحیح نمی باشد", FIELD_MESSAGE]))
    check("a genuine transient HAMTA failure is still retryable",
          gomrok.is_imei_retryable_error([TRANSIENT_MESSAGE])
          and not gomrok.is_imei_fatal_error([TRANSIENT_MESSAGE]))

    print("\n=== 2. the robot writes the verdict into the real store ===")
    with tempfile.TemporaryDirectory() as tmp:
        profiles = fresh_store(tmp)
        inputs = os.path.join(tmp, "inputs.json")
        with patch.object(panel, "PROFILES_PATH", profiles), \
                patch.object(panel, "INPUTS_PATH", inputs), \
                patch.object(panel, "_STORE_LOCK_PATH", profiles + ".lock"):
            changed = gomrok._mark_panel_profile_unusable("p-blocked", FIELD_MESSAGE)
            check("the robot's helper reported success", changed)

            with open(profiles, encoding="utf-8") as handle:
                stored = json.load(handle)["profiles"][0]
            check("registration_status is blocked ON DISK",
                  stored.get("registration_status") == "blocked",
                  str(stored.get("registration_status")))
            check("the profile is disabled", stored.get("enabled") is False)
            check("the busy claim was released",
                  stored.get("run_status") == "idle"
                  and not stored.get("runner_id")
                  and not stored.get("runner_pid"),
                  f"run_status={stored.get('run_status')} pid={stored.get('runner_pid')}")
            check("the reason is stored for the operator",
                  "قابل ورود نیست" in (stored.get("runtime_error") or "")
                  and "قبلا در شبکه داخلی کشور" in (stored.get("runtime_error") or ""),
                  (stored.get("runtime_error") or "")[:48] + "...")

            print("\n=== 3. the panel refuses to run it again ===")
            status, payload = panel.start_profile("p-blocked")
            check("start is rejected with 409", status == 409, str(status))
            check("the rejection explains why",
                  "قابل استفاده نیست" in str(payload.get("error", "")),
                  str(payload.get("error"))[:60])

            print("\n=== 4. the operator sees it in the UI ===")
            js = panel.PAGE_JS
            check("the card shows a blocked badge",
                  "registration_status==='blocked'" in js and "غیرقابل ورود" in js)
            check("the card shows the stored reason",
                  "runtime_error" in js and "prof-blocked-detail" in js)
            check("the start button is disabled for a blocked profile",
                  "blocked\" disabled" in js or "blocked' disabled" in js
                  or "blocked\\\" disabled" in js,
                  "looked for a disabled start button")

    print("\n=== 5. a transient failure must NOT block the profile ===")
    with tempfile.TemporaryDirectory() as tmp:
        profiles = fresh_store(tmp, "p-transient")
        inputs = os.path.join(tmp, "inputs.json")
        with patch.object(panel, "PROFILES_PATH", profiles), \
                patch.object(panel, "INPUTS_PATH", inputs), \
                patch.object(panel, "_STORE_LOCK_PATH", profiles + ".lock"):
            # The robot only calls the helper on a FATAL verdict; prove the
            # classifier keeps this one out of that branch.
            if gomrok.is_imei_fatal_error([TRANSIENT_MESSAGE]):
                gomrok._mark_panel_profile_unusable("p-transient", TRANSIENT_MESSAGE)
            with open(profiles, encoding="utf-8") as handle:
                stored = json.load(handle)["profiles"][0]
            check("the profile stays runnable after a transient failure",
                  stored.get("registration_status") == "pending"
                  and stored.get("enabled") is True,
                  f"status={stored.get('registration_status')} enabled={stored.get('enabled')}")

    print("\n----")
    bad = [n for n, ok in rows if not ok]
    print(f"RESULT: {len(rows) - len(bad)}/{len(rows)} checks passed")
    if bad:
        print("FAILURES:", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
