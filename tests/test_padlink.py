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
    assert len(r) == 63 and r[0] == 0x02         # full USB report, as hid-playstation sends it
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


def test_report_sizes_match_kernel_driver():
    # Sizes used by Linux hid-playstation; the controllers ignore shorter output reports.
    assert len(dualsense_report(False, rgb=(1, 2, 3))) == 63      # DS_OUTPUT_REPORT_USB_SIZE
    assert len(dualsense_report(True, rgb=(1, 2, 3))) == 78       # DS_OUTPUT_REPORT_BT_SIZE
    assert len(ds4_report(False, rgb=(1, 2, 3))) == 32            # DS4_OUTPUT_REPORT_USB_SIZE
    assert len(ds4_report(True, rgb=(1, 2, 3))) == 78             # DS4_OUTPUT_REPORT_BT_SIZE


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


def test_xbox_failure_notifies_once_and_backs_off(tmp_path, monkeypatch):
    from padlink import daemon as D
    from padlink.devices import Controller, BUS_BT
    starts, notes = [], []

    class FailingBridge:
        def __init__(self, ctrl, write, log=None):
            self.warning = None
        def start(self):
            starts.append(1)
            raise PermissionError("[Errno 13] Permission denied: '/dev/input/event22'")
        def stop(self): pass

    class W:
        def write(self, b): pass
        def close(self): pass

    clock = {"t": 1000.0}
    monkeypatch.setattr(D, "XboxBridge", FailingBridge)
    monkeypatch.setattr(D, "write_state", lambda a: None)
    monkeypatch.setattr(D, "notify", lambda *a, **k: notes.append(a))
    monkeypatch.setattr(D.time, "monotonic", lambda: clock["t"])
    d = D.Daemon()
    monkeypatch.setattr(d, "_writer", lambda path: W())
    c = Controller(sys_path=tmp_path, hidraw="/dev/hidraw3", bus=BUS_BT, pid=0x0CE6)
    game = Profile(name="Game", xbox_mode=True)
    for _ in range(10):                         # 20 s of daemon steps
        d._sync_xbox([c], game)
        clock["t"] += 2
    assert len(starts) == 1 and len(notes) == 1
    clock["t"] += 30
    d._sync_xbox([c], game)                     # retried later, same error: no new notification
    assert len(starts) == 2 and len(notes) == 1


def test_preview_xbox_switch_drives_xbox_mode(tmp_path, monkeypatch):
    """The app's preview decides Xbox mode, so it can be tried without starting the game."""
    from padlink import daemon as D
    from padlink import profiles as P
    from padlink.devices import Controller, BUS_USB
    lock = tmp_path / ".preview"
    monkeypatch.setattr(P, "PREVIEW_LOCK", lock)
    monkeypatch.setattr(P, "CONFIG_DIR", tmp_path)
    P.touch_preview_lock(xbox=True)
    assert P.preview_active() and P.preview_wants_xbox()
    P.touch_preview_lock(xbox=False)
    assert P.preview_active() and not P.preview_wants_xbox()

    c = Controller(sys_path=tmp_path, hidraw="/dev/hidraw3", bus=BUS_USB, pid=0x0CE6)
    wants = []
    monkeypatch.setattr(D, "load_profiles", lambda: [Profile(name="Desktop", is_default=True)])
    monkeypatch.setattr(D, "find_controllers", lambda: [c])
    monkeypatch.setattr(D, "detect_games", lambda: [])
    monkeypatch.setattr(D, "preview_active", lambda: True)
    monkeypatch.setattr(D, "preview_wants_xbox", lambda: True)
    monkeypatch.setattr(D, "notify", lambda *a, **k: None)
    monkeypatch.setattr(D, "other_users", lambda h: [])
    d = D.Daemon()
    monkeypatch.setattr(d, "_sync_xbox", lambda ctrls, prof: wants.append(prof.xbox_mode))
    monkeypatch.setattr(d, "_battery", lambda ctrl: __import__("padlink.battery").battery.BatteryInfo(50, "Discharging"))
    d.step()
    monkeypatch.setattr(D, "preview_active", lambda: False)
    d.step()                                   # preview over: the Desktop profile (no Xbox) decides
    assert wants == [True, False]


# ---------- other programs holding the controller ----------

