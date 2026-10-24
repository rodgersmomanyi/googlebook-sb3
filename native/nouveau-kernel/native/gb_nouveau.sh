#!/system/bin/sh
# gb_nouveau.sh -- OPT-IN loader for nouveau on the Surface Book 3 dGPU (NVIDIA TU117, 0000:02:00.0), native only.
# Started by gb_nouveau.rc after sys.boot_completed=1 when persist.vendor.gb.nouveau=1 (default: not loaded; then
# gb_power keeps the dGPU driverless and runtime-suspended = D3cold, which is the battery-optimal state).
#
# Why after boot_completed: i915 must own renderD128/card0 (minigbm gralloc takes the FIRST render node it can drive,
# and lapis' minigbm has a nouveau backend), and nouveau's GSP-RM probe (28.5 MB firmware load + GSP boot) must not
# delay boot.
# Params: runpm=1 is REQUIRED on the SB3 (ACPI evidence: the dGPU's Optimus _DSM does not advertise function 0x1A and
# CONFIG_VGA_SWITCHEROO is off, so runpm=-1 would leave the GPU powered); modeset=2 = headless (no KMS, no connectors,
# no backlight, no HPD polling: drm_hwcomposer skips the device, i915 stays the only display GPU).
# Extra args: persist.vendor.gb.nouveau.args (e.g. "config=NvGspRm=0" for the non-GSP firmware path).
# "gb_nouveau.sh off" (persist.vendor.gb.nouveau=0 at runtime): unbind + unload nouveau and give the dGPU back to the
# driverless runtime PM (nouveau's teardown leaves power/control=on, which would keep the GPU powered).
exec >/dev/kmsg 2>&1
D=/odm/lib/modules
P=0000:02:00.0
RP=0000:00:1d.4
if [ "$1" = off ]; then
  [ -d /sys/module/nouveau ] || exit 0
  for f in /sys/bus/pci/drivers/nouveau/0000:*; do [ -e "$f" ] && echo "${f##*/}" > /sys/bus/pci/drivers/nouveau/unbind; done
  /system/bin/rmmod nouveau && echo "gb_nouveau: nouveau unloaded" || echo "gb_nouveau: rmmod nouveau failed"
  for f in /sys/bus/pci/devices/*; do
    [ "$(cat $f/vendor 2>/dev/null)" = 0x10de ] && [ ! -e $f/driver ] && echo auto > $f/power/control
  done
  exit 0
fi
if [ -d /sys/module/nouveau ]; then echo "gb_nouveau: nouveau already loaded"; exit 0; fi
# nouveau.ko depends (modinfo): i2c-algo-bit,wmi,video,drm_ttm_helper,drm_gpuvm,drm_exec,gpu-sched,drm_display_helper.
# i2c-algo-bit, wmi, video, drm_display_helper are already in modules.load (i915). mxm-wmi.ko is built (Kconfig
# select) but nouveau only calls it with CONFIG_VGA_SWITCHEROO=y, so it is not loaded.
for m in drm_exec drm_gpuvm gpu-sched drm_ttm_helper; do
  n=$(echo $m | /system/bin/tr - _)
  [ -d /sys/module/$n ] && continue
  /system/bin/insmod $D/$m.ko || echo "gb_nouveau: insmod $m.ko failed"
done
ARGS="runpm=1 modeset=2 $(getprop persist.vendor.gb.nouveau.args)"
[ -e /sys/bus/pci/devices/$P ] || echo "gb_nouveau: $P absent (base detached?); nouveau binds when the base is re-attached"
# shellcheck disable=SC2086
if /system/bin/insmod $D/nouveau.ko $ARGS; then echo "gb_nouveau: nouveau loaded ($ARGS)"
else echo "gb_nouveau: insmod nouveau.ko failed ($ARGS)"; exit 1; fi
# report once the 5 s autosuspend delay has passed (expect: driver=nouveau control=auto runtime=suspended D3cold)
sleep 10
st() { d=/sys/bus/pci/devices/$1; [ -e $d ] || { echo "$1 absent"; return; }
  echo "$1 driver=$(basename "$(readlink $d/driver)" 2>/dev/null) control=$(cat $d/power/control) runtime=$(cat $d/power/runtime_status) state=$(cat $d/power_state)"; }
echo "gb_nouveau: dGPU $(st $P) | root port $(st $RP)"
for r in /sys/class/drm/renderD*; do
  [ -e $r/device/vendor ] && echo "gb_nouveau: ${r##*/} -> $(cat $r/device/vendor) $(basename "$(readlink $r/device/driver)")"
done
