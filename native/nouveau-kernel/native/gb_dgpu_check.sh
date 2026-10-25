#!/system/bin/sh
# gb_dgpu_check.sh -- read-only snapshot of the SB3 dGPU (NVIDIA TU117) driver and power state. Changes nothing,
# never opens a DRM node, reads only cached sysfs status attributes (NVIDIA_PLAN.md invariant 12).
# Native lapis is a user build without su: run it as the shell user (adb shell sh /vendor/bin/gb_dgpu_check.sh);
# everything below is world-readable except dmesg (needs the log group, which shell has, or root). The same data,
# collected as root, is in /data/vendor/gb/report/dgpu-<N>.txt (gb_dgpu_report.sh) and in the gblog snapshots.
# Devices are found as in INTERFACES.md 2.2: the dGPU is the first PCI function with vendor 0x10de and class 0x03*,
# the root port is its parent (0000:00:1d.4 on the SB3 if no dGPU is present).
# Expected, base attached, idle (driverless default, or nouveau loaded and idle):
#   dGPU and root port: runtime=suspended state=D3cold; nouveau: renderD128 -> 0x8086 i915, renderD129 -> 0x10de nouveau
# Test hooks: GB_ROOT prefixes every absolute path; GB_TEST_PATH is prepended to PATH.

LC_ALL=C
export LC_ALL
PATH=${GB_TEST_PATH:+$GB_TEST_PATH:}/system/bin:/vendor/bin:$PATH
export PATH
R=${GB_ROOT:-}
set -f
PCI=$R/sys/bus/pci/devices

rd() { V=; [ -r "$1" ] && { IFS= read -r V < "$1" || [ -n "$V" ]; }; }

lsdir() {                                # LS = basenames in directory $1, sorted; the only glob
  LS=
  set +f
  for e in "$1"/*; do { [ -e "$e" ] || [ -L "$e" ]; } && LS="$LS ${e##*/}"; done
  set -f
}

st() {
  d=$PCI/$1
  [ -e "$d" ] || { echo "$1: absent (base detached, or not enumerated)"; return; }
  l=$(readlink "$d/driver" 2>/dev/null)
  rd "$d/vendor"; ve=$V; rd "$d/device"; de=$V; rd "$d/class"; cl=$V
  echo "$1: vendor=$ve device=$de class=$cl driver=${l##*/}"
  rd "$d/power/control"; co=$V; rd "$d/power/runtime_status"; rs=$V; rd "$d/power_state"; ps=$V
  rd "$d/d3cold_allowed"; da=$V; rd "$d/power/runtime_suspended_time"; su=$V; rd "$d/power/runtime_active_time"; ac=$V
  echo "    control=$co runtime=$rs state=$ps d3cold_allowed=$da suspended_ms=$su active_ms=$ac"
  if [ -e "$d/firmware_node/path" ]; then
    rd "$d/firmware_node/path"
    lsdir "$d/firmware_node/power_resources_D3hot"
    echo "    acpi=$V power_resources_D3hot=${LS# }"
  fi
}

NVF=
DGPU=
lsdir "$PCI"
for b in $LS; do
  rd "$PCI/$b/vendor"
  [ "$V" = 0x10de ] || continue
  NVF="$NVF $b"
  rd "$PCI/$b/class"
  case $V in 0x03*) [ -n "$DGPU" ] || DGPU=$b ;; esac
done
RP=0000:00:1d.4
if [ -n "$DGPU" ]; then
  p=$(readlink -f "$PCI/$DGPU" 2>/dev/null)
  p=${p%/*}
  case ${p##*/} in [0-9a-f][0-9a-f][0-9a-f][0-9a-f]:*) RP=${p##*/} ;; esac
fi

echo "== NVIDIA functions and root port"
[ -n "$NVF" ] || echo "no NVIDIA function present (base detached, or not enumerated)"
for b in $NVF; do st "$b"; done
st "$RP"
echo "== root port power resources (on = dGPU powered)"
lsdir "$PCI/$RP/firmware_node/power_resources_D3hot"
for r in $LS; do
  rd "$PCI/$RP/firmware_node/power_resources_D3hot/$r/resource_in_use" && echo "    $r: resource_in_use=$V"
done
echo "== nouveau"
if [ -d "$R/sys/module/nouveau" ]; then
  rd "$R/sys/module/nouveau/refcnt"; echo "    refcnt=$V"
  for p in runpm modeset config debug; do rd "$R/sys/module/nouveau/parameters/$p"; echo "    nouveau.$p=$V"; done
else
  echo "    not loaded"
fi
echo "== DRM nodes (minigbm takes the first render node it can drive; i915 must own renderD128/card0)"
lsdir "$R/sys/class/drm"
for n in $LS; do
  case $n in card[0-9]* | renderD[0-9]*) ;; *) continue ;; esac
  case $n in *-*) continue ;; esac
  [ -e "$R/sys/class/drm/$n/device" ] || continue
  rd "$R/sys/class/drm/$n/device/vendor"
  l=$(readlink "$R/sys/class/drm/$n/device/driver" 2>/dev/null)
  echo "    $n -> $V ${l##*/}"
done
echo "== policy properties"
for p in persist.vendor.gb.gfx_mode persist.vendor.gb.gfx_apps vendor.gb.gfx_active vendor.gb.gfx_reason \
  vendor.gb.gfx_test persist.vendor.gb.gfx_ok vendor.gb.nouveau.mode vendor.gb.nouveau.result vendor.gb.nouveau.clean \
  vendor.gb.dgpu.state vendor.gb.dgpu.payload; do
  echo "    $p=$(getprop "$p" 2>/dev/null)"
done
echo "== recent kernel messages"
dmesg 2>/dev/null | grep -iE "nouveau|gb_power: (dGPU|detach)|pcieport|surface_hotplug|shps|${DGPU:-02:00.0}" | tail -n 40
exit 0
