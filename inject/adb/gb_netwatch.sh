#!/system/bin/sh
# VM network watchdog: Android's Ethernet stack occasionally drops eth0 (VMware NAT, vmxnet3) and then keeps sending
# unanswered DHCPDISCOVERs; cycling the link makes it provision again (DHCP OFFER/ACK at once). If eth0 has carrier
# but no IPv4 address for ~30 s, cycle it. Checks every 10 s; cheap.
exec >/dev/kmsg 2>&1
miss=0
while :; do
  /system/bin/sleep 10
  [ "$(cat /sys/class/net/eth0/carrier 2>/dev/null)" = 1 ] || { miss=0; continue; }
  if /system/bin/ip -4 addr show eth0 2>/dev/null | /system/bin/toybox grep -q 'inet '; then miss=0; continue; fi
  miss=$((miss + 1))
  if [ $miss -ge 3 ]; then
    echo "gb_netwatch: eth0 has carrier but no IPv4 for 30 s, cycling the link"
    /system/bin/ip link set eth0 down; /system/bin/sleep 2; /system/bin/ip link set eth0 up
    miss=0
    /system/bin/sleep 20
  fi
done
