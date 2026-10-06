#!/usr/bin/python3
"""
Wi-Fi you can fix from a Mac, and a hotspot for when it fails anyway.

Two problems this exists for, both met in the field:

* **The saved network vanished.** NetworkManager keeps each Wi-Fi network in one
  file. Pull the battery a few seconds after that file is rewritten and ext4 can
  leave it at 0 bytes -- the Pi then boots with no network to join, and the only
  way back was a monitor and keyboard, which nobody has at the farm.
* **There was no way in without that network.** No phone hotspot, no dashboard.

So the networks live in a plain text file on the boot partition --
`/boot/firmware/hylocropter-wifi.txt`, the FAT one any computer can open, the
same place Raspberry Pi Imager writes to. Every boot, `apply` turns it into
NetworkManager profiles *before* NetworkManager starts, writing them with
fsync + rename so a power cut leaves the old file or the new one, never an empty
one. A wiped profile is simply rewritten on the next boot.

And `watch` runs for as long as the Pi is up: if `wlan0` has not been connected to
anything for `hotspot_after` seconds, it brings up the Pi's own open hotspot, so a
phone can always reach the dashboard at http://10.42.0.1:5000 and SSH. Once the
hotspot is up it stays up until reboot -- switching back on its own would kick off
whoever just joined it.

Stdlib only and outside the app on purpose: a Wi-Fi problem must not stop the
dashboard starting, and a dashboard crash must not take the Wi-Fi with it. Runs as
root (see `install-wifi.sh`); the app itself never touches network config, per
DEPLOYMENT.md §3.
"""

import argparse
import hashlib
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

CONFIG = Path("/boot/firmware/hylocropter-wifi.txt")
PROFILES = Path("/etc/NetworkManager/system-connections")
PREFIX = "hylocropter-wifi-"          # every file we own starts with this
HOTSPOT_FILE = "hylocropter-hotspot.nmconnection"
HOTSPOT_ID = "Hylocropter hotspot"
IFACE = "wlan0"
NAMESPACE = uuid.UUID("5b1f4c3e-2f0a-4a7e-9a52-6c1b8e7d0f11")

DEFAULT_HOTSPOT_SSID = "Hylocropter"
DEFAULT_HOTSPOT_AFTER = 60
HOTSPOT_AFTER_RANGE = (20, 600)

TEMPLATE = """\
# Hylocropter Wi-Fi. Edit this on any computer; it takes effect on the next boot.
# Save as PLAIN TEXT (in TextEdit: Format > Make Plain Text).
#
# Networks the Pi should join, in any order. Each `ssid=` starts a new one and
# the `password=` under it belongs to it. Leave the password empty for an open
# network. The phone hotspot must be WPA2 (or WPA2/WPA3) on 2.4 GHz.
{networks}
# If none of the networks above is reachable for this many seconds, the Pi
# starts its own Wi-Fi network instead. Join it and open http://10.42.0.1:5000
# It stays on until the next reboot.
hotspot_ssid={hotspot_ssid}
# Empty = open network (no password). Otherwise at least 8 characters.
hotspot_password=
hotspot_after={hotspot_after}
"""


def log(msg):
    print(msg, flush=True)


# --------------------------------------------------------------------- parsing

class ConfigError(ValueError):
    pass


def parse(text):
    """The config file → {"networks": [(ssid, password)], "hotspot_*": ...}.

    Raises ConfigError for a file that is clearly not ours (RTF from TextEdit),
    so the caller can leave the existing profiles alone rather than act on
    garbage. A single bad network is skipped with a warning, not fatal.
    """
    text = text.lstrip("\ufeff")
    if text.lstrip().startswith("{\\rtf"):
        raise ConfigError("saved as rich text; re-save it as plain text")

    cfg = {"networks": [], "warnings": [],
           "hotspot_ssid": DEFAULT_HOTSPOT_SSID, "hotspot_password": "",
           "hotspot_after": DEFAULT_HOTSPOT_AFTER}
    current = None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            cfg["warnings"].append(f"line {n}: no '=', ignored")
            continue
        key, value = (s.strip() for s in line.split("=", 1))
        key = key.lower()
        if key == "ssid":
            current = [value, ""]
            cfg["networks"].append(current)
        elif key == "password":
            if current is None:
                cfg["warnings"].append(f"line {n}: password before any ssid, ignored")
            else:
                current[1] = value
        elif key == "hotspot_ssid":
            cfg["hotspot_ssid"] = value
        elif key == "hotspot_password":
            cfg["hotspot_password"] = value
        elif key == "hotspot_after":
            try:
                lo, hi = HOTSPOT_AFTER_RANGE
                cfg["hotspot_after"] = min(hi, max(lo, int(value)))
            except ValueError:
                cfg["warnings"].append(f"line {n}: hotspot_after is not a number")
        else:
            cfg["warnings"].append(f"line {n}: unknown setting '{key}', ignored")

    good = []
    for ssid, password in cfg["networks"]:
        problem = check(ssid, password)
        if problem:
            cfg["warnings"].append(f"network '{ssid}': {problem}, skipped")
        else:
            good.append((ssid, password))
    cfg["networks"] = good

    if cfg["hotspot_ssid"]:
        problem = check(cfg["hotspot_ssid"], cfg["hotspot_password"])
        if problem:
            cfg["warnings"].append(f"hotspot: {problem}; using an open hotspot")
            cfg["hotspot_password"] = ""
    return cfg


