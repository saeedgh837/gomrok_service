"""Verify installed IRICA files against repo using LF-normalized SHA-256."""
import hashlib
import os
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
INSTALL = os.path.expandvars(r"%LOCALAPPDATA%\Programs\irica")

CHECK_FILES = [
    "VERSION",
    "panel.py",
    "telegram_notify.py",
    "crm.py",
    "panel_auth.py",
    "app_paths.py",
    "cloud_sync/apply.py",
    "cloud_sync/errors.py",
    "cloud_sync/importer.py",
    "cloud_sync/mapper.py",
    "cloud_sync/state.py",
]

def lf_hash(path):
    with open(path, "rb") as f:
        data = f.read().replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()

def main():
    if not os.path.isdir(INSTALL):
        print(f"FAIL: Install dir not found: {INSTALL}")
        return 1

    all_ok = True
    for rel in CHECK_FILES:
        repo_file = os.path.join(REPO, rel)
        inst_file = os.path.join(INSTALL, rel)
        if not os.path.isfile(inst_file):
            print(f"MISSING in install: {rel}")
            all_ok = False
            continue
        h_repo = lf_hash(repo_file)
        h_inst = lf_hash(inst_file)
        if h_repo == h_inst:
            print(f"OK   {rel} ({h_repo[:12]}...)")
        else:
            print(f"DIFF {rel}: repo={h_repo[:12]} inst={h_inst[:12]}")
            all_ok = False

    if all_ok:
        print("ALL VERIFIED: 100% hash match between repo and installed copy!")
        return 0
    else:
        print("VERIFICATION FAILED")
        return 1

if __name__ == "__main__":
    sys.exit(main())
