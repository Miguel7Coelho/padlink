"""Profiles: one TOML file per profile in ~/.config/padlink/profiles/.

Example (see data/profiles/):

    name = "Cyberpunk 2077"
    [match]                       # any one of these selects the profile
    steam_appid = [1091500]
    lutris = ["Cyberpunk 2077"]
    exe = ["cyberpunk2077.exe"]

    [xbox]
    enabled = true                # games see an Xbox 360 pad; the real controller is hidden meanwhile

    [triggers]
    mode = "override"             # "override" = padlink owns the triggers, "game" = let the game drive them
    left  = { effect = "resistance", start = 2, force = 180 }
    right = { effect = "semi_auto_gun", start = 4, force = 220 }

    [leds]
    color = "#ff2a6d"
    player_leds = "battery"       # "battery" | "off" | 1-4 (player number) | 0-31 (raw mask)
    mic_led = "off"               # "off" | "on" | "pulse"
"""
from __future__ import annotations

import fnmatch
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .games import RunningGame
from .triggers import TriggerSetting

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))) / "padlink"
PROFILE_DIR = CONFIG_DIR / "profiles"


def parse_color(v) -> tuple[int, int, int] | None:
    if v is None:
        return None
    if isinstance(v, (list, tuple)) and len(v) == 3:
        return tuple(max(0, min(255, int(c))) for c in v)  # type: ignore[return-value]
    s = str(v).lstrip("#")
    if len(s) == 6:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    raise ValueError(f"bad colour {v!r}; use '#rrggbb' or [r, g, b]")


@dataclass
class Profile:
    name: str
    path: Path | None = None
    steam_appids: set[int] = field(default_factory=set)
    lutris_titles: set[str] = field(default_factory=set)
    exe_globs: list[str] = field(default_factory=list)
    trigger_mode: str = "override"
    left: TriggerSetting | None = None
    right: TriggerSetting | None = None
    color: tuple[int, int, int] | None = None
    player_leds: str | int | None = None
    mic_led: str | None = None
    is_default: bool = False
    xbox_mode: bool = False     # present the pad as an Xbox 360 controller and hide the real one

    def matches(self, game: RunningGame) -> int:
        """Return a match score (0 = no match). Steam AppID beats Lutris title beats exe name."""
        if game.steam_appid and game.steam_appid in self.steam_appids:
            return 3
        if game.source == "lutris" and game.name.lower() in {t.lower() for t in self.lutris_titles}:
            return 2
        for pat in self.exe_globs:
            if any(fnmatch.fnmatch(e, pat) for e in game.exe_names):
                return 1
        return 0


def _trigger(d) -> TriggerSetting | None:
    if d is None:
        return None
    if isinstance(d, str):
        return TriggerSetting(d)
    return TriggerSetting(d.get("effect", "off"), int(d.get("start", 0)), int(d.get("force", 255)))


def load_profile(path: Path) -> Profile:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    m = data.get("match", {})
    t = data.get("triggers", {})
    leds = data.get("leds", {})
    return Profile(
        name=data.get("name", path.stem),
        path=path,
        steam_appids={int(x) for x in m.get("steam_appid", [])},
        lutris_titles={str(x) for x in m.get("lutris", [])},
        exe_globs=[str(x).lower() for x in m.get("exe", [])],
        trigger_mode=t.get("mode", "override"),
        left=_trigger(t.get("left")),
        right=_trigger(t.get("right")),
        color=parse_color(leds.get("color")),
        player_leds=leds.get("player_leds"),
        mic_led=leds.get("mic_led"),
        is_default=bool(data.get("default", False)) or path.stem == "default",
        xbox_mode=bool(data.get("xbox", {}).get("enabled", False)),
    )


def _toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _toml_list(items, quote=True) -> str:
    return "[" + ", ".join(_toml_str(str(i)) if quote else str(i) for i in items) + "]"


