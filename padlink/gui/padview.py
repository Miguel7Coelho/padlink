"""Live controller view: the rendered controller (transparent PNG) with the live state on top.

Assets (built by scripts/make-realistic-asset.py from art/controller-realistic-source.png):
  assets/controller-realistic.png         the controller, transparent background
  assets/controller-realistic-labels.png  labels that sit outside the controller (white + alpha),
                                          drawn in the theme's text colour so they read in light
                                          and dark mode
Coordinates below are in the source image's pixels; R_CROP is where the assets were cropped.
"""
from __future__ import annotations

import math
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

import cairo  # noqa: E402

from ..inputs import PadState  # noqa: E402

ACCENT = (0.21, 0.52, 0.89)
FACE_COLORS = {          # symbol colours for the action buttons
    "triangle": (0.27, 0.78, 0.58),
    "circle": (0.94, 0.33, 0.38),
    "cross": (0.40, 0.60, 0.98),
    "square": (0.88, 0.48, 0.80),
}

ASSETS = Path(__file__).with_name("assets")
BODY_PNG = ASSETS / "controller-realistic.png"
LABELS_PNG = ASSETS / "controller-realistic-labels.png"

R_CROP = (109, 28)                         # top-left of the assets inside the source image
SURFACE = (20 / 255, 21 / 255, 26 / 255)   # controller surface colour around the player LEDs
R_L2, R_R2 = (400, 62), (1266, 62)
R_PILL = (176, 46)
R_BUMPER = {"l1": (400, 152), "r1": (1266, 152)}
R_SMALL = {"create": (553, 230), "options": (1115, 230)}
R_TOUCH = (607, 181, 454, 179)             # x, y, w, h of the light bar ring
R_PLAYER = (758, 796, 834, 872, 910)
R_PLAYER_Y = 388
R_MUTE = (832, 532)                        # drawn by padlink, below PS
R_DPAD = (422, 392)
R_FACE = {"triangle": (1245, 320), "circle": (1321, 393), "square": (1170, 394), "cross": (1245, 466)}
R_STICKS = {"l3": (635, 545), "r3": (1032, 545)}
R_PS = (832, 465)

# Xbox mode: what each control is called on the virtual Xbox 360 pad
XBOX_FACE = {            # PlayStation button -> (Xbox letter, Xbox colour)
    "cross": ("A", (0.42, 0.75, 0.27)),
    "circle": ("B", (0.89, 0.26, 0.24)),
    "square": ("X", (0.20, 0.52, 0.92)),
    "triangle": ("Y", (0.96, 0.75, 0.15)),
}


