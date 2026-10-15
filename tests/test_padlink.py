import os
import zlib
from pathlib import Path

import pytest

from padlink import games as games_mod
from padlink.devices import find_controllers
from padlink.games import RunningGame, detect_games
from padlink.profiles import load_profile, pick_profile
from padlink.reports import battery_mask, dualsense_report, ds4_report
from padlink.triggers import EFFECTS, effect_bytes

DATA = Path(__file__).resolve().parent.parent / "data" / "profiles"


# ---------- reports: positions must match DS4Windows DualSenseDevice.PrepareOutReport ----------

def test_dualsense_usb_offsets():
    r = dualsense_report(False, rgb=(1, 2, 3), player_leds=0x04,
                         right_trigger=bytes([1, 2, 3, 4, 5, 6, 7, 8]),
                         left_trigger=bytes([9, 10, 11, 12, 13, 14, 15, 16]))
    assert len(r) == 48 and r[0] == 0x02
    assert r[45:48] == bytes([1, 2, 3])              # DS4Windows USB: outputReport[45..47]
    assert r[44] == 0x04 | 0x20                      # player LEDs at [44]
    # DS4Windows USB: R2 at [11..17] + frequency [20]; L2 at [22..28] + frequency [31]
    assert r[11:18] == bytes([1, 2, 3, 4, 5, 6, 7]) and r[20] == 8
    assert r[22:29] == bytes([9, 10, 11, 12, 13, 14, 15]) and r[31] == 16
    assert r[1] == 0x04 | 0x08                       # only trigger flags, no rumble
    assert r[2] == 0x04 | 0x10                       # lightbar + player LEDs


def test_dualsense_bt_offsets_and_crc():
    r = dualsense_report(True, rgb=(10, 20, 30), right_trigger=bytes([1, 2, 3, 4, 5, 6, 7, 8]),
                         left_trigger=bytes([9, 10, 11, 12, 13, 14, 15, 16]))
    assert len(r) == 78 and r[0] == 0x31 and r[1] == 0x02
    assert r[46:49] == bytes([10, 20, 30])           # DS4Windows BT: outputReport[46..48]
    # DS4Windows BT: R2 at [12..18] + frequency [21]; L2 at [23..29] + frequency [32]
    assert r[12:19] == bytes([1, 2, 3, 4, 5, 6, 7]) and r[21] == 8
    assert r[23:30] == bytes([9, 10, 11, 12, 13, 14, 15]) and r[32] == 16
    crc = zlib.crc32(b"\xA2" + r[:74]) & 0xFFFFFFFF
    assert int.from_bytes(r[74:78], "little") == crc


def test_unflagged_fields_untouched():
    r = dualsense_report(False, rgb=(255, 0, 0))
    assert r[1] == 0                                 # triggers/rumble not flagged
    assert r[11:33] == bytes(22)


def test_ds4_reports():
    usb = ds4_report(False, rgb=(1, 2, 3))
    assert len(usb) == 32 and usb[0] == 0x05 and usb[1] == 0x02 and usb[6:9] == bytes([1, 2, 3])
    bt = ds4_report(True, rgb=(4, 5, 6))
    assert len(bt) == 78 and bt[0] == 0x11 and bt[8:11] == bytes([4, 5, 6])
    assert int.from_bytes(bt[74:], "little") == zlib.crc32(b"\xA2" + bt[:74]) & 0xFFFFFFFF


# ---------- triggers ----------

def test_all_effects_are_8_bytes():
    for e in EFFECTS:
        assert len(effect_bytes(e, 3, 200)) == 8


def test_resistance_matches_ds4windows_algorithm():
    # start=2, force(small)=8 -> bits 2..9 set, each zone strength 7
    b = effect_bytes("resistance", 2, 255)
    zones = 0b1111111100
    strength = sum(7 << (3 * i) for i in range(2, 10))
    assert b[0] == 0x21
    assert b[1] | (b[2] << 8) == zones
    assert int.from_bytes(b[3:7], "little") == strength


def test_unknown_effect():
    with pytest.raises(ValueError):
        effect_bytes("laser")


def test_battery_mask():
    assert battery_mask(100) == 0b11111
    assert battery_mask(50) == 0b111
    assert battery_mask(5) == 0b1
    assert battery_mask(0) == 0


# ---------- device discovery on a fake sysfs ----------

