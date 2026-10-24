#!/bin/bash
# Kernel build #13 = #12 + Surface Book 3 cameras (IPU4P on the ipu6 driver, ov5693/ov8865/ov7251/dw9719, INT3472,
# ipu-bridge, our pinctrl-icelake/intel-lpss/i2c-designware), sensors as cros-ec IIO devices for the lapis sensors
# sub-HAL, hid-surface (drop the bogus SAM Fn button), and nouveau (opt-in; selects FW_CACHE=y in vmlinux).
# Patches/config come from the native components (copied to /build/k13/). Idempotent; markers /build/kbuild13.{done,failed}.
cd /build/gki || exit 1
exec > /build/kbuild13.log 2>&1 < /dev/null
rm -f /build/kbuild13.done /build/kbuild13.failed
export BUILD_NUMBER=16302624
BAZEL="./tools/bazel --output_user_root=/build/.bazel"
P=/build/k13
echo "=== start $(date -Is)"
fail() { echo "FAILED: $*"; touch /build/kbuild13.failed; exit 1; }
# keep build #12's symbol CRCs for the vendor-module ABI comparison
[ -f $P/vmlinux.symvers.k12 ] || cp /build/dist/vmlinux.symvers $P/vmlinux.symvers.k12 || fail "save k12 symvers"

cd /build/gki/common
cp -n arch/x86/configs/gki_defconfig /build/gki_defconfig.pre-k13
cp -n BUILD.bazel /build/BUILD.bazel.pre-k13
apply() {  # apply <patch> <marker-file> <marker-regex>
  if grep -qE "$3" "$2" 2>/dev/null; then echo "already applied: $(basename $1)"; return; fi
  git apply --check "$1" || fail "does not apply: $1"
  git apply "$1" && echo "applied $(basename $1)" || fail "applying $1"
}
apply $P/0001-camera-ipu4p-sb3.patch include/media/ipu6-pci-table.h 'IPU4P|0x8a19'
apply $P/0001-iio-hid-sensors-android-cros-ec-accel-gyro.patch drivers/iio/accel/hid-sensor-accel-3d.c 'cros-ec-accel'
apply $P/0002-iio-apds9960-android-cros-ec-light.patch drivers/iio/light/apds9960.c 'cros-ec-light'
apply $P/0001-HID-surface-drop-the-bogus-SAM-Fn-button.patch drivers/hid/Kconfig 'HID_SURFACE'

python3 - <<'PYEOF' || fail "config edit"
import re
p = "arch/x86/configs/gki_defconfig"
s = open(p).read()
lines = [l.strip() for l in open("/build/k13/camera.config") if re.match(r"\s*(CONFIG_|# CONFIG_.* is not set)", l)]
lines += ["CONFIG_HID_SURFACE=m", "CONFIG_DRM_NOUVEAU=m", "# CONFIG_DRM_NOUVEAU_CH7006 is not set",
          "# CONFIG_DRM_NOUVEAU_SIL164 is not set"]
have = set(s.splitlines())
s = s.replace("# CONFIG_FW_CACHE is not set\n", "")          # nouveau selects FW_CACHE
add = [l for l in lines if l not in have]
if add:
    s = s.rstrip("\n") + "\n" + "\n".join(add) + "\n"
open(p, "w").write(s)
print("config lines added:", len(add))
PYEOF

python3 - <<'PYEOF' || fail "BUILD.bazel edit"
mods = [l.strip() for l in open("/build/k13/bazel_modules.txt") if l.strip().endswith(".ko")]
mods += ["drivers/hid/hid-surface.ko", "drivers/gpu/drm/nouveau/nouveau.ko", "drivers/gpu/drm/drm_exec.ko",
         "drivers/gpu/drm/drm_gpuvm.ko", "drivers/gpu/drm/scheduler/gpu-sched.ko", "drivers/platform/x86/mxm-wmi.ko"]
p = "BUILD.bazel"
s = open(p).read()
anchor = '"drivers/power/supply/test_power.ko"'
assert s.count(anchor) == 2, s.count(anchor)
added = 0
out, pos = [], 0                                     # each x86 list: insert before its closing bracket
for _ in range(s.count(anchor)):
    i = s.index(anchor, pos)
    j = s.index("]", i)
    lst = s[i:j]
    extra = [m for m in mods if '"%s"' % m not in lst]
    out.append(s[pos:j] + "".join(', "%s"' % m for m in extra))
    added += len(extra)
    pos = j
out.append(s[pos:])
open(p, "w").write("".join(out))
print("module entries added:", added)
PYEOF

cd /build/gki
for attempt in 1 2 3; do
  $BAZEL run --config=stamp --config=local //common:kernel_x86_64_dist -- --destdir=/build/dist
  RC=$?
  [ $RC -eq 0 ] && break
  if grep -q "savedefconfig does not match" /build/kbuild13.log && [ $attempt -lt 3 ]; then
    C=$(ls -t /build/gki/out/cache/*/common/defconfig | head -1)
    echo "=== adopting canonical defconfig from $C"; cp "$C" common/arch/x86/configs/gki_defconfig
    continue
  fi
  M=$(grep -A40 "built but not copied" /build/kbuild13.log | grep -oE '"[^"]+\.ko",' | tr -d '",' | sort -u)
  if [ -n "$M" ] && [ $attempt -lt 3 ]; then
    echo "=== adding side-built modules: $M"
    python3 - $M <<'PYEOF'
import sys
p = "/build/gki/common/BUILD.bazel"; s = open(p).read()
anchor = '"drivers/power/supply/test_power.ko"'
out, pos = [], 0
for _ in range(s.count(anchor)):
    i = s.index(anchor, pos); j = s.index("]", i)
    out.append(s[pos:j] + "".join(', "%s"' % m for m in sys.argv[1:] if '"%s"' % m not in s[i:j])); pos = j
out.append(s[pos:]); open(p, "w").write("".join(out))
PYEOF
    continue
  fi
  break
done
echo "=== build rc=$RC $(date -Is)"
ls -la --time-style=+%m-%d_%H:%M /build/dist/bzImage /build/dist/intel-ipu6-isys.ko /build/dist/ov8865.ko \
  /build/dist/hid-surface.ko /build/dist/nouveau.ko /build/dist/hid-sensor-accel-3d.ko 2>&1
if [ $RC -eq 0 ]; then
  # vendor-module ABI: every k12 symbol must keep its CRC
  python3 - <<'PYEOF'
def load(p):
    d = {}
    for l in open(p):
        f = l.split("\t")
        if len(f) > 1: d[f[1]] = f[0]
    return d
a = load("/build/k13/vmlinux.symvers.k12"); b = load("/build/dist/vmlinux.symvers")
changed = [k for k in a if k in b and a[k] != b[k]]; gone = [k for k in a if k not in b]
print(f"=== ABI vs k12: {len(changed)} CRC changes, {len(gone)} removed, {len(set(b)-set(a))} added")
for k in changed[:40]: print("  CRC changed:", k)
for k in gone[:40]: print("  removed:", k)
PYEOF
  touch /build/kbuild13.done
else
  touch /build/kbuild13.failed
fi
