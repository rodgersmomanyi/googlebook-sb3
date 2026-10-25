#!/bin/sh
# Googlebook SB3 installer: tty1 log mirror while the UI runs on the serial console ttyS0 (DESIGN.md 3.4).
PATH=/usr/sbin:/usr/bin:/sbin:/bin
export PATH
echo "Googlebook SB3 installer: the menu is on the serial console (ttyS0)."
echo "This screen shows the installer log."
echo
while [ ! -f /run/gb/log/installer.log ]; do
    sleep 1
done
exec tail -n 40 -f /run/gb/log/installer.log