def test_find_controllers(tmp_path):
    dev = tmp_path / "0005:054C:0CE6.0007"
    (dev / "hidraw" / "hidraw3").mkdir(parents=True)
    (dev / "power_supply" / "ps-controller-battery-aa:bb").mkdir(parents=True)
    (dev / "uevent").write_text("HID_ID=0005:0000054C:00000CE6\nHID_UNIQ=aa:bb:cc:dd:ee:ff\n")
    other = tmp_path / "0003:046D:C52B.0001"
    other.mkdir()
    (other / "uevent").write_text("HID_ID=0003:0000046D:0000C52B\n")
    [c] = find_controllers(tmp_path)
    assert c.hidraw == "/dev/hidraw3" and c.is_bluetooth and c.is_dualsense
    assert c.uniq == "aa:bb:cc:dd:ee:ff" and c.battery_path is not None


# ---------- game detection on a fake /proc ----------

def _proc(root: Path, pid: int, cmd: list[str], env: dict[str, str]):
    d = root / str(pid)
    d.mkdir()
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in cmd) + b"\0")
    (d / "environ").write_bytes(b"\0".join(f"{k}={v}".encode() for k, v in env.items()))


def test_detect_steam_and_lutris(tmp_path, monkeypatch):
    monkeypatch.setattr(games_mod, "steam_game_name", lambda appid: f"App{appid}")
    _proc(tmp_path, 100, ["/usr/bin/steam"], {})
    _proc(tmp_path, 200, ["reaper", "SteamLaunch", "AppId=1091500", "--", "proton"], {})
    _proc(tmp_path, 201, ["Z:\\games\\Cyberpunk2077.exe"], {"SteamAppId": "1091500"})
    _proc(tmp_path, 300, ["/usr/bin/lutris-wrapper", "Need for Speed: Most Wanted", "0", "0", "wine"], {})
    _proc(tmp_path, 301, ["C:\\NFS\\speed.exe"], {"GAME_NAME": "Need for Speed: Most Wanted"})
    found = {(g.source, g.name): g for g in detect_games(tmp_path)}
    steam = found[("steam", "App1091500")]
    assert steam.steam_appid == 1091500 and "cyberpunk2077.exe" in steam.exe_names
    lutris = found[("lutris", "Need for Speed: Most Wanted")]
    assert "speed.exe" in lutris.exe_names
    assert not any(s == "process" for s, _ in found)


# ---------- profiles ----------

def test_profile_matching():
    profiles = [load_profile(p) for p in sorted(DATA.glob("*.toml"))]
    steam = RunningGame("steam", "Cyberpunk 2077", steam_appid=1091500)
    p, g = pick_profile(profiles, [steam])
    assert p.name == "Cyberpunk 2077" and p.right.effect == "semi_auto_gun"

    lut = RunningGame("lutris", "Need for Speed: Most Wanted")
    assert pick_profile(profiles, [lut])[0].name == "Racing"

    by_exe = RunningGame("process", "speed.exe", exe_names={"speed.exe"})
    assert pick_profile(profiles, [by_exe])[0].name == "Racing"

    p, g = pick_profile(profiles, [])
    assert p.is_default and g is None and p.color == (0x30, 0x50, 0xFF)


# ---------- input parsing ----------

from padlink.inputs import parse_dualsense, parse_ds4
from padlink.profiles import profile_to_toml, save_profile, Profile
from padlink.triggers import TriggerSetting


def _ds_usb(**kw):
    r = bytearray(64)
    r[0] = 0x01
    r[1:7] = bytes([kw.get("lx", 128), kw.get("ly", 128), kw.get("rx", 128), kw.get("ry", 128),
                    kw.get("l2", 0), kw.get("r2", 0)])
    r[8] = kw.get("b8", 0x08)
    r[9] = kw.get("b9", 0)
    r[10] = kw.get("b10", 0)
    r[33] = 0x80            # touch 0 inactive
    r[37] = 0x80
    return r


def test_parse_dualsense_usb_buttons():
    s = parse_dualsense(bytes(_ds_usb(l2=200, b8=0x20 | 0x02, b9=0x10 | 0x01, b10=0x04 | 0x01)))
    assert s.l2 == 200
    assert s.pressed == {"cross", "right", "create", "l1", "mute", "ps"}


def test_parse_dualsense_bt_offset_and_touch():
    usb = _ds_usb(lx=10, b8=0x80 | 0x08)
    usb[33] = 0x05          # active touch id 5
    usb[34], usb[35], usb[36] = 0x34, 0x12 | 0x50, 0x20   # x = 0x234, y = (0x20<<4)|5
    bt = bytearray(78)
    bt[0] = 0x31
    bt[2:65] = usb[1:64]
    s = parse_dualsense(bytes(bt))
    assert s.lx == 10 and "triangle" in s.pressed
    assert s.touches[0].active and s.touches[0].x == 0x234 and s.touches[0].y == (0x20 << 4) | 5
    assert not s.touches[1].active