def check(ssid, password):
    if not ssid:
        return "empty ssid"
    if len(ssid.encode()) > 32:
        return "ssid longer than 32 bytes"
    if password and not (8 <= len(password) <= 63
                         or re.fullmatch(r"[0-9a-fA-F]{64}", password)):
        return "password must be 8-63 characters"
    return None


# ------------------------------------------------------------------- rendering

def esc(value):
    """GKeyFile string escaping, which NetworkManager's keyfiles use."""
    out = (value.replace("\\", "\\\\").replace("\n", "\\n")
                .replace("\t", "\\t").replace("\r", "\\r"))
    if out.startswith(" "):
        out = "\\s" + out[1:]
    return out


def client_filename(ssid):
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ssid).strip("-").lower()[:24] or "net"
    digest = hashlib.sha1(ssid.encode()).hexdigest()[:6]
    return f"{PREFIX}{slug}-{digest}.nmconnection"


def client_profile(ssid, password):
    lines = [
        "[connection]",
        f"id={esc('Wi-Fi: ' + ssid)}",
        f"uuid={uuid.uuid5(NAMESPACE, 'client:' + ssid)}",
        "type=wifi",
        f"interface-name={IFACE}",
        "autoconnect=true",
        "autoconnect-priority=10",
        # Keep retrying: a phone hotspot that comes up after the Pi must still
        # be joined without a reboot.
        "autoconnect-retries=0",
        "",
        "[wifi]",
        "mode=infrastructure",
        f"ssid={esc(ssid)}",
        "",
    ]
    if password:
        lines += ["[wifi-security]", "key-mgmt=wpa-psk",
                  f"psk={esc(password)}", ""]
    lines += ["[ipv4]", "method=auto", "", "[ipv6]",
              "addr-gen-mode=default", "method=auto", ""]
    return "\n".join(lines)


def hotspot_profile(ssid, password):
    lines = [
        "[connection]",
        f"id={HOTSPOT_ID}",
        f"uuid={uuid.uuid5(NAMESPACE, 'hotspot')}",
        "type=wifi",
        f"interface-name={IFACE}",
        # Never on its own: `watch` decides. Otherwise it would fight the
        # client profiles for wlan0 at every boot.
        "autoconnect=false",
        "",
        "[wifi]",
        "mode=ap",
        "band=bg",
        f"ssid={esc(ssid)}",
        "",
    ]
    if password:
        lines += ["[wifi-security]", "key-mgmt=wpa-psk",
                  f"psk={esc(password)}", ""]
    lines += ["[ipv4]", "method=shared", "", "[ipv6]", "method=disabled", ""]
    return "\n".join(lines)


def desired(cfg):
    """{filename: contents} for every profile this config should produce."""
    want = {client_filename(s): client_profile(s, p) for s, p in cfg["networks"]}
    if cfg["hotspot_ssid"]:
        want[HOTSPOT_FILE] = hotspot_profile(cfg["hotspot_ssid"],
                                             cfg["hotspot_password"])
    return want


# --------------------------------------------------------------------- writing

def write_atomic(path, text):
    """Temp file, fsync, rename, fsync the directory. Returns True if changed.

    This is the whole fix for the 0-byte profile: after a power cut the file is
    either the old version or the new one.
    """
    data = text.encode()
    try:
        if path.read_bytes() == data:
            return False
    except FileNotFoundError:
        pass
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    try:
        os.chmod(tmp, 0o600)    # NetworkManager ignores keyfiles others can read
    except PermissionError:
        pass                    # FAT (the boot partition) has no modes to set
    os.replace(tmp, path)
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    return True


def sync_profiles(cfg, directory):
    """Make `directory` hold exactly the profiles `cfg` describes.

    Only touches files named PREFIX* or HOTSPOT_FILE -- networks added by hand
    or from the desktop are left alone. Returns the list of changes.
    """
    directory.mkdir(parents=True, exist_ok=True)
    want = desired(cfg)
    changes = []
    for name, text in want.items():
        if write_atomic(directory / name, text):
            changes.append(f"wrote {name}")
    for path in directory.iterdir():
        ours = path.name.startswith(PREFIX) or path.name == HOTSPOT_FILE
        if ours and path.name not in want:
            path.unlink()
            changes.append(f"removed {path.name}")
    return changes