def _mix(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


class PadView(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.state = PadState()
        self.lightbar = (0.19, 0.31, 1.0)
        self.player_mask = 0
        self.is_dualsense = True
        self.xbox = False          # Xbox mode on: show the Xbox 360 names on top
        self._body = self._load(BODY_PNG)
        self._labels = self._load(LABELS_PNG)
        w, h = (self._body.get_width(), self._body.get_height()) if self._body else (1442, 878)
        self._size = (w, h)
        self.set_content_width(w // 2)
        self.set_content_height(h // 2)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_draw_func(self._draw)

    @staticmethod
    def _load(path):
        try:
            return cairo.ImageSurface.create_from_png(str(path))
        except (OSError, cairo.Error):
            return None

    def update(self, state: PadState) -> None:
        self.state = state
        self.queue_draw()

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _rrect(cr, x, y, w, h, r):
        r = min(r, w / 2, h / 2)
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    @staticmethod
    def _text(cr, text, x, y, size, color, bold=True):
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL,
                            cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(size)
        ext = cr.text_extents(text)
        cr.move_to(x - ext.width / 2 - ext.x_bearing, y - ext.height / 2 - ext.y_bearing)
        cr.set_source_rgba(*color)
        cr.show_text(text)
        cr.new_path()

    @staticmethod
    def _glow_fill(cr, color, alpha=0.55):
        cr.set_source_rgba(*color, alpha)
        cr.fill_preserve()
        cr.set_source_rgba(*color, 0.95)
        cr.set_line_width(4)
        cr.stroke()

    @staticmethod
    def _symbol(cr, name, x, y, color):
        cr.set_source_rgba(*color, 1)
        cr.set_line_width(2.2)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        if name == "triangle":
            cr.move_to(x, y - 7)
            cr.line_to(x + 7, y + 5)
            cr.line_to(x - 7, y + 5)
            cr.close_path()
        elif name == "circle":
            cr.new_sub_path()
            cr.arc(x, y, 6.5, 0, 2 * math.pi)
        elif name == "cross":
            cr.move_to(x - 6, y - 6)
            cr.line_to(x + 6, y + 6)
            cr.move_to(x + 6, y - 6)
            cr.line_to(x - 6, y + 6)
        else:
            cr.rectangle(x - 6, y - 6, 12, 12)
        cr.stroke()

    # ------------------------------------------------------------------ draw
    def _draw(self, area, cr, width, height):
        c = self.get_color()
        fg = (c.red, c.green, c.blue)
        s, p = self.state, self.state.pressed
        iw, ih = self._size
        scale = min(width / iw, height / ih)
        cr.translate((width - iw * scale) / 2, (height - ih * scale) / 2)
        cr.scale(scale, scale)

        if self._body is None:
            self._text(cr, "controller image missing (padlink/gui/assets)", iw / 2, ih / 2, 28, (*fg, 0.7))
            return
        cr.set_source_surface(self._body, 0, 0)
        cr.paint()
        if self._labels is not None:              # floating labels in the theme's text colour
            cr.set_source_rgba(*fg, 0.92)
            cr.mask_surface(self._labels, 0, 0)

        cr.translate(-R_CROP[0], -R_CROP[1])      # everything below uses source-image coordinates
        self._draw_triggers(cr, fg, s)
        self._draw_lightbar(cr, s, p)
        self._draw_player_leds(cr)
        self._draw_pressed(cr, p)
        self._draw_sticks(cr, s, p)
        self._draw_ps(cr, p)
        if self.is_dualsense:
            self._draw_mute(cr, p)
        if self.xbox:
            self._draw_xbox_names(cr)

    def _draw_triggers(self, cr, fg, s):
        pw, ph = R_PILL
        for name, (cx, cy), val in (("l2", R_L2, s.l2), ("r2", R_R2, s.r2)):
            x, y = cx - pw / 2, cy - ph / 2
            self._rrect(cr, x, y, pw, ph, ph / 2)
            cr.set_source_rgba(*fg, 0.06)
            cr.fill_preserve()
            cr.set_source_rgba(*fg, 0.85)
            cr.set_line_width(3)
            cr.stroke()
            if val:
                cr.save()
                self._rrect(cr, x + 5, y + 5, pw - 10, ph - 10, ph / 2 - 5)
                cr.clip()
                w = (pw - 10) * val / 255
                cr.rectangle(x + 5 if name == "l2" else x + pw - 5 - w, y, w, ph)
                cr.set_source_rgba(*ACCENT, 0.95)
                cr.fill()
                cr.restore()
            label = {"l2": "LT", "r2": "RT"}[name] if self.xbox else name.upper()
            self._text(cr, f"{label}  {val}", cx, cy, 26, (*fg, 0.95), bold=False)

    def _draw_lightbar(self, cr, s, p):
        tx, ty, tw, th = R_TOUCH
        for i in range(6, 0, -1):
            self._rrect(cr, tx - i * 3, ty - i * 3, tw + i * 6, th + i * 6, 34 + i * 3)
            cr.set_source_rgba(*self.lightbar, 0.05)
            cr.set_line_width(6)
            cr.stroke()
        self._rrect(cr, tx + 2, ty + 2, tw - 4, th - 4, 32)
        cr.set_source_rgba(*self.lightbar, 1)
        cr.set_line_width(7)
        cr.stroke()
        if "touchpad" in p:
            self._rrect(cr, tx + 8, ty + 8, tw - 16, th - 16, 26)
            cr.set_source_rgba(*ACCENT, 0.25)
            cr.fill()
        for t in s.touches:
            if t.active:
                px = tx + 10 + (tw - 20) * min(t.x, s.touch_w) / s.touch_w
                py = ty + 10 + (th - 20) * min(t.y, s.touch_h) / s.touch_h
                g = cairo.RadialGradient(px, py, 0, px, py, 26)
                g.add_color_stop_rgba(0, *ACCENT, 0.95)
                g.add_color_stop_rgba(0.5, *ACCENT, 0.55)
                g.add_color_stop_rgba(1, *ACCENT, 0)
                cr.new_path()
                cr.arc(px, py, 26, 0, 2 * math.pi)
                cr.set_source(g)
                cr.fill()

    def _draw_player_leds(self, cr):
        for i, x in enumerate(R_PLAYER):
            y = R_PLAYER_Y
            cr.new_path()
            cr.arc(x, y, 12, 0, 2 * math.pi)       # cover the image's static LEDs
            cr.set_source_rgb(*SURFACE)
            cr.fill()
            cr.new_path()
            if self.player_mask & (1 << i):
                cr.arc(x, y, 14, 0, 2 * math.pi)
                cr.set_source_rgba(*ACCENT, 0.3)
                cr.fill()
                cr.arc(x, y, 7.5, 0, 2 * math.pi)
                cr.set_source_rgba(*_mix(ACCENT, (1, 1, 1), 0.2), 1)
                cr.fill()
            else:
                cr.arc(x, y, 7, 0, 2 * math.pi)
                cr.set_source_rgba(0.32, 0.33, 0.37, 1)
                cr.fill()

    def _draw_pressed(self, cr, p):
        for name, (cx, cy) in R_BUMPER.items():
            if name in p:
                cr.new_path()
                cr.save()
                cr.translate(cx, cy)
                cr.scale(125, 32)
                cr.arc(0, 0, 1, 0, 2 * math.pi)
                cr.restore()
                self._glow_fill(cr, ACCENT, 0.5)
        for name, (cx, cy) in R_SMALL.items():
            if name in p:
                self._rrect(cr, cx - 15, cy - 30, 30, 60, 15)
                self._glow_fill(cr, ACCENT)
        dx, dy = R_DPAD
        arms = {"up": (dx - 30, dy - 105, 60, 72), "down": (dx - 30, dy + 33, 60, 72),
                "left": (dx - 105, dy - 30, 72, 60), "right": (dx + 33, dy - 30, 72, 60)}
        for name, (x, y, w, h) in arms.items():
            if name in p:
                self._rrect(cr, x, y, w, h, 14)
                self._glow_fill(cr, ACCENT)
        for name, (cx, cy) in R_FACE.items():
            if name in p:
                cr.new_path()
                cr.arc(cx, cy, 38, 0, 2 * math.pi)
                self._glow_fill(cr, FACE_COLORS[name], 0.6)
                cr.save()
                cr.translate(cx, cy)
                cr.scale(2.6, 2.6)
                self._symbol(cr, name, 0, 0, (1, 1, 1))
                cr.restore()

    def _draw_sticks(self, cr, s, p):
        for name, (cx, cy), x, y in (("l3", R_STICKS["l3"], s.lx, s.ly), ("r3", R_STICKS["r3"], s.rx, s.ry)):
            if name in p:
                cr.new_path()
                cr.arc(cx, cy, 80, 0, 2 * math.pi)
                cr.set_source_rgba(*ACCENT, 0.95)
                cr.set_line_width(7)
                cr.stroke()
            # stick position: a glowing marker that moves across the cap
            mx = cx + (x - 128) / 128 * 58
            my = cy + (y - 128) / 128 * 58
            g = cairo.RadialGradient(mx, my, 0, mx, my, 22)
            g.add_color_stop_rgba(0, *ACCENT, 1)
            g.add_color_stop_rgba(0.45, *ACCENT, 0.6)
            g.add_color_stop_rgba(1, *ACCENT, 0)
            cr.new_path()
            cr.arc(mx, my, 22, 0, 2 * math.pi)
            cr.set_source(g)
            cr.fill()

    def _draw_mute(self, cr, p):
        mx, my = R_MUTE
        self._rrect(cr, mx - 38, my - 20, 76, 40, 20)
        if "mute" in p:
            self._glow_fill(cr, (0.95, 0.55, 0.2), 0.75)
        else:
            g = cairo.LinearGradient(0, my - 20, 0, my + 20)
            g.add_color_stop_rgb(0, 0.10, 0.105, 0.12)
            g.add_color_stop_rgb(1, 0.05, 0.055, 0.065)
            cr.set_source(g)
            cr.fill_preserve()
            cr.set_source_rgba(0.82, 0.83, 0.86, 0.95)
            cr.set_line_width(3.5)
            cr.stroke()
        # leader line + label, matching the image's other labels
        cr.set_source_rgba(1, 1, 1, 0.92)
        cr.new_path()
        cr.arc(mx + 38, my, 4, 0, 2 * math.pi)
        cr.fill()
        cr.move_to(mx + 38, my)
        cr.line_to(mx + 54, my)
        cr.set_line_width(2)
        cr.stroke()
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(23)
        ext = cr.text_extents("Mute")
        cr.move_to(mx + 59 - ext.x_bearing, my - ext.height / 2 - ext.y_bearing)
        cr.show_text("Mute")
        cr.new_path()

    def _draw_ps(self, cr, p):
        px, py = R_PS                              # the image has no PS button; draw one
        cr.new_path()
        cr.arc(px, py, 24, 0, 2 * math.pi)
        if "ps" in p:
            self._glow_fill(cr, ACCENT, 0.8)
        else:
            cr.set_source_rgba(1, 1, 1, 0.06)
            cr.fill_preserve()
            cr.set_source_rgba(1, 1, 1, 0.55)
            cr.set_line_width(3)
            cr.stroke()
        self._text(cr, "PS", px, py, 18, (1, 1, 1, 0.9))

    def _tag(self, cr, text, x, y, size=19):
        """A small Xbox-green tag with the Xbox name of a control."""
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(size)
        w = cr.text_extents(text).width + size
        h = size * 1.6
        self._rrect(cr, x - w / 2, y - h / 2, w, h, h / 2)
        cr.set_source_rgba(0.06, 0.49, 0.06, 0.95)
        cr.fill_preserve()
        cr.set_source_rgba(1, 1, 1, 0.85)
        cr.set_line_width(1.5)
        cr.stroke()
        self._text(cr, text, x, y, size, (1, 1, 1, 1))

    def _draw_xbox_names(self, cr):
        # face buttons: the Xbox letter as a coloured badge just outside each button
        fx = sum(x for x, _ in R_FACE.values()) / 4
        fy = sum(y for _, y in R_FACE.values()) / 4
        for name, (cx, cy) in R_FACE.items():
            letter, colour = XBOX_FACE[name]
            d = math.hypot(cx - fx, cy - fy) or 1
            bx, by = cx + (cx - fx) / d * 30 + 24, cy + (cy - fy) / d * 30 - 24
            cr.new_path()
            cr.arc(bx, by, 17, 0, 2 * math.pi)
            cr.set_source_rgba(*colour, 1)
            cr.fill_preserve()
            cr.set_source_rgba(0, 0, 0, 0.6)
            cr.set_line_width(2)
            cr.stroke()
            self._text(cr, letter, bx, by, 21, (1, 1, 1, 1))
        self._tag(cr, "LB", R_BUMPER["l1"][0] - 80, R_BUMPER["l1"][1] + 4)
        self._tag(cr, "RB", R_BUMPER["r1"][0] + 80, R_BUMPER["r1"][1] + 4)
        self._tag(cr, "Back", R_SMALL["create"][0], R_SMALL["create"][1] + 58, 16)
        self._tag(cr, "Start", R_SMALL["options"][0], R_SMALL["options"][1] + 58, 16)
        self._tag(cr, "Guide", R_PS[0] - 78, R_PS[1], 16)