def test_parse_ds4_bt():
    bt = bytearray(78)
    bt[0] = 0x11
    o = 2
    bt[o + 1] = 0
    bt[o + 5] = 0x40 | 0x04      # circle + down
    bt[o + 6] = 0x20             # options
    bt[o + 7] = 0x02             # touch pad button
    bt[o + 9] = 99               # R2
    bt[o + 35] = bt[o + 39] = 0x80
    s = parse_ds4(bytes(bt))
    assert s.r2 == 99 and s.pressed == {"circle", "down", "options", "touchpad"}


def test_profile_roundtrip(tmp_path):
    p = Profile(name='My "Game"', steam_appids={10, 20}, lutris_titles={"Need for Speed: Most Wanted"},
                exe_globs=["game*.exe"], trigger_mode="game",
                left=TriggerSetting("resistance", 3, 200), right=TriggerSetting("vibrate", 100, 50),
                color=(1, 2, 255), player_leds="battery", mic_led="pulse", is_default=True)
    path = save_profile(p, tmp_path)
    q = load_profile(path)
    assert (q.name, q.steam_appids, q.lutris_titles, q.exe_globs, q.trigger_mode) == \
           (p.name, p.steam_appids, p.lutris_titles, p.exe_globs, p.trigger_mode)
    assert (q.left, q.right, q.color, q.player_leds, q.mic_led, q.is_default) == \
           (p.left, p.right, p.color, p.player_leds, p.mic_led, True)
    # only one default allowed
    save_profile(Profile(name="Other", is_default=True), tmp_path)
    assert not load_profile(path).is_default


# ---------- Xbox mode ----------

import importlib.machinery
import importlib.util
import json
import subprocess
import sys

from padlink import xinput as X


def test_translate_buttons_follow_xpad_layout():
    # Square must become Xbox X (BTN_X == BTN_NORTH), Triangle Xbox Y (BTN_Y == BTN_WEST)
    assert X.translate(X.EV_KEY, X.BTN_WEST, 1) == (X.EV_KEY, X.BTN_NORTH, 1)
    assert X.translate(X.EV_KEY, X.BTN_NORTH, 1) == (X.EV_KEY, X.BTN_WEST, 1)
    assert X.translate(X.EV_KEY, X.BTN_SOUTH, 1) == (X.EV_KEY, X.BTN_SOUTH, 1)
    assert X.translate(X.EV_KEY, X.BTN_TL2, 1) is None          # digital L2 dropped; analog ABS_Z kept
    assert X.translate(X.EV_ABS, X.ABS_Z, 200) == (X.EV_ABS, X.ABS_Z, 200)
    assert X.translate(X.EV_ABS, X.ABS_HAT0X, -1) == (X.EV_ABS, X.ABS_HAT0X, -1)
    assert X.translate(X.EV_SYN, 0, 0) == (X.EV_SYN, 0, 0)


def test_translate_stick_range():
    assert X.translate(X.EV_ABS, X.ABS_X, 0)[2] == -32768
    assert X.translate(X.EV_ABS, X.ABS_X, 255)[2] == 32767
    assert abs(X.translate(X.EV_ABS, X.ABS_Y, 128)[2]) < 200      # centre within the 128 flat zone


def test_rumble_forwarder():
    sent = []
    r = X.RumbleForwarder(lambda s, w: sent.append((s, w)))
    r.upload(0, 0xFFFF, 0x8000, 0)
    r.upload(1, 0x4000, 0xFFFF, 0)
    r.play(0, True)
    assert sent[-1] == (255, 128)
    r.play(1, True)
    assert sent[-1] == (255, 255)                 # strongest of the playing effects
    r.play(0, False)
    assert sent[-1] == (64, 255)
    r.erase(1)
    assert sent[-1] == (0, 0)
    r.upload(2, 0xFFFF, 0, 30)                     # 30 ms effect stops by itself
    r.play(2, True)
    import time
    time.sleep(0.1)
    assert sent[-1] == (0, 0)


def test_bridge_rumble_reports():
    from padlink.devices import Controller, BUS_USB
    got = []
    c = Controller(sys_path=Path("/x"), hidraw="/dev/hidraw9", bus=BUS_USB, pid=0x0CE6)
    b = X.XboxBridge(c, got.append, log=lambda *_: None)
    b._send_rumble(200, 50)
    r = got[-1]
    assert r[0] == 0x02 and r[1] == 0x03 and r[3] == 50 and r[4] == 200   # right=light(weak), left=heavy(strong)


