"""Launch the CURRENT panel code on a scratch port with a REAL data copy.

The operator's production panel (v1.0.12 from the install dir) owns port
8750, so feature verification runs here first: an isolated ThreadingHTTPServer
serving the in-repo panel.Handler with IRICA_DATA_DIR pointed at a temp copy
of the real data dir. Read-only for production; the copy gets mutated freely.
"""
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 18760
DATA = os.environ.get("IRICA_DATA_DIR",
                      os.path.join(os.environ["LOCALAPPDATA"], "irica"))

tmp = tempfile.mkdtemp(prefix="irica-e2e-")
for name in ("profiles.json", "estelam.json", "telegram_config.json"):
    src = os.path.join(DATA, name)
    if os.path.isfile(src):
        shutil.copy2(src, os.path.join(tmp, name))

os.environ["IRICA_DATA_DIR"] = tmp

import panel  # noqa: E402  (after env override)

srv = panel.ThreadingHTTPServer(("127.0.0.1", PORT), panel.Handler)
print(f"scratch panel: http://127.0.0.1:{PORT}  (data copy: {tmp})", flush=True)
srv.serve_forever()
