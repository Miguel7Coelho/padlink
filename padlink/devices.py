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
import re
import shutil
import subprocess
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


PROC = Path("/proc")


@dataclass
class OtherUser:
    pid: int
    name: str

    def __str__(self) -> str:
        return f"{self.name} (pid {self.pid})"


def other_users(hidraw: str, proc: Path = PROC) -> list[OtherUser]:
    """Programs other than padlink that have this controller's hidraw node open.

    Programs that drive the controller directly (Steam, Wine's winedevice.exe, games through
    Proton) send their own light bar colour and override padlink. Only your own processes
    are visible without root, which covers all of these.
    """
    try:
        target = os.stat(hidraw).st_rdev
    except OSError:
        return []
    me = os.getpid()
    found: list[OtherUser] = []
    for pid_dir in proc.iterdir():
        if not pid_dir.name.isdigit() or int(pid_dir.name) == me:
            continue
        try:
            fds = list((pid_dir / "fd").iterdir())
        except OSError:
            continue
        hit = False
        for fd in fds:
            try:
                if os.readlink(fd) == hidraw or (target and os.stat(fd).st_rdev == target):
                    hit = True
                    break
            except OSError:
                continue
        if not hit:
            continue
        try:
            cmd = (pid_dir / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
            name = (pid_dir / "comm").read_text().strip()
        except OSError:
            continue
        if "padlink" in cmd:          # padlink's own service / app
            continue
        found.append(OtherUser(int(pid_dir.name), name))
    return found


def conflict_hint(users: list[OtherUser]) -> str:
    names = ", ".join(str(u) for u in users)
    hint = f"also open in {names}. It may override the light bar."
    if any("winedevice" in u.name or "wine" in u.name for u in users):
        hint += " (Wine: see TROUBLESHOOTING.md → \"Light bar and LEDs\")"
    elif any("steam" in u.name.lower() for u in users):
        hint += " (Steam Input: turn off its light bar control)"
    return hint


_MAC_RE = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")


def power_off(ctrl: Controller, run=subprocess.run) -> tuple[bool, str]:
    """Turn a Bluetooth controller off by dropping its connection (it powers down by itself).

    There's no way to turn it back on from the PC: a switched-off controller has its radio off,
    so only its PS button wakes it. Over USB the cable keeps it powered, so this doesn't apply.
    """
    if not ctrl.is_bluetooth:
        return False, "Only works over Bluetooth (USB keeps the controller powered)."
    mac = ctrl.uniq.strip()
    if not _MAC_RE.match(mac):
        return False, f"Unknown Bluetooth address {mac!r}."
    if shutil.which("bluetoothctl"):
        try:
            r = run(["bluetoothctl", "disconnect", mac], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, f"bluetoothctl failed: {e}"
        out = (r.stdout + r.stderr).strip()
        if r.returncode == 0 and "fail" not in out.lower():
            return True, f"{ctrl.model} turned off. Press PS to turn it on again."
        return False, out.splitlines()[-1] if out else f"bluetoothctl exited with {r.returncode}"
    if shutil.which("busctl"):
        # BlueZ over D-Bus; the adapter is almost always hci0
        path = "/org/bluez/hci0/dev_" + mac.upper().replace(":", "_")
        try:
            r = run(["busctl", "call", "org.bluez", path, "org.bluez.Device1", "Disconnect"],
                    capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, f"busctl failed: {e}"
        if r.returncode == 0:
            return True, f"{ctrl.model} turned off. Press PS to turn it on again."
        return False, (r.stderr or r.stdout).strip() or f"busctl exited with {r.returncode}"
    return False, "Neither bluetoothctl nor busctl is installed (package bluez)."


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
