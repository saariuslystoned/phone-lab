"""CLI: `python3 -m phonelab inventory | serve | cua demo | trace`."""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from pathlib import Path

from .adb import Adb, AdbError
from .displays import inventory, to_json
from .sessions import Registry

DEFAULT_RUNS_DIR = "runs/phone-lab-runs"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="phonelab", description="phone-lab: watch and drive Android displays.")
    sub = parser.add_subparsers(dest="command", required=True)
    device = argparse.ArgumentParser(add_help=False)
    device.add_argument("--serial", help="device serial; also read from ANDROID_SERIAL")
    device.add_argument("--model", help='pick the device by model name, e.g. "Pixel 10 Pro Fold"')
    device.add_argument("--allow-emulators", action="store_true",
                        help="let an emulator-* device be chosen implicitly (never the default on a shared machine)")

    sub.add_parser("inventory", help="print every display as JSON", parents=[device])

    srv = sub.add_parser("serve", help="run the live multi-display viewer", parents=[device])
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8791)
    srv.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR)
    srv.add_argument("--max-height", type=int, default=1000, help="preview JPEG height cap")

    trc = sub.add_parser("trace", help="browse recorded runs and diffs")
    trc.add_argument("--host", default="127.0.0.1")
    trc.add_argument("--port", type=int, default=8792)
    trc.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR)

    cua = sub.add_parser("cua", help="cua-driver helpers")
    cua_sub = cua.add_subparsers(dest="cua_command", required=True)
    demo = cua_sub.add_parser("demo", help="drive the synthetic fixture on a Cua display", parents=[device])
    demo.add_argument("--driver", default=os.environ.get("PHONELAB_CUA_DRIVER"),
                      help="path to the cua-driver binary (or set PHONELAB_CUA_DRIVER)")
    demo.add_argument("--duration", type=int, default=300, help="seconds to keep the session alive")
    demo.add_argument("--tap-every", type=float, default=8.0, help="seconds between increment taps")
    demo.add_argument("--no-taps", action="store_true")
    demo.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "trace":
        from .trace import serve_trace
        serve_trace(args.host, args.port, Path(args.runs_dir))
        return 0
    if args.command == "cua" and not args.driver:
        print("error: --driver PATH (or PHONELAB_CUA_DRIVER) is required", file=sys.stderr)
        return 2
    try:
        adb = Adb(args.serial, model=args.model, allow_emulators=args.allow_emulators).resolve()
    except AdbError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    runs_dir = Path(getattr(args, "runs_dir", DEFAULT_RUNS_DIR))
    if args.command == "inventory":
        print(json.dumps([to_json(d) for d in inventory(adb)], indent=2))
        return 0
    if args.command == "serve":
        from .server import serve
        serve(adb, Registry(runs_dir), args.host, args.port, runs_dir, args.max_height)
        return 0
    if args.command == "cua":
        from .cua import CuaDriver, demo as run_demo

        def _terminate(signum, frame):  # let `kill <pid>` stop the session cleanly
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, _terminate)
        return run_demo(adb, CuaDriver(adb, Path(args.driver)), Registry(runs_dir),
                        args.duration, args.tap_every, not args.no_taps)
    return 2


if __name__ == "__main__":
    sys.exit(main())
