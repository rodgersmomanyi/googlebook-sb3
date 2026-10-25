#!/bin/sh
# Googlebook SB3 installer: UI entry started by busybox init (DESIGN.md 3.4).
#   gbinstall.sh --console tty1|ttyS0            the text UI on that console (respawned by init)
#   gbinstall.sh --shutdown cleanup|reboot|poweroff
PATH=/usr/sbin:/usr/bin:/sbin:/bin
LANG=C
export PATH LANG
if [ "$1" = "--console" ] && [ "$2" = "ttyS0" ]; then
    stty 115200 cs8 -parenb -cstopb -crtscts icrnl onlcr
fi
exec /usr/bin/python3 -I /opt/gb/lib/gbinstall.py "$@"
