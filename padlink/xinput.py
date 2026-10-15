"""Xbox mode: present the controller to games as an Xbox 360 pad (what XInput sees under Proton).

While Xbox mode is on, the real controller "sleeps":
  * its evdev nodes (gamepad, motion sensors, touch pad) are grabbed with EVIOCGRAB, so no other
    program receives their events;
  * its /dev/hidraw*, /dev/input/event* and /dev/input/js* nodes are made inaccessible to games through the
    small root helper `padlink-hide` (installed by install.sh). That's the important part, because Steam,
    SDL and Proton open the DualSense through hidraw and would otherwise still see it;
  * padlink opens everything it needs *before* hiding, and already-open file descriptors keep working.
When Xbox mode turns off (profile changes, game exits, padlink stops), everything is ungrabbed and
the helper restores the original permissions, so the DualSense wakes up again.

Rumble that games send to the virtual pad is forwarded to the controller's motors.
"""
from __future__ import annotations

import os
import select
import shutil
import subprocess
import threading
from pathlib import Path

from .devices import Controller
from .reports import dualsense_report, ds4_report

HELPER = "/usr/local/libexec/padlink-hide"
STATE_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "padlink"
STATE_FILE = STATE_DIR / "xbox-mode"

# Linux input constants (stable kernel ABI; hard-coded so the mapping is testable without evdev)
EV_SYN, EV_KEY, EV_ABS, EV_FF, EV_UINPUT = 0x00, 0x01, 0x03, 0x15, 0x0101
UI_FF_UPLOAD, UI_FF_ERASE = 1, 2
ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ, ABS_HAT0X, ABS_HAT0Y = 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x10, 0x11
BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST = 0x130, 0x131, 0x133, 0x134
BTN_TL, BTN_TR, BTN_TL2, BTN_TR2 = 0x136, 0x137, 0x138, 0x139
BTN_SELECT, BTN_START, BTN_MODE, BTN_THUMBL, BTN_THUMBR = 0x13A, 0x13B, 0x13C, 0x13D, 0x13E
FF_RUMBLE = 0x50

XBOX_NAME = "Microsoft X-Box 360 pad"
XBOX_VID, XBOX_PID, XBOX_VER = 0x045E, 0x028E, 0x0114     # same IDs as a wired 360 pad on the xpad driver

# hid-playstation -> xpad. Note the swap: hid-playstation reports Triangle as BTN_NORTH and Square
# as BTN_WEST (positional), but xpad reports Xbox X (left) as BTN_X (=BTN_NORTH) and Y (top) as
# BTN_Y (=BTN_WEST). SDL/Proton map 045E:028E by xpad's codes, so without the swap
# Triangle and Square would come out reversed in games.
BUTTON_MAP = {
    BTN_SOUTH: BTN_SOUTH,     # Cross    -> A
    BTN_EAST: BTN_EAST,       # Circle   -> B
    BTN_WEST: BTN_NORTH,      # Square   -> X
    BTN_NORTH: BTN_WEST,      # Triangle -> Y
    BTN_TL: BTN_TL,           # L1 -> LB
    BTN_TR: BTN_TR,           # R1 -> RB
    BTN_SELECT: BTN_SELECT,   # Create  -> Back
    BTN_START: BTN_START,     # Options -> Start
    BTN_MODE: BTN_MODE,       # PS      -> Guide
    BTN_THUMBL: BTN_THUMBL,   # L3
    BTN_THUMBR: BTN_THUMBR,   # R3
}
STICKS = {ABS_X, ABS_Y, ABS_RX, ABS_RY}
PASS_ABS = {ABS_Z, ABS_RZ, ABS_HAT0X, ABS_HAT0Y}       # triggers 0-255 and d-pad -1..1 are identical


def translate(etype: int, code: int, value: int) -> tuple[int, int, int] | None:
    """One hid-playstation event -> one xpad-style event (or None to drop it)."""
    if etype == EV_KEY:
        out = BUTTON_MAP.get(code)
        return (EV_KEY, out, value) if out is not None else None    # L2/R2 digital bits dropped: analog covers them
    if etype == EV_ABS:
        if code in STICKS:     # 0..255 centre 128 -> -32768..32767 (both have Y growing downwards)
            v = (max(0, min(255, value)) * 65535 + 127) // 255 - 32768     # 0 -> -32768, 255 -> 32767
            return EV_ABS, code, v
        if code in PASS_ABS:
            return EV_ABS, code, value
        return None
    if etype == EV_SYN:
        return EV_SYN, code, value
    return None


