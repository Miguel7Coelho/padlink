"""Apply settings to a controller (works for both DualSense and DS4)."""
from __future__ import annotations

from .devices import Controller, HidrawWriter
from .reports import battery_mask, dualsense_report, ds4_report, player_mask
from .triggers import TriggerSetting

MIC = {"off": 0, "on": 1, "pulse": 2}


def resolve_player_leds(value, battery_pct: int | None) -> int | None:
    if value is None:
        return None
    if value == "battery":
        return battery_mask(battery_pct or 0)
    if value == "off":
        return 0
    n = int(value)
    return player_mask(n) if 0 <= n <= 4 else n & 0x1F


def apply(
    ctrl: Controller,
    *,
    left: TriggerSetting | None = None,
    right: TriggerSetting | None = None,
    rgb: tuple[int, int, int] | None = None,
    player_leds: int | None = None,
    mic_led: str | None = None,
    writer: HidrawWriter | None = None,
) -> None:
    if ctrl.is_dualsense:
        report = dualsense_report(
            ctrl.is_bluetooth,
            left_trigger=left.to_bytes() if left else None,
            right_trigger=right.to_bytes() if right else None,
            rgb=rgb,
            player_leds=player_leds,
            mic_led=MIC.get(mic_led) if mic_led else None,
        )
    else:
        if rgb is None:
            return  # DS4: only the lightbar can be changed here
        report = ds4_report(ctrl.is_bluetooth, rgb=rgb)

    if writer is not None:
        writer.write(report)
    else:
        with HidrawWriter(ctrl.hidraw) as w:
            w.write(report)
