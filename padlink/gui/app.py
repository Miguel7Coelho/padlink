"""padlink GUI (GTK4 + libadwaita). Run with `padlink gui` or `python3 -m padlink gui`.

Fedora Workstation already ships PyGObject, GTK4 and libadwaita, so there's nothing to install.
"""
from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from ..battery import read_battery  # noqa: E402
from ..settings import load_battery_settings  # noqa: E402
from ..control import apply, resolve_player_leds  # noqa: E402
from ..devices import Controller, HidrawWriter, find_controllers  # noqa: E402
from ..inputs import BUTTON_NAMES, InputReader  # noqa: E402
from ..profiles import Profile  # noqa: E402
from ..xinput import xbox_mode_active  # noqa: E402
from .padview import PadView  # noqa: E402
from .profiles_page import ProfilesPage  # noqa: E402
from .service_page import ServicePage  # noqa: E402

APP_ID = "io.github.miguel7coelho.padlink"


def _battery_icon(pct, charging) -> str:
    if pct is None:
        return "battery-missing-symbolic"
    level = min(100, (pct + 5) // 10 * 10)
    return f"battery-level-{level}{'-charging' if charging else ''}-symbolic"


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="padlink", default_width=1040, default_height=760,
                         width_request=360, height_request=480)
        self.controllers: list[Controller] = []
        self.ctrl: Controller | None = None
        self.reader: InputReader | None = None
        self.writer: HidrawWriter | None = None
        self._last_serial = -1

        self.toasts = Adw.ToastOverlay()
        self.stack = Adw.ViewStack()

        # header
        header = Adw.HeaderBar()
        switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)
        self.ctrl_model = Gtk.StringList()
        self.ctrl_dd = Gtk.DropDown(model=self.ctrl_model, tooltip_text="Controller")
        self.ctrl_dd.connect("notify::selected", self._controller_selected)
        header.pack_start(self.ctrl_dd)
        self.batt_icon = Gtk.Image(icon_name="battery-missing-symbolic")
        self.batt_label = Gtk.Label(label="")
        self.batt_label.add_css_class("numeric")
        bbox = Gtk.Box(spacing=6)
        bbox.append(self.batt_icon)
        bbox.append(self.batt_label)
        header.pack_end(bbox)

        # Controller page
        self.pad = PadView()
        self.readout = Gtk.Label(xalign=0, wrap=True)
        self.readout.add_css_class("monospace")
        self.readout.add_css_class("dim-label")
        live = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                       margin_top=12, margin_bottom=18, margin_start=18, margin_end=18)
        live.append(self.pad)
        live.append(self.readout)
        self.empty = Adw.StatusPage(icon_name="input-gaming-symbolic", title="Connect your controller",
                                    description="Plug in a DualSense or DualShock 4 with USB, or pair it over Bluetooth "
                                                "(hold Create + PS until the light bar flashes).")
        self.ctrl_stack = Gtk.Stack()
        self.ctrl_stack.add_named(self.empty, "empty")
        self.ctrl_stack.add_named(live, "live")
        self.stack.add_titled_with_icon(self.ctrl_stack, "controller", "Controller", "input-gaming-symbolic")

        self.profiles_page = ProfilesPage(self)
        self.stack.add_titled_with_icon(self.profiles_page, "profiles", "Profiles", "document-edit-symbolic")
        self.service_page = ServicePage(self)
        self.stack.add_titled_with_icon(self.service_page, "service", "Service", "emblem-system-symbolic")
        self.stack.connect("notify::visible-child-name", self._page_changed)

        self.toasts.set_child(self.stack)
        tv = Adw.ToolbarView()
        tv.add_top_bar(header)
        bar = Adw.ViewSwitcherBar(stack=self.stack)
        tv.add_bottom_bar(bar)
        tv.set_content(self.toasts)
        self.set_content(tv)

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640sp"))
        bp.add_setter(bar, "reveal", True)
        bp.add_setter(switcher, "visible", False)
        self.add_breakpoint(bp)

        self.connect("close-request", self._on_close)
        self._scan()
        GLib.timeout_add_seconds(2, self._scan)
        GLib.timeout_add(33, self._frame)

    # ---------- controllers ----------
    def _scan(self):
        found = find_controllers()
        if [c.hidraw for c in found] != [c.hidraw for c in self.controllers]:
            self.controllers = found
            self.ctrl_model.splice(0, self.ctrl_model.get_n_items(), [f"{c.model} · {c.connection}" for c in found])
            self.ctrl_dd.set_visible(bool(found))
            if found:
                self.ctrl_dd.set_selected(0)
            self._controller_selected()
        self._update_battery()
        return GLib.SOURCE_CONTINUE

    def _controller_selected(self, *_):
        i = self.ctrl_dd.get_selected()
        ctrl = self.controllers[i] if self.controllers and i < len(self.controllers) else None
        if ctrl is not None and self.ctrl is not None and ctrl.hidraw == self.ctrl.hidraw:
            return
        if self.reader:
            self.reader.stop()
            self.reader = None
        if self.writer:
            self.writer.close()
            self.writer = None
        self.ctrl = ctrl
        if ctrl is None:
            self.ctrl_stack.set_visible_child_name("empty")
            return
        self.pad.is_dualsense = ctrl.is_dualsense
        self.reader = InputReader(ctrl.hidraw, ctrl.is_dualsense).start()
        self.ctrl_stack.set_visible_child_name("live")
        self.profiles_page.on_shown()

    def _retry_reader(self):
        if self.ctrl is None or self.reader is not None:
            return GLib.SOURCE_REMOVE
        if self.ctrl.hidraw in xbox_mode_active():
            return GLib.SOURCE_CONTINUE
        self.reader = InputReader(self.ctrl.hidraw, self.ctrl.is_dualsense).start()
        self.ctrl_stack.set_visible_child_name("live")
        return GLib.SOURCE_REMOVE

    def _update_battery(self):
        if self.ctrl is None:
            self.batt_label.set_label("")
            self.batt_icon.set_visible(False)
            return
        b = read_battery(self.ctrl.battery_path)
        self.batt_icon.set_visible(True)
        self.batt_icon.set_from_icon_name(_battery_icon(b.percent, b.charging))
        self.batt_label.set_label(f"{b.percent}%" if b.percent is not None else "")
        self.batt_icon.set_tooltip_text(f"Battery {b}")
        low = load_battery_settings().is_low(b.percent, b.charging)
        for w in (self.batt_label, self.batt_icon):
            (w.add_css_class if low else w.remove_css_class)("error")    # libadwaita's red

    def _frame(self):
        r = self.reader
        if r is None:
            return GLib.SOURCE_CONTINUE
        if r.error:
            if self.ctrl and self.ctrl.hidraw in xbox_mode_active():
                self.empty.set_icon_name("input-gaming-symbolic")
                self.empty.set_title("Xbox controller mode is on")
                self.empty.set_description("Games see your controller as an Xbox 360 pad right now, so the "
                                           "DualSense itself is asleep. The live view comes back as soon as "
                                           "Xbox mode turns off, for example when you close the game.")
                self.ctrl_stack.set_visible_child_name("empty")
                self.reader = None
                GLib.timeout_add_seconds(3, self._retry_reader)
                return GLib.SOURCE_CONTINUE
            if "ermission" in r.error:
                self.empty.set_title("No permission to read the controller")
                self.empty.set_description("Install the udev rule from the Service page, then reconnect the controller.")
            self.ctrl_stack.set_visible_child_name("empty")
            return GLib.SOURCE_CONTINUE
        if r.serial != self._last_serial:
            self._last_serial = r.serial
            s = r.state
            self.pad.update(s)
            self.profiles_page.show_pull(s.l2, s.r2)
            pressed = ", ".join(BUTTON_NAMES[b] for b in BUTTON_NAMES if b in s.pressed) or "—"
            self.readout.set_label(
                f"Left stick  {s.lx:3d} {s.ly:3d}    Right stick  {s.rx:3d} {s.ry:3d}    L2 {s.l2:3d}   R2 {s.r2:3d}\n"
                f"Gyro  {s.gyro[0]:6d} {s.gyro[1]:6d} {s.gyro[2]:6d}    Accel  {s.accel[0]:6d} {s.accel[1]:6d} {s.accel[2]:6d}\n"
                f"Pressed: {pressed}")
        return GLib.SOURCE_CONTINUE

    # ---------- output ----------
    def mirror_profile(self, p: Profile) -> int | None:
        """Show a profile's light bar and player indicator on the drawing."""
        if p.color:
            self.pad.lightbar = tuple(c / 255 for c in p.color)
        pct = read_battery(self.ctrl.battery_path).percent if self.ctrl else None
        mask = resolve_player_leds(p.player_leds, pct)
        self.pad.player_mask = mask or 0
        self.pad.queue_draw()
        return mask

    def apply_profile_preview(self, p: Profile):
        """Called by the profile editor: send the profile to the controller and mirror it on the drawing."""
        mask = self.mirror_profile(p)
        if self.ctrl is None:
            return
        if self.ctrl.hidraw in xbox_mode_active():
            return      # the pad is asleep in Xbox mode; nothing can be written to it from here
        try:
            if self.writer is None:
                self.writer = HidrawWriter(self.ctrl.hidraw)
            own = p.trigger_mode == "override"
            apply(self.ctrl, left=p.left if own else None, right=p.right if own else None,
                  rgb=p.color, player_leds=mask, mic_led=p.mic_led, writer=self.writer)
        except PermissionError:
            self.toast("No permission to write to the controller — see the Service page.")
        except OSError as e:
            self.toast(f"Controller write failed: {e}")
            self.writer = None

    def toast(self, text: str):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=3))

    def _page_changed(self, *_):
        name = self.stack.get_visible_child_name()
        if name == "profiles":
            self.profiles_page.on_shown()
        elif name == "service":
            self.service_page.refresh()

    def _on_close(self, *_):
        if self.reader:
            self.reader.stop()
        if self.writer:
            self.writer.close()
        return False


class PadlinkApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_activate(self):
        win = self.get_active_window() or MainWindow(self)
        win.present()


def main(argv=None) -> int:
    return PadlinkApp().run(argv if argv is not None else sys.argv[:1])


if __name__ == "__main__":
    sys.exit(main())
