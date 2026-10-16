#!/usr/bin/env bash
# padlink silent installer
#
#   ./install.sh               install / update everything (asks for your sudo password once)
#   ./install.sh --uninstall   remove padlink (keeps your profiles)
#   ./install.sh --uninstall --purge   remove padlink and your profiles
#   ./install.sh --no-service  don't enable the background service
#   ./install.sh --verbose     show every command instead of just a summary
#   ./install.sh --no-extension  don't install the GNOME top-bar battery indicator
#
# What it installs:
#   system packages   PyGObject + GTK4 + libadwaita (the app), python-evdev (Xbox mode), acl
#   ~/.local/share/padlink        the program
#   ~/.local/bin/padlink, padlink-gui
#   ~/.local/share/applications   app launcher
#   ~/.config/padlink/profiles    example profiles (only if you have none yet)
#   ~/.config/systemd/user/padlink.service   background service, enabled and started
#   ~/.local/share/gnome-shell/extensions/padlink-battery@…   top-bar battery indicator (GNOME only)
#   /etc/udev/rules.d/70-padlink.rules       your user may talk to Sony pads + create virtual pads (uinput)
#   /etc/modules-load.d/padlink-uinput.conf  load uinput at boot
#   /usr/local/libexec/padlink-hide          root helper that puts the real pad to sleep in Xbox mode
#   /etc/sudoers.d/padlink                   lets padlink run only that helper without a password
set -euo pipefail

APP_ID="io.github.miguel7coelho.padlink"
EXT_UUID="padlink-battery@miguel7coelho.github.io"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERBOSE=0; UNINSTALL=0; PURGE=0; SERVICE=1; EXTENSION=1
for a in "$@"; do
  case "$a" in
    --verbose|-v) VERBOSE=1 ;;
    --uninstall)  UNINSTALL=1 ;;
    --purge)      PURGE=1 ;;
    --no-service) SERVICE=0 ;;
    --no-extension) EXTENSION=0 ;;
    -h|--help)    sed -n '2,26p' "$0"; exit 0 ;;
    *) echo "unknown option: $a" >&2; exit 1 ;;
  esac
done

# --- who are we installing for -------------------------------------------------------------
if [[ $EUID -eq 0 && -n "${SUDO_USER:-}" && "${SUDO_USER}" != root ]]; then
  echo "Run ./install.sh as your normal user (without sudo); it asks for the password itself." >&2
  exit 1
fi
TARGET_USER="$(id -un)"
HOME_DIR="$HOME"
if [[ $EUID -eq 0 ]]; then SUDO=(); else SUDO=(sudo); fi

DATA_HOME="${XDG_DATA_HOME:-$HOME_DIR/.local/share}"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME_DIR/.config}"
APP_DIR="$DATA_HOME/padlink"
BIN_DIR="$HOME_DIR/.local/bin"
PROFILE_DIR="$CONFIG_HOME/padlink/profiles"
UNIT_DIR="$CONFIG_HOME/systemd/user"
LOG_DIR="${XDG_CACHE_HOME:-$HOME_DIR/.cache}/padlink"
LOG="$LOG_DIR/install.log"
HELPER=/usr/local/libexec/padlink-hide

mkdir -p "$LOG_DIR"
: > "$LOG"
say()  { echo "  $*"; }
step() { printf '• %s\n' "$*"; echo "=== $*" >> "$LOG"; }
run()  {                       # run a command, quietly unless --verbose; keep everything in the log
  echo "+ $*" >> "$LOG"
  if [[ $VERBOSE -eq 1 ]]; then "$@" 2>&1 | tee -a "$LOG"; return "${PIPESTATUS[0]}"
  else "$@" >> "$LOG" 2>&1; fi
}
try()  { run "$@" || echo "  (ignored failure: $*)" >> "$LOG"; }
fail() { echo "✗ $*" >&2; echo "  Details: $LOG" >&2; exit 1; }
trap 'fail "Installation stopped at line $LINENO."' ERR

sudo_once() {
  [[ $EUID -eq 0 ]] && return 0
  command -v sudo >/dev/null || fail "sudo is required for the system parts."
  if ! sudo -n true 2>/dev/null; then
    echo "padlink needs administrator rights once (packages, udev rule, helper)."
    sudo -v || fail "sudo authentication failed."
  fi
  # keep the sudo timestamp fresh while we work
  ( while true; do sudo -n true 2>/dev/null; sleep 50; kill -0 "$$" 2>/dev/null || exit; done ) &
  KEEPALIVE=$!
  trap 'kill $KEEPALIVE 2>/dev/null || true' EXIT
}

