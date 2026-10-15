"""Battery level from the kernel's power_supply node (readable by any user, no root)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class BatteryInfo:
    percent: int | None
    status: str          # "Charging", "Discharging", "Full", "Unknown"

    @property
    def charging(self) -> bool:
        return self.status in ("Charging", "Full")

    def __str__(self) -> str:
        if self.percent is None:
            return "unknown"
        return f"{self.percent}% ({self.status.lower()})"


def read_battery(path: Path | None) -> BatteryInfo:
    if path is None:
        return BatteryInfo(None, "Unknown")
    try:
        pct = int((path / "capacity").read_text().strip())
    except (OSError, ValueError):
        pct = None
    try:
        status = (path / "status").read_text().strip()
    except OSError:
        status = "Unknown"
    return BatteryInfo(pct, status)
