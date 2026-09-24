"""Browser-verification helper — a throwaway CRM panel on a scratch data dir.

Not part of the product. It exists so the case-list period filter can be
driven in a real browser against mock data, and it is deliberately built to
make that safe:

- `IRICA_DATA_DIR` is set before `app_paths` is imported, so the store, the
  logs and the profile files all land in a throwaway directory; the live data
  directory is never opened.
- `panel.main()` is NOT called: `crm_scheduler_start()` would launch the real
  periodic inquiry robots against the real sites. Only the HTTP handler runs.
- no operator account is seeded. The helper mints its own in-memory session
  and prints the token, so no credential appears in any file.

Usage (the port and data dir can be overridden for a second instance):

    python lab/scripts/serve_mock_panel.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

PORT = int(os.environ.get("IRICA_MOCK_PORT") or "8791")
DATA_DIR = os.environ.get("IRICA_MOCK_DATA") or tempfile.mkdtemp(
    prefix="irica_mock_")
os.environ["IRICA_DATA_DIR"] = DATA_DIR
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import crm  # noqa: E402  (needs IRICA_DATA_DIR set first)
import panel  # noqa: E402
import panel_auth  # noqa: E402

#: (name, serial, stage) — the first three are registered in period 1 (which
#: is then closed), the last three in period 2 (the open one). The stages are
#: chosen so the filter's four readings are all reachable: the default set is
#: the in-flight cases (A, D), the current period adds its finished ones
#: (E, F), and period 1 as a list holds all three of its cases.
CASES = [
    ("دستگاه آلفا", "111111111111111", "inquiry"),
    ("دستگاه بتا", "222222222222222", "done"),
    ("دستگاه گاما", "333333333333333", "problem"),
    ("دستگاه دلتا", "444444444444444", "inquiry"),
    ("دستگاه اپسیلون", "555555555555555", "done"),
    ("دستگاه زتا", "666666666666666", "closed"),
]


def seed():
    """Two periods and six cases, through the real CRM API where it exists."""
    first, errors = crm.open_period(actor="mock")
    assert not errors, errors
    for name, serial, _stage in CASES[:3]:
        device, errors = crm.add_device({"name": name, "serial1": serial},
                                        require_period=True)
        assert device, errors
    crm.close_period(first["id"], actor="mock", reason="mock: period closed")

    second, errors = crm.open_period(actor="mock")
    assert not errors, errors
    for name, serial, _stage in CASES[3:]:
        device, errors = crm.add_device({"name": name, "serial1": serial},
                                        require_period=True)
        assert device, errors

    # Stages are set directly: walking the real stage machine would need the
    # inquiry gates (green hamta/gomrok rows) that have nothing to do with what
    # is being verified here.
    data = crm.store_read()
    stages = {serial: stage for _name, serial, stage in CASES}
    periods = {serial: (first if index < 3 else second)
               for index, (_name, serial, _stage) in enumerate(CASES)}
    for device in data.get("devices", []):
        serial = device.get("serial1")
        if serial in stages:
            device["stage"] = stages[serial]
            device["period_id"] = periods[serial]["id"]
    crm.store_write(data)
    return first, second


def main():
    first, second = seed()
    token = panel_auth.create_session("mock-operator")
    print("mock data dir    :", DATA_DIR)
    print("period 1 (closed):", first["id"], "| period 2 (open):", second["id"])
    print("session token    :", token)
    print("URL              : http://127.0.0.1:%d/crm" % PORT)
    sys.stdout.flush()
    httpd = panel.ThreadingHTTPServer(("127.0.0.1", PORT), panel.Handler)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
