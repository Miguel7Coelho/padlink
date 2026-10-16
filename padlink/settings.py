"""Global settings: ~/.config/padlink/settings.toml (all optional).

    [battery]
    low_below = 25          # notify and turn the light bar red below this percentage
    critical_below = 10     # second, urgent notification
    low_light = true        # turn the light bar red while the battery is low
    low_color = "#ff0000"
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass

from .profiles import CONFIG_DIR, parse_color

SETTINGS_FILE = CONFIG_DIR / "settings.toml"


@dataclass
class BatterySettings:
    low_below: int = 25
    critical_below: int = 10
    low_light: bool = True
    low_color: tuple[int, int, int] = (255, 0, 0)

    def is_low(self, percent: int | None, charging: bool) -> bool:
        return percent is not None and not charging and percent < self.low_below


def load_battery_settings(path=SETTINGS_FILE) -> BatterySettings:
    s = BatterySettings()
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f).get("battery", {})
    except (OSError, tomllib.TOMLDecodeError):
        return s
    try:
        s.low_below = max(0, min(100, int(data.get("low_below", s.low_below))))
        s.critical_below = max(0, min(s.low_below, int(data.get("critical_below", s.critical_below))))
        s.low_light = bool(data.get("low_light", s.low_light))
        if "low_color" in data:
            s.low_color = parse_color(data["low_color"]) or s.low_color
    except (TypeError, ValueError):
        return BatterySettings()
    return s
