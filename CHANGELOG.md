# Changelog

All notable changes to padlink are documented here.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and padlink
follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.1] - 2026-10-15

### Added
- Detects other programs that have the controller open (Wine's `winedevice.exe`, Steam, Proton
  games) and names them in `padlink list`, after `padlink led` / `padlink player`, and in a desktop
  notification from the background service. These programs send their own light bar colour and
  override padlink, which was the cause of "the light bar is not being controlled" reports.
- Troubleshooting for Wine prefixes that grab the controller (`winebus` `DisableHidraw`).

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

[Unreleased]: https://github.com/Miguel7Coelho/padlink/compare/v0.1.1...develop
[0.1.1]: https://github.com/Miguel7Coelho/padlink/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Miguel7Coelho/padlink/releases/tag/v0.1.0
