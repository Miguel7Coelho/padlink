"""Background service control + what padlink currently detects."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from ..devices import find_controllers  # noqa: E402
from ..games import detect_games  # noqa: E402
from ..profiles import load_profiles, pick_profile  # noqa: E402

UNIT = "padlink.service"
UNIT_PATH = Path(os.path.expanduser("~/.config/systemd/user")) / UNIT
UDEV_CMD = "~/.local/share/padlink/install.sh"


def _systemctl(*args) -> tuple[int, str]:
    if not shutil.which("systemctl"):
        return 1, "systemctl not found"
    r = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)
    return r.returncode, (r.stdout or r.stderr).strip()


def install_unit() -> None:
    """Same unit file install.sh writes; used by the Install button if the service is missing."""
    wrapper = Path(os.path.expanduser("~/.local/bin/padlink"))
    if wrapper.exists():
        exec_start, env = f"{wrapper} daemon -v", ""
    else:
        exec_start = f"{sys.executable} -m padlink daemon -v"
        env = f"Environment=PYTHONPATH={Path(__file__).resolve().parents[2]}\n"
    UNIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    UNIT_PATH.write_text(
        "[Unit]\nDescription=padlink - DualSense/DS4 profiles, triggers, Xbox mode and battery monitor\n"
        "After=graphical-session.target\n\n"
        f"[Service]\nExecStart={exec_start}\n{env}"
        "ExecStopPost=-/usr/bin/sudo -n /usr/local/libexec/padlink-hide show-all\n"
        "Restart=on-failure\nRestartSec=3\n\n[Install]\nWantedBy=default.target\n"
    )
    _systemctl("daemon-reload")


class ServicePage(Adw.PreferencesPage):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self._updating = False

        g = Adw.PreferencesGroup(title="Background service",
                                 description="Switches profiles automatically when a game starts and warns when the battery is low.")
        self.install_row = Adw.ActionRow(title="Service not installed",
                                         subtitle="Adds a systemd user service that runs as you")
        b = Gtk.Button(label="Install", valign=Gtk.Align.CENTER)
        b.add_css_class("suggested-action")
        b.connect("clicked", self._install)
        self.install_row.add_suffix(b)
        self.running = Adw.SwitchRow(title="Running")
        self.enabled = Adw.SwitchRow(title="Start at login")
        self.running.connect("notify::active", self._toggle_running)
        self.enabled.connect("notify::active", self._toggle_enabled)
        for r in (self.install_row, self.running, self.enabled):
            g.add(r)
        self.add(g)

        self.games_group = Adw.PreferencesGroup(title="Right now")
        self.profile_row = Adw.ActionRow(title="Active profile")
        self.games_group.add(self.profile_row)
        self.game_rows: list[Gtk.Widget] = []
        self.add(self.games_group)

        g = Adw.PreferencesGroup(title="Permissions")
        self.perm_row = Adw.ActionRow(title="Controller access")
        copy = Gtk.Button(icon_name="edit-copy-symbolic", valign=Gtk.Align.CENTER,
                          tooltip_text="Copy the installer command")
        copy.add_css_class("flat")
        copy.connect("clicked", self._copy_udev)
        self.perm_row.add_suffix(copy)
        g.add(self.perm_row)
        self.add(g)

        self.refresh()
        GLib.timeout_add_seconds(2, self._tick)

    def _tick(self):
        if self.get_mapped():
            self.refresh()
        return GLib.SOURCE_CONTINUE

    def refresh(self):
        self._updating = True
        installed = UNIT_PATH.exists() or _systemctl("cat", UNIT)[0] == 0
        self.install_row.set_visible(not installed)
        self.running.set_sensitive(installed)
        self.enabled.set_sensitive(installed)
        active = _systemctl("is-active", UNIT)[1] == "active"
        self.running.set_active(active)
        self.running.set_subtitle("Watching for games" if active else "Stopped")
        self.enabled.set_active(_systemctl("is-enabled", UNIT)[1] == "enabled")
        self._updating = False

        games = detect_games()
        p, g = pick_profile(load_profiles(), games)
        self.profile_row.set_subtitle(GLib.markup_escape_text(
            (p.name if p else "none") + (f" — matched {g.name}" if g else "")))
        for r in self.game_rows:
            self.games_group.remove(r)
        self.game_rows = []
        for game in games or []:
            r = Adw.ActionRow(title=GLib.markup_escape_text(game.name),
                              subtitle=GLib.markup_escape_text(
                                  f"{game.source.capitalize()}"
                                  + (f" · AppID {game.steam_appid}" if game.steam_appid else "")
                                  + (f" · {', '.join(sorted(game.exe_names))}" if game.exe_names else "")))
            r.add_prefix(Gtk.Image(icon_name="input-gaming-symbolic"))
            self.games_group.add(r)
            self.game_rows.append(r)
        if not games:
            r = Adw.ActionRow(title="No game running", subtitle="Steam and Lutris games are detected automatically")
            self.games_group.add(r)
            self.game_rows.append(r)

        ctrls = find_controllers()
        if not ctrls:
            self.perm_row.set_subtitle("No controller connected")
        else:
            ok = all(os.access(c.hidraw, os.R_OK | os.W_OK) for c in ctrls)
            self.perm_row.set_subtitle("OK — padlink can talk to your controller" if ok else
                                       "No access to the controller. Run the installer (copy button), then reconnect it.")

    def _install(self, *_):
        install_unit()
        _systemctl("enable", "--now", UNIT)
        self.window.toast("Service installed and started")
        self.refresh()

    def _toggle_running(self, row, _p):
        if self._updating:
            return
        code, out = _systemctl("start" if row.get_active() else "stop", UNIT)
        if code:
            self.window.toast(out[:120])
        GLib.timeout_add(500, lambda: (self.refresh(), False)[1])

    def _toggle_enabled(self, row, _p):
        if self._updating:
            return
        code, out = _systemctl("enable" if row.get_active() else "disable", UNIT)
        if code:
            self.window.toast(out[:120])

    def _copy_udev(self, *_):
        Gdk.Display.get_default().get_clipboard().set(UDEV_CMD)
        self.window.toast("Command copied — paste it in a terminal")
