"""estelam_all.py
Runner-e «anjam-e hame-ye estelam-ha»: aval estelam-e GOMROK, sepas estelam-e HAMTA.
Panel faghat YEK pid darad (pid-e hamin runner) — process-haye farzand ba
taskkill /T baste mishavand, pas dokme-ye «توقف»-e panel har do marhale ra mikoshad.

Estefade:
    python estelam_all.py                    # hame-ye device-haye pending
    python estelam_all.py --device-id X      # faghat yek device
"""
import argparse
import os
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _child_env():
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env.pop("VIRTUAL_ENV", None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _run(script, extra):
    cmd = [sys.executable, os.path.join(_HERE, script)] + extra
    print(f"\n=== ejra: {' '.join(cmd[1:])} ===", flush=True)
    try:
        return subprocess.call(cmd, cwd=_HERE, env=_child_env(),
                               stdin=subprocess.DEVNULL)
    except Exception as e:
        print(f">>> KHATA dar ejra-ye {script}: {e}", flush=True)
        return 1


def main():
    ap = argparse.ArgumentParser(description="Anjam-e hame-ye estelam-ha (gomrok + hamta)")
    ap.add_argument("--device-id", help="faghat hamin device")
    a = ap.parse_args()
    dev = ["--device-id", str(a.device_id)] if a.device_id else []

    rc1 = _run("estelam_gomrok.py", dev)
    print(f">>> estelam-e gomrok tamam (exit={rc1})", flush=True)
    # HAMTA hamishe ejra mishavad — ta'khir-e gomrok nabayad estelam-e asalat ra bebandad
    rc2 = _run("estelam_hamta.py", ["--from-store"] + dev)
    print(f">>> estelam-e hamta tamam (exit={rc2})", flush=True)
    return 0 if (rc1 == 0 and rc2 == 0) else 1


if __name__ == "__main__":
    sys.exit(main())
