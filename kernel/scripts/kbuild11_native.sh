#!/bin/bash
# Kernel rebuild #11: native Surface Book 3 drivers on top of build #10 (vmwgfx fixes kept, VM still supported).
#   - IPTS: drivers/hid/ipts from linux-surface 0005-ipts.patch + MEI 34E4 id
#   - test_power: host battery bridge for the VM (VMware forwards only the first of the SB3's two batteries)
#   - Surface Aggregator stack, base HID keyboard/touchpad, batteries/charger, DTX, GPE, hot-plug, platform profile,
#     soc_button_array, i915 (Ice Lake Gen11), HID sensors via ISH, APDS9960 light sensor, ACPI thermal
# --config=local = incremental. Log /build/kbuild11.log; markers /build/kbuild11.done | /build/kbuild11.failed
cd /build/gki || exit 1
exec > /build/kbuild11.log 2>&1 < /dev/null
rm -f /build/kbuild11.done /build/kbuild11.failed
export BUILD_NUMBER=16302624
BAZEL="./tools/bazel --output_user_root=/build/.bazel"
echo "=== start $(date -Is)"
fail() { echo "FAILED: $*"; touch /build/kbuild11.failed; exit 1; }
for m in GB_RGBA_DUMB GB_HWCURSOR GB_DUMB_IMPORT GB_PRIME_LEAK GB_PRIME_NOHANDLE GB_DUMB_BASE_OWNS GB_DUMB_DETACH; do
  grep -rq "$m" common/drivers/gpu/drm/vmwgfx/ && echo "patch $m present" || fail "missing $m"
done

cd /build/gki/common
cp -n arch/x86/configs/gki_defconfig /build/gki_defconfig.pre-native
cp -n BUILD.bazel /build/BUILD.bazel.pre-native

# 1. IPTS (touchscreen + pen). 0005-ipts.patch targets v6.18.58: take only its drivers/hid part (the ipts driver) and
#    add the iTouch MEI PCI id (8086:34E4) in this tree's naming. Its intel-iommu identity-map quirk is replaced by
#    iommu.passthrough=1 on the native command line.
if [ ! -d drivers/hid/ipts ]; then
  git apply --include='drivers/hid/*' /build/0005-ipts.patch || fail "applying ipts driver (drivers/hid part)"
  echo "applied drivers/hid part of 0005-ipts.patch"
fi
if ! grep -q "MEI_DEV_ID_ICP_LP_3" drivers/misc/mei/hw-me-regs.h; then
  python3 - <<'PYEOF' || fail "MEI 34E4 id"
h = "drivers/misc/mei/hw-me-regs.h"; s = open(h).read()
a = "#define MEI_DEV_ID_ICP_LP     0x34E0  /* Ice Lake Point LP */\n"
assert s.count(a) == 1
open(h, "w").write(s.replace(a, a + "#define MEI_DEV_ID_ICP_LP_3   0x34E4  /* Ice Lake Point LP 3 (iTouch) */\n"))
c = "drivers/misc/mei/pci-me.c"; s = open(c).read()
a = "\t{MEI_PCI_DEVICE(MEI_DEV_ID_ICP_LP, MEI_ME_PCH12_CFG)},\n"
assert s.count(a) == 1
open(c, "w").write(s.replace(a, a + "\t{MEI_PCI_DEVICE(MEI_DEV_ID_ICP_LP_3, MEI_ME_PCH12_CFG)},\n"))
print("added MEI_DEV_ID_ICP_LP_3")
PYEOF
fi

# 2. config delta
python3 - <<'PYEOF' || fail "config edit"
p = "arch/x86/configs/gki_defconfig"
s = open(p).read()
want = """CONFIG_SURFACE_AGGREGATOR=m
CONFIG_SURFACE_AGGREGATOR_REGISTRY=m
CONFIG_SURFACE_AGGREGATOR_HUB=m
CONFIG_SURFACE_AGGREGATOR_CDEV=m
CONFIG_SURFACE_ACPI_NOTIFY=m
CONFIG_SURFACE_DTX=m
CONFIG_SURFACE_GPE=m
CONFIG_SURFACE_HOTPLUG=m
CONFIG_SURFACE_PLATFORM_PROFILE=m
CONFIG_SURFACE_HID=m
CONFIG_BATTERY_SURFACE=m
CONFIG_CHARGER_SURFACE=m
CONFIG_INPUT_SOC_BUTTON_ARRAY=m
CONFIG_DRM_I915=m
CONFIG_HID_SENSOR_HUB=m
CONFIG_HID_SENSOR_ACCEL_3D=m
CONFIG_HID_SENSOR_GYRO_3D=m
CONFIG_HID_SENSOR_ALS=m
CONFIG_HID_SENSOR_DEVICE_ROTATION=m
CONFIG_HID_SENSOR_INCLINOMETER_3D=m
CONFIG_HID_SENSOR_MAGNETOMETER_3D=m
CONFIG_APDS9960=m
CONFIG_ACPI_THERMAL=m
CONFIG_HID_IPTS=m
CONFIG_TEST_POWER=m
""".split()
add = [w for w in want if w not in s.split()]
if add:
    s = s.rstrip("\n") + "\n" + "\n".join(add) + "\n"
    open(p, "w").write(s)
