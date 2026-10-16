# Troubleshooting

Start with the [quick checks](#quick-checks). Then find your problem below. If nothing fits,
[open a bug report](https://github.com/Miguel7Coelho/padlink/issues/new?template=bug_report.yml)
with the output of the [diagnostics](#collecting-diagnostics).

- [Quick checks](#quick-checks)
- [Installation](#installation)
- [Controller not found](#controller-not-found)
- [Permission denied](#permission-denied)
- [Adaptive triggers](#adaptive-triggers)
- [Light bar and LEDs](#light-bar-and-leds)
- [Battery](#battery)
- [Game detection and profiles](#game-detection-and-profiles)
- [Xbox mode](#xbox-mode)
- [Background service](#background-service)
- [The app](#the-app)
- [Collecting diagnostics](#collecting-diagnostics)
- [Resetting everything](#resetting-everything)

---

## Quick checks

```bash
padlink list                         # controller found? connection? battery?
padlink games                        # game detected? which profile?
systemctl --user status padlink      # service running?
journalctl --user -u padlink -n 50   # what the service did
```

---

## Installation

### `install.sh: Permission denied`
The executable bit was lost (for example when extracting with some archive tools):
```bash
chmod +x install.sh packaging/padlink-hide && ./install.sh
```

### "Run ./install.sh as your normal user (without sudo)"
The installer sets things up for the user who runs it, and asks for the password itself. Run
`./install.sh` without `sudo`.

### The installer stopped with an error
Open `~/.cache/padlink/install.log`. The last lines show the command that failed. The usual causes:
- **No network / mirror error** while installing packages: retry, or install the packages from
  [Requirements](README.md#requirements) by hand and run the installer again.
- **`visudo` reported an error**: report it, and attach the log.

### `padlink: command not found`
`~/.local/bin` isn't in your `PATH`. Log out and back in (Fedora adds it automatically when the
folder exists), or add `export PATH="$HOME/.local/bin:$PATH"` to `~/.bashrc`.

---

## Controller not found

`padlink list` prints "No controllers found."

1. **Check that Linux sees it:**
   ```bash
   grep -l 054C /sys/bus/hid/devices/*/uevent
   ```
   Nothing printed?
   - **USB:** use a cable that carries data (many are charge-only) and try another port.
   - **Bluetooth:** pair it in GNOME Settings → Bluetooth. Hold **Create + PS** (on a DS4,
     **Share + PS**) until the light bar flashes quickly.
2. **Check the driver:**
   ```bash
   uname -r                      # 5.12+ for DualSense, 6.2+ for DualShock 4
   lsmod | grep hid_playstation
   sudo dmesg | grep -i -E "playstation|dualsense|sony"
   ```
   On an older kernel the DS4 is handled by `hid-sony`. Lights still work, but update the kernel if
   you can.
3. **Third-party or clone pads** often use other IDs and aren't supported.

---

## Permission denied

You see `Permission denied … /dev/hidrawN`, or the Service page reports no access.

```bash
ls -l /etc/udev/rules.d/70-padlink.rules     # installed?
getfacl /dev/hidraw* 2>/dev/null | grep -B3 "user:$USER"
```

- No rule → run `./install.sh`.
- Rule present but no ACL for you → **reconnect the controller** (or
  `sudo udevadm trigger --subsystem-match=hidraw`). Access is given to the user logged in at
  the screen, so it won't apply over SSH.
- Xbox mode is active → this is expected. The controller is asleep on purpose; see [Xbox mode](#xbox-mode).

---

## Adaptive triggers

### Nothing changes when I set an effect
- **DualShock 4:** it has no adaptive triggers.
- **Over Bluetooth only:** try USB. If USB works and Bluetooth doesn't, please
  [report it](https://github.com/Miguel7Coelho/padlink/issues/new?template=bug_report.yml),
  including your controller firmware version (PS5 → Settings → Accessories).
- **The profile is set to `mode = "game"`:** in that mode padlink leaves the triggers alone. Use
  `"override"`.
- **Test without profiles:** `padlink trigger right rigid`. If that works, the problem is in
  profile selection; see [Game detection](#game-detection-and-profiles).

### The effect works, then disappears in a game
Games with native DualSense support through Proton (or through Steam Input) send their own trigger
effects. In `override` mode padlink re-sends yours every 5 seconds. For those games, `mode = "game"`
usually feels better.

### "Starts at" / "Strength" are greyed out
Not every effect uses them; for example, `rigid` and `soft` have fixed shapes. The fields switch on
for effects that use them.

---

## Light bar and LEDs

### The colour doesn't change, or changes back on its own
Another program is driving the controller and sending its own colour. If adaptive triggers still
work but the light bar won't change, this is almost certainly the cause. padlink tells you which
program it is:
```bash
padlink list          # "⚠ also open in …" names the program
```
(or `sudo lsof /dev/hidrawN`, using the `hidrawN` that `padlink list` shows).

**`winedevice.exe` (Wine):** a Windows app running under Wine, such as a DAW, a launcher or a game,
opened the controller through `hidraw`. Close that app, or stop its prefix:
```bash
tr '\0' '\n' < /proc/<pid>/environ | grep WINEPREFIX    # which prefix
WINEPREFIX=<that prefix> wineserver -k
```
To stop that prefix from grabbing controllers at all (it falls back to normal input devices and
leaves the light bar alone):
```bash
WINEPREFIX=<that prefix> wine reg add 'HKLM\System\CurrentControlSet\Services\winebus' /v DisableHidraw /t REG_DWORD /d 1 /f
```
Games running through **Proton** do the same thing on purpose (DualSense support). Use a profile
with `mode = "game"` for those, or accept that the game sets the colour while it runs.

**Steam:** Steam Input sets the light bar while Steam is running. Either:
- *Steam → Settings → Controller → Enable Steam Input for PlayStation controllers* → off, or set it
  per game (game → Properties → Controller → *Disable Steam Input*), or
- let Steam handle the colour; padlink still handles the triggers.

### Player indicator shows the wrong number of LEDs
With `player_leds = "battery"`, 5 LEDs = 81–100%, 4 = 61–80%, and so on. It updates when the
battery level changes.

---

## Battery

### Battery shows "unknown"
```bash
ls /sys/class/power_supply/ | grep -i controller
```
- Nothing listed → the kernel driver didn't create the battery node (common with `hid-sony` on
  older kernels). Update the kernel.
- Over USB, the controller charges, and the level can read "Full" or "Charging".

### The battery isn't shown in the top bar
- **Log out and back in once** after installing. On Wayland, GNOME only loads new extensions at login.
- Check it's enabled: `gnome-extensions info padlink-battery@miguel7coelho.github.io`
  (or the *Extensions* app). Enable it with
  `gnome-extensions enable padlink-battery@miguel7coelho.github.io`.
- If *all* extensions are off: `gsettings set org.gnome.shell disable-user-extensions false`.
- The indicator hides itself when no controller is connected, and appears within 10 seconds of
  connecting one.
- Not on GNOME (KDE, Xfce…)? The extension is GNOME-only; use `padlink list` or the app.

### The light bar turned red
That's the low-battery warning: the battery is below 25% (or your `low_below` setting). Plug the
controller in and the profile's colour comes back. To keep notifications but not the red light, set
`low_light = false` in `~/.config/padlink/settings.toml`.

### No low-battery notification
Notifications come from the **background service** (see [Background service](#background-service)),
and need `notify-send` (package `libnotify`). They are sent once below 25% and once below 10% per
discharge (configurable in `~/.config/padlink/settings.toml`).

---

## Game detection and profiles

Run `padlink games` **while the game is running**.

| You see | Fix |
|---|---|
| `No running Steam/Lutris games detected` | See below |
| The game, but `Profile: Desktop` | Your profile's rules don't match. Copy the AppID / title / exe shown into the profile, or use **Use** on the Profiles page |
| The wrong profile | A Steam AppID match beats a Lutris title, which beats an exe name. Check other profiles for overlapping rules |

**Steam:** detection reads `SteamAppId` from the game's processes. For a *non-Steam game* added to
Steam, match it by `exe` instead.
**Steam Flatpak:** detected; game names come from
`~/.var/app/com.valvesoftware.Steam/.local/share/Steam`.
**Lutris:** the title must match the Lutris library name exactly (case doesn't matter).
**Heroic, Bottles, other launchers:** match by `exe` (for example `exe = ["game*.exe"]`).

A profile didn't load? The service log names the file and the TOML error:
```bash
journalctl --user -u padlink | grep skipping
```

---

## Xbox mode

### The game shows two controllers
The real controller wasn't put to sleep. Check:
```bash
ls -l /usr/local/libexec/padlink-hide /etc/sudoers.d/padlink   # both should exist
sudo -n /usr/local/libexec/padlink-hide show-all && echo OK     # must print OK with no password prompt
journalctl --user -u padlink | grep -i "hide"
```
If either file is missing or `sudo -n` asks for a password, run `./install.sh` again.
Steam itself may keep a controller it saw at startup: restart Steam after enabling Xbox mode the
first time.

### No Xbox controller appears
```bash
ls -l /dev/uinput            # should exist
lsmod | grep uinput          # module loaded?
getfacl /dev/uinput | grep "user:$USER"
python3 -c "import evdev"    # python-evdev installed?
```
- No `/dev/uinput` → `sudo modprobe uinput` (the installer loads it at boot from now on).
- No ACL → reconnect / log out and in, or `sudo udevadm trigger --sysname-match=uinput`.
- `import evdev` fails → `sudo dnf install python3-evdev` (or run the installer again).

To test without profiles, run `padlink xbox`.

### The controller is still missing after padlink stopped / crashed
```bash
sudo /usr/local/libexec/padlink-hide show-all
```
Or reconnect the controller; a reconnect always creates fresh device nodes with normal permissions.

### Buttons are in the wrong place
In Xbox mode: Cross = A, Circle = B, **Square = X, Triangle = Y**, Create = Back, Options = Start,
PS = Guide. If Square and Triangle are swapped in a specific game, report it with the game name.
The touch pad and Mute button aren't mapped in Xbox mode.

### No rumble
Rumble is forwarded only in Xbox mode, and only if the game sends it. Check that rumble is enabled
in the game's options. In Steam, the per-game controller configuration can also turn it off.

---

## Background service

```bash
systemctl --user status padlink
journalctl --user -u padlink -n 100
systemctl --user restart padlink
```

- **`Unit padlink.service not found`** → run `./install.sh`, or press **Install** on the Service page.
- **It doesn't start at login** → `systemctl --user enable padlink`.
- **The controller keeps the profile I selected in the app instead of switching for my game**: with
  *Preview on controller* on, the profile selected in the app's editor stays on the controller (on
  every tab) while padlink's window is open, and automatic game switching pauses. Close the window,
  or turn *Preview on controller* off, and the service applies the right profile immediately.

---

## The app

### `padlink gui` says PyGObject / GTK4 / libadwaita is missing
```bash
sudo dnf install python3-gobject gtk4 libadwaita        # Fedora
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 # Debian/Ubuntu
sudo pacman -S python-gobject gtk4 libadwaita           # Arch
```
libadwaita **1.5+** is required (Fedora 40+, Ubuntu 24.04+).

### The live view says "Xbox controller mode is on"
That's expected: the controller is asleep while games see the Xbox pad. The live view comes back
when Xbox mode turns off.

### Icons are missing
Install the Adwaita icon theme (`adwaita-icon-theme`).

---

## Collecting diagnostics

Paste this output into your bug report:

```bash
{
  echo "== system";   uname -r; cat /etc/os-release | head -2; python3 --version
  echo "== padlink";  padlink list; padlink games
  echo "== hid";      grep -H HID_ID /sys/bus/hid/devices/*/uevent | grep -i 054C
  echo "== perms";    ls -l /dev/hidraw* /dev/uinput 2>&1
  echo "== service";  systemctl --user status padlink --no-pager 2>&1 | head -15
  echo "== log";      journalctl --user -u padlink -n 60 --no-pager 2>&1
} > padlink-diagnostics.txt 2>&1
```

Also attach `~/.cache/padlink/install.log` if the problem started during installation.

---

## Resetting everything

```bash
sudo /usr/local/libexec/padlink-hide show-all   # wake the controller if it's asleep
./install.sh --uninstall --purge                # remove padlink and profiles
./install.sh                                    # fresh install
```