def test_other_users_finds_wine_and_skips_padlink(tmp_path):
    from padlink.devices import other_users, conflict_hint
    dev = tmp_path / "hidraw6"
    dev.write_text("")
    proc = tmp_path / "proc"

    def fake(pid, comm, cmd, target):
        d = proc / str(pid)
        (d / "fd").mkdir(parents=True)
        (d / "comm").write_text(comm + "\n")
        (d / "cmdline").write_bytes(cmd.replace(" ", "\0").encode() + b"\0")
        (d / "fd" / "73").symlink_to(target)

    fake(200644, "winedevice.exe", "C:\\windows\\system32\\winedevice.exe", dev)
    fake(416940, "python3", "/usr/bin/python3 -m padlink daemon -v", dev)
    fake(5000, "firefox", "/usr/lib64/firefox/firefox", tmp_path / "other")
    users = other_users(str(dev), proc)
    assert [(u.pid, u.name) for u in users] == [(200644, "winedevice.exe")]
    assert "winedevice.exe (pid 200644)" in conflict_hint(users) and "Wine" in conflict_hint(users)
    assert other_users(str(tmp_path / "missing"), proc) == []


def test_battery_change_only_updates_player_leds(tmp_path, monkeypatch):
    """A battery step must not re-send the profile colour (it would undo `padlink led`)."""
    from padlink import daemon as D
    from padlink.devices import Controller, BUS_BT
    sent = []

    class W:
        def write(self, b): sent.append(b)
        def close(self): pass

    c = Controller(sys_path=tmp_path, hidraw="/dev/hidraw6", bus=BUS_BT, pid=0x0CE6)
    prof = Profile(name="Desktop", color=(0x30, 0x50, 0xFF), player_leds="battery", is_default=True)
    pct = {"v": 55}
    monkeypatch.setattr(D, "load_profiles", lambda: [prof])
    monkeypatch.setattr(D, "find_controllers", lambda: [c])
    monkeypatch.setattr(D, "detect_games", lambda: [])
    monkeypatch.setattr(D, "preview_active", lambda: False)
    monkeypatch.setattr(D, "write_state", lambda a: None)
    monkeypatch.setattr(D, "other_users", lambda h: [])
    monkeypatch.setattr(D, "notify", lambda *a, **k: None)
    d = D.Daemon()
    monkeypatch.setattr(d, "_writer", lambda path: W())
    from padlink.battery import BatteryInfo
    monkeypatch.setattr(d, "_battery", lambda ctrl: BatteryInfo(pct["v"], "Discharging"))
    d.step()                                   # first apply: colour + LEDs
    assert sent and sent[-1][3] & 0x04         # BT: valid_flag1 at byte 3 has the light bar bit
    sent.clear()
    pct["v"] = 45
    d.last_reapply = float("inf")              # no trigger re-send in this step
    d.step()
    assert len(sent) == 1
    assert sent[0][3] == 0x10                  # only player LEDs flagged, light bar untouched


# ---------- low battery: notification at 25% and red light bar ----------

def _daemon_with_battery(tmp_path, monkeypatch, settings_text=None):
    from padlink import daemon as D
    from padlink import settings as S
    from padlink.devices import Controller, BUS_BT
    bat = tmp_path / "bat"
    bat.mkdir()
    sent, notes = [], []

    class W:
        def write(self, b): sent.append(b)
        def close(self): pass

    c = Controller(sys_path=tmp_path, hidraw="/dev/hidraw6", bus=BUS_BT, pid=0x0CE6, uniq="aa", battery_path=bat)
    prof = Profile(name="Desktop", color=(0x30, 0x50, 0xFF), player_leds=1, is_default=True)
    cfg = tmp_path / "settings.toml"
    if settings_text:
        cfg.write_text(settings_text)
    monkeypatch.setattr(D, "load_battery_settings", lambda: S.load_battery_settings(cfg))
    monkeypatch.setattr(D, "load_profiles", lambda: [prof])
    monkeypatch.setattr(D, "find_controllers", lambda: [c])
    monkeypatch.setattr(D, "detect_games", lambda: [])
    monkeypatch.setattr(D, "preview_active", lambda: False)
    monkeypatch.setattr(D, "write_state", lambda a: None)
    monkeypatch.setattr(D, "other_users", lambda h: [])
    monkeypatch.setattr(D, "notify", lambda title, body, *a: notes.append((title, body)))
    d = D.Daemon()
    d.last_reapply = float("inf")
    monkeypatch.setattr(d, "_writer", lambda path: W())

    def set_battery(pct, status="Discharging"):
        (bat / "capacity").write_text(f"{pct}\n")
        (bat / "status").write_text(status + "\n")
    return d, sent, notes, set_battery