def test_controller_nodes(tmp_path):
    from padlink.devices import Controller, BUS_BT
    for i, name, ev, js in ((5, "DualSense Wireless Controller", "event20", "js0"),
                            (6, "DualSense Wireless Controller Motion Sensors", "event21", None),
                            (7, "DualSense Wireless Controller Touchpad", "event22", None)):
        d = tmp_path / "input" / f"input{i}"
        (d / ev).mkdir(parents=True)
        if js:
            (d / js).mkdir()
        (d / "name").write_text(name + "\n")
    c = Controller(sys_path=tmp_path, hidraw="/dev/hidraw3", bus=BUS_BT, pid=0x0CE6)
    n = X.controller_nodes(c)
    assert n == {"gamepad": ["/dev/input/event20"], "other": ["/dev/input/event21", "/dev/input/event22"],
                 "js": ["/dev/input/js0"]}


HELPER = Path(__file__).resolve().parent.parent / "packaging" / "padlink-hide"


def _fake_tree(tmp_path):
    sysfs, dev = tmp_path / "sys", tmp_path / "dev"
    hid = sysfs / "devices" / "virtual" / "0005:054C:0CE6.0007"
    (hid / "hidraw" / "hidraw3").mkdir(parents=True)
    (hid / "input" / "input5" / "event20").mkdir(parents=True)
    other = sysfs / "devices" / "pci" / "0003:046D:C52B.0001" / "hidraw" / "hidraw1"
    other.mkdir(parents=True)
    (sysfs / "class" / "hidraw").mkdir(parents=True)
    (sysfs / "class" / "input").mkdir(parents=True)
    (sysfs / "class" / "hidraw" / "hidraw3").symlink_to(hid / "hidraw" / "hidraw3")
    (sysfs / "class" / "hidraw" / "hidraw1").symlink_to(other)
    (sysfs / "class" / "input" / "event20").symlink_to(hid / "input" / "input5" / "event20")
    (dev / "input").mkdir(parents=True)
    for p in (dev / "hidraw3", dev / "hidraw1", dev / "input" / "event20"):
        p.write_text("")
        os.chmod(p, 0o660)
    env = dict(os.environ, PADLINK_SYSFS=str(sysfs), PADLINK_DEV=str(dev), PADLINK_STATE=str(tmp_path / "state.json"))
    return dev, env


@pytest.mark.skipif(os.geteuid() == 0, reason="helper ignores test paths when run as root (by design)")
def test_helper_hide_show(tmp_path):
    dev, env = _fake_tree(tmp_path)
    run = lambda *a: subprocess.run([sys.executable, str(HELPER), *a], env=env, capture_output=True, text=True)
    r = run("hide", "hidraw3", "event20", "hidraw1", "../etc/passwd")
    assert r.returncode == 2 and "hidraw1" in r.stderr and "passwd" in r.stderr
    assert oct((dev / "hidraw3").stat().st_mode & 0o777) == "0o600"
    assert oct((dev / "input" / "event20").stat().st_mode & 0o777) == "0o600"
    assert oct((dev / "hidraw1").stat().st_mode & 0o777) == "0o660"     # not Sony: untouched
    assert run("show-all").returncode == 0
    assert oct((dev / "hidraw3").stat().st_mode & 0o777) == "0o660"
    assert oct((dev / "input" / "event20").stat().st_mode & 0o777) == "0o660"
    assert json.loads((tmp_path / "state.json").read_text()) == {}


def test_daemon_toggles_xbox_mode_with_profile(tmp_path, monkeypatch):
    from padlink import daemon as D
    from padlink.devices import Controller, BUS_USB
    events = []

    class FakeBridge:
        def __init__(self, ctrl, write, log=None):
            self.ctrl, self.warning = ctrl, None
        def start(self): events.append(("start", self.ctrl.hidraw))
        def stop(self): events.append(("stop", self.ctrl.hidraw))

    class FakeWriter:
        def write(self, b): pass
        def close(self): pass

    monkeypatch.setattr(D, "XboxBridge", FakeBridge)
    monkeypatch.setattr(D, "write_state", lambda active: events.append(("state", tuple(active))))
    d = D.Daemon()
    monkeypatch.setattr(d, "_writer", lambda path: FakeWriter())
    c = Controller(sys_path=tmp_path, hidraw="/dev/hidraw3", bus=BUS_USB, pid=0x0CE6)
    game = Profile(name="Old game", xbox_mode=True)
    desk = Profile(name="Desktop")

    d._sync_xbox([c], game)
    d._sync_xbox([c], game)            # already running: nothing new
    d._sync_xbox([c], desk)            # game closed -> real pad wakes up
    assert [e for e in events if e[0] != "state"] == [("start", "/dev/hidraw3"), ("stop", "/dev/hidraw3")]
    assert events[-1] == ("state", ())
