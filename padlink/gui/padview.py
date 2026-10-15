"""Live controller schematic drawn with cairo. Labels use Sony's control names."""
from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..inputs import PadState  # noqa: E402

W, H = 600, 420          # logical canvas; scaled to the widget
ACCENT = (0.21, 0.52, 0.89)
FACE_COLORS = {          # the classic PlayStation symbol colours
    "triangle": (0.25, 0.75, 0.55),
    "circle": (0.90, 0.30, 0.35),
    "cross": (0.35, 0.55, 0.95),
    "square": (0.85, 0.45, 0.75),
}

TOUCHPAD = (205, 92, 190, 100)          # x, y, w, h
DPAD_C = (140, 190)
FACE_C = (460, 190)
LSTICK = (215, 272)
RSTICK = (385, 272)
STICK_R = 34


class PadView(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.state = PadState()
        self.lightbar = (0.19, 0.31, 1.0)
        self.player_mask = 0
        self.show_labels = True
        self.is_dualsense = True
        self.set_content_width(W)
        self.set_content_height(H)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_draw_func(self._draw)

    def update(self, state: PadState) -> None:
        self.state = state
        self.queue_draw()

    # ---------- helpers ----------
    def _fg(self):
        c = self.get_color()          # follows light/dark theme
        return c.red, c.green, c.blue

    def _rrect(self, cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    def _shape(self, cr, pressed: bool, fg, alpha_idle=0.10, color=None):
        if pressed:
            cr.set_source_rgba(*(color or ACCENT), 0.95)
        else:
            cr.set_source_rgba(*fg, alpha_idle)
        cr.fill_preserve()
        cr.set_source_rgba(*fg, 0.55)
        cr.set_line_width(1.5)
        cr.stroke()

    def _label(self, cr, text, x, y, fg, size=11, bold=False, alpha=0.75):
        if not self.show_labels:
            return
        cr.select_font_face("Sans", 0, 1 if bold else 0)
        cr.set_font_size(size)
        ext = cr.text_extents(text)
        cr.move_to(x - ext.width / 2 - ext.x_bearing, y)
        cr.set_source_rgba(*fg, alpha)
        cr.show_text(text)
        cr.new_path()      # show_text leaves a current point; without this the next arc draws a line from it

    # ---------- drawing ----------
    def _draw(self, area, cr, width, height):
        fg = self._fg()
        s = self.state
        p = s.pressed
        cr.new_path()
        scale = min(width / W, height / H)
        cr.translate((width - W * scale) / 2, (height - H * scale) / 2)
        cr.scale(scale, scale)

        # Triggers (L2 / R2) with analogue fill, then L1 / R1
        for name, x, val in (("l2", 105, s.l2), ("r2", 405, s.r2)):
            self._rrect(cr, x, 14, 90, 26, 8)
            self._shape(cr, False, fg)
            if val:
                cr.save()
                self._rrect(cr, x, 14, 90, 26, 8)
                cr.clip()
                cr.rectangle(x, 14, 90 * val / 255, 26)
                cr.set_source_rgba(*ACCENT, 0.9)
                cr.fill()
                cr.restore()
            self._label(cr, f"{name.upper()}  {val}", x + 45, 32, fg, 11, True, 0.9)
        for name, x in (("l1", 110), ("r1", 410)):
            self._rrect(cr, x, 48, 80, 14, 6)
            self._shape(cr, name in p, fg)
            self._label(cr, name.upper(), x + 40, 59, fg, 10, True)

        # Body
        cr.move_to(150, 70)
        cr.line_to(450, 70)
        cr.curve_to(520, 70, 560, 120, 575, 200)
        cr.curve_to(592, 285, 592, 370, 540, 385)
        cr.curve_to(500, 395, 472, 345, 440, 305)
        cr.line_to(160, 305)
        cr.curve_to(128, 345, 100, 395, 60, 385)
        cr.curve_to(8, 370, 8, 285, 25, 200)
        cr.curve_to(40, 120, 80, 70, 150, 70)
        cr.close_path()
        cr.set_source_rgba(*fg, 0.05)
        cr.fill_preserve()
        cr.set_source_rgba(*fg, 0.35)
        cr.set_line_width(2)
        cr.stroke()

        # Light bar: strips along both sides of the touch pad
        tx, ty, tw, th = TOUCHPAD
        for x in (tx - 9, tx + tw + 3):
            self._rrect(cr, x, ty + 6, 6, th - 12, 3)
            cr.set_source_rgba(*self.lightbar, 1.0)
            cr.fill()

        # Touch pad (+ live touch points)
        self._rrect(cr, tx, ty, tw, th, 14)
        self._shape(cr, "touchpad" in p, fg, 0.07)
        for t in s.touches:
            if t.active:
                px = tx + tw * min(t.x, s.touch_w) / s.touch_w
                py = ty + th * min(t.y, s.touch_h) / s.touch_h
                cr.arc(px, py, 7, 0, 2 * math.pi)
                cr.set_source_rgba(*ACCENT, 0.85)
                cr.fill()
        self._label(cr, "Touch pad", tx + tw / 2, ty + th / 2 + 4, fg, 11)

        # Player indicator (5 LEDs under the touch pad)
        for i in range(5):
            x, y = 268 + i * 16, ty + th + 10
            if self.player_mask & (1 << i):        # lit: glowing dot, readable on light and dark themes
                cr.arc(x, y, 6.5, 0, 2 * math.pi)
                cr.set_source_rgba(*ACCENT, 0.35)
                cr.fill()
                cr.arc(x, y, 3.6, 0, 2 * math.pi)
                cr.set_source_rgba(*ACCENT, 1)
                cr.fill()
            else:                                  # off: faint dot
                cr.arc(x, y, 3.0, 0, 2 * math.pi)
                cr.set_source_rgba(*fg, 0.18)
                cr.fill()
        self._label(cr, "Player indicator", 300, ty + th + 26, fg, 9, alpha=0.55)

        # Create / Options
        for name, label, x in (("create", "Create", 172), ("options", "Options", 428)):
            self._rrect(cr, x - 7, 92, 14, 26, 7)
            self._shape(cr, name in p, fg)
            self._label(cr, label, x, 134, fg, 10)

        # Directional buttons
        cx, cy = DPAD_C
        arm, half = 26, 11
        for name, (dx, dy) in (("up", (0, -1)), ("down", (0, 1)), ("left", (-1, 0)), ("right", (1, 0))):
            x = cx + dx * (arm - 4) - half
            y = cy + dy * (arm - 4) - half
            self._rrect(cr, x, y, half * 2, half * 2, 4)
            self._shape(cr, name in p, fg)
        self._label(cr, "Directional buttons", cx, cy + 56, fg, 10)

        # Action buttons
        cx, cy = FACE_C
        for name, (dx, dy) in (("triangle", (0, -1)), ("cross", (0, 1)), ("square", (-1, 0)), ("circle", (1, 0))):
            bx, by = cx + dx * 33, cy + dy * 33
            cr.arc(bx, by, 15, 0, 2 * math.pi)
            self._shape(cr, name in p, fg, color=FACE_COLORS[name])
            self._symbol(cr, name, bx, by, (1, 1, 1) if name in p else FACE_COLORS[name])
        self._label(cr, "Action buttons", cx, cy + 66, fg, 10)

        # Sticks (position + L3/R3)
        for name, (sx, sy), x, y in (("l3", LSTICK, s.lx, s.ly), ("r3", RSTICK, s.rx, s.ry)):
            cr.arc(sx, sy, STICK_R, 0, 2 * math.pi)
            self._shape(cr, False, fg, 0.06)
            kx = sx + (x - 128) / 128 * (STICK_R - 12)
            ky = sy + (y - 128) / 128 * (STICK_R - 12)
            cr.arc(kx, ky, 16, 0, 2 * math.pi)
            self._shape(cr, name in p, fg, 0.18)
            label = "Left stick / L3" if name == "l3" else "Right stick / R3"
            self._label(cr, label, sx, sy + STICK_R + 16, fg, 10)

        # PS button and Mute button
        cr.arc(300, 238, 13, 0, 2 * math.pi)
        self._shape(cr, "ps" in p, fg)
        self._label(cr, "PS", 300, 242, fg, 10, True, 0.9)
        if self.is_dualsense:
            self._rrect(cr, 288, 260, 24, 9, 4.5)
            self._shape(cr, "mute" in p, fg, color=(0.95, 0.55, 0.2))
            self._label(cr, "Mute", 300, 283, fg, 9, alpha=0.6)

        # DualSense Edge extras
        for name, label, x in (("fn_l", "Fn", 195), ("fn_r", "Fn", 405), ("paddle_l", "Back L", 120), ("paddle_r", "Back R", 480)):
            if name in p:
                self._label(cr, label, x, 400, fg, 11, True, 1.0)

    def _symbol(self, cr, name, x, y, color):
        cr.set_source_rgba(*color, 1)
        cr.set_line_width(2)
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