def controller_nodes(ctrl: Controller) -> dict[str, list[str]]:
    """The evdev/joystick nodes that belong to this controller: {'gamepad': [...], 'other': [...], 'js': [...]}."""
    out: dict[str, list[str]] = {"gamepad": [], "other": [], "js": []}
    input_dir = ctrl.sys_path / "input"
    if not input_dir.is_dir():
        return out
    for inp in sorted(input_dir.iterdir()):
        name = (inp / "name").read_text().strip() if (inp / "name").exists() else ""
        is_pad = not any(w in name for w in ("Motion Sensors", "Touchpad"))
        for node in sorted(inp.iterdir()):
            if node.name.startswith("event"):
                out["gamepad" if is_pad else "other"].append(f"/dev/input/{node.name}")
            elif node.name.startswith("js"):
                out["js"].append(f"/dev/input/{node.name}")
    return out


def run_helper(action: str, nodes: list[str]) -> tuple[bool, str]:
    if not os.path.exists(HELPER):
        return False, "padlink-hide helper is not installed (run install.sh)"
    cmd = [HELPER, action, *[os.path.basename(n) for n in nodes]]
    if os.geteuid() != 0:
        if not shutil.which("sudo"):
            return False, "sudo not found"
        cmd = ["sudo", "-n", *cmd]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def write_state(active: list[str]) -> None:
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        if active:
            STATE_FILE.write_text("\n".join(active) + "\n")
        elif STATE_FILE.exists():
            STATE_FILE.unlink()
    except OSError:
        pass


def xbox_mode_active() -> list[str]:
    try:
        return [l for l in STATE_FILE.read_text().splitlines() if l]
    except OSError:
        return []


class RumbleForwarder:
    """Tracks force-feedback effects games upload to the virtual pad and drives the real motors."""

    def __init__(self, send):
        self.send = send                     # callable(strong 0-255, weak 0-255)
        self.effects: dict[int, tuple[int, int, int]] = {}   # id -> (strong, weak, length_ms)
        self.playing: set[int] = set()
        self._timers: dict[int, threading.Timer] = {}
        self._lock = threading.Lock()

    def upload(self, effect_id: int, strong: int, weak: int, length_ms: int) -> None:
        with self._lock:
            self.effects[effect_id] = (strong >> 8, weak >> 8, length_ms)
            if effect_id in self.playing:
                self._update()

    def erase(self, effect_id: int) -> None:
        with self._lock:
            self.effects.pop(effect_id, None)
            self.playing.discard(effect_id)
            self._update()

    def play(self, effect_id: int, on: bool) -> None:
        with self._lock:
            t = self._timers.pop(effect_id, None)
            if t:
                t.cancel()
            if on and effect_id in self.effects:
                self.playing.add(effect_id)
                length = self.effects[effect_id][2]
                if 0 < length < 0xFFFF:
                    t = threading.Timer(length / 1000, self.play, (effect_id, False))
                    t.daemon = True
                    self._timers[effect_id] = t
                    t.start()
            else:
                self.playing.discard(effect_id)
            self._update()

    def _update(self) -> None:
        strong = max((self.effects[i][0] for i in self.playing if i in self.effects), default=0)
        weak = max((self.effects[i][1] for i in self.playing if i in self.effects), default=0)
        self.send(strong, weak)

    def stop_all(self) -> None:
        with self._lock:
            for t in self._timers.values():
                t.cancel()
            self._timers.clear()
            self.playing.clear()
        self.send(0, 0)


