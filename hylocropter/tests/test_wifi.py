"""The Wi-Fi recovery script (`deploy/hylocropter_wifi.py`).

It exists because a power cut emptied the Pi's only saved network and the farm
has no monitor. So these pin the parts that keep that from recurring: the file
someone edits on a Mac parses forgivingly, a bad file never wipes the working
profiles, and only our own profiles are ever touched.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "deploy"))
import hylocropter_wifi as wifi  # noqa: E402


def test_two_networks_and_hotspot_settings():
    cfg = wifi.parse(
        "# comment\n"
        "ssid=Galaxy S25\npassword=hunter2hunter2\n"
        "ssid=Farm Office\npassword=\n"
        "hotspot_ssid=Hylo\nhotspot_after=90\n")
    assert cfg["networks"] == [("Galaxy S25", "hunter2hunter2"),
                               ("Farm Office", "")]
    assert cfg["hotspot_ssid"] == "Hylo"
    assert cfg["hotspot_after"] == 90
    assert cfg["warnings"] == []


def test_mac_quirks_bom_and_crlf():
    cfg = wifi.parse("﻿ssid=Galaxy S25\r\npassword=hunter2hunter2\r\n")
    assert cfg["networks"] == [("Galaxy S25", "hunter2hunter2")]


def test_rich_text_is_refused_not_acted_on():
    with pytest.raises(wifi.ConfigError):
        wifi.parse("{\\rtf1\\ansi ssid=Galaxy S25}")


def test_short_password_skips_that_network_only():
    cfg = wifi.parse("ssid=A\npassword=short\nssid=B\npassword=longenough\n")
    assert cfg["networks"] == [("B", "longenough")]
    assert any("'A'" in w for w in cfg["warnings"])


def test_defaults_give_an_open_hotspot():
    cfg = wifi.parse("")
    assert cfg["networks"] == []
    assert cfg["hotspot_ssid"] == "Hylocropter"
    assert cfg["hotspot_password"] == ""
    assert "[wifi-security]" not in wifi.desired(cfg)[wifi.HOTSPOT_FILE]


def test_hotspot_after_is_clamped():
    assert wifi.parse("hotspot_after=1")["hotspot_after"] == 20
    assert wifi.parse("hotspot_after=99999")["hotspot_after"] == 600


def test_hotspot_never_autoconnects():
    # If it did, it would race the phone hotspot for wlan0 at every boot.
    text = wifi.hotspot_profile("Hylocropter", "")
    assert "autoconnect=false" in text
    assert "mode=ap" in text and "method=shared" in text


def test_client_profile_escapes_and_is_stable():
    text = wifi.client_profile(" lead\\slash", "hunter2hunter2")
    assert "ssid=\\slead\\\\slash" in text
    assert "psk=hunter2hunter2" in text
    # Same SSID → same file and UUID, so a reboot rewrites rather than duplicates.
    assert wifi.client_filename("Galaxy S25") == wifi.client_filename("Galaxy S25")
    assert text == wifi.client_profile(" lead\\slash", "hunter2hunter2")


def test_sync_rewrites_an_emptied_profile(tmp_path):
    cfg = wifi.parse("ssid=Galaxy S25\npassword=hunter2hunter2\n")
    wifi.sync_profiles(cfg, tmp_path)
    name = wifi.client_filename("Galaxy S25")
    (tmp_path / name).write_text("")             # what the power cut did
    changes = wifi.sync_profiles(cfg, tmp_path)
    assert changes == [f"wrote {name}"]
    assert "psk=hunter2hunter2" in (tmp_path / name).read_text()
    assert (tmp_path / name).stat().st_mode & 0o777 == 0o600


def test_sync_is_a_no_op_when_nothing_changed(tmp_path):
    cfg = wifi.parse("ssid=Galaxy S25\npassword=hunter2hunter2\n")
    wifi.sync_profiles(cfg, tmp_path)
    assert wifi.sync_profiles(cfg, tmp_path) == []


def test_sync_leaves_other_profiles_alone(tmp_path):
    desktop = tmp_path / "Galaxy S25.nmconnection"   # made from the desktop
    desktop.write_text("[connection]\n")
    wifi.sync_profiles(wifi.parse("ssid=Old\npassword=hunter2hunter2\n"), tmp_path)
    old = wifi.client_filename("Old")
    changes = wifi.sync_profiles(wifi.parse("ssid=New\npassword=hunter2hunter2\n"),
                                 tmp_path)
    assert f"removed {old}" in changes
    assert desktop.exists()
    assert not list(tmp_path.glob(".*.tmp"))
