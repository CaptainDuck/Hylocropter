"""
A second, https copy of the dashboard, so a phone will share its location.

Browsers only hand a page the device's position in a secure context -- https,
or localhost. The Pi serves plain http on its own hotspot, so the map's "this
device" location could never work there. This serves the same app on a second
port with a certificate the Pi makes for itself on first run.

It is self-signed, because there is no internet at the farm to get a real one,
so the browser warns the first time ("Your connection is not private") and you
choose to continue. After that the page is https and the location prompt
appears. The plain http address keeps working exactly as before; this is an
addition, not a replacement, so nothing already bookmarked breaks.

Needs the `openssl` command, which Raspberry Pi OS ships. If it is missing or
fails, the https copy simply does not start and the map says why.
"""

import logging
import subprocess
import threading
from pathlib import Path

log = logging.getLogger("hylocropter.tls")

DEFAULT_PORT = 5443

# Names the certificate claims. Self-signed means the browser warns whatever it
# says, but a matching name keeps the warning to the one about the issuer.
# 10.42.0.1 is where NetworkManager's own hotspot puts the Pi.
NAMES = ["DNS:hylocropter.local", "DNS:hylocropter", "DNS:localhost",
         "IP:127.0.0.1", "IP:10.42.0.1"]

# Phones refuse to trust certificates valid for longer than 825 days, even ones
# you install by hand, so stay under it.
VALID_DAYS = 825

_state = {"running": False, "port": None, "error": None}


def ensure_cert(tls_dir):
    """Return (cert, key) paths, making them once. Raises RuntimeError."""
    tls_dir = Path(tls_dir)
    cert, key = tls_dir / "cert.pem", tls_dir / "key.pem"
    if cert.exists() and key.exists():
        return cert, key
    tls_dir.mkdir(parents=True, exist_ok=True)
    cmd = ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
           "-days", str(VALID_DAYS), "-subj", "/CN=hylocropter",
           "-addext", "subjectAltName=" + ",".join(NAMES),
           "-keyout", str(key), "-out", str(cert)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
    except FileNotFoundError:
        raise RuntimeError("the openssl command is not installed")
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("openssl could not make a certificate: "
                           + (exc.stderr or b"").decode(errors="replace")[-200:])
    key.chmod(0o600)
    log.info("made a self-signed certificate in %s", tls_dir)
    return cert, key


def start(app, host, port, tls_dir):
    """Serve `app` over https on `port` in a background thread. Never raises."""
    if not port:
        _state.update(running=False, port=None, error="turned off")
        return status()
    try:
        cert, key = ensure_cert(tls_dir)
        from werkzeug.serving import make_server
        server = make_server(host, port, app, threaded=True,
                             ssl_context=(str(cert), str(key)))
    except Exception as exc:
        _state.update(running=False, port=None, error=str(exc))
        log.warning("https copy of the dashboard did not start: %s", exc)
        return status()
    threading.Thread(target=server.serve_forever, name="https",
                     daemon=True).start()
    _state.update(running=True, port=port, error=None)
    log.info("also serving https on port %s (for the phone's location)", port)
    return status()


def status():
    return dict(_state)