# ---------------------------------------------------------------- NetworkManager

def nmcli(*args, check=False):
    return subprocess.run(["nmcli", *args], capture_output=True, text=True,
                          timeout=30, check=check)


def nm_running():
    try:
        return nmcli("-t", "-f", "RUNNING", "general").stdout.strip() == "running"
    except (OSError, subprocess.SubprocessError):
        return False


def wlan_state():
    """(state, connection) for wlan0, e.g. ("connected", "Wi-Fi: Galaxy S25")."""
    out = nmcli("-t", "-f", "DEVICE,STATE,CONNECTION", "device").stdout
    for line in out.splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[0] == IFACE:
            return parts[1], parts[2]
    return "missing", ""


# -------------------------------------------------------------------- commands

def load_config():
    """The parsed config, or None if missing/unusable (logged)."""
    try:
        text = CONFIG.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        log(f"{CONFIG} not found; leaving Wi-Fi networks as they are")
        return None
    try:
        cfg = parse(text)
    except ConfigError as e:
        log(f"{CONFIG}: {e}; leaving Wi-Fi networks as they are")
        return None
    for w in cfg["warnings"]:
        log(f"{CONFIG}: {w}")
    return cfg


def cmd_apply(_args):
    cfg = load_config()
    if cfg is None:
        # Still make sure the fallback hotspot exists: being reachable is the
        # point, and it needs nothing from the config.
        cfg = parse("")
    changes = sync_profiles(cfg, PROFILES)
    for c in changes:
        log(c)
    log(f"{len(cfg['networks'])} network(s) configured; "
        f"hotspot {cfg['hotspot_ssid'] or 'off'}")
    if changes and nm_running():
        nmcli("connection", "reload")
    return 0


def cmd_watch(_args):
    cfg = load_config() or parse("")
    if not cfg["hotspot_ssid"]:
        log("hotspot disabled (hotspot_ssid is empty); nothing to watch")
        return 0
    grace = cfg["hotspot_after"]
    log(f"watching {IFACE}: hotspot '{cfg['hotspot_ssid']}' after {grace}s "
        "without a connection")
    since = time.monotonic()
    while True:
        try:
            state, conn = wlan_state()
        except (OSError, subprocess.SubprocessError) as e:
            log(f"nmcli failed: {e}")
            state, conn = "unknown", ""
        now = time.monotonic()
        if conn == HOTSPOT_ID:
            since = None                      # we're the hotspot; stay put
        elif state.startswith("connected"):
            since = None
        elif since is None:
            since = now
            log(f"{IFACE} lost its connection ({state})")
        elif now - since >= grace:
            log(f"no network for {int(now - since)}s; starting hotspot "
                f"'{cfg['hotspot_ssid']}' (http://10.42.0.1:5000)")
            r = nmcli("connection", "up", "id", HOTSPOT_ID)
            if r.returncode != 0:
                log(f"hotspot failed: {(r.stderr or r.stdout).strip()}")
            since = now                       # retry after another grace period
        time.sleep(5)


def cmd_init(args):
    """Write the config file once, seeded from the network wlan0 is on now."""
    if CONFIG.exists() and not args.force:
        log(f"{CONFIG} already exists; not touching it (use --force)")
        return 0
    networks = []
    _, conn = wlan_state() if nm_running() else ("", "")
    if conn and conn != HOTSPOT_ID:
        r = nmcli("-s", "-g", "802-11-wireless.ssid,802-11-wireless-security.psk",
                  "connection", "show", "id", conn)
        vals = r.stdout.splitlines()
        if r.returncode == 0 and vals and vals[0]:
            networks.append((vals[0], vals[1] if len(vals) > 1 else ""))
    block = "".join(f"ssid={s}\npassword={p}\n" for s, p in networks) or \
        "ssid=\npassword=\n"
    text = TEMPLATE.format(networks=block, hotspot_ssid=DEFAULT_HOTSPOT_SSID,
                           hotspot_after=DEFAULT_HOTSPOT_AFTER)
    write_atomic(CONFIG, text)
    log(f"wrote {CONFIG}" + (f" with '{networks[0][0]}'" if networks else ""))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("apply", help="config file -> NetworkManager profiles")
    sub.add_parser("watch", help="start the hotspot when wlan0 stays offline")
    p = sub.add_parser("init", help="create the config file from the current network")
    p.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    return {"apply": cmd_apply, "watch": cmd_watch, "init": cmd_init}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
