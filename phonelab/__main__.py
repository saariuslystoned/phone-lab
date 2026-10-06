"""CLI: `python3 -m phonelab inventory | serve | cua demo | tree | marks | tap | trace | trail`."""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
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


def _ms(start: float) -> int:
    return int((time.time() - start) * 1000)


def run_marks(adb, dumper, args, device_dir: Path, display_lookup=None) -> tuple[int, dict]:
    """Tree + screencap of one logical display → overlay PNG and marks JSON. Returns (exit code, result)."""
    from .marks import render, select

    t0 = time.time()
    reply = dumper.tree(args.logical_id)
    tree_ms = _ms(t0)
    if not reply.get("ok"):
        return 1, {"ok": False, "error": adb.redact(str(reply.get("error", "tree failed"))),
                   "display_id": args.logical_id}
    marks = select(reply, include_system=args.include_system)

    t1 = time.time()
    displays = (display_lookup or inventory)(adb)
    match = [d for d in displays if d.logical_id == args.logical_id]
    if not match:
        return 1, {"ok": False, "error": f"no display with logical id {args.logical_id}", "display_id": args.logical_id}
    display = match[0]
    if display.state == "OFF":
        return 1, {"ok": False, "error": f"logical display {args.logical_id} is OFF", "display_id": args.logical_id}
    png = adb.screencap(display.sf_id)
    screencap_ms = _ms(t1)
    if png is None:
        return 1, {"ok": False, "error": "screencap returned no PNG", "display_id": args.logical_id}

    t2 = time.time()
    crop = 0 if args.no_crop else display.status_bar_px
    overlay = render(png, marks, crop_status_bar_px=crop)
    render_ms = _ms(t2)

    if args.out:
        out = Path(args.out)
    else:
        stamp = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"
        out = device_dir / "marks" / f"marks-{args.logical_id}-{stamp}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(overlay)
    result = {
        "ok": True, "display_id": args.logical_id, "sf_id": display.sf_id, "unique_id": display.unique_id,
        "include_system": bool(args.include_system), "crop_status_bar_px": crop,
        "count": len(marks), "marks": marks, "png": str(out),
        "timings_ms": {"tree": tree_ms, "tree_device": reply.get("cost_ms"), "screencap": screencap_ms,
                       "render": render_ms, "total": _ms(t0)},
    }
    out.with_suffix(".json").write_text(json.dumps(result, indent=2))
    result["json"] = str(out.with_suffix(".json"))
    return 0, result


