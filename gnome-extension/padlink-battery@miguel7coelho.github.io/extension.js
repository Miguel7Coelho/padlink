// padlink battery: DualSense / DualShock 4 battery level in the GNOME top bar.
//
// Reads the kernel's power_supply nodes (the same data padlink uses), so it works whether or not
// padlink's background service is running. Shows the lowest battery when several controllers are
// connected, turns red below the "low" threshold (25% by default, or [battery] low_below in
// ~/.config/padlink/settings.toml) and hides itself when no controller is connected.

import GLib from 'gi://GLib';
import Gio from 'gi://Gio';
import GObject from 'gi://GObject';
import St from 'gi://St';
import Clutter from 'gi://Clutter';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';

const POWER_SUPPLY = '/sys/class/power_supply';
// hid-playstation names its batteries "ps-controller-battery-<mac>"; older hid-sony uses "sony_controller_battery_<mac>"
const PREFIXES = ['ps-controller-battery-', 'sony_controller_battery_'];
const DEFAULT_LOW = 25;
const REFRESH_SECONDS = 10;
const APP_ID = 'io.github.miguel7coelho.padlink.desktop';

function readText(path) {
    try {
        const [ok, bytes] = GLib.file_get_contents(path);
        return ok ? new TextDecoder().decode(bytes).trim() : null;
    } catch (e) {
        return null;
    }
}

function lowThreshold() {
    const text = readText(GLib.build_filenamev([GLib.get_user_config_dir(), 'padlink', 'settings.toml']));
    const m = text ? text.match(/^\s*low_below\s*=\s*(\d+)/m) : null;
    return m ? Math.min(100, parseInt(m[1], 10)) : DEFAULT_LOW;
}

function modelName(dir) {
    const uevent = readText(GLib.build_filenamev([dir, 'device', 'uevent'])) ?? '';
    const id = (uevent.match(/^HID_ID=.*$/m) ?? [''])[0].toUpperCase();
    if (id.includes('0DF2'))
        return 'DualSense Edge';
    if (id.includes('0CE6'))
        return 'DualSense';
    return 'DualShock 4';
}

function controllers() {
    const found = [];
    let enumerator;
    try {
        enumerator = Gio.File.new_for_path(POWER_SUPPLY).enumerate_children(
            'standard::name', Gio.FileQueryInfoFlags.NONE, null);
    } catch (e) {
        return found;
    }
    let info;
    while ((info = enumerator.next_file(null)) !== null) {
        const name = info.get_name();
        if (!PREFIXES.some(p => name.startsWith(p)))
            continue;
        const dir = GLib.build_filenamev([POWER_SUPPLY, name]);
        const percent = parseInt(readText(GLib.build_filenamev([dir, 'capacity'])) ?? '', 10);
        if (Number.isNaN(percent))
            continue;
        const status = readText(GLib.build_filenamev([dir, 'status'])) ?? 'Unknown';
        found.push({
            model: modelName(dir),
            percent,
            status,
            charging: status === 'Charging' || status === 'Full',
        });
    }
    enumerator.close(null);
    return found;
}

const BatteryIndicator = GObject.registerClass(
class BatteryIndicator extends PanelMenu.Button {
    _init() {
        super._init(0.5, 'padlink battery');

        const box = new St.BoxLayout({style_class: 'panel-status-indicators-box'});
        this._icon = new St.Icon({icon_name: 'input-gaming-symbolic', style_class: 'system-status-icon'});
        this._label = new St.Label({text: '', y_align: Clutter.ActorAlign.CENTER, style_class: 'padlink-label'});
        box.add_child(this._icon);
        box.add_child(this._label);
        this.add_child(box);

        this._section = new PopupMenu.PopupMenuSection();
        this.menu.addMenuItem(this._section);
        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());
        const open = new PopupMenu.PopupMenuItem('Open padlink');
        open.connect('activate', () => {
            const app = Gio.DesktopAppInfo.new(APP_ID);
            if (app)
                app.launch([], null);
        });
        this.menu.addMenuItem(open);

        this.refresh();
    }

    refresh() {
        const list = controllers();
        this.visible = list.length > 0;
        if (!this.visible)
            return;

        const low = lowThreshold();
        const worst = list.reduce((a, b) => (b.percent < a.percent ? b : a));
        this._label.text = `${worst.percent}%${worst.charging ? ' ⚡' : ''}`;
        const isLow = !worst.charging && worst.percent < low;
        if (isLow) {
            this._label.add_style_class_name('padlink-low');
            this._icon.add_style_class_name('padlink-low');
        } else {
            this._label.remove_style_class_name('padlink-low');
            this._icon.remove_style_class_name('padlink-low');
        }

        this._section.removeAll();
        for (const c of list) {
            const item = new PopupMenu.PopupMenuItem(
                `${c.model}: ${c.percent}% (${c.status.toLowerCase()})`, {reactive: false});
            if (!c.charging && c.percent < low)
                item.label.add_style_class_name('padlink-low');
            this._section.addMenuItem(item);
        }
    }
});

export default class PadlinkBatteryExtension extends Extension {
    enable() {
        this._indicator = new BatteryIndicator();
        Main.panel.addToStatusArea(this.uuid, this._indicator);
        this._timer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, REFRESH_SECONDS, () => {
            this._indicator?.refresh();
            return GLib.SOURCE_CONTINUE;
        });
    }

    disable() {
        if (this._timer) {
            GLib.source_remove(this._timer);
            this._timer = null;
        }
        this._indicator?.destroy();
        this._indicator = null;
    }
}
