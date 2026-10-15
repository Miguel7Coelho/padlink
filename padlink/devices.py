"""Find connected PlayStation controllers through sysfs (no extra dependencies).

The kernel's hid-playstation driver binds the pad and creates, under one HID device directory
(/sys/bus/hid/devices/<BUS>:054C:<PID>.<N>/):
  hidraw/hidrawN            -> raw reports; we write output reports here (triggers, LEDs)
  power_supply/ps-controller-battery-<mac>/{capacity,status}
  leds/*:rgb:indicator, leds/*:white:player-1..5
  input/inputN              -> the evdev nodes games read
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

SONY_VID = 0x054C
MODELS = {
    0x05C4: "DualShock 4 v1",
    0x09CC: "DualShock 4 v2",
    0x0BA0: "DualShock 4 USB dongle",
    0x0CE6: "DualSense",
    0x0DF2: "DualSense Edge",
}
DUALSENSE_PIDS = {0x0CE6, 0x0DF2}

BUS_USB = 0x03
BUS_BT = 0x05

HID_ROOT = Path("/sys/bus/hid/devices")


@dataclass
class Controller:
    sys_path: Path            # HID device dir in sysfs
    hidraw: str               # /dev/hidrawN
    bus: int                  # BUS_USB or BUS_BT
    pid: int
    uniq: str = ""            # MAC address (BT) / serial, used as a stable id
    battery_path: Path | None = None
    extra: dict = field(default_factory=dict)

    @property
    def model(self) -> str:
        return MODELS.get(self.pid, f"Sony {self.pid:04x}")

    @property
    def is_dualsense(self) -> bool:
        return self.pid in DUALSENSE_PIDS

    @property
    def is_bluetooth(self) -> bool:
        return self.bus == BUS_BT

    @property
    def connection(self) -> str:
        return "Bluetooth" if self.is_bluetooth else "USB"

    def __str__(self) -> str:
        return f"{self.model} ({self.connection}) {self.uniq or ''} [{self.hidraw}]".strip()


def _read_uevent(path: Path) -> dict[str, str]:
    out = {}
    try:
        for line in path.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                out[k] = v
    except OSError:
        pass
    return out


def find_controllers(hid_root: Path = HID_ROOT) -> list[Controller]:
    found: list[Controller] = []
    if not hid_root.exists():
        return found
    for dev in sorted(hid_root.iterdir()):
        ue = _read_uevent(dev / "uevent")
        hid_id = ue.get("HID_ID", "")          # e.g. 0005:0000054C:00000CE6
        try:
            bus_s, vid_s, pid_s = hid_id.split(":")
            bus, vid, pid = int(bus_s, 16), int(vid_s, 16), int(pid_s, 16)
        except ValueError:
            continue
        if vid != SONY_VID or pid not in MODELS:
            continue
        hidraw_dir = dev / "hidraw"
        nodes = sorted(hidraw_dir.iterdir()) if hidraw_dir.is_dir() else []
        if not nodes:
            continue                           # e.g. the audio interface of a USB DualSense
        battery = None
        ps_dir = dev / "power_supply"
        if ps_dir.is_dir():
            for p in ps_dir.iterdir():
                battery = p
                break
        found.append(Controller(
            sys_path=dev,
            hidraw=f"/dev/{nodes[0].name}",
            bus=bus,
            pid=pid,
            uniq=ue.get("HID_UNIQ", ""),
            battery_path=battery,
        ))
    return found


class HidrawWriter:
    """Keeps /dev/hidrawN open for writing output reports."""

    def __init__(self, path: str):
        self.path = path
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)

    def write(self, report: bytes) -> None:
        os.write(self.fd, report)

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