def run_tap(adb, dumper, args, packages: tuple[str, ...]) -> tuple[int, dict]:
    """Fresh tree → resolve '#N' or ref → guard → `input -d <id> tap x y`. Exit 0 ok, 1 error, 3 refused."""
    from .marks import resolve, select

    t0 = time.time()
    reply = dumper.tree(args.logical_id)
    tree_ms = _ms(t0)
    base = {"display_id": args.logical_id, "target": args.target}
    if not reply.get("ok"):
        return 1, {"ok": False, **base, "error": adb.redact(str(reply.get("error", "tree failed")))}
    marks = select(reply, include_system=args.include_system)
    try:
        mark = resolve(marks, args.target)
    except ValueError as exc:
        return 1, {"ok": False, **base, "error": str(exc)}
    if mark is None:
        return 1, {"ok": False, **base, "error": f"{args.target} not found among {len(marks)} marks"}
    found = {"n": mark["n"], "ref": mark["ref"], "label": mark["label"], "tap": mark["tap"]}
    if args.expect_ref and mark["ref"] != args.expect_ref:
        return 3, {"ok": False, **base, **found, "refused": "stale",
                   "error": f"{args.target} is now ref {mark['ref']}, expected {args.expect_ref}; re-run marks"}
    if mark.get("package") not in packages:
        return 3, {"ok": False, **base, **found, "refused": "package",
                   "error": f"package {mark.get('package')!r} not allowed (add it with --app)"}
    t1 = time.time()
    adb.shell("input", "-d", str(args.logical_id), "tap", str(mark["tap"][0]), str(mark["tap"][1]))
    tap_ms = _ms(t1)
    return 0, {"ok": True, **base, **found, "timings_ms": {"tree": tree_ms, "tap": tap_ms, "total": _ms(t0)}}


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
    tree.add_argument("--app", action="append", default=[], help="additional package name for text/act")

    mk = sub.add_parser("marks", help="number the tap targets of a display; write an overlay PNG, print JSON",
                        parents=[device])
    mk.add_argument("logical_id", type=int, help="logical display id")
    mk.add_argument("--out", help="overlay PNG path (default runs/phone-lab-runs/<device-tag>/marks/...)")
    mk.add_argument("--include-system", action="store_true",
                    help="also mark system windows (status bar, navigation, IME); off by default")
    mk.add_argument("--no-crop", action="store_true", help="keep the status bar in the overlay")
    mk.add_argument("--treedump-jar", help="path to treedump.jar (or set PHONELAB_TREEDUMP_JAR)")
    mk.add_argument("--app", action="append", default=[], help="additional package name for text/act")
    mk.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR)
    mk.add_argument("--device-tag", help="override the device tag (derived from model by default)")

    tp = sub.add_parser("tap", help="tap a mark ('#N') or ref on a display with `input -d`", parents=[device])
    tp.add_argument("logical_id", type=int, help="logical display id")
    tp.add_argument("target", help="'#N' (mark number) or a ref")
    tp.add_argument("--expect-ref", help="refuse unless the target resolves to this ref (stale-overlay guard)")
    tp.add_argument("--include-system", action="store_true",
                    help="number system windows too (must match the `marks` call)")
    tp.add_argument("--treedump-jar", help="path to treedump.jar (or set PHONELAB_TREEDUMP_JAR)")
    tp.add_argument("--app", action="append", default=[],
                    help="additional package that may be tapped (default: the two Cua apps only)")

    srv = sub.add_parser("serve", help="run the live multi-display viewer", parents=[device])
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8791, help="0 picks a free port and prints it")
    srv.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR)
    srv.add_argument("--device-tag", help="override the device tag (derived from model by default)")
    srv.add_argument("--max-height", type=int, default=1000, help="preview JPEG height cap")
    srv.add_argument("--driver", default=os.environ.get("PHONELAB_CUA_DRIVER"),
                     help="path to the cua-driver binary (or set PHONELAB_CUA_DRIVER)")
    srv.add_argument("--treedump-jar", help="path to treedump.jar (or set PHONELAB_TREEDUMP_JAR)")
    srv.add_argument("--app", action="append", default=[], help="additional package name for text/act")
    srv.add_argument("--stream-human", action="store_true",
                     help="stream the human display (logical 0) with screenrecord h264 instead of screencap; "
                          "opt-in, needs ffmpeg on PATH")
    srv.add_argument("--stream-bitrate", type=int, default=4_000_000, help="screenrecord --bit-rate")
    srv.add_argument("--stream-max-fps", type=float, default=5.0, help="cap on delivered stream frames per second")

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
    rec.add_argument("--cua-size", help="Cua display WIDTHxHEIGHT (driver default 1080x1920)")
    rec.add_argument("--cua-density", type=int, help="Cua display density dpi (driver default 320)")
    rec.add_argument("--app", action="append", default=[], help="additional package name for text/act")

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
    rep.add_argument("--cua-size", help="Cua display WIDTHxHEIGHT (driver default 1080x1920)")
    rep.add_argument("--cua-density", type=int, help="Cua display density dpi (driver default 320)")
    rep.add_argument("--app", action="append", default=[], help="additional package name for text/act")
    rep.add_argument(
        "--max-heal-px",
        type=int,
        default=120,
        help="self-heal a missing ref within this many px (0 = only in place)",
    )

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
        from .tree import TreeDumper, TreeError, packages_with
        from .refs import assign_refs
        try:
            pkgs = packages_with(args.app)
        except TreeError as exc:
            print(f"error: {adb.redact(str(exc))}", file=sys.stderr)
            return 2
        dumper = TreeDumper(adb, jar, text_packages=pkgs, act_packages=pkgs)
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
    if args.command in ("marks", "tap"):
        jar = _resolve_treedump_jar(args.treedump_jar)
        if not jar:
            print("error: --treedump-jar (or PHONELAB_TREEDUMP_JAR) is required", file=sys.stderr)
            return 2
        from .tree import TreeDumper, TreeError, packages_with
        try:
            pkgs = packages_with(args.app)
        except TreeError as exc:
            print(f"error: {adb.redact(str(exc))}", file=sys.stderr)
            return 2
        dumper = TreeDumper(adb, jar, text_packages=pkgs, act_packages=pkgs)
        try:
            dumper.start()
            if args.command == "marks":
                code, result = run_marks(adb, dumper, args, device_dir)
            else:
                code, result = run_tap(adb, dumper, args, pkgs)
            print(adb.redact(json.dumps(result, indent=2)))
            return code
        except (TreeError, AdbError) as exc:
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
                     driver=driver, treedump_jar=jar, stream_human=args.stream_human,
                     stream_bitrate=args.stream_bitrate, stream_max_fps=args.stream_max_fps,
                     apps=args.app)
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
                    cua_size=args.cua_size,
                    cua_density=args.cua_density,
                    apps=getattr(args, "app", []),
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
                    max_heal_px=args.max_heal_px,
                    cua_size=args.cua_size,
                    cua_density=args.cua_density,
                    apps=getattr(args, "app", []),
                )
        except Exception as exc:
            print(f"error: {adb.redact(str(exc))}", file=sys.stderr)
            return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
