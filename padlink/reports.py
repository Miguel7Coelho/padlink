"""Build output reports for DualSense and DualShock 4.

The design rule is to set only the "valid flag" bits for the fields we want to change.
The controller ignores unflagged fields, so our reports don't fight the kernel driver
(which uses rumble/LEDs) and only touch what the profile asks for.

DualSense layout (common block, offsets relative to its start):
  0  valid_flag0   0x01|0x02 motors, 0x04 right trigger, 0x08 left trigger
  1  valid_flag1   0x01 mic LED, 0x04 lightbar, 0x10 player LEDs
  2  motor right (light/fast)   3 motor left (heavy/slow)
  8  mute LED
  10..16  right trigger (mode + 6 params), 19 right trigger frequency
  21..27  left trigger,                    30 left trigger frequency
  38 valid_flag2   41 lightbar_setup   42 LED brightness   43 player LEDs   44..46 RGB
USB: report 0x02, common block at byte 1, 48 bytes long.
BT:  report 0x31, byte 1 = 0x02, common block at byte 2, 78 bytes long, CRC32 in the last 4.
     (same as DS4Windows and SDL; CRC = crc32(0xA2 + report[0:74]), little-endian)
"""
from __future__ import annotations

import zlib

from .triggers import OFF

DS_USB_LEN = 48
DS_BT_LEN = 78
DS4_USB_LEN = 32
DS4_BT_LEN = 78
BT_OUTPUT_SEED = b"\xA2"


def _bt_crc(report: bytearray) -> None:
    crc = zlib.crc32(BT_OUTPUT_SEED + bytes(report[:-4])) & 0xFFFFFFFF
    report[-4:] = crc.to_bytes(4, "little")


def dualsense_report(
    bluetooth: bool,
    *,
    right_trigger: bytes | None = None,
    left_trigger: bytes | None = None,
    rgb: tuple[int, int, int] | None = None,
    player_leds: int | None = None,
    mic_led: int | None = None,
    rumble: tuple[int, int] | None = None,
) -> bytes:
    """Any argument left as None isn't flagged, so the controller keeps its current value."""
    if bluetooth:
        r = bytearray(DS_BT_LEN)
        r[0], r[1] = 0x31, 0x02
        o = 2
    else:
        r = bytearray(DS_USB_LEN)
        r[0] = 0x02
        o = 1

    f0 = f1 = 0
    if rumble is not None:
        f0 |= 0x01 | 0x02
        r[o + 2], r[o + 3] = rumble[0] & 0xFF, rumble[1] & 0xFF
    if right_trigger is not None:
        f0 |= 0x04
        t = right_trigger or OFF
        r[o + 10:o + 17] = t[:7]
        r[o + 19] = t[7]
    if left_trigger is not None:
        f0 |= 0x08
        t = left_trigger or OFF
        r[o + 21:o + 28] = t[:7]
        r[o + 30] = t[7]
    if mic_led is not None:
        f1 |= 0x01
        r[o + 8] = mic_led & 0xFF          # 0 off, 1 on, 2 pulse
    if rgb is not None:
        f1 |= 0x04
        r[o + 38] |= 0x02                  # valid_flag2: allow lightbar_setup
        r[o + 41] = 0x02                   # lightbar_setup: end the startup blue fade so our colour shows
        r[o + 44:o + 47] = bytes(c & 0xFF for c in rgb)
    if player_leds is not None:
        f1 |= 0x10
        r[o + 43] = (player_leds & 0x1F) | 0x20   # 0x20 = no fade-in
    r[o + 0], r[o + 1] = f0, f1

    if bluetooth:
        _bt_crc(r)
    return bytes(r)


def ds4_report(
    bluetooth: bool,
    *,
    rgb: tuple[int, int, int] | None = None,
    flash: tuple[int, int] | None = None,
    rumble: tuple[int, int] | None = None,
) -> bytes:
    """DualShock 4: report 0x05 (USB) / 0x11 (BT). No adaptive triggers on this pad."""
    if bluetooth:
        r = bytearray(DS4_BT_LEN)
        r[0] = 0x11
        r[1] = 0xC0 | 0x04                 # HID + CRC flags, poll interval
        o = 3
    else:
        r = bytearray(DS4_USB_LEN)
        r[0] = 0x05
        o = 1
    flags = 0
    if rumble is not None:
        flags |= 0x01
        r[o + 3], r[o + 4] = rumble[0] & 0xFF, rumble[1] & 0xFF
    if rgb is not None:
        flags |= 0x02
        r[o + 5:o + 8] = bytes(c & 0xFF for c in rgb)
    if flash is not None:
        flags |= 0x04
        r[o + 8], r[o + 9] = flash[0] & 0xFF, flash[1] & 0xFF
    r[o] = flags
    if bluetooth:
        _bt_crc(r)
    return bytes(r)


def player_mask(n: int) -> int:
    """Player number (1-4) -> the PS5-style pattern under the touchpad."""
    return {0: 0x00, 1: 0x04, 2: 0x0A, 3: 0x15, 4: 0x1B}.get(n, 0x1F)


def battery_mask(percent: int) -> int:
    """Battery percentage -> 0-5 player LEDs lit, left to right."""
    lit = max(1, min(5, (percent + 19) // 20)) if percent > 0 else 0
    return (1 << lit) - 1
