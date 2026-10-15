"""Detect which game is running by scanning /proc (your own processes only, no root).

Signals, strongest first:
  Steam:  processes started by Steam carry SteamAppId / SteamGameId in their environment, and the
          launcher shows up as `reaper SteamLaunch AppId=<id> ...`. The name comes from
          steamapps/appmanifest_<id>.acf.
  Lutris: games run under `lutris-wrapper <Game Title> ...` (argv[1] is the title), and newer
          Lutris versions also export LUTRIS_GAME_UUID / GAME_NAME. Both are checked.
  Any launcher (Heroic, Bottles, native): profiles can also match on the executable name.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

PROC = Path("/proc")
STEAM_LIBRARY_HINTS = [
    "~/.local/share/Steam",
    "~/.steam/steam",
    "~/.var/app/com.valvesoftware.Steam/.local/share/Steam",   # Flatpak Steam
]
_APPID_RE = re.compile(r"AppId=(\d+)")
# Steam's own helper processes; they carry SteamAppId=0 or similar and aren't games
_IGNORE_EXE = {"steam", "steamwebhelper", "reaper", "pressure-vessel-wrap", "srt-bwrap",
               "pv-bwrap", "steam-runtime-launcher-service", "lutris", "python3", "bash", "sh"}


@dataclass
class RunningGame:
    source: str                    # "steam" | "lutris" | "process"
    name: str
    steam_appid: int | None = None
    exe_names: set[str] = field(default_factory=set)
    pids: set[int] = field(default_factory=set)

    def __str__(self) -> str:
        sid = f" (AppID {self.steam_appid})" if self.steam_appid else ""
        return f"[{self.source}] {self.name}{sid}"


def _read(path: Path, binary: bool = False):
    try:
        return path.read_bytes() if binary else path.read_text()
    except OSError:
        return None


def _environ(pid_dir: Path) -> dict[str, str]:
    raw = _read(pid_dir / "environ", binary=True)
    if not raw:
        return {}
    env = {}
    for item in raw.split(b"\0"):
        if b"=" in item:
            k, v = item.split(b"=", 1)
            env[k.decode(errors="replace")] = v.decode(errors="replace")
    return env


def _cmdline(pid_dir: Path) -> list[str]:
    raw = _read(pid_dir / "cmdline", binary=True)
    if not raw:
        return []
    return [a.decode(errors="replace") for a in raw.rstrip(b"\0").split(b"\0")]


def _exe_name(cmd: list[str]) -> str:
    if not cmd:
        return ""
    # Wine/Proton games show their .exe somewhere in argv, e.g. "Z:\\...\\Game.exe"
    for a in cmd:
        if a.lower().endswith(".exe"):
            return re.split(r"[\\/]", a)[-1].lower()
    return os.path.basename(cmd[0]).lower()


def steam_library_dirs() -> list[Path]:
    dirs: list[Path] = []
    for hint in STEAM_LIBRARY_HINTS:
        root = Path(os.path.expanduser(hint))
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if (root / "steamapps").is_dir():
            dirs.append(root / "steamapps")
        text = _read(vdf)
        if text:
            for m in re.finditer(r'"path"\s+"([^"]+)"', text):
                p = Path(m.group(1)) / "steamapps"
                if p.is_dir() and p not in dirs:
                    dirs.append(p)
    return dirs


_name_cache: dict[int, str] = {}


def steam_game_name(appid: int) -> str:
    if appid in _name_cache:
        return _name_cache[appid]
    name = f"Steam app {appid}"
    for lib in steam_library_dirs():
        text = _read(lib / f"appmanifest_{appid}.acf")
        if text:
            m = re.search(r'"name"\s+"([^"]+)"', text)
            if m:
                name = m.group(1)
                break
    _name_cache[appid] = name
    return name


def detect_games(proc: Path = PROC) -> list[RunningGame]:
    me = os.getuid()
    games: dict[str, RunningGame] = {}

    for pid_dir in proc.iterdir():
        if not pid_dir.name.isdigit():
            continue
        try:
            if pid_dir.stat().st_uid != me:
                continue
        except OSError:
            continue
        cmd = _cmdline(pid_dir)
        if not cmd:
            continue
        exe = _exe_name(cmd)
        pid = int(pid_dir.name)
        env = _environ(pid_dir)
        joined = " ".join(cmd)

        # Steam
        appid = None
        for key in ("SteamAppId", "SteamGameId", "STEAM_COMPAT_APP_ID"):
            v = env.get(key, "")
            if v.isdigit() and int(v) > 0:
                appid = int(v)
                break
        if appid is None and "SteamLaunch" in joined:
            m = _APPID_RE.search(joined)
            if m and int(m.group(1)) > 0:
                appid = int(m.group(1))
        if appid is not None:
            key = f"steam:{appid}"
            g = games.get(key) or RunningGame("steam", steam_game_name(appid), steam_appid=appid)
            if exe and exe not in _IGNORE_EXE:
                g.exe_names.add(exe)
            g.pids.add(pid)
            games[key] = g
            continue

        # Lutris
        title = env.get("GAME_NAME") or ""
        if not title and os.path.basename(cmd[0]).startswith("lutris-wrapper") and len(cmd) > 1:
            title = cmd[1]
        if not title and env.get("LUTRIS_GAME_UUID"):
            title = env.get("LUTRIS_GAME_SLUG", env["LUTRIS_GAME_UUID"])
        if title:
            key = f"lutris:{title.lower()}"
            g = games.get(key) or RunningGame("lutris", title)
            if exe and exe not in _IGNORE_EXE and not exe.startswith("lutris-wrapper"):
                g.exe_names.add(exe)
            g.pids.add(pid)
            games[key] = g
            continue

        # Anything else is only matched by exe name in profiles; remember Wine .exe processes
        if exe.endswith(".exe"):
            key = f"process:{exe}"
            g = games.get(key) or RunningGame("process", exe, exe_names={exe})
            g.pids.add(pid)
            games[key] = g

    # Drop plain .exe entries that already belong to a Steam/Lutris game
    owned = {e for g in games.values() if g.source != "process" for e in g.exe_names}
    return [g for k, g in games.items() if not (g.source == "process" and g.name in owned)]
