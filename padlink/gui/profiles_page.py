"""Profile editor: game matching, adaptive triggers, light bar, player indicator, mute light.

Every change is sent to the controller right away (preview) so you can feel trigger settings
before saving. While previewing, the background service stays hands-off (see PREVIEW_LOCK).
"""
from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from ..games import detect_games  # noqa: E402
from ..profiles import (Profile, clear_preview_lock, delete_profile, load_profiles,  # noqa: E402
                        save_profile, touch_preview_lock)
from ..triggers import TriggerSetting  # noqa: E402

# effect id -> (label, start range or None, uses strength)
EFFECTS = [
    ("off", "Off", None, False),
    ("rigid", "Rigid", None, False),
    ("pulse", "Pulse", None, False),
    ("soft", "Soft", None, False),
    ("medium", "Medium", None, False),
    ("hard", "Hard", None, False),
    ("very_hard", "Very hard", None, False),
    ("hardest", "Hardest", (0, 255), False),
    ("full_click", "Full click", None, True),
    ("gamecube", "GameCube-style click", None, False),
    ("choppy", "Choppy", None, False),
    ("resistance", "Resistance (from zone)", (0, 9), True),
    ("bow", "Bow", (0, 8), True),
    ("galloping", "Galloping", (0, 8), True),
    ("semi_auto_gun", "Semi-automatic gun", (2, 7), True),
    ("auto_gun", "Automatic gun", (0, 9), True),
    ("machine", "Machine", (0, 8), True),
    ("vibrate", "Vibrate", (0, 255), True),
]
EFFECT_IDS = [e[0] for e in EFFECTS]
PLAYER_OPTIONS = [("off", "Off"), (1, "Player 1"), (2, "Player 2"), (3, "Player 3"), (4, "Player 4"),
                  ("battery", "Battery level")]
MIC_OPTIONS = [("off", "Off"), ("on", "On"), ("pulse", "Pulsing")]
MODE_OPTIONS = [("override", "padlink"), ("game", "The game")]


def _combo(title: str, labels: list[str], subtitle: str = "") -> Adw.ComboRow:
    row = Adw.ComboRow(title=title, model=Gtk.StringList.new(labels))
    if subtitle:
        row.set_subtitle(subtitle)
    return row


def _split(text: str, sep: str) -> list[str]:
    return [t.strip() for t in text.split(sep) if t.strip()]


class TriggerEditor:
    """Effect + start + strength rows for one trigger, plus a live pull meter."""

    def __init__(self, group: Adw.PreferencesGroup, on_change):
        self.on_change = on_change
        self.effect = _combo("Effect", [e[1] for e in EFFECTS])
        self.start = Adw.SpinRow.new_with_range(0, 255, 1)
        self.start.set_title("Starts at")
        self.force = Adw.SpinRow.new_with_range(0, 255, 5)
        self.force.set_title("Strength")
        self.force.set_value(255)
        self.meter = Gtk.LevelBar(min_value=0, max_value=255, hexpand=True, valign=Gtk.Align.CENTER)
        self.meter.set_size_request(160, -1)
        self.meter_label = Gtk.Label(label="0", width_chars=4, xalign=1)
        self.meter_label.add_css_class("numeric")
        pull = Adw.ActionRow(title="Pull", subtitle="Press the trigger to feel and see it")
        pull.add_suffix(self.meter)
        pull.add_suffix(self.meter_label)
        for r in (self.effect, self.start, self.force, pull):
            group.add(r)
        self.effect.connect("notify::selected", self._effect_changed)
        self.start.connect("notify::value", lambda *_: self.on_change())
        self.force.connect("notify::value", lambda *_: self.on_change())
        self._effect_changed()

    def _effect_changed(self, *_):
        _, _, rng, uses_force = EFFECTS[self.effect.get_selected()]
        if rng:
            lo, hi = rng
            adj = self.start.get_adjustment()
            adj.set_lower(lo)
            adj.set_upper(hi)
            self.start.set_subtitle("zone 0 (released) – 9 (fully pressed)" if hi <= 9 else "0 (released) – 255")
        self.start.set_sensitive(rng is not None)
        self.force.set_sensitive(uses_force)
        self.on_change()

    def set(self, t: TriggerSetting | None):
        t = t or TriggerSetting()
        self.effect.set_selected(EFFECT_IDS.index(t.effect) if t.effect in EFFECT_IDS else 0)
        self.start.set_value(t.start)
        self.force.set_value(t.force)

    def get(self) -> TriggerSetting:
        return TriggerSetting(EFFECT_IDS[self.effect.get_selected()],
                              int(self.start.get_value()), int(self.force.get_value()))

    def show_pull(self, value: int):
        self.meter.set_value(value)
        self.meter_label.set_label(str(value))


