"""The https copy of the dashboard, which exists so a phone will share its
location with the map (browsers refuse to on plain http)."""

import shutil
import ssl
import urllib.request

import pytest

import tls

needs_openssl = pytest.mark.skipif(shutil.which("openssl") is None,
                                   reason="openssl not installed")


@needs_openssl
def test_a_certificate_is_made_once_and_then_reused(tmp_path):
    cert, key = tls.ensure_cert(tmp_path)
    assert cert.exists() and key.exists()
    assert oct(key.stat().st_mode & 0o777) == "0o600", "the key is private"
    first = cert.read_bytes()
    tls.ensure_cert(tmp_path)
    assert cert.read_bytes() == first, "reused, not remade on every start"


@needs_openssl
def test_the_dashboard_answers_over_https(tmp_path):
    from flask import Flask
    app = Flask("t")
    app.add_url_rule("/", "i", lambda: "hello")
    state = tls.start(app, "127.0.0.1", 0 or _free_port(), tmp_path)
    assert state["running"], state
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE        # self-signed, as a phone would see it
    body = urllib.request.urlopen(f"https://127.0.0.1:{state['port']}/",
                                  context=ctx, timeout=5).read()
    assert body == b"hello"


def test_a_missing_openssl_is_reported_not_raised(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("openssl")
    monkeypatch.setattr(tls.subprocess, "run", boom)
    state = tls.start(object(), "127.0.0.1", 5999, tmp_path / "none")
    assert not state["running"]
    assert "openssl" in state["error"]


def test_port_zero_turns_it_off(tmp_path):
    state = tls.start(object(), "127.0.0.1", 0, tmp_path)
    assert not state["running"] and state["error"] == "turned off"


def _free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