def _rgb(report):          # Bluetooth report: light bar colour at bytes 46..48 when flagged
    return tuple(report[46:49]) if report[3] & 0x04 else None


def test_low_battery_turns_light_bar_red_and_notifies(tmp_path, monkeypatch):
    d, sent, notes, set_battery = _daemon_with_battery(tmp_path, monkeypatch)
    set_battery(30)
    d.step()
    assert _rgb(sent[-1]) == (0x30, 0x50, 0xFF) and notes == []
    set_battery(25)                         # not *under* 25 yet
    d.step()
    assert notes == []
    sent.clear()
    set_battery(24)
    d.step()
    assert [_rgb(r) for r in sent if _rgb(r)] == [(255, 0, 0)]
    assert len(notes) == 1 and "battery low" in notes[0][0] and "24%" in notes[0][1]
    set_battery(23)                         # still low: no repeat notification, no extra writes
    sent.clear()
    d.step()
    assert len(notes) == 1 and not [r for r in sent if _rgb(r)]
    set_battery(9)
    d.step()
    assert len(notes) == 2 and "critical" in notes[1][0]


def test_light_bar_restored_when_charging(tmp_path, monkeypatch):
    d, sent, notes, set_battery = _daemon_with_battery(tmp_path, monkeypatch)
    set_battery(20)
    d.step()
    assert _rgb(sent[-1]) == (255, 0, 0)
    sent.clear()
    set_battery(20, "Charging")
    d.step()
    assert [_rgb(r) for r in sent if _rgb(r)] == [(0x30, 0x50, 0xFF)]


def test_low_battery_settings(tmp_path, monkeypatch):
    d, sent, notes, set_battery = _daemon_with_battery(
        tmp_path, monkeypatch, '[battery]\nlow_below = 40\nlow_color = "#ff8800"\n')
    set_battery(35)
    d.step()
    assert _rgb(sent[-1]) == (0xFF, 0x88, 0x00) and len(notes) == 1
    other = tmp_path / "no-light"
    other.mkdir()
    d2, sent2, notes2, set2 = _daemon_with_battery(other, monkeypatch, "[battery]\nlow_light = false\n")
    set2(9)
    d2.step()
    assert all(_rgb(r) != (255, 0, 0) for r in sent2)        # light bar left alone
    assert [n[0].split()[-1] for n in notes2] == ["critical"]  # 9% is already critical: one urgent notice


# ---------- turning the controller off ----------

def test_power_off(monkeypatch, tmp_path):
    from types import SimpleNamespace
    from padlink import devices as Dv
    from padlink.devices import Controller, BUS_BT, BUS_USB, power_off
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="Attempting to disconnect from D4:2F:4B:05:14:DD\nSuccessful disconnected\n", stderr="")

    bt = Controller(sys_path=tmp_path, hidraw="/dev/hidraw6", bus=BUS_BT, pid=0x0CE6, uniq="d4:2f:4b:05:14:dd")
    usb = Controller(sys_path=tmp_path, hidraw="/dev/hidraw6", bus=BUS_USB, pid=0x0CE6, uniq="")

    monkeypatch.setattr(Dv.shutil, "which", lambda name: "/usr/bin/" + name if name == "bluetoothctl" else None)
    ok, msg = power_off(bt, run=fake_run)
    assert ok and "turned off" in msg and calls[-1] == ["bluetoothctl", "disconnect", "d4:2f:4b:05:14:dd"]

    ok, msg = power_off(usb, run=fake_run)
    assert not ok and "Bluetooth" in msg

    bad = Controller(sys_path=tmp_path, hidraw="/dev/hidraw6", bus=BUS_BT, pid=0x0CE6, uniq="d4:2f; rm -rf /")
    n = len(calls)
    ok, msg = power_off(bad, run=fake_run)
    assert not ok and len(calls) == n            # never reaches a command

    fail = lambda cmd, **kw: SimpleNamespace(returncode=1, stdout="Failed to disconnect: org.bluez.Error.NotConnected", stderr="")
    ok, msg = power_off(bt, run=fail)
    assert not ok and "NotConnected" in msg

    # D-Bus fallback when bluetoothctl is missing
    monkeypatch.setattr(Dv.shutil, "which", lambda name: "/usr/bin/busctl" if name == "busctl" else None)
    ok, msg = power_off(bt, run=fake_run)
    assert ok and calls[-1][:3] == ["busctl", "call", "org.bluez"] and calls[-1][3] == "/org/bluez/hci0/dev_D4_2F_4B_05_14_DD"
