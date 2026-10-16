"""Live controller drawing (cairo). An original schematic with Sony's control names as labels.

Every control sits where it is on the real pad, so what lights up here matches what you press.
The style adapts to the light/dark theme: a single-tone shell with depth, recessed stick wells,
and a glow around the touch pad in the light bar's current colour.
"""
from __future__ import annotations

import math

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

import cairo  # noqa: E402

from ..inputs import PadState  # noqa: E402

W, H = 600, 430          # logical canvas; scaled to the widget
ACCENT = (0.21, 0.52, 0.89)
FACE_COLORS = {          # symbol colours for the action buttons
    "triangle": (0.27, 0.78, 0.58),
    "circle": (0.94, 0.33, 0.38),
    "cross": (0.40, 0.60, 0.98),
    "square": (0.88, 0.48, 0.80),
}

TOUCHPAD = (210, 96, 180, 96)          # x, y, w, h
DPAD_C = (145, 196)
FACE_C = (455, 196)
LSTICK = (220, 278)
RSTICK = (380, 278)
STICK_R = 36


def _mix(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


class PadView(Gtk.DrawingArea):
    def __init__(self):
        super().__init__()
        self.state = PadState()
        self.lightbar = (0.19, 0.31, 1.0)
        self.player_mask = 0
        self.show_labels = True
        self.is_dualsense = True
        self.style = "outline"            # "outline" (flat, bold lines) or "shaded"
        self.set_content_width(W)
        self.set_content_height(H)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_draw_func(self._draw)

    def update(self, state: PadState) -> None:
        self.state = state
        self.queue_draw()

    # ------------------------------------------------------------------ palette
    def _palette(self):
        c = self.get_color()                     # theme foreground
        fg = (c.red, c.green, c.blue)
        dark = (c.red + c.green + c.blue) / 3 > 0.5    # light text => dark theme
        if dark:
            return dict(dark=True, fg=fg,
                        body_top=(0.26, 0.27, 0.32), body_bot=(0.16, 0.17, 0.20),
                        edge=(1, 1, 1, 0.10), highlight=(1, 1, 1, 0.07), shadow=(0, 0, 0, 0.45),
                        part=(0.30, 0.31, 0.36), part_hi=(0.38, 0.39, 0.45), well=(0.10, 0.10, 0.12),
                        cap_top=(0.36, 0.37, 0.42), cap_bot=(0.22, 0.23, 0.27),
                        pad_top=(0.21, 0.22, 0.26), pad_bot=(0.17, 0.18, 0.21))
        return dict(dark=False, fg=fg,
                    body_top=(0.98, 0.98, 0.99), body_bot=(0.86, 0.87, 0.90),
                    edge=(0, 0, 0, 0.16), highlight=(1, 1, 1, 0.9), shadow=(0, 0, 0, 0.16),
                    part=(0.93, 0.93, 0.95), part_hi=(1, 1, 1), well=(0.62, 0.63, 0.67),
                    cap_top=(0.97, 0.97, 0.98), cap_bot=(0.80, 0.81, 0.84),
                    pad_top=(0.95, 0.95, 0.97), pad_bot=(0.88, 0.89, 0.92))

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
    def _vgrad(y0, y1, top, bot, alpha=1.0):
        g = cairo.LinearGradient(0, y0, 0, y1)
        g.add_color_stop_rgba(0, *top, alpha)
        g.add_color_stop_rgba(1, *bot, alpha)
        return g

    def _part(self, cr, P, y0, y1, pressed=False, color=None):
        """Fill the current path as a raised part (or accent when pressed), keep path for stroke."""
        if pressed:
            c = color or ACCENT
            cr.set_source(self._vgrad(y0, y1, _mix(c, (1, 1, 1), 0.18), _mix(c, (0, 0, 0), 0.15)))
        else:
            cr.set_source(self._vgrad(y0, y1, P["part_hi"], P["part"]))
        cr.fill_preserve()
        cr.set_source_rgba(*P["edge"])
        cr.set_line_width(1.2)
        cr.stroke()

    def _label(self, cr, P, text, x, y, size=10.5, bold=False, alpha=0.70):
        if not self.show_labels:
            return
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL,
                            cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(size)
        ext = cr.text_extents(text)
        cr.move_to(x - ext.width / 2 - ext.x_bearing, y)
        cr.set_source_rgba(*P["fg"], alpha)
        cr.show_text(text)
        cr.new_path()

    def _text(self, cr, text, x, y, size, color, bold=True):
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL,
                            cairo.FONT_WEIGHT_BOLD if bold else cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(size)
        ext = cr.text_extents(text)
        cr.move_to(x - ext.width / 2 - ext.x_bearing, y - ext.height / 2 - ext.y_bearing)
        cr.set_source_rgba(*color)
        cr.show_text(text)
        cr.new_path()

    # ------------------------------------------------------------------ body
    @staticmethod
    def _body_path(cr, dy=0.0, grow=0.0):
        g = grow
        cr.new_path()
        cr.move_to(170, 72 - g + dy)
        cr.curve_to(240, 62 - g + dy, 360, 62 - g + dy, 430, 72 - g + dy)
        cr.curve_to(500, 76 - g + dy, 548, 104 + dy, 566 + g, 168 + dy)
        cr.curve_to(586 + g, 240 + dy, 590 + g, 330 + dy, 566 + g, 388 + g + dy)
        cr.curve_to(548, 422 + g + dy, 506, 418 + g + dy, 486, 392 + dy)
        cr.curve_to(468, 368 + dy, 452, 348 + dy, 424, 342 + dy)
        cr.curve_to(384, 333 + dy, 216, 333 + dy, 176, 342 + dy)
        cr.curve_to(148, 348 + dy, 132, 368 + dy, 114, 392 + dy)
        cr.curve_to(94, 418 + g + dy, 52, 422 + g + dy, 34 - g, 388 + g + dy)
        cr.curve_to(10 - g, 330 + dy, 14 - g, 240 + dy, 34 - g, 168 + dy)
        cr.curve_to(52, 104 + dy, 100, 76 - g + dy, 170, 72 - g + dy)
        cr.close_path()

    def _draw_body(self, cr, P):
        # soft shadow: a few widening, fading copies below the shell
        for i in range(6, 0, -1):
            self._body_path(cr, dy=7 + i * 1.5, grow=i * 1.6)
            cr.set_source_rgba(*P["shadow"][:3], P["shadow"][3] / 7)
            cr.fill()
        self._body_path(cr)
        cr.set_source(self._vgrad(62, 420, P["body_top"], P["body_bot"]))
        cr.fill_preserve()
        cr.set_source_rgba(*P["edge"])
        cr.set_line_width(1.6)
        cr.stroke()
        # top highlight along the upper edge
        cr.save()
        self._body_path(cr)
        cr.clip()
        self._body_path(cr, dy=3)
        cr.set_source_rgba(*P["highlight"])
        cr.set_line_width(2)
        cr.stroke()
        cr.restore()

    # ------------------------------------------------------------------ draw
    def _draw(self, area, cr, width, height):
        P = self._palette()
        s = self.state
        p = s.pressed
        cr.new_path()
        scale = min(width / W, height / H)
        cr.translate((width - W * scale) / 2, (height - H * scale) / 2)
        cr.scale(scale, scale)

        if self.style == "outline":
            self._draw_outline(cr, P, s, p)
            return
        self._draw_triggers(cr, P, s, p)
        self._draw_body(cr, P)
        self._draw_touchpad(cr, P, s, p)
        self._draw_small_buttons(cr, P, p)
        self._draw_dpad(cr, P, p)
        self._draw_face(cr, P, p)
        self._draw_sticks(cr, P, s, p)
        self._draw_center(cr, P, p)
        for name, label, x in (("fn_l", "Fn", 205), ("fn_r", "Fn", 395),
                               ("paddle_l", "Back L", 120), ("paddle_r", "Back R", 480)):
            if name in p:
                self._label(cr, P, label, x, 424, 11, True, 1.0)

    def _draw_triggers(self, cr, P, s, p):
        for name, x, val in (("l2", 98, s.l2), ("r2", 412, s.r2)):
            w, h, y = 90, 30, 12
            self._rrect(cr, x, y, w, h, 11)
            self._part(cr, P, y, y + h)
            if val:
                cr.save()
                self._rrect(cr, x + 2, y + 2, w - 4, h - 4, 9)
                cr.clip()
                fill = (w - 4) * val / 255
                fx = x + 2 if name == "l2" else x + w - 2 - fill
                cr.rectangle(fx, y, fill, h)
                cr.set_source(self._vgrad(y, y + h, _mix(ACCENT, (1, 1, 1), 0.25), ACCENT))
                cr.fill()
                cr.restore()
            self._text(cr, f"{name.upper()}  {val}", x + w / 2, y + h / 2, 12,
                       (*P["fg"], 0.92))
        for name, x in (("l1", 104), ("r1", 418)):
            self._rrect(cr, x, 50, 78, 14, 7)
            self._part(cr, P, 50, 64, name in p)
            self._text(cr, name.upper(), x + 39, 57, 9.5,
                       (1, 1, 1, 0.95) if name in p else (*P["fg"], 0.8))

    def _draw_touchpad(self, cr, P, s, p):
        tx, ty, tw, th = TOUCHPAD
        # light bar glow around the touch pad, in the current light bar colour
        for i in range(7, 0, -1):
            self._rrect(cr, tx - i * 2.2, ty - i * 2.2, tw + i * 4.4, th + i * 4.4, 16 + i * 2)
            cr.set_source_rgba(*self.lightbar, 0.075)
            cr.fill()
        self._rrect(cr, tx - 3, ty - 3, tw + 6, th + 6, 17)
        cr.set_source_rgba(*self.lightbar, 0.95)
        cr.set_line_width(2.2)
        cr.stroke()
        # pad surface
        self._rrect(cr, tx, ty, tw, th, 14)
        if "touchpad" in p:
            cr.set_source(self._vgrad(ty, ty + th, _mix(ACCENT, (1, 1, 1), 0.3), ACCENT))
        else:
            cr.set_source(self._vgrad(ty, ty + th, P["pad_top"], P["pad_bot"]))
        cr.fill_preserve()
        cr.set_source_rgba(*P["edge"])
        cr.set_line_width(1.2)
        cr.stroke()
        for t in s.touches:
            if t.active:
                px = tx + tw * min(t.x, s.touch_w) / s.touch_w
                py = ty + th * min(t.y, s.touch_h) / s.touch_h
                g = cairo.RadialGradient(px, py, 0, px, py, 13)
                g.add_color_stop_rgba(0, *ACCENT, 0.95)
                g.add_color_stop_rgba(0.55, *ACCENT, 0.55)
                g.add_color_stop_rgba(1, *ACCENT, 0)
                cr.arc(px, py, 13, 0, 2 * math.pi)
                cr.set_source(g)
                cr.fill()
        self._label(cr, P, "Touch pad", tx + tw / 2, ty + th / 2 + 4, 11, alpha=0.55)
        self._label(cr, P, "Light bar", tx + tw / 2, ty - 14, 9, alpha=0.5)

        # player indicator
        for i in range(5):
            x, y = 268 + i * 16, ty + th + 13
            if self.player_mask & (1 << i):
                cr.arc(x, y, 6.5, 0, 2 * math.pi)
                cr.set_source_rgba(*ACCENT, 0.32)
                cr.fill()
                cr.arc(x, y, 3.4, 0, 2 * math.pi)
                cr.set_source_rgba(*ACCENT, 1)
                cr.fill()
            else:
                cr.arc(x, y, 2.8, 0, 2 * math.pi)
                cr.set_source_rgba(*P["fg"], 0.18)
                cr.fill()
        self._label(cr, P, "Player indicator", 300, ty + th + 30, 9, alpha=0.5)

    def _draw_small_buttons(self, cr, P, p):
        for name, label, x in (("create", "Create", 178), ("options", "Options", 422)):
            self._rrect(cr, x - 6, 98, 12, 26, 6)
            self._part(cr, P, 98, 124, name in p)
            self._label(cr, P, label, x, 140, 10)

    def _draw_dpad(self, cr, P, p):
        cx, cy = DPAD_C
        a, r = 30, 12            # arm length from centre, half width
        # one cross-shaped piece
        cr.new_path()
        cr.move_to(cx - r, cy - a)
        for dx, dy in ((r, -a), (r, -r), (a, -r), (a, r), (r, r), (r, a), (-r, a), (-r, r),
                       (-a, r), (-a, -r), (-r, -r), (-r, -a)):
            cr.line_to(cx + dx, cy + dy)
        cr.close_path()
        self._part(cr, P, cy - a, cy + a)
        # pressed arms
        arms = {"up": (cx - r, cy - a, 2 * r, a - 2), "down": (cx - r, cy + 2, 2 * r, a - 2),
                "left": (cx - a, cy - r, a - 2, 2 * r), "right": (cx + 2, cy - r, a - 2, 2 * r)}
        for name, (x, y, w, h) in arms.items():
            if name in p:
                self._rrect(cr, x + 1.5, y + 1.5, w - 3, h - 3, 4)
                cr.set_source(self._vgrad(y, y + h, _mix(ACCENT, (1, 1, 1), 0.2), ACCENT))
                cr.fill()
        # arrows
        for ang, (dx, dy) in ((0, (0, -1)), (math.pi, (0, 1)), (-math.pi / 2, (-1, 0)), (math.pi / 2, (1, 0))):
            x, y = cx + dx * (a - 9), cy + dy * (a - 9)
            cr.save()
            cr.translate(x, y)
            cr.rotate(ang)
            cr.move_to(0, -3.5)
            cr.line_to(3.5, 2.5)
            cr.line_to(-3.5, 2.5)
            cr.close_path()
            cr.restore()
            cr.set_source_rgba(*P["fg"], 0.35)
            cr.fill()
        cr.arc(cx, cy, 5, 0, 2 * math.pi)       # centre dimple
        cr.set_source_rgba(*P["edge"])
        cr.set_line_width(1)
        cr.stroke()
        self._label(cr, P, "Directional buttons", cx, cy + a + 22, 10)

    def _draw_face(self, cr, P, p):
        cx, cy = FACE_C
        for name, (dx, dy) in (("triangle", (0, -1)), ("cross", (0, 1)), ("square", (-1, 0)), ("circle", (1, 0))):
            bx, by = cx + dx * 34, cy + dy * 34
            col = FACE_COLORS[name]
            pressed = name in p
            # drop shadow
            cr.arc(bx, by + 2.5, 16, 0, 2 * math.pi)
            cr.set_source_rgba(*P["shadow"][:3], P["shadow"][3] * 0.8)
            cr.fill()
            cr.arc(bx, by, 16, 0, 2 * math.pi)
            if pressed:
                g = cairo.RadialGradient(bx - 4, by - 5, 2, bx, by, 18)
                g.add_color_stop_rgb(0, *_mix(col, (1, 1, 1), 0.35))
                g.add_color_stop_rgb(1, *col)
            else:
                g = cairo.RadialGradient(bx - 5, by - 6, 2, bx, by, 18)
                g.add_color_stop_rgb(0, *P["part_hi"])
                g.add_color_stop_rgb(1, *P["part"])
            cr.set_source(g)
            cr.fill_preserve()
            cr.set_source_rgba(*P["edge"])
            cr.set_line_width(1.2)
            cr.stroke()
            self._symbol(cr, name, bx, by, (1, 1, 1) if pressed else col)
        self._label(cr, P, "Action buttons", cx, cy + 34 + 36, 10)

    def _draw_sticks(self, cr, P, s, p):
        for name, (sx, sy), x, y in (("l3", LSTICK, s.lx, s.ly), ("r3", RSTICK, s.rx, s.ry)):
            # recessed well
            g = cairo.RadialGradient(sx, sy - 6, STICK_R * 0.3, sx, sy, STICK_R + 2)
            g.add_color_stop_rgba(0, *P["well"], 0.55)
            g.add_color_stop_rgba(1, *P["well"], 0.95)
            cr.arc(sx, sy, STICK_R, 0, 2 * math.pi)
            cr.set_source(g)
            cr.fill_preserve()
            cr.set_source_rgba(*P["edge"])
            cr.set_line_width(1.2)
            cr.stroke()
            # cap follows the stick
            kx = sx + (x - 128) / 128 * (STICK_R - 16)
            ky = sy + (y - 128) / 128 * (STICK_R - 16)
            cr.arc(kx, ky + 3, 20, 0, 2 * math.pi)
            cr.set_source_rgba(*P["shadow"][:3], P["shadow"][3])
            cr.fill()
            cap = cairo.RadialGradient(kx - 6, ky - 8, 3, kx, ky, 22)
            cap.add_color_stop_rgb(0, *P["cap_top"])
            cap.add_color_stop_rgb(1, *P["cap_bot"])
            cr.arc(kx, ky, 20, 0, 2 * math.pi)
            cr.set_source(cap)
            cr.fill_preserve()
            cr.set_source_rgba(*(ACCENT if name in p else P["edge"][:3]), 1 if name in p else P["edge"][3])
            cr.set_line_width(2.5 if name in p else 1.2)
            cr.stroke()
            # concave top
            cr.arc(kx, ky, 13, 0, 2 * math.pi)
            dish = cairo.RadialGradient(kx + 3, ky + 4, 1, kx, ky, 14)
            dish.add_color_stop_rgba(0, *P["cap_top"], 0.0)
            dish.add_color_stop_rgba(1, *P["cap_bot"], 0.9)
            cr.set_source(dish)
            cr.fill()
            label = "Left stick / L3" if name == "l3" else "Right stick / R3"
            self._label(cr, P, label, sx, sy + STICK_R + 16, 10)

    def _draw_center(self, cr, P, p):
        cr.arc(300, 246, 13, 0, 2 * math.pi)
        self._part(cr, P, 233, 259, "ps" in p)
        self._text(cr, "PS", 300, 246, 10, (1, 1, 1, 0.95) if "ps" in p else (*P["fg"], 0.8))
        if self.is_dualsense:
            self._rrect(cr, 287, 268, 26, 9, 4.5)
            self._part(cr, P, 268, 277, "mute" in p, color=(0.95, 0.55, 0.2))
            self._label(cr, P, "Mute", 300, 291, 9, alpha=0.55)

    # ================================================================== outline style
    # Flat drawing with bold outlines: round pads around the D-pad and action buttons,
    # ringed sticks, and the touch pad outlined in the light bar colour.
    O_DPAD = (138, 196)
    O_FACE = (462, 196)
    O_LSTICK = (228, 292)
    O_RSTICK = (372, 292)

    def _o_colors(self, P):
        line = (*P["fg"], 0.88)
        fill = (0.17, 0.18, 0.21) if P["dark"] else (1.0, 1.0, 1.0)
        soft = (0.22, 0.23, 0.27) if P["dark"] else (0.95, 0.95, 0.97)
        return line, fill, soft

    @staticmethod
    def _o_body_shapes(cr):
        """Calls back with each shape path; the union of these is the controller outline."""
        def shoulders():
            cr.new_path()
            cr.arc(146, 196, 116, 0, 2 * math.pi)
        def shoulders_r():
            cr.new_path()
            cr.arc(454, 196, 116, 0, 2 * math.pi)
        def middle():
            # top edge at y=80 = the shoulders' highest point, so the top reads as one straight line
            cr.new_path()
            cr.rectangle(146, 80, 308, 210)
        def grip_l():
            cr.new_path()
            cr.move_to(30, 196)
            cr.curve_to(28, 300, 34, 404, 92, 414)
            cr.curve_to(146, 422, 172, 380, 214, 300)
            cr.line_to(150, 190)
            cr.close_path()
        def grip_r():
            cr.new_path()
            cr.move_to(570, 196)
            cr.curve_to(572, 300, 566, 404, 508, 414)
            cr.curve_to(454, 422, 428, 380, 386, 300)
            cr.line_to(450, 190)
            cr.close_path()
        return (shoulders, shoulders_r, middle, grip_l, grip_r)

    def _o_line(self, cr, color, width):
        cr.set_source_rgba(*color)
        cr.set_line_width(width)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)
        cr.stroke()

    def _o_fill_line(self, cr, fill, line, width=2.6):
        cr.set_source_rgba(*fill, 1) if len(fill) == 3 else cr.set_source_rgba(*fill)
        cr.fill_preserve()
        self._o_line(cr, line, width)

    def _draw_outline(self, cr, P, s, p):
        line, fill, soft = self._o_colors(P)
        LW = 3.2

        # ---- triggers and bumpers (behind the body)
        for name, x, val in (("l2", 92, s.l2), ("r2", 418, s.r2)):
            self._rrect(cr, x, 10, 90, 30, 12)
            self._o_fill_line(cr, fill, line, 2.6)
            if val:
                cr.save()
                self._rrect(cr, x + 3, 13, 84, 24, 9)
                cr.clip()
                w = 84 * val / 255
                cr.rectangle(x + 3 if name == "l2" else x + 87 - w, 10, w, 30)
                cr.set_source_rgba(*ACCENT, 0.95)
                cr.fill()
                cr.restore()
            self._text(cr, f"{name.upper()}  {val}", x + 45, 25, 12, line)
        for name, x0, x1 in (("l1", 70, 200), ("r1", 400, 530)):
            cr.new_path()
            cr.move_to(x0, 74)
            cr.curve_to(x0 + 18, 48, x1 - 18, 46, x1, 68)
            cr.line_to(x1, 80)
            cr.line_to(x0, 86)
            cr.close_path()
            self._o_fill_line(cr, ACCENT if name in p else fill, line, 2.6)
            self._text(cr, name.upper(), (x0 + x1) / 2, 66, 10,
                       (1, 1, 1, 0.95) if name in p else line)

        # ---- body: thick stroke of every shape, then fill them all -> only the union's edge remains
        shapes = self._o_body_shapes(cr)
        for f in shapes:
            f()
            self._o_line(cr, line, LW * 2)
        for f in shapes:
            f()
            cr.set_source_rgb(*fill)
            cr.fill()

        # ---- round pads around the D-pad and the action buttons
        for cx, cy in (self.O_DPAD, self.O_FACE):
            cr.new_path()
            cr.arc(cx, cy, 64, 0, 2 * math.pi)
            self._o_fill_line(cr, soft, line, 2.2)

        # ---- touch pad, outlined in the light bar colour
        tx, ty, tw, th = 214, 100, 172, 84
        for i in range(5, 0, -1):
            self._rrect(cr, tx - i * 2, ty - i * 2, tw + i * 4, th + i * 4, 14 + i * 2)
            cr.set_source_rgba(*self.lightbar, 0.07)
            cr.fill()
        self._rrect(cr, tx, ty, tw, th, 12)
        cr.set_source_rgba(*(ACCENT if "touchpad" in p else soft), 0.9 if "touchpad" in p else 1)
        cr.fill_preserve()
        self._o_line(cr, (*self.lightbar, 1), 3.2)
        for t in s.touches:
            if t.active:
                px = tx + tw * min(t.x, s.touch_w) / s.touch_w
                py = ty + th * min(t.y, s.touch_h) / s.touch_h
                cr.new_path()
                cr.arc(px, py, 8, 0, 2 * math.pi)
                cr.set_source_rgba(*ACCENT, 0.9)
                cr.fill()
        self._label(cr, P, "Touch pad", 300, ty + th / 2 + 4, 11, alpha=0.6)
        self._label(cr, P, "outline = light bar", 300, ty + th - 10, 8.5, alpha=0.45)

        # ---- Create / Options
        for name, label, x in (("create", "Create", 184), ("options", "Options", 416)):
            self._rrect(cr, x - 6, 104, 12, 24, 6)
            self._o_fill_line(cr, ACCENT if name in p else fill, line, 2.2)
            self._label(cr, P, label, x - 32 if name == "create" else x + 34, 120, 10)

        # ---- player indicator
        for i in range(5):
            x, y = 268 + i * 16, 198
            cr.new_path()
            if self.player_mask & (1 << i):
                cr.arc(x, y, 6, 0, 2 * math.pi)
                cr.set_source_rgba(*ACCENT, 0.3)
                cr.fill()
                cr.arc(x, y, 3.4, 0, 2 * math.pi)
                cr.set_source_rgba(*ACCENT, 1)
                cr.fill()
            else:
                cr.arc(x, y, 2.8, 0, 2 * math.pi)
                cr.set_source_rgba(*P["fg"], 0.25)
                cr.fill()
        self._label(cr, P, "Player indicator", 300, 214, 9, alpha=0.5)

        # ---- D-pad: one rounded cross
        cx, cy = self.O_DPAD
        a, r = 34, 12
        cr.new_path()
        pts = ((r, -a), (r, -r), (a, -r), (a, r), (r, r), (r, a), (-r, a), (-r, r),
               (-a, r), (-a, -r), (-r, -r), (-r, -a))
        cr.move_to(cx - r, cy - a)
        for dx, dy in pts:
            cr.line_to(cx + dx, cy + dy)
        cr.close_path()
        self._o_fill_line(cr, fill, line, 2.6)
        arms = {"up": (cx - r, cy - a, 2 * r, a - 3), "down": (cx - r, cy + 3, 2 * r, a - 3),
                "left": (cx - a, cy - r, a - 3, 2 * r), "right": (cx + 3, cy - r, a - 3, 2 * r)}
        for name, (x, y, w, h) in arms.items():
            if name in p:
                self._rrect(cr, x + 2.5, y + 2.5, w - 5, h - 5, 4)
                cr.set_source_rgba(*ACCENT, 1)
                cr.fill()
        self._label(cr, P, "Directional buttons", cx, cy + 82, 10)

        # ---- action buttons
        fx, fy = self.O_FACE
        for name, (dx, dy) in (("triangle", (0, -1)), ("cross", (0, 1)), ("square", (-1, 0)), ("circle", (1, 0))):
            bx, by = fx + dx * 36, fy + dy * 36
            col = FACE_COLORS[name]
            cr.new_path()
            cr.arc(bx, by, 17, 0, 2 * math.pi)
            self._o_fill_line(cr, col if name in p else fill, line, 2.6)
            self._symbol(cr, name, bx, by, (1, 1, 1) if name in p else col)
        self._label(cr, P, "Action buttons", fx, fy + 82, 10)

        # ---- sticks: outer ring + moving cap with an inner ring
        for name, (sx, sy), x, y in (("l3", self.O_LSTICK, s.lx, s.ly), ("r3", self.O_RSTICK, s.rx, s.ry)):
            cr.new_path()
            cr.arc(sx, sy, 42, 0, 2 * math.pi)
            self._o_fill_line(cr, soft, line, 2.6)
            kx = sx + (x - 128) / 128 * 14
            ky = sy + (y - 128) / 128 * 14
            cr.new_path()
            cr.arc(kx, ky, 27, 0, 2 * math.pi)
            self._o_fill_line(cr, ACCENT if name in p else fill, line, 2.6)
            cr.new_path()
            cr.arc(kx, ky, 17, 0, 2 * math.pi)
            self._o_line(cr, (1, 1, 1, 0.9) if name in p else line, 2.0)
            label = "Left stick / L3" if name == "l3" else "Right stick / R3"
            self._label(cr, P, label, sx, sy + 60, 10)

        # ---- centre: Mute pill above the PS button
        if self.is_dualsense:
            self._rrect(cr, 284, 232, 32, 11, 5.5)
            self._o_fill_line(cr, (0.95, 0.55, 0.2) if "mute" in p else fill, line, 2.2)
        cr.new_path()
        cr.arc(300, 268, 15, 0, 2 * math.pi)
        self._o_fill_line(cr, ACCENT if "ps" in p else fill, line, 2.6)
        self._text(cr, "PS", 300, 268, 10, (1, 1, 1, 0.95) if "ps" in p else line)
        if self.is_dualsense:
            self._label(cr, P, "Mute", 338, 241, 9, alpha=0.55)

        for name, label, x in (("fn_l", "Fn", 205), ("fn_r", "Fn", 395),
                               ("paddle_l", "Back L", 120), ("paddle_r", "Back R", 480)):
            if name in p:
                self._label(cr, P, label, x, 428, 11, True, 1.0)

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
