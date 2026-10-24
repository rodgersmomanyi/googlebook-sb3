#!/system/bin/sh
# gb_dgpu_check.sh -- read-only hardware check of the SB3 dGPU power state, to run natively as root
# (adb shell su 0 sh /vendor/bin/gb_dgpu_check.sh, or from the serial/VT shell). Changes nothing.
# Expected, base attached, idle, both modes (driverless default or nouveau opt-in):
#   0000:02:00.0  runtime=suspended state=D3cold      0000:00:1d.4  runtime=suspended state=D3cold
# nouveau opt-in additionally: driver=nouveau control=auto, renderD128 -> 0x8086 i915, renderD129 -> 0x10de nouveau.
P=0000:02:00.0
RP=0000:00:1d.4
st() {
  d=/sys/bus/pci/devices/$1
  [ -e $d ] || { echo "$1: absent (base detached, or dGPU not enumerated)"; return; }
  drv=$(basename "$(readlink $d/driver)" 2>/dev/null)
  echo "$1: vendor=$(cat $d/vendor) device=$(cat $d/device) class=$(cat $d/class) driver=${drv:-none}"
  echo "    control=$(cat $d/power/control) runtime=$(cat $d/power/runtime_status) state=$(cat $d/power_state)" \
       "d3cold_allowed=$(cat $d/d3cold_allowed 2>/dev/null) suspended_ms=$(cat $d/power/runtime_suspended_time)" \
       "active_ms=$(cat $d/power/runtime_active_time)"
  [ -e $d/firmware_node/path ] && echo "    acpi=$(cat $d/firmware_node/path) power_resources_D3hot=$(ls $d/firmware_node/power_resources_D3hot 2>/dev/null | tr '\n' ' ')"
}
echo "== dGPU and root port"; st $P; st $RP
echo "== root port power resource (RP13.PRPD; on=dGPU powered)"
for r in /sys/bus/pci/devices/$RP/firmware_node/power_resources_D3hot/*; do
  [ -e "$r/resource_in_use" ] && echo "    ${r##*/}: resource_in_use=$(cat $r/resource_in_use)"
done
echo "== nouveau"
if [ -d /sys/module/nouveau ]; then
  for p in runpm modeset config; do echo "    nouveau.$p=$(cat /sys/module/nouveau/parameters/$p 2>/dev/null)"; done
else echo "    not loaded"; fi
echo "== DRM nodes (render node order matters: minigbm takes the first one it can drive)"
for r in /sys/class/drm/card[0-9] /sys/class/drm/renderD*; do
  [ -e $r/device ] && echo "    ${r##*/} -> $(cat $r/device/vendor) $(basename "$(readlink $r/device/driver)")"
done
echo "== recent kernel messages"
dmesg 2>/dev/null | grep -iE "nouveau|gb_power: (dGPU|detach)|pcieport 0000:00:1d.4|surface_hotplug|shps|02:00.0" | tail -40
