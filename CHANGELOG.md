# Changelog

All notable changes to padlink are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and padlink
follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Turn the controller off from the app (⏻ button in the header) or with `padlink off`. Works over
  Bluetooth: padlink drops the connection through BlueZ and the controller powers down; press PS to
  turn it on again. Over USB the button is disabled, since the cable keeps the controller powered.
- The Controller tab shows when Xbox mode is on: a banner, and the Xbox names (A/B/X/Y, LB/RB,
  LT/RT, Back/Start/Guide) drawn on the controller.

### Fixed
- Switching on "Show as an Xbox 360 controller" in the profile editor did nothing until the
  profile's game was running. With "Preview on controller" on, the previewed profile's Xbox switch
  now turns Xbox mode on and off right away (through the background service).
- Xbox mode failed with "Permission denied: /dev/input/eventN": the controller's motion sensor and
  touch pad nodes are root-only by default. The udev rule now gives the logged-in user access to all
  of the controller's input nodes (run `install.sh` again), and Xbox mode no longer fails if one of
  those extra nodes can't be grabbed.
- When Xbox mode couldn't start, the background service retried and showed the same error
  notification every 2 seconds. It now notifies once per error and retries every 30 seconds, and the notification is no longer
  "critical" (GNOME keeps critical notifications on screen until each one is dismissed).
- The app kept sending the light bar and triggers of the previewed profile while Xbox mode was on
  (it opens the controller before Xbox mode hides it).

## [0.2.0] - 2026-10-15

### Added
- GNOME top-bar battery indicator (extension `padlink-battery@miguel7coelho.github.io`): battery
  percentage of connected controllers, red when low, ⚡ while charging; installed by `install.sh`
  (`--no-extension` to skip).
- Low-battery light bar: below 25% the light bar turns red until the controller is charging, then
  returns to the profile colour.
- `~/.config/padlink/settings.toml` with `[battery]` `low_below`, `critical_below`, `low_light`, `low_color`.
- The app's battery indicator turns red when the battery is low.
- Branch and tag protection: CODEOWNERS, repository rulesets for `main`, `develop` and `v*` tags, and `scripts/apply-repo-protection.sh`.

### Fixed
- Switching from the Profiles page to the Controller tab ended the preview, and a few seconds later
  the background service put the active game's profile back on the controller. The selected
  profile now stays on the controller on every tab (and while minimized) as long as padlink is
  open; closing the window hands control back to the service immediately.

### Changed
- New controller view: a rendered controller on a transparent background with the live state drawn
  on top (L2/R2 meters, pressed highlights, stick position markers, touch points, player LEDs, and
  the touch pad ring in the current light bar colour). Labels outside the controller follow the
  theme's text colour. The asset is built from `art/controller-realistic-source.png` by
  `scripts/make-realistic-asset.py`.
- Low-battery notification now comes below 25% (was at 20%); thresholds are configurable.

## [0.1.1] - 2026-10-15

### Added
- Detects other programs that have the controller open (Wine's `winedevice.exe`, Steam, Proton
  games) and names them in `padlink list`, after `padlink led` / `padlink player`, and in a desktop
  notification from the background service. These programs send their own light bar colour and
  override padlink, which was the cause of "the light bar is not being controlled" reports.
- Troubleshooting for Wine prefixes that grab the controller (`winebus` `DisableHidraw`).

### Fixed
- The background service re-sent the whole profile, light bar colour included, every time the
  battery level changed, undoing colours set with `padlink led`. A battery change now only updates
  the player indicator.

### Changed
- DualSense USB output reports are now 63 bytes, the size used by the Linux `hid-playstation`
  driver (previously 48).

## [0.1.0] - 2026-10-15

First public release.

> **Vibe-coded and in continuous improvement.** padlink was built through AI-assisted
> development (Claude) and keeps evolving. It's covered by automated tests with simulated
> controllers but hasn't been verified on every controller, distribution and game yet.
> Please report what works and what doesn't:
> https://github.com/Miguel7Coelho/padlink/issues/new/choose

### Added
- Controller discovery through sysfs for DualSense, DualSense Edge and DualShock 4 (USB and Bluetooth).
- Adaptive triggers: 17 effects ported from DS4Windows, set per trigger.
- Light bar colour, player indicator (player number or battery bar) and Mute button light.
- Battery level and charging state; desktop notifications at 20% and 10%.
- Game detection for Steam (AppID, including Flatpak Steam), Lutris (title) and any launcher (`.exe` name).
- TOML profiles with automatic switching and a default profile.
- Xbox mode: a virtual Xbox 360 pad through uinput with rumble forwarding; the real controller is
  hidden from games while it's active and restored afterwards (`padlink-hide` helper).
- GTK4 / libadwaita app: live controller view using Sony's control names, profile editor with live
  preview and pull meters, service control page.
- Background service (systemd user unit).
- Command line: `list`, `games`, `trigger`, `led`, `player`, `apply`, `effects`, `xbox`, `gui`, `daemon`.
- `install.sh`: one-command silent installer with `--uninstall`, `--purge`, `--verbose`, `--no-service`.

### Known limitations
- Not yet verified on real hardware over Bluetooth; trigger reports over Bluetooth follow the
  DS4Windows/SDL layout.
- Touch pad and Mute button are not mapped in Xbox mode.

[Unreleased]: https://github.com/Miguel7Coelho/padlink/compare/v0.2.0...develop
[0.2.0]: https://github.com/Miguel7Coelho/padlink/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/Miguel7Coelho/padlink/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Miguel7Coelho/padlink/releases/tag/v0.1.0
