"""Command line interface.

  padlink list                         controllers, connection type and battery
  padlink games                        running games and which profile would be used
  padlink trigger right rigid          set one trigger (left|right|both) right now
  padlink trigger both resistance --start 3 --force 200
  padlink led "#00ffaa"                set the lightbar colour
  padlink led off                      lightbar off
  padlink player 2                     player LEDs (1-4, 'battery', 'off')
  padlink apply cyberpunk              apply a profile by file name
  padlink effects                      list trigger effects
  padlink gui                          open the graphical app
  padlink xbox                         Xbox 360 mode now (real pad hidden) until Ctrl+C
  padlink daemon -v                    run the background service
"""
from __future__ import annotations

import argparse
import sys

from .battery import read_battery
from .control import apply, resolve_player_leds
from .devices import Controller, find_controllers
from .games import detect_games
from .profiles import PROFILE_DIR, load_profiles, parse_color, pick_profile
from .triggers import EFFECTS, TriggerSetting


def _controllers(args) -> list[Controller]:
    ctrls = find_controllers()
    if not ctrls:
        sys.exit("No DualSense/DualShock 4 found. Is it connected (USB or paired over Bluetooth)?")
    if args.index is not None:
        try:
            return [ctrls[args.index]]
        except IndexError:
            sys.exit(f"No controller #{args.index}; run `padlink list`.")
    return ctrls


def _each(args, **kw) -> None:
    for c in _controllers(args):
        try:
            apply(c, **kw)
        except PermissionError:
            sys.exit(f"Permission denied on {c.hidraw}. Install packaging/70-padlink.rules (see README).")


def cmd_list(args) -> None:
    ctrls = find_controllers()
    if not ctrls:
        print("No controllers found.")
    for i, c in enumerate(ctrls):
        print(f"#{i}  {c}   battery: {read_battery(c.battery_path)}")


def cmd_games(args) -> None:
    games = detect_games()
    profiles = load_profiles()
    if not games:
        print("No running Steam/Lutris games detected.")
    for g in games:
        print(f"{g}  exe={sorted(g.exe_names)}")
    p, g = pick_profile(profiles, games)
    print(f"\nProfile: {p.name if p else '(none)'}" + (f"  <- {g.name}" if g else ""))
    print(f"Profiles folder: {PROFILE_DIR}  ({len(profiles)} loaded)")


def cmd_trigger(args) -> None:
    t = TriggerSetting(args.effect, args.start, args.force)
    t.to_bytes()  # validates the effect name
    left = t if args.side in ("left", "both") else None
    right = t if args.side in ("right", "both") else None
    _each(args, left=left, right=right)


def cmd_led(args) -> None:
    rgb = (0, 0, 0) if args.color == "off" else parse_color(args.color)
    _each(args, rgb=rgb)


def cmd_player(args) -> None:
    for c in _controllers(args):
        mask = resolve_player_leds(args.value, read_battery(c.battery_path).percent)
        apply(c, player_leds=mask)


def cmd_apply(args) -> None:
    profiles = {p.path.stem: p for p in load_profiles() if p.path}
    p = profiles.get(args.profile)
    if p is None:
        sys.exit(f"No profile '{args.profile}'. Available: {', '.join(profiles) or 'none'}")
    for c in _controllers(args):
        apply(c, left=p.left, right=p.right, rgb=p.color,
              player_leds=resolve_player_leds(p.player_leds, read_battery(c.battery_path).percent),
              mic_led=p.mic_led)
    print(f"Applied '{p.name}'.")


def cmd_effects(args) -> None:
    print("\n".join(EFFECTS))


def cmd_xbox(args) -> None:
    """Xbox mode in the foreground until Ctrl+C (the background service does this per profile)."""
    import signal
    import time
    from .devices import HidrawWriter
    from .xinput import XboxBridge, write_state
    try:
        import evdev  # noqa: F401
    except ImportError:
        sys.exit("Xbox mode needs python-evdev (Fedora: sudo dnf install python3-evdev)")
    bridges, writers = [], []
    try:
        for c in _controllers(args):
            w = HidrawWriter(c.hidraw)
            writers.append(w)
            b = XboxBridge(c, w.write)
            b.start()
            bridges.append(b)
        write_state([b.ctrl.hidraw for b in bridges])
        print("Xbox mode on. The real controller is hidden from games. Ctrl+C to stop.")
        running = [True]
        signal.signal(signal.SIGTERM, lambda *_: running.__setitem__(0, False))
        while running[0]:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    except PermissionError as e:
        print(f"Permission denied: {e}. Run install.sh (it sets up uinput and controller access).")
    finally:
        for b in bridges:
            b.stop()
        for w in writers:
            w.close()
        write_state([])


def cmd_gui(args) -> None:
    try:
        from .gui.app import main as gui_main
    except (ImportError, ValueError) as e:
        sys.exit(f"GUI needs PyGObject + GTK4 + libadwaita (Fedora: sudo dnf install python3-gobject gtk4 libadwaita): {e}")
    sys.exit(gui_main())


def cmd_daemon(args) -> None:
    from .daemon import Daemon
    Daemon(interval=args.interval, verbose=args.verbose).run()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="padlink", description="DualSense / DS4 companion for Linux")
    ap.add_argument("-i", "--index", type=int, help="only act on controller #N (see `padlink list`)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list").set_defaults(fn=cmd_list)
    sub.add_parser("games").set_defaults(fn=cmd_games)
    sub.add_parser("effects").set_defaults(fn=cmd_effects)

    t = sub.add_parser("trigger")
    t.add_argument("side", choices=["left", "right", "both"])
    t.add_argument("effect", choices=EFFECTS + ("none",))
    t.add_argument("--start", type=int, default=0, help="start position (0-9 zones or 0-255, depends on effect)")
    t.add_argument("--force", type=int, default=255, help="strength 0-255")
    t.set_defaults(fn=cmd_trigger)

    led = sub.add_parser("led")
    led.add_argument("color", help="'#rrggbb' or 'off'")
    led.set_defaults(fn=cmd_led)

    pl = sub.add_parser("player")
    pl.add_argument("value", help="1-4, 'battery', 'off', or a raw 0-31 mask")
    pl.set_defaults(fn=cmd_player)

    a = sub.add_parser("apply")
    a.add_argument("profile", help="profile file name without .toml")
    a.set_defaults(fn=cmd_apply)

    sub.add_parser("gui").set_defaults(fn=cmd_gui)
    sub.add_parser("xbox", help="Xbox 360 mode until Ctrl+C").set_defaults(fn=cmd_xbox)

    d = sub.add_parser("daemon")
    d.add_argument("-v", "--verbose", action="store_true")
    d.add_argument("--interval", type=float, default=2.0)
    d.set_defaults(fn=cmd_daemon)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
