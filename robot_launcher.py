"""Launch a robot script with optional diagnostics enabled before imports."""
import os
import runpy
import sys
import traceback

import system_debug


def main():
    if len(sys.argv) < 2:
        raise SystemExit("robot script is required")
    script = os.path.abspath(sys.argv[1])
    if os.path.dirname(script) != os.path.dirname(os.path.abspath(__file__)):
        raise SystemExit("robot script must be in the project root")
    service = os.path.splitext(os.path.basename(script))[0]
    sys.argv = [script] + sys.argv[2:]
    system_debug.configure(service)
    system_debug.event(f"launcher target={script}")
    try:
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
