"""Launch a robot script with optional diagnostics enabled before imports.

The packaged release runs on an embedded CPython whose `python*._pth` makes the
interpreter ISOLATED: `PYTHONPATH` is ignored and -- unlike a normal install --
the script's own directory is NOT prepended to `sys.path`. Every project import
(`system_debug`, `panel`, `captcha_collector`, the solver packages) would fail
with ModuleNotFoundError. So repair `sys.path` here, before the first project
import, for both this launcher and the robot it runs.
"""
import os
import runpy
import sys
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import system_debug  # noqa: E402  (needs the bootstrap above)


def stop_for_manual_verification(profile_id, runner_id, fingerprint):
    """Record review state without changing the claim, evidence or case stage."""
    import panel
    message = ("Manual verification and case review are required. "
               "Unattended verification is disabled for CRM-linked attempts.")
    try:
        panel.publish_profile_result(profile_id, runner_id, fingerprint,
                                     {"receipt": "", "payment": "", "link": "",
                                      "identity_verified": False})
    except Exception:
        raise panel.ProfileIdentityStopped(message + " Review state could not be saved.") from None
    raise panel.ProfileIdentityStopped(message)


def main():
    if len(sys.argv) < 2:
        raise SystemExit("robot script is required")
    script = os.path.abspath(sys.argv[1])
    if os.path.dirname(script) != os.path.dirname(os.path.abspath(__file__)):
        raise SystemExit("robot script must be in the project root")
    service = os.path.splitext(os.path.basename(script))[0]
    sys.argv = [script] + sys.argv[2:]
    from maintenance_gate import job_lifetime
    with job_lifetime():
        try:
            system_debug.configure(service)
            system_debug.event(f"launcher target={script}")
            runpy.run_path(script, run_name="__main__")
        except SystemExit as exc:
            system_debug.event(f"SYSTEM EXIT code={exc.code}")
            raise
        except BaseException as exc:
            system_debug.event(f"FATAL {type(exc).__name__}: {exc}")
            system_debug.event(traceback.format_exc().rstrip())
            raise


if __name__ == "__main__":
    main()