# Add/remove the extension in GNOME's enabled list (takes effect at next login on Wayland,
# where GNOME can't load newly installed extensions until the session restarts).
set_extension_enabled() {
  command -v gsettings >/dev/null || return 0
  local current new
  current=$(gsettings get org.gnome.shell enabled-extensions 2>/dev/null) || return 0
  new=$(python3 - "$current" "$EXT_UUID" "$1" <<'PY'
import ast, sys
cur, uuid, on = sys.argv[1], sys.argv[2], sys.argv[3] == "true"
cur = cur.replace("@as ", "", 1)
items = [x for x in ast.literal_eval(cur) if x != uuid]
if on:
    items.append(uuid)
print(repr(items))
PY
) || return 0
  run gsettings set org.gnome.shell enabled-extensions "$new"
}

# ============================================================================================
uninstall() {
  echo "Removing padlink…"
  sudo_once
  step "Stopping the service"
  try systemctl --user disable --now padlink.service
  try "${SUDO[@]}" "$HELPER" show-all
  step "Removing files"
  rm -rf "$APP_DIR" "$BIN_DIR/padlink" "$BIN_DIR/padlink-gui" \
         "$DATA_HOME/applications/$APP_ID.desktop" "$UNIT_DIR/padlink.service" \
         "$UNIT_DIR/default.target.wants/padlink.service"
  try gnome-extensions disable "$EXT_UUID"
  set_extension_enabled false
  rm -rf "$DATA_HOME/gnome-shell/extensions/$EXT_UUID"
  try systemctl --user daemon-reload
  run "${SUDO[@]}" rm -f /etc/udev/rules.d/70-padlink.rules /etc/modules-load.d/padlink-uinput.conf \
      "$HELPER" /etc/sudoers.d/padlink
  try "${SUDO[@]}" udevadm control --reload
  if [[ $PURGE -eq 1 ]]; then rm -rf "$CONFIG_HOME/padlink"; say "Profiles removed."
  else say "Your profiles are still in $PROFILE_DIR"; fi
  echo "✓ padlink removed. (System packages like GTK4 were left installed.)"
}

# ============================================================================================
install_packages() {
  local pm="" pkgs=() missing=()
  if   command -v dnf     >/dev/null; then pm=dnf;    pkgs=(python3 python3-gobject python3-cairo gtk4 libadwaita python3-evdev acl)
  elif command -v apt-get >/dev/null; then pm=apt;    pkgs=(python3 python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 python3-evdev acl)
  elif command -v pacman  >/dev/null; then pm=pacman; pkgs=(python python-gobject python-cairo gtk4 libadwaita python-evdev acl)
  elif command -v zypper  >/dev/null; then pm=zypper; pkgs=(python3 python3-gobject python3-gobject-cairo typelib-1_0-Gtk-4_0 typelib-1_0-Adw-1 python3-evdev acl)
  else say "Unknown package manager; make sure PyGObject, GTK4, libadwaita and python-evdev are installed."; return; fi

  for p in "${pkgs[@]}"; do
    case $pm in
      dnf|zypper) rpm -q "$p" >/dev/null 2>&1 || missing+=("$p") ;;
      apt)        dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p") ;;
      pacman)     pacman -Q "$p" >/dev/null 2>&1 || missing+=("$p") ;;
    esac
  done
  if [[ ${#missing[@]} -eq 0 ]]; then say "all packages already installed"; return; fi
  say "installing: ${missing[*]}"
  case $pm in
    dnf)    run "${SUDO[@]}" dnf install -y -q "${missing[@]}" ;;
    apt)    run "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y -q "${missing[@]}" ;;
    pacman) run "${SUDO[@]}" pacman -S --needed --noconfirm "${missing[@]}" ;;
    zypper) run "${SUDO[@]}" zypper --non-interactive install "${missing[@]}" ;;
  esac
}

install_system() {
  run "${SUDO[@]}" install -D -m 0644 "$SRC/packaging/70-padlink.rules" /etc/udev/rules.d/70-padlink.rules
  echo uinput > "$LOG_DIR/padlink-uinput.conf"
  run "${SUDO[@]}" install -D -m 0644 "$LOG_DIR/padlink-uinput.conf" /etc/modules-load.d/padlink-uinput.conf
  try "${SUDO[@]}" modprobe uinput
  run "${SUDO[@]}" install -D -m 0755 -o root -g root "$SRC/packaging/padlink-hide" "$HELPER"

  local tmp="$LOG_DIR/padlink.sudoers"
  echo "$TARGET_USER ALL=(root) NOPASSWD: $HELPER" > "$tmp"
  run "${SUDO[@]}" visudo -cf "$tmp"
  run "${SUDO[@]}" install -D -m 0440 -o root -g root "$tmp" /etc/sudoers.d/padlink

  try "${SUDO[@]}" udevadm control --reload
  try "${SUDO[@]}" udevadm trigger --subsystem-match=hidraw --action=change
  try "${SUDO[@]}" udevadm trigger --subsystem-match=misc --sysname-match=uinput --action=change
  try "${SUDO[@]}" udevadm settle --timeout=5
}