print("config lines added:", len(add))
PYEOF

# 3. module lists (module_implicit_outs + modules_superset)
python3 - <<'PYEOF' || fail "BUILD.bazel edit"
mods = """lib/crc/crc-itu-t.ko
drivers/platform/surface/aggregator/surface_aggregator.ko
drivers/platform/surface/surface_aggregator_registry.ko
drivers/platform/surface/surface_aggregator_hub.ko
drivers/platform/surface/surface_aggregator_cdev.ko
drivers/platform/surface/surface_acpi_notify.ko
drivers/platform/surface/surface_dtx.ko
drivers/platform/surface/surface_gpe.ko
drivers/platform/surface/surface_hotplug.ko
drivers/platform/surface/surface_platform_profile.ko
drivers/acpi/platform_profile.ko
drivers/hid/surface-hid/surface_hid_core.ko
drivers/hid/surface-hid/surface_hid.ko
drivers/power/supply/surface_battery.ko
drivers/power/supply/surface_charger.ko
drivers/input/misc/soc_button_array.ko
drivers/gpu/drm/i915/i915.ko
drivers/gpu/drm/display/drm_display_helper.ko
drivers/gpu/drm/drm_buddy.ko
drivers/i2c/algos/i2c-algo-bit.ko
drivers/acpi/video.ko
drivers/platform/x86/wmi.ko
drivers/char/agp/intel-gtt.ko
drivers/hid/hid-sensor-hub.ko
drivers/iio/common/hid-sensors/hid-sensor-iio-common.ko
drivers/iio/common/hid-sensors/hid-sensor-trigger.ko
drivers/iio/buffer/kfifo_buf.ko
drivers/iio/buffer/industrialio-triggered-buffer.ko
drivers/iio/accel/hid-sensor-accel-3d.ko
drivers/iio/gyro/hid-sensor-gyro-3d.ko
drivers/iio/light/hid-sensor-als.ko
drivers/iio/orientation/hid-sensor-rotation.ko
drivers/iio/orientation/hid-sensor-incl-3d.ko
drivers/iio/magnetometer/hid-sensor-magn-3d.ko
drivers/iio/light/apds9960.ko
drivers/acpi/thermal.ko
drivers/hid/ipts/ipts.ko
drivers/mfd/mfd-core.ko
drivers/power/supply/test_power.ko""".split()
# built as side effects of the above; also named in other architectures' lists, so check the x86 anchor explicitly
side = ["drivers/platform/x86/wmi-bmof.ko", "drivers/iio/buffer/kfifo_buf.ko",
        "drivers/iio/buffer/industrialio-triggered-buffer.ko", "drivers/gpu/drm/display/drm_display_helper.ko"]
p = "BUILD.bazel"
s = open(p).read()
anchor = '"drivers/acpi/battery.ko"]'
extra = [m for m in mods if '"%s"' % m not in s]
if extra:                      # (a rerun finds them all present; the anchor has moved then)
    assert s.count(anchor) == 2, s.count(anchor)
    s = s.replace(anchor, '"drivers/acpi/battery.ko", ' + ", ".join('"%s"' % m for m in extra + side) + "]")
    open(p, "w").write(s)
print("modules added:", len(extra))
PYEOF

cd /build/gki
$BAZEL run --config=stamp --config=local //common:kernel_x86_64_dist -- --destdir=/build/dist
RC=$?
echo "=== build rc=$RC $(date -Is)"
ls -la --time-style=+%m-%d_%H:%M /build/dist/bzImage /build/dist/i915.ko /build/dist/surface_aggregator.ko /build/dist/ipts.ko \
  /build/dist/surface_hid.ko /build/dist/surface_battery.ko /build/dist/vmwgfx.ko 2>&1
[ $RC -eq 0 ] && touch /build/kbuild11.done || touch /build/kbuild11.failed