def _toml_trigger(t: TriggerSetting | None) -> str:
    t = t or TriggerSetting()
    return f"{{ effect = {_toml_str(t.effect)}, start = {int(t.start)}, force = {int(t.force)} }}"


def profile_to_toml(p: Profile) -> str:
    lines = [f"name = {_toml_str(p.name)}"]
    if p.is_default:
        lines.append("default = true")
    lines += ["", "[match]"]
    if p.steam_appids:
        lines.append(f"steam_appid = {_toml_list(sorted(p.steam_appids), quote=False)}")
    if p.lutris_titles:
        lines.append(f"lutris = {_toml_list(sorted(p.lutris_titles))}")
    if p.exe_globs:
        lines.append(f"exe = {_toml_list(p.exe_globs)}")
    lines += ["", "[xbox]", f"enabled = {'true' if p.xbox_mode else 'false'}"]
    lines += ["", "[triggers]", f"mode = {_toml_str(p.trigger_mode)}",
              f"left  = {_toml_trigger(p.left)}", f"right = {_toml_trigger(p.right)}", "", "[leds]"]
    if p.color is not None:
        lines.append('color = "#{:02x}{:02x}{:02x}"'.format(*p.color))
    if p.player_leds is not None:
        v = p.player_leds
        lines.append(f"player_leds = {v}" if isinstance(v, int) else f"player_leds = {_toml_str(str(v))}")
    if p.mic_led is not None:
        lines.append(f"mic_led = {_toml_str(p.mic_led)}")
    return "\n".join(lines) + "\n"


def slugify(name: str) -> str:
    s = "".join(c.lower() if c.isalnum() else "-" for c in name).strip("-")
    while "--" in s:
        s = s.replace("--", "-")
    return s or "profile"


def save_profile(p: Profile, directory: Path = PROFILE_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = p.path or directory / f"{slugify(p.name)}.toml"
    if p.path is None:
        n = 2
        while path.exists():
            path = directory / f"{slugify(p.name)}-{n}.toml"
            n += 1
    if p.is_default:   # only one default profile
        for other in load_profiles(directory):
            if other.is_default and other.path != path and other.path:
                other.is_default = False
                other.path.write_text(profile_to_toml(other))
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(profile_to_toml(p))
    tmp.replace(path)
    p.path = path
    return path


def delete_profile(p: Profile) -> None:
    if p.path and p.path.exists():
        p.path.unlink()


# The GUI touches this file while you edit a profile; the daemon stays hands-off while it's fresh.
PREVIEW_LOCK = CONFIG_DIR / ".preview"
PREVIEW_TTL = 6.0


def touch_preview_lock() -> None:
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        PREVIEW_LOCK.touch()
    except OSError:
        pass


def clear_preview_lock() -> None:
    """Hand the controller back to the background service right away (app closed / preview off)."""
    try:
        PREVIEW_LOCK.unlink()
    except OSError:
        pass


def preview_active() -> bool:
    import time
    try:
        return time.time() - PREVIEW_LOCK.stat().st_mtime < PREVIEW_TTL
    except OSError:
        return False


def load_profiles(directory: Path = PROFILE_DIR) -> list[Profile]:
    profiles = []
    if directory.is_dir():
        for p in sorted(directory.glob("*.toml")):
            try:
                profiles.append(load_profile(p))
            except (OSError, ValueError, tomllib.TOMLDecodeError) as e:
                print(f"padlink: skipping {p.name}: {e}")
    return profiles


def pick_profile(profiles: list[Profile], games: list[RunningGame]) -> tuple[Profile | None, RunningGame | None]:
    best: tuple[int, Profile | None, RunningGame | None] = (0, None, None)
    for g in games:
        for p in profiles:
            s = p.matches(g)
            if s > best[0]:
                best = (s, p, g)
    if best[1] is not None:
        return best[1], best[2]
    default = next((p for p in profiles if p.is_default), None)
    return default, None
