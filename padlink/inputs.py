"""Read live input from the controller's hidraw node and decode it using Sony's control names.

hidraw gets a copy of every input report even while hid-playstation is bound, so reading here
doesn't disturb games. Layouts come from DS4Windows (DualSenseDevice.cs / DS4Device.cs):

DualSense  USB report 0x01 (64 B), offset 0;  BT report 0x31 (78 B), offset 1
  [o+1..4] sticks LX LY RX RY   [o+5] L2  [o+6] R2
  [o+8]  low nibble = directional buttons (hat 0-7, 8 = none), 0x10 square 0x20 cross 0x40 circle 0x80 triangle
  [o+9]  0x01 L1 0x02 R1 0x04 L2 0x08 R2 0x10 Create 0x20 Options 0x40 L3 0x80 R3
  [o+10] 0x01 PS 0x02 touch pad button 0x04 mute  (Edge: 0x10 Fn L, 0x20 Fn R, 0x40/0x80 back paddles)
  [o+16..21] gyro, [o+22..27] accelerometer (int16 LE)   [o+33..40] two touch points
DualShock 4  USB 0x01 (64 B), offset 0;  BT 0x11 (78 B), offset 2
  [o+1..4] sticks  [o+5] hat + face (as above)  [o+6] same bits as DualSense [o+9] (Share instead of Create)
  [o+7] 0x01 PS 0x02 touch pad button   [o+8] L2  [o+9] R2
  [o+13..18] gyro [o+19..24] accelerometer   [o+35..42] two touch points
"""
from __future__ import annotations

import os
import select
import struct
import threading
from dataclasses import dataclass, field

BUTTON_NAMES = {
    # Sony's names (controller.dl.playstation.net, DualSense guide)
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "triangle": "△", "circle": "○", "cross": "✕", "square": "□",
    "l1": "L1", "r1": "R1", "l2": "L2", "r2": "R2", "l3": "L3", "r3": "R3",
    "create": "Create", "options": "Options", "ps": "PS",
    "touchpad": "Touch pad", "mute": "Mute",
    "fn_l": "Fn (left)", "fn_r": "Fn (right)", "paddle_l": "Back (left)", "paddle_r": "Back (right)",
}

_HAT = {
    0: ("up",), 1: ("up", "right"), 2: ("right",), 3: ("down", "right"),
    4: ("down",), 5: ("down", "left"), 6: ("left",), 7: ("up", "left"),
}


@dataclass
class Touch:
    active: bool = False
    x: int = 0
    y: int = 0


@dataclass
class PadState:
    lx: int = 128
    ly: int = 128
    rx: int = 128
    ry: int = 128
    l2: int = 0
    r2: int = 0
    pressed: set[str] = field(default_factory=set)
    touches: tuple[Touch, Touch] = field(default_factory=lambda: (Touch(), Touch()))
    gyro: tuple[int, int, int] = (0, 0, 0)
    accel: tuple[int, int, int] = (0, 0, 0)
    touch_w: int = 1920
    touch_h: int = 1080
    valid: bool = False


def _face_and_hat(b: int, pressed: set[str]) -> None:
    pressed.update(_HAT.get(b & 0x0F, ()))
    if b & 0x10: pressed.add("square")
    if b & 0x20: pressed.add("cross")
    if b & 0x40: pressed.add("circle")
    if b & 0x80: pressed.add("triangle")


def _shoulders(b: int, pressed: set[str], share_name: str) -> None:
    for bit, name in ((0x01, "l1"), (0x02, "r1"), (0x04, "l2"), (0x08, "r2"),
                      (0x10, share_name), (0x20, "options"), (0x40, "l3"), (0x80, "r3")):
        if b & bit:
            pressed.add(name)


def _touch(r: bytes, i: int) -> Touch:
    return Touch(
        active=(r[i] & 0x80) == 0,
        x=((r[i + 2] & 0x0F) << 8) | r[i + 1],
        y=(r[i + 3] << 4) | ((r[i + 2] & 0xF0) >> 4),
    )


def parse_dualsense(r: bytes) -> PadState | None:
    if not r:
        return None
    if r[0] == 0x31 and len(r) >= 78:
        o = 1
    elif r[0] == 0x01 and len(r) >= 64:
        o = 0
    elif r[0] == 0x01 and len(r) >= 10:
        # Short Bluetooth report (before the driver switches the pad to full mode): DS4-like layout
        s = PadState(lx=r[1], ly=r[2], rx=r[3], ry=r[4], l2=r[8], r2=r[9], valid=True)
        _face_and_hat(r[5], s.pressed)
        _shoulders(r[6], s.pressed, "create")
        if r[7] & 0x01: s.pressed.add("ps")
        if r[7] & 0x02: s.pressed.add("touchpad")
        return s
    else:
        return None
    s = PadState(lx=r[o + 1], ly=r[o + 2], rx=r[o + 3], ry=r[o + 4], l2=r[o + 5], r2=r[o + 6], valid=True)
    _face_and_hat(r[o + 8], s.pressed)
    _shoulders(r[o + 9], s.pressed, "create")
    b = r[o + 10]
    for bit, name in ((0x01, "ps"), (0x02, "touchpad"), (0x04, "mute"),
                      (0x10, "fn_l"), (0x20, "fn_r"), (0x40, "paddle_l"), (0x80, "paddle_r")):
        if b & bit:
            s.pressed.add(name)
    s.gyro = struct.unpack_from("<3h", r, o + 16)
    s.accel = struct.unpack_from("<3h", r, o + 22)
    s.touches = (_touch(r, o + 33), _touch(r, o + 37))
    return s


def parse_ds4(r: bytes) -> PadState | None:
    if not r:
        return None
    if r[0] == 0x11 and len(r) >= 78:
        o = 2
    elif r[0] == 0x01 and len(r) >= 64:
        o = 0
    else:
        return None
    s = PadState(lx=r[o + 1], ly=r[o + 2], rx=r[o + 3], ry=r[o + 4], l2=r[o + 8], r2=r[o + 9],
                 touch_h=943, valid=True)
    _face_and_hat(r[o + 5], s.pressed)
    _shoulders(r[o + 6], s.pressed, "create")   # "Share" on DS4, same position as Create
    if r[o + 7] & 0x01: s.pressed.add("ps")
    if r[o + 7] & 0x02: s.pressed.add("touchpad")
    s.gyro = struct.unpack_from("<3h", r, o + 13)
    s.accel = struct.unpack_from("<3h", r, o + 19)
    s.touches = (_touch(r, o + 35), _touch(r, o + 39))
    return s


class InputReader:
    """Background thread that keeps `state` updated with the latest decoded report."""

    def __init__(self, hidraw: str, dualsense: bool):
        self.hidraw = hidraw
        self.parse = parse_dualsense if dualsense else parse_ds4
        self.state = PadState()
        self.error: str | None = None
        self.serial = 0               # bumps on every new report
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> "InputReader":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        try:
            fd = os.open(self.hidraw, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            self.error = str(e)
            return
        try:
            while not self._stop.is_set():
                ready, _, _ = select.select([fd], [], [], 0.25)
                if not ready:
                    continue
                try:
                    data = os.read(fd, 128)
                except BlockingIOError:
                    continue
                except OSError as e:           # unplugged
                    self.error = str(e)
                    return
                st = self.parse(data)
                if st is not None:
                    self.state = st
                    self.serial += 1
        finally:
            os.close(fd)
