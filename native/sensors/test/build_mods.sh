#!/bin/bash
# Build the patched sensor modules + i2c-stub/i2c-dev test helpers with the vermagic of /build/dist (QEMU test only).
set -e
export PATH=/build/gki/prebuilts/clang/host/linux-x86/clang-r584948c/bin:$PATH
S=/build/native/sensors/ksrc
O=/build/native/sensors/kout
KR=$(modinfo -F vermagic /build/dist/hid-sensor-accel-3d.ko | cut -d" " -f1)
MK="make -C $S O=$O ARCH=x86_64 LLVM=1 LLVM_IAS=1 -j3 KERNELRELEASE=$KR"
$MK modules_prepare
cp /build/dist/kernel_x86_64_Module.symvers $O/Module.symvers
for d in drivers/iio/accel drivers/iio/gyro drivers/iio/light; do $MK M=$S/$d modules; done
$MK M=/build/native/sensors/vmtest/extmod modules
for f in $S/drivers/iio/accel/hid-sensor-accel-3d.ko $S/drivers/iio/gyro/hid-sensor-gyro-3d.ko $S/drivers/iio/light/apds9960.ko /build/native/sensors/vmtest/extmod/*.ko; do echo "$(modinfo -F vermagic $f) $f"; done
