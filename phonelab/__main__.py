"""CLI: `python3 -m phonelab inventory | serve | cua demo | tree | trace | trail`."""
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


def _resolve_treedump_jar(cli_val: str | None) -> Path | None:
    if cli_val:
        return Path(cli_val)
    env_val = os.environ.get("PHONELAB_TREEDUMP_JAR")
    if env_val:
        return Path(env_val)
    repo_jar = Path(__file__).resolve().parent.parent / "tools" / "treedump" / "build" / "treedump.jar"
    if repo_jar.is_file():
        return repo_jar
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="phonelab", description="phone-lab: watch and drive Android displays.")
    sub = parser.add_subparsers(dest="command", required=True)
    device = argparse.ArgumentParser(add_help=False)
    device.add_argument("--serial", help="device serial; also read from ANDROID_SERIAL")
    device.add_argument("--model", help='pick the device by model name, e.g. "Pixel 10 Pro Fold"')
    device.add_argument("--allow-emulators", action="store_true",
                        help="let an emulator-* device be chosen implicitly (never the default on a shared machine)")

    sub.add_parser("inventory", help="print every display as JSON", parents=[device])

    tree = sub.add_parser("tree", help="dump element tree with refs as JSON", parents=[device])
    tree.add_argument("logical_id", type=int, help="logical display id")
    tree.add_argument("--treedump-jar", help="path to treedump.jar (or set PHONELAB_TREEDUMP_JAR)")

    srv = sub.add_parser("serve", help="run the live multi-display viewer", parents=[device])
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8791, help="0 picks a free port and prints it")
    srv.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR)
    srv.add_argument("--device-tag", help="override the device tag (derived from model by default)")
    srv.add_argument("--max-height", type=int, default=1000, help="preview JPEG height cap")
    srv.add_argument("--driver", default=os.environ.get("PHONELAB_CUA_DRIVER"),
                     help="path to the cua-driver binary (or set PHONELAB_CUA_DRIVER)")
    srv.add_argument("--treedump-jar", help="path to treedump.jar (or set PHONELAB_TREEDUMP_JAR)")

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
    demo.add_argument("--device-tag", help="override the device tag (derived from model by default)")

    trail = sub.add_parser("trail", help="record and replay trails")
    trail_sub = trail.add_subparsers(dest="trail_command", required=True)

    rec = trail_sub.add_parser("record", help="record a trail from a script", parents=[device])
    rec.add_argument("script", help="path to script file or - for stdin")
    rec.add_argument("--name", required=True, help="trail name")
    rec.add_argument("--driver", default=os.environ.get("PHONELAB_CUA_DRIVER"),
                     help="path to the cua-driver binary (or set PHONELAB_CUA_DRIVER)")
    rec.add_argument("--treedump-jar", help="path to treedump.jar (or set PHONELAB_TREEDUMP_JAR)")
    rec.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR)
    rec.add_argument("--device-tag", help="override the device tag (derived from model by default)")
    rec.add_argument("--trails-dir", help="override trails destination directory")
    rec.add_argument("--agent-only", action="store_true", help="skip human display capture")

    rep = trail_sub.add_parser("replay", help="replay a trail", parents=[device])
    rep.add_argument("trail", help="path to trail.json")
    rep.add_argument("--times", type=int, default=1, help="number of times to replay")
    rep.add_argument("--source-run-id", help="recording run id this replay came from")
    rep.add_argument("--continue-on-fail", action="store_true", help="keep running steps after a failure")
    rep.add_argument("--driver", default=os.environ.get("PHONELAB_CUA_DRIVER"),
                     help="path to the cua-driver binary (or set PHONELAB_CUA_DRIVER)")
    rep.add_argument("--treedump-jar", help="path to treedump.jar (or set PHONELAB_TREEDUMP_JAR)")
    rep.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR)
    rep.add_argument("--device-tag", help="override the device tag (derived from model by default)")
    rep.add_argument("--agent-only", action="store_true", help="skip human display capture")

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
    if args.command == "trail":
        driver_val = getattr(args, "driver", None) or os.environ.get("PHONELAB_CUA_DRIVER")
        if not driver_val:
            print("error: --driver PATH (or PHONELAB_CUA_DRIVER) is required", file=sys.stderr)
            return 2
        jar = _resolve_treedump_jar(getattr(args, "treedump_jar", None))
        if not jar:
            print("error: --treedump-jar (or PHONELAB_TREEDUMP_JAR) is required", file=sys.stderr)
            return 2

    try:
        adb = Adb(args.serial, model=args.model, allow_emulators=args.allow_emulators).resolve()
    except AdbError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    tag = getattr(args, "device_tag", None) or adb.tag
    adb.tag = tag
    runs_root = Path(getattr(args, "runs_dir", DEFAULT_RUNS_DIR))
    device_dir = runs_root / tag
    registry = Registry(runs_root, tag)

    if args.command == "inventory":
        print(json.dumps([to_json(d) for d in inventory(adb)], indent=2))
        return 0
    if args.command == "tree":
        jar = _resolve_treedump_jar(args.treedump_jar)
        if not jar:
            print("error: --treedump-jar (or PHONELAB_TREEDUMP_JAR) is required", file=sys.stderr)
            return 2
        from .tree import TreeDumper, TreeError
        from .refs import assign_refs
        dumper = TreeDumper(adb, jar)
        try:
            dumper.start()
            reply = dumper.tree(args.logical_id)
            if reply.get("ok"):
                assign_refs(reply)
            print(json.dumps(reply, indent=2))
            return 0 if reply.get("ok") else 1
        except TreeError as exc:
            print(f"error: {adb.redact(str(exc))}", file=sys.stderr)
            return 1
        finally:
            dumper.stop()
    if args.command == "serve":
        from .server import serve

        def _terminate(signum, frame):  # `kill <pid>` must stop the device-side treedump process too
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, _terminate)
        jar = _resolve_treedump_jar(args.treedump_jar)
        driver = Path(args.driver) if args.driver else None
        return serve(adb, registry, args.host, args.port, device_dir, args.max_height,
                     driver=driver, treedump_jar=jar)
    if args.command == "cua":
        from .cua import CuaDriver, demo as run_demo

        def _terminate(signum, frame):  # let `kill <pid>` stop the session cleanly
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, _terminate)
        return run_demo(adb, CuaDriver(adb, Path(args.driver)), registry,
                        args.duration, args.tap_every, not args.no_taps)
    if args.command == "trail":
        from .replay import AdbBackend, record, replay

        def _terminate(signum, frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, _terminate)

        driver_val = getattr(args, "driver", None) or os.environ.get("PHONELAB_CUA_DRIVER")
        jar = _resolve_treedump_jar(getattr(args, "treedump_jar", None))
        backend = AdbBackend(adb, Path(driver_val), jar)  # type: ignore
        trails_dir = Path(args.trails_dir) if getattr(args, "trails_dir", None) else device_dir / "trails"

        try:
            if args.trail_command == "record":
                if args.script == "-":
                    script_text = sys.stdin.read()
                else:
                    script_text = Path(args.script).read_text()
                return record(
                    backend,
                    registry,
                    device_dir,
                    trails_dir,
                    args.name,
                    script_text,
                    capture_human=not args.agent_only,
                )
            if args.trail_command == "replay":
                return replay(
                    backend,
                    registry,
                    device_dir,
                    Path(args.trail),
                    times=args.times,
                    source_run_id=args.source_run_id,
                    capture_human=not args.agent_only,
                    stop_on_fail=not args.continue_on_fail,
                )
        except Exception as exc:
            print(f"error: {adb.redact(str(exc))}", file=sys.stderr)
            return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
