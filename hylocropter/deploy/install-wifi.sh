#!/bin/sh
# Install the Wi-Fi recovery services. Run on the Pi:  sudo sh install-wifi.sh
# Safe to re-run; it never overwrites an existing hylocropter-wifi.txt.
set -eu
here=$(dirname "$(readlink -f "$0")")

install -m 755 "$here/hylocropter_wifi.py" /usr/local/sbin/hylocropter-wifi
install -m 644 "$here/hylocropter-wifi.service" \
               "$here/hylocropter-wifi-fallback.service" /etc/systemd/system/

/usr/local/sbin/hylocropter-wifi init      # seeds from the current network
/usr/local/sbin/hylocropter-wifi apply     # profiles now, not just next boot

systemctl daemon-reload
systemctl enable hylocropter-wifi.service
systemctl enable --now hylocropter-wifi-fallback.service
sync
echo "Installed. Edit /boot/firmware/hylocropter-wifi.txt to change networks."