class ProfilesPage(Gtk.Box):
    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.window = window
        self.profiles: list[Profile] = []
        self.current: Profile | None = None
        self.loading = False
        self.dirty = False
        self._preview_src = 0
        self._games = []

        # ---- sidebar ----
        side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=230)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("navigation-sidebar")
        self.listbox.connect("row-selected", self._row_selected)
        sc = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        sc.set_child(self.listbox)
        side.append(sc)
        new_btn = Gtk.Button(label="New profile", margin_start=12, margin_end=12, margin_bottom=12)
        new_btn.add_css_class("suggested-action")
        new_btn.connect("clicked", self._new_profile)
        side.append(new_btn)
        self.append(side)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))

        # ---- editor ----
        page = Adw.PreferencesPage(hexpand=True)
        self.editor = page

        g = Adw.PreferencesGroup(title="Profile")
        btns = Gtk.Box(spacing=6)
        self.delete_btn = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Delete profile")
        self.delete_btn.add_css_class("flat")
        self.delete_btn.connect("clicked", self._delete)
        self.save_btn = Gtk.Button(label="Save")
        self.save_btn.add_css_class("suggested-action")
        self.save_btn.connect("clicked", self._save)
        btns.append(self.delete_btn)
        btns.append(self.save_btn)
        g.set_header_suffix(btns)
        self.name = Adw.EntryRow(title="Name")
        self.default = Adw.SwitchRow(title="Default profile", subtitle="Used when no game matches")
        self.preview = Adw.SwitchRow(title="Preview on controller",
                                     subtitle="Keep this profile on the controller while padlink is open. "
                                              "Game profiles switch automatically again when you close it.",
                                     active=True)
        for r in (self.name, self.default, self.preview):
            g.add(r)
        page.add(g)

        g = Adw.PreferencesGroup(title="Game matching",
                                 description="Any match selects this profile. Steam AppID wins over Lutris title, which wins over executable name.")
        self.steam = Adw.EntryRow(title="Steam AppIDs (comma-separated)")
        self.lutris = Adw.EntryRow(title="Lutris game titles (separate with ;)")
        self.exe = Adw.EntryRow(title="Executable names (comma-separated, * allowed)")
        self.running = _combo("Running game", ["(none detected)"], "Fill the fields from a game that's running now")
        use = Gtk.Button(label="Use", valign=Gtk.Align.CENTER)
        use.connect("clicked", self._use_running)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Rescan")
        refresh.add_css_class("flat")
        refresh.connect("clicked", lambda *_: self.refresh_games())
        self.running.add_suffix(refresh)
        self.running.add_suffix(use)
        for r in (self.steam, self.lutris, self.exe, self.running):
            g.add(r)
        page.add(g)

        g = Adw.PreferencesGroup(title="Xbox controller mode")
        self.xbox = Adw.SwitchRow(title="Show as an Xbox 360 controller",
                                  subtitle="For games that only understand XInput. While this profile is active, "
                                           "games see an Xbox pad and the DualSense itself sleeps; it wakes up "
                                           "again when the profile switches off.")
        g.add(self.xbox)
        page.add(g)

        g = Adw.PreferencesGroup(title="Adaptive triggers")
        self.mode = _combo("Who controls the triggers", [m[1] for m in MODE_OPTIONS],
                           "Choose “The game” for titles with native DualSense support through Proton")
        g.add(self.mode)
        page.add(g)
        gl = Adw.PreferencesGroup(title="L2 trigger")
        self.left = TriggerEditor(gl, self._changed)
        page.add(gl)
        gr = Adw.PreferencesGroup(title="R2 trigger")
        self.right = TriggerEditor(gr, self._changed)
        page.add(gr)

        g = Adw.PreferencesGroup(title="Lights")
        self.color_btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER)
        lb = Adw.ActionRow(title="Light bar")
        lb.add_suffix(self.color_btn)
        self.player = _combo("Player indicator", [o[1] for o in PLAYER_OPTIONS])
        self.mic = _combo("Mute button light", [o[1] for o in MIC_OPTIONS])
        for r in (lb, self.player, self.mic):
            g.add(r)
        page.add(g)

        self.append(page)   # Adw.PreferencesPage scrolls by itself

        for w, sig in ((self.name, "changed"), (self.steam, "changed"), (self.lutris, "changed"),
                       (self.exe, "changed"), (self.default, "notify::active"), (self.xbox, "notify::active"), (self.mode, "notify::selected"),
                       (self.color_btn, "notify::rgba"), (self.player, "notify::selected"),
                       (self.mic, "notify::selected")):
            w.connect(sig, self._changed)

        GLib.timeout_add_seconds(2, self._keep_preview_lock)
        # coming back on screen (window restored, page switched back): send the edited profile again
        self.connect("map", lambda *_: self._schedule_preview())
        self.preview.connect("notify::active", lambda *_: (None if self.preview.get_active()
                                                           else clear_preview_lock()))
        self.reload()

    # ---------- list ----------
    def reload(self, select_path=None):
        self.profiles = load_profiles()
        if not self.profiles:
            p = Profile(name="Desktop", is_default=True, color=(0x30, 0x50, 0xFF), player_leds="battery")
            save_profile(p)
            self.profiles = load_profiles()
        while (row := self.listbox.get_row_at_index(0)) is not None:
            self.listbox.remove(row)
        sel = None
        for p in self.profiles:
            row = Adw.ActionRow(title=GLib.markup_escape_text(p.name),
                                subtitle="Default" if p.is_default else self._match_summary(p))
            row.profile = p
            self.listbox.append(row)
            if select_path and p.path == select_path:
                sel = row
        self.listbox.select_row(sel or self.listbox.get_row_at_index(0))

    @staticmethod
    def _match_summary(p: Profile) -> str:
        parts = []
        if p.steam_appids:
            parts.append("Steam")
        if p.lutris_titles:
            parts.append("Lutris")
        if p.exe_globs:
            parts.append(".exe")
        return " · ".join(parts) or "no game rules"

    def _row_selected(self, _lb, row):
        if row is None or row.profile is self.current:
            return
        if self.dirty and self.current is not None:
            self._confirm("Discard changes?", f"“{self.current.name}” has unsaved changes.", "Discard",
                          lambda: self._load(row.profile),
                          lambda: self._reselect_current())
        else:
            self._load(row.profile)

    def _reselect_current(self):
        for i, p in enumerate(self.profiles):
            if p is self.current:
                self.listbox.select_row(self.listbox.get_row_at_index(i))

    # ---------- form <-> profile ----------
    def _load(self, p: Profile):
        self.loading = True
        self.current = p
        self.name.set_text(p.name)
        self.default.set_active(p.is_default)
        self.steam.set_text(", ".join(str(a) for a in sorted(p.steam_appids)))
        self.lutris.set_text("; ".join(sorted(p.lutris_titles)))
        self.exe.set_text(", ".join(p.exe_globs))
        self.mode.set_selected(0 if p.trigger_mode != "game" else 1)
        self.xbox.set_active(p.xbox_mode)
        self.left.set(p.left)
        self.right.set(p.right)
        rgba = Gdk.RGBA()
        r, g, b = p.color or (0, 0, 0)
        rgba.red, rgba.green, rgba.blue, rgba.alpha = r / 255, g / 255, b / 255, 1
        self.color_btn.set_rgba(rgba)
        keys = [o[0] for o in PLAYER_OPTIONS]
        self.player.set_selected(keys.index(p.player_leds) if p.player_leds in keys else 0)
        mkeys = [o[0] for o in MIC_OPTIONS]
        self.mic.set_selected(mkeys.index(p.mic_led) if p.mic_led in mkeys else 0)
        self.loading = False
        self.dirty = False
        self.save_btn.set_sensitive(p.path is None)
        self._schedule_preview()

    def form_profile(self) -> Profile:
        rgba = self.color_btn.get_rgba()
        appids = set()
        for a in _split(self.steam.get_text(), ","):
            if a.isdigit():
                appids.add(int(a))
        return Profile(
            name=self.name.get_text().strip() or "Untitled",
            path=self.current.path if self.current else None,
            steam_appids=appids,
            lutris_titles=set(_split(self.lutris.get_text(), ";")),
            exe_globs=[e.lower() for e in _split(self.exe.get_text(), ",")],
            trigger_mode=MODE_OPTIONS[self.mode.get_selected()][0],
            left=self.left.get(),
            right=self.right.get(),
            color=(round(rgba.red * 255), round(rgba.green * 255), round(rgba.blue * 255)),
            player_leds=PLAYER_OPTIONS[self.player.get_selected()][0],
            mic_led=MIC_OPTIONS[self.mic.get_selected()][0],
            is_default=self.default.get_active(),
            xbox_mode=self.xbox.get_active(),
        )

    def _changed(self, *_):
        if self.loading:
            return
        self.dirty = True
        self.save_btn.set_sensitive(True)
        self._schedule_preview()

    # ---------- live preview ----------
    def _schedule_preview(self):
        if self._preview_src:
            GLib.source_remove(self._preview_src)
        self._preview_src = GLib.timeout_add(80, self._do_preview)

    def is_previewing(self) -> bool:
        """The selected profile stays on the controller on every tab while padlink is open.

        (It used to stop when you left the Profiles page, so switching to the Controller tab
        handed the controller back to the background service, which put the active game's
        profile back after a few seconds.)
        """
        return self.preview.get_active() and not self.window.closing

    def _do_preview(self):
        self._preview_src = 0
        p = self.form_profile()
        if self.is_previewing():
            touch_preview_lock()
            self.window.apply_profile_preview(p)
        else:
            self.window.mirror_profile(p)     # drawing only, nothing sent to the controller
        return GLib.SOURCE_REMOVE

    def _keep_preview_lock(self):
        if self.is_previewing():
            touch_preview_lock()
        return GLib.SOURCE_CONTINUE

    def on_shown(self):
        self.refresh_games()
        self._schedule_preview()

    def show_pull(self, l2: int, r2: int):
        self.left.show_pull(l2)
        self.right.show_pull(r2)

    # ---------- running games ----------
    def refresh_games(self):
        self._games = detect_games()
        labels = [str(g) for g in self._games] or ["(none detected)"]
        self.running.set_model(Gtk.StringList.new(labels))

    def _use_running(self, *_):
        i = self.running.get_selected()
        if not self._games or i >= len(self._games):
            self.window.toast("Start the game first, then press refresh.")
            return
        g = self._games[i]
        if g.steam_appid:
            ids = set(_split(self.steam.get_text(), ",")) | {str(g.steam_appid)}
            self.steam.set_text(", ".join(sorted(ids)))
        if g.source == "lutris":
            titles = set(_split(self.lutris.get_text(), ";")) | {g.name}
            self.lutris.set_text("; ".join(sorted(titles)))
        if g.exe_names:
            exes = list(dict.fromkeys(_split(self.exe.get_text(), ",") + sorted(g.exe_names)))
            self.exe.set_text(", ".join(exes))
        if self.name.get_text() in ("", "New profile"):
            self.name.set_text(g.name)

    # ---------- actions ----------
    def _new_profile(self, *_):
        def go():
            self.listbox.unselect_all()
            p = Profile(name="New profile", color=(255, 255, 255), player_leds=1,
                        left=TriggerSetting(), right=TriggerSetting())
            self._load(p)
            self.dirty = True
            self.save_btn.set_sensitive(True)
            self.name.grab_focus()
            self.refresh_games()
        if self.dirty:
            self._confirm("Discard changes?", "The current profile has unsaved changes.", "Discard", go)
        else:
            go()

    def _save(self, *_):
        p = self.form_profile()
        try:
            path = save_profile(p)
        except OSError as e:
            self.window.toast(f"Couldn't save: {e}")
            return
        self.dirty = False
        self.current = None
        self.reload(select_path=path)
        self.window.toast(f"Saved “{p.name}”")

    def _delete(self, *_):
        if self.current is None or self.current.path is None:
            self.reload()
            return
        p = self.current

        def go():
            delete_profile(p)
            self.dirty = False
            self.current = None
            self.reload()
            self.window.toast(f"Deleted “{p.name}”")
        self._confirm("Delete profile?", f"“{p.name}” will be removed.", "Delete", go, destructive=True)

    def _confirm(self, heading, body, action, on_yes, on_no=None, destructive=False):
        d = Adw.AlertDialog(heading=heading, body=body)
        d.add_response("cancel", "Cancel")
        d.add_response("ok", action)
        d.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE if destructive
                                  else Adw.ResponseAppearance.SUGGESTED)

        def done(dlg, res):
            if dlg.choose_finish(res) == "ok":
                on_yes()
            elif on_no:
                on_no()
        d.choose(self.window, None, done)
