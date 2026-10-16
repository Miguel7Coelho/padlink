"""Background service: watch running games, switch profiles, keep an eye on the battery.

Loop (every `interval` seconds):
  1. list controllers (hotplug is handled by just re-scanning sysfs)
  2. detect running Steam/Lutris games and choose the best profile
  3. if the profile changed or a new controller appeared, apply it
  4. in trigger mode "override", re-send trigger effects every few seconds, because games with
     native DualSense support (Proton with hidraw, Steam Input) can overwrite them
  5. warn with a desktop notification when the battery runs low
"""
from __future__ import annotations

import shutil
import signal
import subprocess
import time

from .battery import BatteryInfo, read_battery
from .control import apply, resolve_player_leds
from .devices import HidrawWriter, conflict_hint, find_controllers, other_users
from .games import detect_games
from .profiles import Profile, load_profiles, pick_profile, preview_active
from .settings import BatterySettings, load_battery_settings
from .xinput import XboxBridge, run_helper, write_state

REAPPLY_EVERY = 5.0


def notify(title: str, body: str, urgency: str = "normal") -> None:
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "-a", "padlink", "-u", urgency, title, body], check=False)
    print(f"[padlink] {title}: {body}")


class Daemon:
    def __init__(self, interval: float = 2.0, verbose: bool = False):
        self.interval = interval
        self.verbose = verbose
        self.running = True
        self.writers: dict[str, HidrawWriter] = {}
        self.applied: dict[str, str] = {}          # hidraw -> profile name
        self.last_reapply = 0.0
        self.battery_warned: dict[str, int] = {}
        self.last_battery: dict[str, int | None] = {}
        self.current: Profile | None = None
        self.bridges: dict[str, XboxBridge] = {}   # hidraw -> running Xbox mode
        self.battery_settings = BatterySettings()
        self.low_lit: set[str] = set()                 # controllers showing the low-battery colour
        self.conflicts_seen: dict[str, frozenset[int]] = {}   # hidraw -> pids already reported
        self.last_conflict_check = 0.0

    def log(self, msg: str) -> None:
        if self.verbose:
            print(f"[padlink] {msg}", flush=True)

    def _writer(self, path: str) -> HidrawWriter | None:
        w = self.writers.get(path)
        if w is None:
            try:
                w = HidrawWriter(path)
            except PermissionError:
                notify("padlink", f"No permission to write {path}. Install the udev rule (see README).", "critical")
                return None
            except OSError:
                return None
            self.writers[path] = w
        return w

    def _apply(self, ctrl, profile: Profile, battery_pct: int | None, triggers_only: bool = False) -> None:
        w = self._writer(ctrl.hidraw)
        if w is None:
            return
        own_triggers = profile.trigger_mode == "override"
        try:
            if triggers_only:
                apply(ctrl, left=profile.left, right=profile.right, writer=w)
            else:
                apply(
                    ctrl,
                    left=profile.left if own_triggers else None,
                    right=profile.right if own_triggers else None,
                    rgb=self._colour_for(ctrl, profile),
                    player_leds=resolve_player_leds(profile.player_leds, battery_pct),
                    mic_led=profile.mic_led,
                    writer=w,
                )
        except OSError as e:          # controller disconnected mid-write
            self.log(f"write failed on {ctrl.hidraw}: {e}")
            w.close()
            self.writers.pop(ctrl.hidraw, None)
            self.applied.pop(ctrl.hidraw, None)

    def _apply_player_leds(self, ctrl, profile: Profile, battery_pct: int | None) -> None:
        w = self._writer(ctrl.hidraw)
        if w is None:
            return
        try:
            apply(ctrl, player_leds=resolve_player_leds(profile.player_leds, battery_pct), writer=w)
        except OSError as e:
            self.log(f"write failed on {ctrl.hidraw}: {e}")

    def _colour_for(self, ctrl, profile: Profile | None):
        if ctrl.hidraw in self.low_lit:
            return self.battery_settings.low_color
        return profile.color if profile else None

    def _battery(self, ctrl) -> BatteryInfo:
        """Read the battery and send low / critical notifications once per discharge."""
        info = read_battery(ctrl.battery_path)
        bs = self.battery_settings
        key = ctrl.uniq or ctrl.hidraw
        pct = info.percent
        if pct is not None and not info.charging:
            last = self.battery_warned.get(key, 101)
            if pct < bs.critical_below <= last:
                notify(f"{ctrl.model} battery critical", f"{pct}% left. Charge it soon.", "critical")
                self.battery_warned[key] = bs.critical_below - 1
            elif pct < bs.low_below <= last:
                extra = " The light bar stays red until you charge it." if bs.low_light else ""
                notify(f"{ctrl.model} battery low", f"{pct}% left.{extra}")
                self.battery_warned[key] = bs.low_below - 1
        elif info.charging:
            self.battery_warned.pop(key, None)
        return info

    def _update_low_light(self, ctrl, profile: Profile | None, info: BatteryInfo) -> None:
        """Red light bar while the battery is low; the profile colour comes back when charging."""
        bs = self.battery_settings
        low = bs.low_light and bs.is_low(info.percent, info.charging)
        lit = ctrl.hidraw in self.low_lit
        if low == lit:
            return
        w = self._writer(ctrl.hidraw)
        if w is None:
            return
        if low:
            self.low_lit.add(ctrl.hidraw)
            self.log(f"{ctrl}: battery {info.percent}%, light bar red")
        else:
            self.low_lit.discard(ctrl.hidraw)
            self.log(f"{ctrl}: battery {info}, light bar back to the profile colour")
        rgb = self._colour_for(ctrl, profile)
        if rgb is None:
            return
        try:
            apply(ctrl, rgb=rgb, writer=w)
        except OSError as e:
            self.log(f"write failed on {ctrl.hidraw}: {e}")

    def step(self) -> None:
        profiles = load_profiles()
        controllers = find_controllers()
        games = detect_games()
        profile, game = pick_profile(profiles, games)

        if profile is not self.current and (profile is None or self.current is None or profile.name != self.current.name):
            label = f"{profile.name}" if profile else "none"
            why = f" for {game}" if game else ""
            self.log(f"profile -> {label}{why}")
            if profile and game:
                notify("padlink", f"Profile '{profile.name}' ({game.name})", "low")
            self.applied.clear()
            self.current = profile

        live = {c.hidraw for c in controllers}
        for gone in set(self.bridges) - live:       # unplugged while in Xbox mode
            self.bridges.pop(gone).stop()
        for gone in set(self.writers) - live:
            self.writers.pop(gone).close()
            self.applied.pop(gone, None)
        self._sync_xbox(controllers, profile)

        now = time.monotonic()
        reapply = now - self.last_reapply >= REAPPLY_EVERY
        previewing = preview_active()
        if previewing:
            self.applied.clear()          # re-apply the real profile once the GUI preview ends
        # include the file's mtime so saving an edited profile re-applies it
        key = ""
        if profile is not None:
            try:
                key = f"{profile.name}:{profile.path.stat().st_mtime if profile.path else 0}"
            except OSError:
                key = profile.name
        self.battery_settings = load_battery_settings()
        for gone in self.low_lit - live:
            self.low_lit.discard(gone)
        for c in controllers:
            info = self._battery(c)
            pct = info.percent
            if previewing:
                continue
            self._update_low_light(c, profile, info)
            if profile is None:
                continue
            battery_changed = profile.player_leds == "battery" and self.last_battery.get(c.hidraw) != pct
            self.last_battery[c.hidraw] = pct
            if self.applied.get(c.hidraw) != key:
                self.log(f"applying '{profile.name}' to {c}")
                self._apply(c, profile, pct)
                self.applied[c.hidraw] = key
            elif battery_changed:
                # only the battery bar changes; don't re-send the colour (it would undo `padlink led`)
                self._apply_player_leds(c, profile, pct)
            elif reapply and profile.trigger_mode == "override" and c.is_dualsense:
                self._apply(c, profile, pct, triggers_only=True)
        if reapply:
            self.last_reapply = now
        if now - self.last_conflict_check >= 15:
            self.last_conflict_check = now
            self._check_conflicts(controllers)

    def _check_conflicts(self, controllers) -> None:
        """Tell the user once when another program grabs the controller (it will fight over the lights)."""
        for c in controllers:
            if c.hidraw in self.bridges:
                continue                     # in Xbox mode the real pad is hidden on purpose
            users = other_users(c.hidraw)
            pids = frozenset(u.pid for u in users)
            if users and pids != self.conflicts_seen.get(c.hidraw):
                msg = conflict_hint(users)
                self.log(f"{c}: {msg}")
                notify(f"{c.model}: light bar may be overridden", msg)
            self.conflicts_seen[c.hidraw] = pids

    def _sync_xbox(self, controllers, profile: Profile | None) -> None:
        """Xbox mode on exactly when the active profile wants it; the real pad sleeps meanwhile."""
        want = bool(profile and profile.xbox_mode)
        for c in controllers:
            running = c.hidraw in self.bridges
            if want and not running:
                w = self._writer(c.hidraw)          # open hidraw *before* it gets hidden
                if w is None:
                    continue
                bridge = XboxBridge(c, w.write, log=self.log)
                try:
                    bridge.start()
                except Exception as e:
                    self.log(f"Xbox mode failed for {c}: {e!r}")
                    bridge.stop()
                    notify("padlink", f"Xbox mode failed: {e}", "critical")
                    continue
                self.bridges[c.hidraw] = bridge
                if bridge.warning:
                    notify("padlink", bridge.warning, "normal")
            elif not want and running:
                self.bridges.pop(c.hidraw).stop()
        write_state(list(self.bridges))

    def stop_xbox(self) -> None:
        for b in self.bridges.values():
            b.stop()
        self.bridges.clear()
        write_state([])

    def run(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "running", False))
        run_helper("show-all", [])     # if a previous run crashed while hiding the pad, wake it up
        self.log("started")
        try:
            while self.running:
                try:
                    self.step()
                except Exception as e:   # keep the service alive; log and carry on
                    print(f"[padlink] error: {e!r}", flush=True)
                time.sleep(self.interval)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop_xbox()
            for w in self.writers.values():
                w.close()
            self.log("stopped")
