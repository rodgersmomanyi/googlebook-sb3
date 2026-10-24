#!/system/bin/sh
# Network ADB for scrcpy (screen + audio on the Windows host): authorize the host's ADB public key (inject\adb\adb_keys,
# so no prompt), enable ADB, and listen on the VM's NAT address. lapis's adbd-killer limits adbd to localhost at boot;
# this runs after boot_completed and undoes that. Reachable only from the host through VMware NAT; key auth stays on.
exec >/dev/kmsg 2>&1
K=/data/misc/adb/adb_keys
if ! /system/bin/toybox cmp -s /odm/adb_keys $K 2>/dev/null; then
  /system/bin/toybox cat /odm/adb_keys > $K
  /system/bin/chown system:shell $K; /system/bin/chmod 0640 $K; /system/bin/restorecon $K 2>/dev/null
fi
/system/bin/settings put global development_settings_enabled 1
/system/bin/settings put global adb_enabled 1
/system/bin/setprop service.adb.listen_addrs tcp:5555
/system/bin/setprop ctl.restart adbd
echo "gb_adb: adbd on tcp:5555 ($(/system/bin/ip -4 addr show eth0 2>/dev/null | /system/bin/toybox grep -o 'inet [0-9.]*'))"
exit 0
