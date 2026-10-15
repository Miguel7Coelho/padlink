# Security

## What runs with elevated rights

padlink itself always runs as **your user**. Exactly one component runs as root:

**`/usr/local/libexec/padlink-hide`**: used by Xbox mode to put the real controller to sleep. It
is installed with a sudoers rule (`/etc/sudoers.d/padlink`) that lets **only the installing user**
run **only this file** without a password:

```text
<you> ALL=(root) NOPASSWD: /usr/local/libexec/padlink-hide
```

What the helper does, and its limits:

- It accepts only node names matching `hidrawN`, `eventN` or `jsN`, never paths.
- It acts only on nodes whose sysfs device belongs to a Sony controller (vendor `054C`, products
  `05C4`, `09CC`, `0BA0`, `0CE6`, `0DF2`). Anything else is refused.
- `hide` changes the node's permissions to `0600` (root only) and removes ACLs. `show` restores
  the saved mode and asks udev to re-apply the normal access rules.
- `show` only restores nodes that `hide` changed. It records them in `/run/padlink/hidden.json`
  (root-only, cleared at reboot).
- It never deletes, writes to or reads from a device, and it runs Python in isolated mode
  (`python3 -I`), so environment variables and the working directory can't inject code. Under
  sudo, test-only path overrides are ignored.

The source is short: [`packaging/padlink-hide`](packaging/padlink-hide). The installer also adds a
udev rule that gives the logged-in user (`uaccess`) access to Sony controller `hidraw` nodes and
`/dev/uinput`. Any program you run can then create virtual input devices, the same as with Steam's
own udev rules.

To remove all of it: `./install.sh --uninstall`.

## Reporting a vulnerability

Please **don't** open a public issue for security problems. Use
[GitHub private vulnerability reporting](https://github.com/Miguel7Coelho/padlink/security/advisories/new)
instead. You'll get a reply within a week.
