"""DualSense adaptive-trigger presets.

Ported from DS4Windows (DualSenseDevice.TriggerEffectData.ChangeData, GPLv3, (c) Travis Nickles).
Each effect becomes 8 bytes that go into the output report:
  [mode, start_resistance, effect_force, range_force,
   near_release, near_middle, pressed_strength, frequency]
"""
from __future__ import annotations

from dataclasses import dataclass

EFFECTS = (
    "off", "rigid", "pulse", "full_click", "gamecube", "soft", "hard", "very_hard", "hardest",
    "vibrate", "choppy", "medium", "resistance", "bow", "galloping", "semi_auto_gun",
    "auto_gun", "machine",
)

OFF = bytes(8)


@dataclass
class TriggerSetting:
    effect: str = "off"
    start: int = 0      # where the effect starts. 0-255 for some effects, 0-9 zones for others
    force: int = 255    # strength; 0 means "max" like in DS4Windows

    def to_bytes(self) -> bytes:
        return effect_bytes(self.effect, self.start, self.force)


def _clamp(v: int, lo: int = 0, hi: int = 255) -> int:
    return max(lo, min(hi, int(v)))


def _resistance(start: int, force: int) -> bytes:
    start = min(start, 9)
    force = 1 if force <= 0 else min(force, 8)
    b = (force - 1) & 7
    num = 0
    num2 = 0
    for i in range(start, 10):
        num |= b << (3 * i)
        num2 |= 1 << i
    return bytes([
        0x21, num2 & 0xFF, (num2 >> 8) & 0xFF,
        num & 0xFF, (num >> 8) & 0xFF, (num >> 16) & 0xFF, (num >> 24) & 0xFF, 0,
    ])


def _two_pos_mask(start: int, max_end: int) -> int:
    s = min(start, 8)
    e = min(s + 2, max_end)
    return ((1 << s) | (1 << e)) & 0xFF


def _gun_mask(start: int) -> int:
    s = max(2, min(start, 7))
    e = s + 1
    return ((1 << s) | (1 << e)) & 0xFF


def effect_bytes(effect: str, start: int = 0, max_value: int = 255) -> bytes:
    effect = effect.lower().replace("-", "_")
    start = _clamp(start)
    max_value = _clamp(max_value)
    force = 255 if max_value == 0 else max_value
    small = max(1, min(8, force // 32 + 1))
    freq = max(1, min(40, force // 6))

    if effect in ("off", "none"):
        return OFF
    if effect == "rigid":
        return bytes([0x01, 0, 0, 0, 0, 0, 0, 0])
    if effect == "pulse":
        return bytes([0x02, 0, 0, 0, 0, 0, 0, 0])
    if effect == "full_click":
        sr = int(0x94 * (max_value / 255.0))
        ef = int((0xB4 - sr) * (max_value / 255.0) + sr)
        return bytes([0x02, sr & 0xFF, ef & 0xFF, 0xFF, 0, 0, 0, 0])
    if effect == "gamecube":
        return bytes([0x02, 144, 160, 255, 0, 0, 0, 0])
    if effect == "soft":
        return bytes([0x21, 69, 160, 255, 0, 0, 0, 0])
    if effect == "hard":
        return bytes([0x21, 32, 160, 255, 255, 255, 255, 0])
    if effect == "very_hard":
        return bytes([0x21, 16, 160, 255, 255, 255, 255, 0])
    if effect == "hardest":
        return bytes([0x02, start, 255, 255, 255, 255, 255, 0])
    if effect == "vibrate":
        return bytes([0x26, start, force, freq, 0, 0, 0, 0])
    if effect == "choppy":
        return bytes([0x21, 2, 39, 33, 39, 38, 2, 0])
    if effect == "medium":
        return bytes([0x22, 2, 35, 1, 6, 6, 1, 33])
    if effect == "resistance":
        return _resistance(start, small)
    if effect == "bow":
        return bytes([0x22, _two_pos_mask(start, 8), 0, small, 0, 0, 0, 0])
    if effect == "galloping":
        return bytes([35, _two_pos_mask(start, 9), 0, 0x08, freq, 0, 0, 0])
    if effect == "semi_auto_gun":
        return bytes([0x25, _gun_mask(start), 0, small, 0, 0, 0, 0])
    if effect == "auto_gun":
        b = bytearray(_resistance(start, small))
        b[0] = 38
        b[7] = freq
        return bytes(b)
    if effect == "machine":
        return bytes([39, _two_pos_mask(start, 9), 0, small, freq, 0, 0, 0])
    raise ValueError(f"unknown trigger effect {effect!r}; choose from: {', '.join(EFFECTS)}")