install_app() {
  local py=/usr/bin/python3
  [[ -x $py ]] || py="$(command -v python3)"
  "$py" -c 'import sys; sys.exit(sys.version_info < (3, 11))' || fail "Python 3.11 or newer is required."

  rm -rf "$APP_DIR.new"
  mkdir -p "$APP_DIR.new"
  cp -r "$SRC/padlink" "$SRC/data" "$SRC/packaging" "$SRC/gnome-extension" "$SRC/README.md" "$APP_DIR.new/"
  cp "$SRC/install.sh" "$APP_DIR.new/"
  find "$APP_DIR.new" -name __pycache__ -prune -exec rm -rf {} +
  rm -rf "$APP_DIR"
  mv "$APP_DIR.new" "$APP_DIR"

  mkdir -p "$BIN_DIR"
  cat > "$BIN_DIR/padlink" <<EOF
#!/bin/sh
PYTHONPATH="$APP_DIR\${PYTHONPATH:+:\$PYTHONPATH}" exec $py -m padlink "\$@"
EOF
  cat > "$BIN_DIR/padlink-gui" <<EOF
#!/bin/sh
exec "$BIN_DIR/padlink" gui "\$@"
EOF
  chmod 0755 "$BIN_DIR/padlink" "$BIN_DIR/padlink-gui"

  mkdir -p "$DATA_HOME/applications"
  sed "s|^Exec=.*|Exec=$BIN_DIR/padlink-gui|" "$SRC/packaging/$APP_ID.desktop" > "$DATA_HOME/applications/$APP_ID.desktop"
  try update-desktop-database -q "$DATA_HOME/applications"

  mkdir -p "$PROFILE_DIR"
  if ! compgen -G "$PROFILE_DIR/*.toml" >/dev/null; then
    cp "$SRC"/data/profiles/*.toml "$PROFILE_DIR/"
    say "example profiles copied to $PROFILE_DIR"
  else
    say "kept your existing profiles"
  fi
}

install_service() {
  mkdir -p "$UNIT_DIR"
  cat > "$UNIT_DIR/padlink.service" <<EOF
[Unit]
Description=padlink - DualSense/DS4 profiles, triggers, Xbox mode and battery monitor
After=graphical-session.target

[Service]
ExecStart=$BIN_DIR/padlink daemon -v
# if padlink ever dies while the pad is asleep in Xbox mode, wake it up
ExecStopPost=-/usr/bin/sudo -n $HELPER show-all
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF
  if [[ $SERVICE -eq 0 ]]; then say "service written but not enabled (--no-service)"; return; fi
  if systemctl --user show-environment >/dev/null 2>&1; then
    run systemctl --user daemon-reload
    run systemctl --user enable padlink.service
    run systemctl --user restart padlink.service
    say "background service running"
  else
    say "no user session bus here; the service starts at your next login"
    mkdir -p "$UNIT_DIR/default.target.wants"
    ln -sf "$UNIT_DIR/padlink.service" "$UNIT_DIR/default.target.wants/padlink.service"
  fi
}

install_extension() {
  if [[ $EXTENSION -eq 0 ]]; then say "skipped (--no-extension)"; return; fi
  if ! command -v gnome-shell >/dev/null; then say "not GNOME; skipped"; return; fi
  local dest="$DATA_HOME/gnome-shell/extensions/$EXT_UUID"
  rm -rf "$dest"; mkdir -p "$dest"
  cp "$SRC/gnome-extension/$EXT_UUID/"* "$dest/"
  set_extension_enabled true
  if gnome-extensions enable "$EXT_UUID" >> "$LOG" 2>&1; then
    say "battery indicator enabled in the top bar"
  else
    say "battery indicator installed; it appears in the top bar after you log out and back in"
  fi
}

check() {
  PYTHONPATH="$APP_DIR" /usr/bin/python3 -c 'import padlink.cli' >> "$LOG" 2>&1 || fail "padlink doesn't start; see the log."
  if PYTHONPATH="$APP_DIR" /usr/bin/python3 -c 'import gi; gi.require_version("Gtk","4.0"); gi.require_version("Adw","1"); from gi.repository import Gtk, Adw' >> "$LOG" 2>&1; then
    say "app: OK"
  else
    say "app: GTK4/libadwaita not importable — the command line and service still work"
  fi
  if /usr/bin/python3 -c 'import evdev' >> "$LOG" 2>&1; then say "Xbox mode: OK"; else say "Xbox mode: python-evdev missing"; fi
  case ":$PATH:" in *":$BIN_DIR:"*) ;; *) say "note: add $BIN_DIR to your PATH to use 'padlink' in a terminal";; esac
}

# ============================================================================================
if [[ $UNINSTALL -eq 1 ]]; then uninstall; exit 0; fi

echo "Installing padlink for $TARGET_USER…"
sudo_once
step "System packages";            install_packages
step "Controller access + helper"; install_system
step "Program";                    install_app
step "Background service";         install_service
step "Top-bar battery indicator";  install_extension
step "Checking";                   check
echo "✓ padlink installed. Open it from the app grid, or run: padlink gui"
echo "  If your controller was already connected, reconnect it once."
echo "  Log: $LOG"