class XboxBridge:
    """Runs Xbox mode for one controller in a background thread."""

    def __init__(self, ctrl: Controller, write_report, log=print):
        self.ctrl = ctrl
        self.write_report = write_report     # callable(bytes) on an hidraw fd opened before hiding
        self.log = log
        self.hidden: list[str] = []
        self.warning: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._devices = []
        self._ui = None
        self.rumble = RumbleForwarder(self._send_rumble)

    def _send_rumble(self, strong: int, weak: int) -> None:
        c = self.ctrl
        # DualSense/DS4: "right" = light/fast motor (weak), "left" = heavy/slow motor (strong)
        report = (dualsense_report(c.is_bluetooth, rumble=(weak, strong)) if c.is_dualsense
                  else ds4_report(c.is_bluetooth, rumble=(weak, strong)))
        try:
            self.write_report(report)
        except OSError:
            pass

    def start(self) -> None:
        import evdev
        from evdev import AbsInfo, UInput

        nodes = controller_nodes(self.ctrl)
        if not nodes["gamepad"]:
            raise RuntimeError(f"no gamepad input node for {self.ctrl}")
        # 1. open + grab every input node of the controller (we keep reading the gamepad one)
        for path in nodes["gamepad"] + nodes["other"]:
            d = evdev.InputDevice(path)
            d.grab()
            self._devices.append(d)
        self.pad = self._devices[0]

        # 2. the virtual Xbox 360 pad
        stick = AbsInfo(0, -32768, 32767, 16, 128, 0)
        trig = AbsInfo(0, 0, 255, 0, 0, 0)
        hat = AbsInfo(0, -1, 1, 0, 0, 0)
        caps = {
            EV_KEY: sorted(set(BUTTON_MAP.values())),
            EV_ABS: [(ABS_X, stick), (ABS_Y, stick), (ABS_RX, stick), (ABS_RY, stick),
                     (ABS_Z, trig), (ABS_RZ, trig), (ABS_HAT0X, hat), (ABS_HAT0Y, hat)],
            EV_FF: [FF_RUMBLE],
        }
        self._ui = UInput(caps, name=XBOX_NAME, vendor=XBOX_VID, product=XBOX_PID,
                          version=XBOX_VER, bustype=0x03, max_effects=16)

        # 3. put the real controller to sleep for everyone else
        to_hide = [self.ctrl.hidraw] + nodes["gamepad"] + nodes["other"] + nodes["js"]
        ok, msg = run_helper("hide", to_hide)
        if ok:
            self.hidden = to_hide
        else:
            self.warning = f"couldn't hide the controller from games ({msg}); games may see two pads"
            self.log(self.warning)

        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.log(f"Xbox mode on for {self.ctrl}")

    def _loop(self) -> None:
        from evdev import ecodes
        ui, pad = self._ui, self.pad
        try:
            while not self._stop.is_set():
                r, _, _ = select.select([pad.fd, ui.fd], [], [], 0.25)
                if pad.fd in r:
                    for ev in pad.read():
                        out = translate(ev.type, ev.code, ev.value)
                        if out:
                            ui.write(*out)
                if ui.fd in r:
                    for ev in ui.read():
                        if ev.type == EV_UINPUT and ev.code == UI_FF_UPLOAD:
                            up = ui.begin_upload(ev.value)
                            eff = up.effect
                            if eff.type == ecodes.FF_RUMBLE:
                                rm = eff.u.ff_rumble_effect
                                self.rumble.upload(eff.id, rm.strong_magnitude, rm.weak_magnitude, eff.replay.length)
                            up.retval = 0
                            ui.end_upload(up)
                        elif ev.type == EV_UINPUT and ev.code == UI_FF_ERASE:
                            er = ui.begin_erase(ev.value)
                            self.rumble.erase(er.effect_id)
                            er.retval = 0
                            ui.end_erase(er)
                        elif ev.type == EV_FF:
                            self.rumble.play(ev.code, ev.value > 0)
        except OSError as e:          # controller unplugged
            self.log(f"Xbox mode input ended: {e}")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        self.rumble.stop_all()
        for d in self._devices:
            try:
                d.ungrab()
            except OSError:
                pass
            d.close()
        self._devices = []
        if self._ui:
            self._ui.close()
            self._ui = None
        if self.hidden:
            ok, msg = run_helper("show", self.hidden)
            if not ok:
                self.log(f"couldn't restore controller access: {msg}")
            self.hidden = []
        self.log(f"Xbox mode off for {self.ctrl}")
